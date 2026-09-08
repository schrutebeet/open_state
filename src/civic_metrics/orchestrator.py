from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from civic_metrics.artifacts import store_artifact
from civic_metrics.catalog import Catalog, DatasetDefinition, IndicatorDefinition, sync_catalog
from civic_metrics.connectors import CONNECTORS
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.connectors.datacomex import MissingCredentialsError
from civic_metrics.genai_validation import GenAIDataValidator, data_source_type, skipped_validation
from civic_metrics.http import HttpClient
from civic_metrics.models import GenAIValidationLog, IngestionRun, Observation, SourceDataset
from civic_metrics.processors import DerivedIndicatorEngine, FormulaError
from civic_metrics.repository import save_observation_with_status
from civic_metrics.settings import Settings
from civic_metrics.validation import validate_candidate

LOGGER = logging.getLogger(__name__)


@dataclass
class DatasetResult:
    dataset: str
    status: str
    required: bool = True
    fetched_observations: int = 0
    inserted_observations: int = 0
    extracted_by_indicator: dict[str, int] = field(default_factory=dict)
    history_by_indicator: dict[str, int] = field(default_factory=dict)
    snapshot_by_indicator: dict[str, int] = field(default_factory=dict)
    expected_indicators: int = 0
    extracted_indicators: int = 0
    missing_indicators: list[str] = field(default_factory=list)
    artifact_path: str | None = None
    error: str | None = None
    genai_validation: dict[str, object] | list[dict[str, object]] | None = None


@dataclass
class PipelineResult:
    run_id: int
    status: str
    started_at: str
    finished_at: str
    datasets: list[DatasetResult] = field(default_factory=list)
    derived_inserted: int = 0
    derived_skipped: int = 0
    derived_skipped_details: list[str] = field(default_factory=list)
    derived_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "datasets": [asdict(item) for item in self.datasets],
            "derived_inserted": self.derived_inserted,
            "derived_skipped": self.derived_skipped,
            "derived_skipped_details": self.derived_skipped_details,
            "derived_errors": self.derived_errors,
        }


class PipelineOrchestrator:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        session_factory: sessionmaker[Session],
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.session_factory = session_factory

    def run(self, selected_datasets: Iterable[str] | None = None) -> PipelineResult:
        selected = set(selected_datasets or [])
        started_at = datetime.now(UTC)
        with self.session_factory() as session:
            sync_catalog(session, self.catalog)
            run = IngestionRun(started_at=started_at, status="running", summary_json={})
            session.add(run)
            session.commit()
            run_id = run.id

        http = HttpClient(self.settings.http_timeout_seconds)
        results: list[DatasetResult] = []
        try:
            for dataset_definition in self.catalog.datasets:
                if selected and dataset_definition.code not in selected:
                    continue
                if not dataset_definition.enabled:
                    continue
                indicators = [
                    item
                    for item in self.catalog.indicators
                    if item.enabled and item.dataset == dataset_definition.code
                ]
                if not indicators:
                    continue
                result = self._run_dataset(
                    run_id,
                    dataset_definition,
                    indicators,
                    http,
                )
                results.append(result)
                if result.status == "failed" and self.settings.fail_fast:
                    break

            (
                derived_inserted,
                derived_skipped,
                derived_skipped_details,
                derived_errors,
            ) = self._run_derived()
        finally:
            http.close()

        failures = sum(item.status == "failed" and item.required for item in results)
        successes = sum(item.status == "success" for item in results)
        incomplete = sum(
            item.required and item.status in {"partial", "empty", "skipped"} for item in results
        )
        if failures == 0 and incomplete == 0 and not derived_errors:
            status = "success"
        elif successes == 0 and failures > 0:
            status = "failed"
        else:
            status = "partial"
        finished_at = datetime.now(UTC)
        pipeline_result = PipelineResult(
            run_id=run_id,
            status=status,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            datasets=results,
            derived_inserted=derived_inserted,
            derived_skipped=derived_skipped,
            derived_skipped_details=derived_skipped_details,
            derived_errors=derived_errors,
        )
        with self.session_factory() as session:
            run = session.get(IngestionRun, run_id)
            if run is not None:
                run.status = status
                run.finished_at = finished_at
                run.summary_json = pipeline_result.to_dict()
                session.commit()
        return pipeline_result

    def _run_dataset(
        self,
        run_id: int,
        definition: DatasetDefinition,
        indicators: list[IndicatorDefinition],
        http: HttpClient,
    ) -> DatasetResult:
        connector_type = CONNECTORS.get(definition.connector)
        if connector_type is None:
            return DatasetResult(
                dataset=definition.code,
                status="failed",
                required=definition.required,
                expected_indicators=len(indicators),
                error=f"Unknown connector {definition.connector}",
            )
        connector = connector_type()
        try:
            frequencies = {item.frequency for item in indicators}
            if len(frequencies) != 1:
                raise ValueError(
                    f"Dataset {definition.code} mixes indicator frequencies: {sorted(frequencies)}"
                )
            documents = connector.collect(
                definition,
                ConnectorContext(
                    settings=self.settings,
                    http=http,
                    frequency=next(iter(frequencies)),
                ),
                indicators,
            )
            candidates = [candidate for _, rows in documents for candidate in rows]
            validations = []
            with self.session_factory() as session:
                dataset_row = session.scalar(
                    select(SourceDataset)
                    .options(selectinload(SourceDataset.source))
                    .where(SourceDataset.code == definition.code)
                )
                run = session.get(IngestionRun, run_id)
                if dataset_row is None or run is None:
                    raise RuntimeError(f"Database catalog row missing for {definition.code}")
                inserted = 0
                extracted_by_indicator = {item.code: 0 for item in indicators}
                history_by_indicator = {item.code: 0 for item in indicators}
                definitions = {item.code: item for item in indicators}
                artifacts = []
                for document, rows in documents:
                    document_artifact = store_artifact(
                        session, self.settings.resolved_artifacts_dir(), run, dataset_row, document,
                    )
                    artifacts.append(document_artifact)
                    for candidate in rows:
                        extracted_by_indicator[candidate.indicator_code] += 1
                        validate_candidate(candidate, definitions[candidate.indicator_code])
                        _, written = save_observation_with_status(
                            session, candidate, document_artifact
                        )
                        if written:
                            inserted += 1
                            history_by_indicator[candidate.indicator_code] += 1
                artifact = artifacts[0]
                if self.settings.genai_validation_enabled and self.settings.should_validate_dataset(
                    dataset_row.id
                ):
                    validator = GenAIDataValidator(
                        model=self.settings.genai_validation_model,
                        max_payload_chars=self.settings.genai_validation_max_payload_chars,
                        api_key=(
                            self.settings.openai_api_key.get_secret_value()
                            if self.settings.openai_api_key is not None
                            else None
                        ),
                    )
                    for (payload, validation_candidates), document_artifact in zip(
                        documents, artifacts, strict=True
                    ):
                        source_type = data_source_type(definition, payload)
                        validation = (
                            skipped_validation(source_type)
                            if source_type == "API"
                            else validator.validate(
                                definition,
                                indicators,
                                payload,
                                validation_candidates,
                                dataset_id=dataset_row.id,
                            )
                        )
                        validations.append(validation)
                        reason = validation.description if validation.status == "skipped" else None
                        session.add(
                            GenAIValidationLog(
                                run_id=run.id,
                                dataset_id=dataset_row.id,
                                raw_artifact_id=document_artifact.id,
                                validated_at=datetime.now(UTC),
                                model=validation.model,
                                status=validation.status,
                                decision=validation.decision,
                                confidence=(
                                    Decimal(str(validation.confidence))
                                    if validation.confidence is not None
                                    else None
                                ),
                                description=validation.description,
                                error=validation.error,
                                payload_truncated=validation.payload_truncated,
                                request_summary_json={
                                    "source_url": payload.source_url,
                                    "content_type": payload.content_type,
                                    "payload_sha256": payload.sha256,
                                    "payload_bytes": len(payload.body),
                                    "candidate_count": len(validation_candidates),
                                    "indicator_codes": [item.code for item in indicators],
                                    "max_payload_chars": (
                                        self.settings.genai_validation_max_payload_chars
                                    ),
                                    "value": "NA" if validation.status == "skipped" else None,
                                    "reason": reason,
                                    "data_source_type": source_type,
                                },
                                response_json={
                                    **validation.to_dict(),
                                    "value": "NA" if validation.status == "skipped" else None,
                                    "reason": reason,
                                    "data_source_type": source_type,
                                },
                            )
                        )
                session.commit()
                path = artifact.local_path
            expected_codes = {item.code for item in indicators}
            extracted_codes = {item.indicator_code for item in candidates}
            missing_codes = sorted(expected_codes - extracted_codes)
            if not candidates:
                status = "empty"
                error = "No observations matched the configured selectors"
            elif missing_codes:
                status = "partial"
                error = f"Missing indicators: {', '.join(missing_codes)}"
            else:
                status = "success"
                error = None
            genai_validation = None
            if validations:
                genai_validation = [item.to_dict() for item in validations]
                failed_validation = next(
                    (item for item in validations if item.status == "failed"), None
                )
                error_validation = next(
                    (item for item in validations if item.status == "error"), None
                )
                if failed_validation is not None and self.settings.genai_validation_strict:
                    status = "partial"
                    error = failed_validation.description or "GenAI validation found a mismatch"
                if error_validation is not None:
                    LOGGER.warning(
                        "GenAI validation failed for dataset %s: %s",
                        definition.code,
                        error_validation.error,
                    )
            validation_output = (
                genai_validation[0]
                if genai_validation is not None and len(genai_validation) == 1
                else genai_validation
            )
            LOGGER.info(
                "dataset=%s status=%s candidates=%s inserted=%s indicators=%s/%s",
                definition.code,
                status,
                len(candidates),
                inserted,
                len(extracted_codes),
                len(expected_codes),
            )
            return DatasetResult(
                dataset=definition.code,
                status=status,
                required=definition.required,
                fetched_observations=len(candidates),
                inserted_observations=inserted,
                extracted_by_indicator=extracted_by_indicator,
                history_by_indicator=history_by_indicator,
                expected_indicators=len(expected_codes),
                extracted_indicators=len(extracted_codes),
                missing_indicators=missing_codes,
                artifact_path=path,
                error=error,
                genai_validation=validation_output,
            )
        except MissingCredentialsError as exc:
            LOGGER.warning("Dataset %s skipped: %s", definition.code, exc)
            return DatasetResult(
                dataset=definition.code,
                status="skipped",
                required=definition.required,
                expected_indicators=len(indicators),
                error=str(exc),
            )
        except Exception as exc:  # keep independent sources isolated
            LOGGER.exception("Dataset %s failed", definition.code)
            return DatasetResult(
                dataset=definition.code,
                status="failed",
                required=definition.required,
                expected_indicators=len(indicators),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_derived(self) -> tuple[int, int, list[str], list[str]]:
        inserted = 0
        skipped = 0
        skipped_details: list[str] = []
        errors: list[str] = []
        with self.session_factory() as session:
            before_count = session.scalar(select(func.count()).select_from(Observation)) or 0
            engine = DerivedIndicatorEngine(session)
            for definition in self.catalog.indicators:
                if not definition.enabled or definition.extraction.kind != "derived":
                    continue
                try:
                    materialised = engine.materialise_history(
                        definition,
                        self.settings.lookback_period,
                    )
                    if not materialised:
                        skipped += 1
                        skipped_details.append(
                            f"{definition.code}: no values materialised; "
                            "dependencies may be missing"
                        )
                    else:
                        inserted += len(materialised)
                except FormulaError as exc:
                    skipped += 1
                    message = f"{definition.code}: {exc}"
                    skipped_details.append(message)
                    errors.append(message)
                    LOGGER.warning("Could not derive %s", message)
            session.flush()
            after_count = session.scalar(select(func.count()).select_from(Observation)) or 0
            inserted = max(0, int(after_count) - int(before_count))
            session.commit()
        return inserted, skipped, skipped_details, errors

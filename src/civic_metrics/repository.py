from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from civic_metrics.domain import ObservationCandidate
from civic_metrics.models import Indicator, Observation, ObservationDependency, RawArtifact


def save_observation(
    session: Session,
    candidate: ObservationCandidate,
    artifact: RawArtifact | None,
) -> Observation:
    indicator = session.scalar(select(Indicator).where(Indicator.code == candidate.indicator_code))
    if indicator is None:
        raise KeyError(f"Unknown indicator {candidate.indicator_code}")

    existing = session.scalar(
        select(Observation).where(
            and_(
                Observation.indicator_id == indicator.id,
                Observation.period_start == candidate.period.start,
                Observation.period_end == candidate.period.end,
                Observation.geography == candidate.geography,
                Observation.source_code == candidate.source_code,
                Observation.dataset_code == candidate.dataset_code,
                Observation.source_series == candidate.source_series,
                Observation.value == candidate.value,
            )
        )
    )
    if existing is not None:
        return existing

    observation = Observation(
        indicator_id=indicator.id,
        raw_artifact_id=artifact.id if artifact else None,
        source_code=candidate.source_code,
        dataset_code=candidate.dataset_code,
        period_start=candidate.period.start,
        period_end=candidate.period.end,
        period_label=candidate.period.label,
        frequency=candidate.period.frequency,
        geography=candidate.geography,
        value=candidate.value,
        unit=candidate.unit,
        status=candidate.status,
        is_provisional=candidate.is_provisional,
        source_series=candidate.source_series,
        source_url=candidate.source_url,
        published_at=candidate.published_at,
        retrieved_at=datetime.now(timezone.utc),
        metadata_json={
            **candidate.metadata,
            "source_code": candidate.source_code,
            "dataset_code": candidate.dataset_code,
        },
    )
    session.add(observation)
    session.flush()
    return observation


def latest_observation(session: Session, indicator_code: str) -> Observation | None:
    return session.scalar(
        select(Observation)
        .join(Indicator)
        .where(Indicator.code == indicator_code)
        .order_by(Observation.period_end.desc(), Observation.retrieved_at.desc())
        .limit(1)
    )


def observation_for_period(
    session: Session,
    indicator_code: str,
    period_start: object,
    period_end: object,
) -> Observation | None:
    return session.scalar(
        select(Observation)
        .join(Indicator)
        .where(
            Indicator.code == indicator_code,
            Observation.period_start == period_start,
            Observation.period_end == period_end,
        )
        .order_by(Observation.retrieved_at.desc())
        .limit(1)
    )


def add_dependencies(
    session: Session,
    observation: Observation,
    dependency_observations: list[Observation],
) -> None:
    for dependency in dependency_observations:
        exists = session.scalar(
            select(ObservationDependency).where(
                ObservationDependency.observation_id == observation.id,
                ObservationDependency.depends_on_observation_id == dependency.id,
            )
        )
        if exists is None:
            session.add(
                ObservationDependency(
                    observation_id=observation.id,
                    depends_on_observation_id=dependency.id,
                )
            )


def decimal_value(observation: Observation) -> Decimal:
    return Decimal(str(observation.value))

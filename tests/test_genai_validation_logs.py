from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.models import GenAIValidationLog, IngestionRun, Source, SourceDataset


def test_genai_validation_log_is_persisted_with_its_dataset_and_run() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    init_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        source = Source(
            code="test_source",
            name="Test source",
            base_url="https://example.test",
            institution_type="test",
        )
        session.add(source)
        session.flush()
        dataset = SourceDataset(
            code="test_dataset",
            source_id=source.id,
            connector="test",
        )
        run = IngestionRun(started_at=datetime.now(UTC), status="running", summary_json={})
        session.add_all([dataset, run])
        session.flush()
        session.add(
            GenAIValidationLog(
                run_id=run.id,
                dataset_id=dataset.id,
                validated_at=datetime.now(UTC),
                model="gpt-5-nano",
                status="passed",
                decision="Valid",
                confidence=Decimal("0.95"),
                description="Source and result agree.",
                payload_truncated=False,
                request_summary_json={"candidate_count": 1},
                response_json={
                    "decision": "Valid",
                    "confidence": 0.95,
                    "description": "Source and result agree.",
                },
            )
        )
        session.commit()

        saved = session.scalar(select(GenAIValidationLog))

    assert saved is not None
    assert saved.decision == "Valid"
    assert saved.request_summary_json["candidate_count"] == 1

from datetime import UTC, date, datetime
from decimal import Decimal

from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.genai_validation import _candidate_dict, _payload_evidence


def test_json_payload_evidence_is_readable_and_reports_truncation() -> None:
    payload = DatasetPayload(
        dataset_code="sample",
        source_code="source",
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/data.json",
        content_type="application/json",
        body=b'{"value": 123, "period": "2026-01"}',
        sha256="0" * 64,
    )

    evidence, truncated = _payload_evidence(payload, 10)

    assert evidence.startswith("{\n")
    assert len(evidence) == 10
    assert truncated is True


def test_candidate_is_serialized_without_losing_decimal_precision() -> None:
    candidate = ObservationCandidate(
        indicator_code="metric",
        source_code="source",
        dataset_code="sample",
        period=Period(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
            label="2026-01",
            frequency="monthly",
        ),
        value=Decimal("123.4500"),
        unit="people",
    )

    assert _candidate_dict(candidate)["value"] == "123.4500"

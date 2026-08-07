import json
from datetime import UTC, date, datetime
from decimal import Decimal

from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.genai_validation import _OUTPUT_SCHEMA, _candidate_dict, _payload_evidence


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

    assert evidence.startswith('{"value":')
    assert len(evidence) == 10
    assert truncated is True


def test_json_payload_evidence_uses_lossless_table_encoding_for_repeated_objects() -> None:
    payload = DatasetPayload(
        dataset_code="sample",
        source_code="source",
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/data.json",
        content_type="application/json",
        body=(
            b'{"Data":[{"Fecha":1774994400000,"Anyo":2026,"Valor":10.0851},'
            b'{"Fecha":1767222000000,"Anyo":2026,"Valor":9.9755}]}'
        ),
        sha256="0" * 64,
    )

    evidence, truncated = _payload_evidence(payload, 1_000)
    compacted = json.loads(evidence)

    assert truncated is False
    assert compacted["Data"] == {
        "__civic_metrics_encoding__": "table",
        "columns": ["Fecha", "Anyo", "Valor"],
        "rows": [[1774994400000, 2026, 10.0851], [1767222000000, 2026, 9.9755]],
    }


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


def test_validation_schema_allows_only_the_requested_json_shape() -> None:
    schema = _OUTPUT_SCHEMA["schema"]

    assert schema["required"] == ["decision", "confidence", "description"]
    assert schema["properties"]["decision"]["enum"] == ["Valid", "Invalid"]
    assert schema["properties"]["confidence"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    assert schema["additionalProperties"] is False

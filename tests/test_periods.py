from datetime import UTC, datetime

from civic_metrics.parsers.common import period_from_ine_date, period_from_label


def test_spanish_quarter_label_with_number_before_t() -> None:
    period = period_from_label("2T 2026", "quarterly")
    assert period.label == "2026-Q2"


def test_ine_timestamp_uses_the_spanish_calendar_at_a_month_boundary() -> None:
    # 2026-03-31 22:30 UTC is 2026-04-01 00:30 in Europe/Madrid.
    timestamp_ms = datetime(2026, 3, 31, 22, 30, tzinfo=UTC).timestamp() * 1_000

    period = period_from_ine_date(timestamp_ms, "monthly")

    assert period.label == "2026-04"

from civic_metrics.parsers.common import period_from_label


def test_spanish_quarter_label_with_number_before_t() -> None:
    period = period_from_label("2T 2026", "quarterly")
    assert period.label == "2026-Q2"

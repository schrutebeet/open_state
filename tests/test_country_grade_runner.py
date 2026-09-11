from datetime import date
from pathlib import Path

from civic_metrics.country_grade_runner import parse_month


def test_parse_month_expands_inclusive_ranges_and_single_months() -> None:
    assert parse_month("202601-202604") == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
    ]
    assert parse_month("202606") == [date(2026, 6, 1)]


def test_source_wrapper_points_at_project_src() -> None:
    assert Path("src/generate_country_grade.py").is_file()

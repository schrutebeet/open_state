from pathlib import Path

from openpyxl import Workbook

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.social_security_pensions import SocialSecurityPensionsConnector

ROOT = Path(__file__).parents[1]


def test_pensioner_seed_is_complete_and_keeps_each_source() -> None:
    catalog = load_catalog(ROOT / "config")
    dataset = catalog.dataset_by_code["social_security_pensioners"]
    indicators = [item for item in catalog.indicators if item.dataset == dataset.code]

    _, candidates = SocialSecurityPensionsConnector._seed_pensioner_document(dataset, indicators)

    assert len(candidates) == 12
    assert [item.period.label for item in candidates] == [
        "2025-09", "2025-10", "2025-11", "2025-12",
        "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08",
    ]
    assert candidates[0].value == 9389688
    assert candidates[-1].value == 9527484
    assert len({item.source_url for item in candidates}) == 12
    assert all(item.indicator_code == "pensioner_count" for item in candidates)


def test_ca_total_skips_incomplete_total_row() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "CA_Total sistema"
    sheet.append(["Total sistema"])
    sheet.append(["Total sistema", 100, None, 1_500, None, None, None, None, None, 1_700])

    values = SocialSecurityPensionsConnector._extract_ca_total(workbook)

    assert values["pension_count"][0] == 100
    assert values["average_pension"][0] == 1500

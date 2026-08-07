from pathlib import Path

from civic_metrics.catalog import load_catalog


def test_catalog_has_unique_references() -> None:
    catalog = load_catalog(Path("config"))
    assert len(catalog.categories) == 4
    assert len(catalog.datasets) == 19
    assert len(catalog.indicators) == 55
    assert len({item.code for item in catalog.indicators}) == len(catalog.indicators)
    assert catalog.indicator_by_code["goods_trade_balance"].formula == (
        "goods_exports - goods_imports"
    )
    assert catalog.indicator_by_code["public_debt_gdp"].dependencies == [
        "public_debt_total",
        "gdp_nominal",
    ]

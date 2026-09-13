from pathlib import Path

from civic_metrics.catalog import load_catalog


def test_catalog_has_unique_references() -> None:
    catalog = load_catalog(Path("config"))
    assert len(catalog.categories) == 10
    assert len(catalog.datasets) == 217
    assert len(catalog.indicators) == 471
    assert len({item.code for item in catalog.indicators}) == len(catalog.indicators)
    assert all(item.name_es and item.description_es for item in catalog.indicators)
    assert catalog.indicator_by_code["gdp_nominal"].name_es == "PIB nominal"
    assert catalog.indicator_by_code["goods_trade_balance"].formula == (
        "goods_exports - goods_imports"
    )
    assert catalog.indicator_by_code["public_debt_gdp"].dependencies == [
        "public_debt_total",
        "gdp_nominal",
    ]
    assert catalog.indicator_by_code["energy_intensity_gdp"].dataset == (
        "eurostat_energy_intensity"
    )
    assert catalog.indicator_by_code["energy_intensity_gdp"].extraction.dimension_filters == {
        "freq": "A",
        "nrg_bal": "EI_GDP_CLV15",
        "unit": "KGOE_TEUR",
        "geo": "ES",
    }
    assert catalog.indicator_by_code["passenger_cars_per_capita"].dataset == (
        "eurostat_passenger_cars_per_capita"
    )
    assert catalog.indicator_by_code[
        "enterprise_e_invoicing_rate"
    ].extraction.dimension_filters == {
        "freq": "A",
        "size_emp": "GE10",
        "nace_r2": "C10-S951_X_K",
        "indic_is": "E_INV4S_AP",
        "unit": "PC_ENT",
        "geo": "ES",
    }
    assert (
        catalog.indicator_by_code["freshwater_abstraction_households"].extraction.dimension_filters[
            "wat_proc"
        ]
        == "ABS_HH"
    )
    assert catalog.indicator_by_code["female_ict_specialist_employment_share"].dependencies == [
        "ict_specialists_female_count",
        "employment_count_15_74",
    ]
    assert catalog.indicator_by_code["employment_rate_age_55_74"].dependencies == [
        "employment_count_55_64_detail",
        "employment_count_65_74_detail",
        "employment_rate_over_55",
        "employment_rate_65_74_detail",
    ]
    assert catalog.indicator_by_code[
        "long_term_unemployment_youth_rate"
    ].extraction.series_code == ("EPA712546")
    assert (
        catalog.indicator_by_code["general_practitioners_per_100k"].extraction.dimension_filters[
            "med_spec"
        ]
        == "GEN_PRAC"
    )
    assert "social_security_affiliates_adjusted" not in catalog.indicator_by_code


def test_reported_eurostat_selectors_are_fully_specified() -> None:
    catalog = load_catalog(Path("config"))
    indicators = catalog.indicator_by_code

    assert set(indicators["foreign_tourist_arrival_share"].extraction.eurostat_operands) == {
        "numerator",
        "denominator",
    }
    assert set(indicators["road_freight_international_share"].extraction.eurostat_operands) == {
        "numerator",
        "denominator",
    }
    for code, unit in (
        ("rail_passenger_count", "THS_PAS"),
        ("rail_passenger_km", "MIO_PKM"),
    ):
        filters = indicators[code].extraction.dimension_filters
        assert filters["unit"] == unit
        assert filters["tra_cov"] == "TOTAL"

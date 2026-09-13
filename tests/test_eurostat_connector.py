import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from civic_metrics.catalog import DatasetDefinition, ExtractionDefinition, IndicatorDefinition
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.connectors.eurostat import EurostatJsonStatConnector
from civic_metrics.domain import DatasetPayload
from civic_metrics.settings import Settings


def _indicator(
    code: str,
    filters: dict[str, str],
    *,
    frequency: str = "monthly",
    calculation: str = "value",
    operands: dict[str, dict[str, str]] | None = None,
) -> IndicatorDefinition:
    return IndicatorDefinition(
        code=code,
        name=code,
        description=code,
        category="economy",
        dataset="eurostat_hicp_components",
        unit="percent",
        frequency=frequency,
        extraction=ExtractionDefinition(
            kind="eurostat_jsonstat",
            dimension_filters=filters,
            eurostat_calculation=calculation,
            eurostat_operands=operands or {},
        ),
    )


def test_build_query_filters_dimensions_and_requests_only_lookback_periods() -> None:
    dataset = DatasetDefinition(
        code="eurostat_hicp_components",
        source="eurostat",
        connector="eurostat_jsonstat",
        endpoint="https://example.test/data",
        config={"query": {"geo": "ES", "lang": "en"}},
    )
    common = {"freq": "M", "unit": "RCH_A", "geo": "ES"}

    query = EurostatJsonStatConnector._build_query(
        dataset,
        [
            _indicator("food", {**common, "coicop": "CP01"}),
            _indicator("housing", {**common, "coicop": "CP04"}),
        ],
        requested=12,
    )

    assert query == {
        "geo": "ES",
        "lang": "en",
        "freq": "M",
        "unit": "RCH_A",
        "coicop": ["CP01", "CP04"],
        "lastTimePeriod": 12,
    }
    request = httpx.Request("GET", dataset.endpoint, params=query)
    assert request.url.params.get_list("coicop") == ["CP01", "CP04"]


def test_build_query_keeps_regional_aggregate_dimension_unfiltered() -> None:
    dataset = DatasetDefinition(
        code="eurostat_regional_gdp_per_capita",
        source="eurostat",
        connector="eurostat_jsonstat",
        endpoint="https://example.test/data",
        config={"query": {"freq": "A", "unit": "EUR_HAB"}},
    )
    indicator = IndicatorDefinition(
        code="regional_gdp_per_capita_range",
        name="Regional GDP per capita range",
        description="Range across Spanish NUTS-3 regions.",
        category="economy",
        dataset=dataset.code,
        unit="euros_per_person",
        frequency="annual",
        extraction=ExtractionDefinition(
            kind="eurostat_jsonstat",
            dimension_filters={"freq": "A", "unit": "EUR_HAB"},
            eurostat_calculation="range",
            eurostat_aggregate_dimension="geo",
            eurostat_aggregate_prefix="ES",
            eurostat_aggregate_code_length=5,
        ),
    )

    query = EurostatJsonStatConnector._build_query(dataset, [indicator], requested=12)

    assert "geo" not in query
    assert query["lastTimePeriod"] == 12


def test_build_query_avoids_last_time_filter_for_explicit_or_mixed_time_scopes() -> None:
    dataset = DatasetDefinition(
        code="eurostat_test",
        source="eurostat",
        connector="eurostat_jsonstat",
        endpoint="https://example.test/data",
    )
    annual = _indicator("annual", {"freq": "A", "geo": "ES"}, frequency="annual")
    monthly = _indicator("monthly", {"freq": "M", "geo": "ES"})

    mixed_query = EurostatJsonStatConnector._build_query(
        dataset, [annual, monthly], requested=12
    )
    explicit_time_query = EurostatJsonStatConnector._build_query(
        dataset,
        [_indicator("fixed_time", {"freq": "M", "geo": "ES", "time": "2025-01"})],
        requested=12,
    )

    assert "lastTimePeriod" not in mixed_query
    assert "lastTimePeriod" not in explicit_time_query


def test_collect_retries_full_time_range_if_recent_slice_has_too_few_values(monkeypatch) -> None:
    dataset = DatasetDefinition(
        code="eurostat_hicp_components",
        source="eurostat",
        connector="eurostat_jsonstat",
    )
    indicators = [
        _indicator("food", {"freq": "M", "unit": "RCH_A", "coicop": "CP01", "geo": "ES"}),
        _indicator("housing", {"freq": "M", "unit": "RCH_A", "coicop": "CP04", "geo": "ES"}),
    ]
    full_payload = _payload()
    sparse_document = json.loads(full_payload.body)
    sparse_document["value"] = {"2": 1.3, "5": 2.3}
    recent_payload = replace(
        full_payload,
        body=json.dumps(sparse_document).encode(),
        metadata={
            "lookback_periods": 2,
            "query": {"lastTimePeriod": 2},
            "time_limit_applied": True,
        },
    )
    calls: list[bool] = []
    connector = EurostatJsonStatConnector()

    def fake_fetch(dataset, context, selected_indicators, *, include_last_time=True):
        calls.append(include_last_time)
        return recent_payload if include_last_time else full_payload

    monkeypatch.setattr(connector, "_fetch", fake_fetch)
    context = ConnectorContext(settings=Settings(lookback_period=2), http=None)

    documents = connector.collect(dataset, context, indicators)

    assert calls == [True, False]
    observations = documents[0][1]
    assert len(observations) == 4
    assert {item.period.label for item in observations} == {"2025-02", "2025-03"}


def _payload() -> DatasetPayload:
    return DatasetPayload(
        dataset_code="eurostat_hicp_components",
        source_code="eurostat",
        fetched_at=datetime.now(UTC),
        source_url="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/test",
        content_type="application/json",
        body=(Path(__file__).parent / "fixtures" / "eurostat_hicp_sample.json").read_bytes(),
        sha256="test",
        metadata={"lookback_periods": 2},
    )


def test_filters_jsonstat_and_applies_lookback_to_each_indicator() -> None:
    common = {"freq": "M", "unit": "RCH_A", "geo": "ES"}
    observations = EurostatJsonStatConnector().extract(
        DatasetDefinition(
            code="eurostat_hicp_components",
            source="eurostat",
            connector="eurostat_jsonstat",
        ),
        _payload(),
        [
            _indicator("food_inflation_yoy", {**common, "coicop": "CP01"}),
            _indicator("housing_energy_inflation_yoy", {**common, "coicop": "CP04"}),
        ],
    )

    assert [(item.indicator_code, item.period.label, item.value) for item in observations] == [
        ("food_inflation_yoy", "2025-02", Decimal("1.2")),
        ("food_inflation_yoy", "2025-03", Decimal("1.3")),
        ("housing_energy_inflation_yoy", "2025-02", Decimal("2.2")),
        ("housing_energy_inflation_yoy", "2025-03", Decimal("2.3")),
    ]
    assert all(
        item.source_series == "coicop=CP01 | freq=M | geo=ES | unit=RCH_A"
        for item in observations[:2]
    )
    assert observations[0].metadata["dimension_labels"]["coicop"] == "Food"


def test_rejects_a_selector_that_matches_more_than_one_series() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        EurostatJsonStatConnector().extract(
            DatasetDefinition(
                code="eurostat_hicp_components",
                source="eurostat",
                connector="eurostat_jsonstat",
            ),
            _payload(),
            [_indicator("inflation", {"freq": "M", "unit": "RCH_A", "geo": "ES"})],
        )


def test_calculates_a_ratio_from_two_explicit_series() -> None:
    common = {"freq": "M", "unit": "RCH_A", "geo": "ES"}
    observations = EurostatJsonStatConnector().extract(
        DatasetDefinition(
            code="eurostat_hicp_components",
            source="eurostat",
            connector="eurostat_jsonstat",
        ),
        _payload(),
        [
            _indicator(
                "food_to_housing_inflation_ratio",
                {},
                calculation="ratio_percent",
                operands={
                    "numerator": {**common, "coicop": "CP01"},
                    "denominator": {**common, "coicop": "CP04"},
                },
            )
        ],
    )

    assert [(item.period.label, item.value) for item in observations] == [
        ("2025-02", Decimal("54.54545454545454545454545455")),
        ("2025-03", Decimal("56.52173913043478260869565217")),
    ]


def test_calculates_a_percent_from_the_sum_of_multiple_series() -> None:
    common = {"freq": "M", "unit": "RCH_A", "geo": "ES"}
    observations = EurostatJsonStatConnector().extract(
        DatasetDefinition(
            code="eurostat_hicp_components",
            source="eurostat",
            connector="eurostat_jsonstat",
        ),
        _payload(),
        [
            _indicator(
                "combined_share",
                {},
                calculation="ratio_sum_percent",
                operands={
                    "numerator_1": {**common, "coicop": "CP01"},
                    "numerator_2": {**common, "coicop": "CP04"},
                    "denominator": {**common, "coicop": "CP04"},
                },
            )
        ],
    )

    assert [(item.period.label, item.value) for item in observations] == [
        ("2025-02", Decimal("154.5454545454545454545454545")),
        ("2025-03", Decimal("156.5217391304347826086956522")),
    ]


def test_regional_range_and_coefficient_variation_exclude_parent_regions() -> None:
    dataset = DatasetDefinition(
        code="eurostat_regional_gdp_per_capita",
        source="eurostat",
        connector="eurostat_jsonstat",
    )
    common = {
        "freq": "A",
        "unit": "EUR_HAB",
    }

    def regional_indicator(code: str, calculation: str) -> IndicatorDefinition:
        return IndicatorDefinition(
            code=code,
            name=code,
            description=code,
            category="economy",
            dataset=dataset.code,
            unit="euros_per_person" if calculation == "range" else "percent",
            frequency="annual",
            extraction=ExtractionDefinition(
                kind="eurostat_jsonstat",
                dimension_filters=common,
                eurostat_calculation=calculation,
                eurostat_aggregate_dimension="geo",
                eurostat_aggregate_prefix="ES",
                eurostat_aggregate_code_length=5,
            ),
        )

    document = {
        "id": ["freq", "unit", "geo", "time"],
        "size": [1, 1, 4, 2],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}, "label": {"A": "Annual"}}},
            "unit": {"category": {"index": {"EUR_HAB": 0}, "label": {"EUR_HAB": "Euro"}}},
            "geo": {
                "category": {
                    "index": {"ES111": 0, "ES112": 1, "ES113": 2, "ES11": 3},
                    "label": {
                        "ES111": "Province 1",
                        "ES112": "Province 2",
                        "ES113": "Province 3",
                        "ES11": "Parent",
                    },
                }
            },
            "time": {
                "category": {
                    "index": {"2022": 0, "2023": 1},
                    "label": {"2022": "2022", "2023": "2023"},
                }
            },
        },
        "value": [100, 110, 200, 220, 300, 330, 999, 999],
    }
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code="eurostat",
        fetched_at=datetime.now(UTC),
        source_url="https://ec.europa.eu/eurostat/api/test",
        content_type="application/json",
        body=json.dumps(document).encode(),
        sha256="test",
        metadata={"lookback_periods": 2},
    )

    observations = EurostatJsonStatConnector().extract(
        dataset,
        payload,
        [
            regional_indicator("regional_range", "range"),
            regional_indicator("regional_cv", "coefficient_variation"),
        ],
    )

    assert [(row.indicator_code, row.period.label, row.value) for row in observations[:2]] == [
        ("regional_range", "2022", Decimal("200")),
        ("regional_range", "2023", Decimal("220")),
    ]
    cv_2022 = next(
        row.value
        for row in observations
        if row.indicator_code == "regional_cv" and row.period.label == "2022"
    )
    assert float(cv_2022) == pytest.approx(40.8248290463863)
    assert (
        next(
            row.metadata["eurostat_status"]["region_count"]
            for row in observations
            if row.indicator_code == "regional_range" and row.period.label == "2022"
        )
        == 3
    )


def test_latest_release_dimension_selects_newest_available_vintage_per_year() -> None:
    dataset = DatasetDefinition(
        code="eurostat_mip_indicators",
        source="eurostat",
        connector="eurostat_jsonstat",
    )
    indicator = IndicatorDefinition(
        code="current_account_balance_gdp",
        name="Current account balance as GDP share",
        description="Annual current account balance",
        category="economy",
        dataset=dataset.code,
        unit="percent_of_gdp",
        frequency="annual",
        extraction=ExtractionDefinition(
            kind="eurostat_jsonstat",
            dimension_filters={"freq": "A", "indic_ip": "TIPSBP20", "geo": "ES"},
            eurostat_latest_dimension="release",
        ),
    )
    document = {
        "id": ["freq", "indic_ip", "release", "geo", "time"],
        "size": [1, 1, 2, 1, 2],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}, "label": {"A": "Annual"}}},
            "indic_ip": {
                "category": {
                    "index": {"TIPSBP20": 0},
                    "label": {"TIPSBP20": "Current account balance - % GDP"},
                }
            },
            "release": {
                "category": {
                    "index": {"SA25": 0, "SA26": 1},
                    "label": {
                        "SA25": "Statistical annex 2025",
                        "SA26": "Statistical annex 2026",
                    },
                }
            },
            "geo": {"category": {"index": {"ES": 0}, "label": {"ES": "Spain"}}},
            "time": {
                "category": {
                    "index": {"2023": 0, "2024": 1},
                    "label": {"2023": "2023", "2024": "2024"},
                }
            },
        },
        "value": [1, 2, 3, 4],
    }
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code="eurostat",
        fetched_at=datetime.now(UTC),
        source_url="https://ec.europa.eu/eurostat/api/test",
        content_type="application/json",
        body=json.dumps(document).encode(),
        sha256="test",
        metadata={"lookback_periods": 2},
    )

    observations = EurostatJsonStatConnector().extract(dataset, payload, [indicator])

    assert [(row.period.label, row.value) for row in observations] == [
        ("2023", Decimal("3")),
        ("2024", Decimal("4")),
    ]
    assert all("release=SA26" in row.source_series for row in observations)

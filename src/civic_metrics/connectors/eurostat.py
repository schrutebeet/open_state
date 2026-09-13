from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext, lookback_periods
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import period_from_label

LOGGER = logging.getLogger(__name__)


class EurostatJsonStatConnector(Connector):
    """Extract explicitly selected series from Eurostat's JSON-stat API.

    Every indicator must select every non-time dimension.  This makes a missing
    filter a configuration error rather than silently selecting an arbitrary
    cell from a multidimensional table.
    """

    connector_name = "eurostat_jsonstat"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        return self._fetch(dataset, context, [])

    def collect(
        self,
        dataset: DatasetDefinition,
        context: ConnectorContext,
        indicators: list[IndicatorDefinition],
    ) -> list[tuple[DatasetPayload, list[ObservationCandidate]]]:
        payload = self._fetch(dataset, context, indicators)
        observations = self.extract(dataset, payload, indicators)
        requested = int(payload.metadata.get("lookback_periods", 0))
        counts = Counter(item.indicator_code for item in observations)
        if payload.metadata.get("time_limit_applied") and any(
            counts[indicator.code] < requested for indicator in indicators
        ):
            # ``lastTimePeriod`` counts calendar positions, including positions
            # with missing values. Retry without it when that slice produced too
            # few real observations, preserving the connector's valid-value lookback.
            LOGGER.debug(
                "Eurostat %s had fewer than %s observations inside its recent-time slice; "
                "retrying with the full available time range",
                dataset.code,
                requested,
            )
            payload = self._fetch(dataset, context, indicators, include_last_time=False)
            observations = self.extract(dataset, payload, indicators)
        return [(payload, observations)]

    def _fetch(
        self,
        dataset: DatasetDefinition,
        context: ConnectorContext,
        indicators: list[IndicatorDefinition],
        *,
        include_last_time: bool = True,
    ) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        frequency = context.frequency or "monthly"
        requested = lookback_periods(context, frequency)
        query = self._build_query(
            dataset, indicators, requested, include_last_time=include_last_time
        )
        configured_time_filter = any(
            str(key).lower()
            in {"time", "time_period", "sincetimeperiod", "untiltimeperiod", "lasttimeperiod"}
            for key in dataset.config.get("query", {})
        )
        response = context.http.get(dataset.endpoint, params=query)
        return context.http.payload(
            dataset.code,
            dataset.source,
            response,
            {
                "query": query,
                "lookback_periods": requested,
                "dataset_id": dataset.config.get("dataset_id"),
                "time_limit_applied": (
                    include_last_time
                    and not configured_time_filter
                    and "lastTimePeriod" in query
                ),
            },
        )

    @staticmethod
    def _build_query(
        dataset: DatasetDefinition,
        indicators: list[IndicatorDefinition],
        requested: int,
        *,
        include_last_time: bool = True,
    ) -> dict[str, Any]:
        """Ask Eurostat for just the selected series and recent observations.

        The API accepts repeated dimension parameters for multiple selected values,
        and ``lastTimePeriod`` for a recent slice. Keeping this restriction server-side
        cuts large JSON-stat payloads before they are downloaded and decoded.
        """
        query: dict[str, Any] = {
            str(key): value for key, value in dataset.config.get("query", {}).items()
        }
        selected_values: dict[str, set[str]] = defaultdict(set)
        aggregate_dimensions: set[str] = set()
        selectors_for_dataset: list[dict[str, str]] = []

        for indicator in indicators:
            extraction = indicator.extraction
            operation = extraction.eurostat_calculation
            if extraction.eurostat_aggregate_dimension:
                aggregate_dimensions.add(extraction.eurostat_aggregate_dimension)

            if operation == "value" or operation in {"range", "coefficient_variation"}:
                selectors = [extraction.dimension_filters]
            else:
                selectors = list(extraction.eurostat_operands.values())

            selectors_for_dataset.extend(selectors)

        # Filter only dimensions fixed by every selector. Otherwise a filter
        # needed by one indicator could hide extra series and mask ambiguity in
        # another indicator's less-specific selector.
        common_dimensions = (
            set.intersection(*(set(selector) for selector in selectors_for_dataset))
            if selectors_for_dataset
            else set()
        )
        for selector in selectors_for_dataset:
            for dimension, value in selector.items():
                if dimension in common_dimensions and dimension not in aggregate_dimensions:
                    selected_values[dimension].add(str(value))

        # A regional aggregation deliberately leaves its region dimension open.
        for dimension, values in selected_values.items():
            for existing in list(query):
                if existing.lower() == dimension.lower():
                    del query[existing]
            ordered_values = sorted(values)
            query[dimension] = ordered_values[0] if len(ordered_values) == 1 else ordered_values

        time_parameters = {
            "time",
            "time_period",
            "sincetimeperiod",
            "untiltimeperiod",
            "lasttimeperiod",
        }
        query_has_time_filter = any(key.lower() in time_parameters for key in query)
        query_has_time_filter |= any(
            dimension.lower() in {"time", "time_period"} for dimension in selected_values
        )
        one_frequency = len({indicator.frequency for indicator in indicators}) == 1
        if include_last_time and one_frequency and not query_has_time_filter:
            query["lastTimePeriod"] = requested
        return query

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        document = json.loads(payload.body.decode("utf-8-sig"))
        points = self._points(document)
        results: list[ObservationCandidate] = []
        requested = int(payload.metadata.get("lookback_periods", 0))

        for indicator in indicators:
            if indicator.extraction.eurostat_calculation == "value":
                calculated = self._values_from_selector(
                    points,
                    indicator.extraction.dimension_filters,
                    indicator.code,
                    latest_dimension=indicator.extraction.eurostat_latest_dimension,
                )
            else:
                calculated = self._calculate(points, indicator)
            selected = sorted(
                calculated,
                key=lambda point: period_from_label(point["time"], indicator.frequency).end,
                reverse=True,
            )
            if requested:
                selected = selected[:requested]
            for point in reversed(selected):
                period = period_from_label(point["time"], indicator.frequency)
                results.append(
                    ObservationCandidate(
                        indicator_code=indicator.code,
                        source_code=dataset.source,
                        dataset_code=dataset.code,
                        period=period,
                        value=point["value"],
                        unit=indicator.unit,
                        source_series=point["source_series"],
                        source_url=payload.source_url,
                        metadata={
                            "dataset_id": dataset.config.get("dataset_id"),
                            "dimension_codes": point["coordinates"],
                            "dimension_labels": point["labels"],
                            "eurostat_status": point["status"],
                            "eurostat_calculation": indicator.extraction.eurostat_calculation,
                        },
                    )
                )
        return results

    def _values_from_selector(
        self,
        points: list[dict[str, Any]],
        filters: dict[str, str],
        indicator_code: str,
        *,
        latest_dimension: str | None = None,
    ) -> list[dict[str, Any]]:
        matching = [point for point in points if self._matches(point, filters)]
        if latest_dimension:
            if latest_dimension in filters:
                raise ValueError(
                    f"Latest Eurostat dimension {latest_dimension} must not be fixed "
                    f"in the selector for {indicator_code}"
                )
            if any(latest_dimension not in point["coordinates"] for point in matching):
                raise ValueError(
                    f"Unknown latest Eurostat dimension {latest_dimension} for {indicator_code}"
                )
            newest_release: dict[str, str] = {}
            for point in matching:
                period = point["time"]
                release = point["coordinates"][latest_dimension]
                if release > newest_release.get(period, ""):
                    newest_release[period] = release
            matching = [
                point
                for point in matching
                if point["coordinates"][latest_dimension] == newest_release[point["time"]]
            ]
        series = self._series_coordinates(
            matching,
            ignored_dimensions={latest_dimension} if latest_dimension else set(),
        )
        if len(series) > 1:
            rendered = "; ".join(self._render_series(item) for item in sorted(series))
            raise ValueError(f"Eurostat selector for {indicator_code} is ambiguous: {rendered}")
        if not series:
            return []
        source_series = self._render_series(next(iter(series)))
        if latest_dimension:
            return [
                {
                    **point,
                    "source_series": self._render_series(
                        tuple(
                            sorted(
                                (name, code)
                                for name, code in point["coordinates"].items()
                                if name != "time"
                            )
                        )
                    ),
                }
                for point in matching
            ]
        return [{**point, "source_series": source_series} for point in matching]

    def _calculate(
        self,
        points: list[dict[str, Any]],
        indicator: IndicatorDefinition,
    ) -> list[dict[str, Any]]:
        operation = indicator.extraction.eurostat_calculation
        if operation in {"range", "coefficient_variation"}:
            return self._aggregate_regions(points, indicator)
        operands = indicator.extraction.eurostat_operands
        if operation == "sum":
            if len(operands) < 2:
                raise ValueError(
                    f"Eurostat sum for {indicator.code} requires at least two operands"
                )
            expected = tuple(operands)
        elif operation == "ratio_sum_percent":
            numerators = tuple(sorted(name for name in operands if name.startswith("numerator_")))
            expected = (*numerators, "denominator")
            if len(numerators) < 2:
                raise ValueError(
                    f"Eurostat ratio-of-sums for {indicator.code} requires at least two "
                    "numerator operands"
                )
        else:
            expected = (
                ("numerator", "denominator")
                if operation in {"ratio", "ratio_percent"}
                else ("minuend", "subtrahend")
            )
        if set(operands) != set(expected):
            raise ValueError(
                f"Eurostat calculation for {indicator.code} requires operands {', '.join(expected)}"
            )
        selected = {
            name: self._values_from_selector(points, filters, f"{indicator.code}:{name}")
            for name, filters in operands.items()
        }
        values = {
            name: {point["time"]: point for point in operand_points}
            for name, operand_points in selected.items()
        }
        shared_times = set.intersection(*(set(item) for item in values.values()))
        calculated: list[dict[str, Any]] = []
        for time in shared_times:
            if operation in {"sum", "ratio_sum_percent"}:
                components = [values[name][time] for name in expected]
                if operation == "sum":
                    value = sum((component["value"] for component in components), Decimal("0"))
                else:
                    numerator_values = [values[name][time]["value"] for name in expected[:-1]]
                    denominator_value = values["denominator"][time]["value"]
                    if denominator_value == 0:
                        continue
                    value = sum(numerator_values, Decimal("0")) / denominator_value * Decimal("100")
                coordinates = {
                    name: item["coordinates"]
                    for name, item in zip(expected, components, strict=True)
                }
                labels = {
                    name: item["labels"] for name, item in zip(expected, components, strict=True)
                }
                status = {
                    name: item["status"] for name, item in zip(expected, components, strict=True)
                }
                source_series = " || ".join(
                    f"{name}: {item['source_series']}"
                    for name, item in zip(expected, components, strict=True)
                )
            else:
                left = values[expected[0]][time]
                right = values[expected[1]][time]
            if operation == "difference":
                value = left["value"] - right["value"]
            elif operation in {"ratio", "ratio_percent"}:
                if right["value"] == 0:
                    continue
                value = left["value"] / right["value"]
                if operation == "ratio_percent":
                    value *= Decimal("100")
            if operation not in {"sum", "ratio_sum_percent"}:
                coordinates = {
                    expected[0]: left["coordinates"],
                    expected[1]: right["coordinates"],
                }
                labels = {
                    expected[0]: left["labels"],
                    expected[1]: right["labels"],
                }
                status = {expected[0]: left["status"], expected[1]: right["status"]}
                source_series = (
                    f"{expected[0]}: {left['source_series']} || "
                    f"{expected[1]}: {right['source_series']}"
                )
            calculated.append(
                {
                    "time": time,
                    "value": value,
                    "coordinates": coordinates,
                    "labels": labels,
                    "status": status,
                    "source_series": source_series,
                }
            )
        return calculated

    def _aggregate_regions(
        self,
        points: list[dict[str, Any]],
        indicator: IndicatorDefinition,
    ) -> list[dict[str, Any]]:
        operation = indicator.extraction.eurostat_calculation
        dimension = indicator.extraction.eurostat_aggregate_dimension
        prefix = indicator.extraction.eurostat_aggregate_prefix
        code_length = indicator.extraction.eurostat_aggregate_code_length
        if not dimension or not prefix or code_length is None:
            raise ValueError(
                f"Regional Eurostat calculation for {indicator.code} requires "
                "aggregate dimension, prefix and code length"
            )
        filters = indicator.extraction.dimension_filters
        if dimension in filters:
            raise ValueError(
                f"Regional Eurostat selector for {indicator.code} must not fix {dimension}"
            )
        matching = [
            point
            for point in points
            if self._matches(point, filters)
            and point["coordinates"].get(dimension, "").startswith(prefix)
            and len(point["coordinates"].get(dimension, "")) == code_length
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for point in matching:
            grouped.setdefault(point["time"], []).append(point)

        result: list[dict[str, Any]] = []
        for time, regional_points in grouped.items():
            # Guard against accidentally mixing observations from two configured series.
            signatures = {
                tuple(
                    sorted(
                        (key, value)
                        for key, value in point["coordinates"].items()
                        if key not in {"time", dimension}
                    )
                )
                for point in regional_points
            }
            if len(signatures) != 1 or len(regional_points) < 2:
                continue
            values = [point["value"] for point in regional_points]
            if operation == "range":
                value = max(values) - min(values)
            else:
                mean = sum(values, Decimal("0")) / Decimal(len(values))
                if mean == 0:
                    continue
                variance = sum(((item - mean) ** 2 for item in values), Decimal("0")) / Decimal(
                    len(values)
                )
                value = variance.sqrt() / abs(mean) * Decimal("100")
            exemplar = regional_points[0]
            coordinates = {
                key: code
                for key, code in exemplar["coordinates"].items()
                if key not in {"time", dimension}
            }
            coordinates[dimension] = f"{prefix}*[{code_length}]"
            labels = {
                key: label
                for key, label in exemplar["labels"].items()
                if key not in {"time", dimension}
            }
            labels[dimension] = f"Regions {prefix} (code length {code_length})"
            result.append(
                {
                    "time": time,
                    "value": value,
                    "coordinates": coordinates,
                    "labels": labels,
                    "status": {
                        "region_count": len(regional_points),
                        "regions": sorted(
                            point["coordinates"][dimension] for point in regional_points
                        ),
                    },
                    "source_series": (
                        f"{operation}({dimension} starts {prefix}, "
                        f"code length {code_length}, n={len(regional_points)})"
                    ),
                }
            )
        return result

    @staticmethod
    def _matches(point: dict[str, Any], filters: dict[str, str]) -> bool:
        coordinates = point["coordinates"]
        unknown = set(filters) - set(coordinates)
        if unknown:
            raise ValueError(f"Unknown Eurostat dimensions: {', '.join(sorted(unknown))}")
        return all(coordinates[dimension] == value for dimension, value in filters.items())

    @staticmethod
    def _series_coordinates(
        points: list[dict[str, Any]],
        *,
        ignored_dimensions: set[str] | None = None,
    ) -> set[tuple[tuple[str, str], ...]]:
        ignored = ignored_dimensions or set()
        return {
            tuple(
                sorted(
                    (name, code)
                    for name, code in point["coordinates"].items()
                    if name != "time" and name not in ignored
                )
            )
            for point in points
        }

    @staticmethod
    def _render_series(series: tuple[tuple[str, str], ...]) -> str:
        return " | ".join(f"{dimension}={code}" for dimension, code in series)

    @classmethod
    def _points(cls, document: dict[str, Any]) -> list[dict[str, Any]]:
        dimensions = document.get("id")
        sizes = document.get("size")
        if not isinstance(dimensions, list) or not isinstance(sizes, list):
            raise ValueError("Eurostat response is missing JSON-stat dimensions")
        if "time" not in dimensions:
            raise ValueError("Eurostat response has no time dimension")
        if len(dimensions) != len(sizes):
            raise ValueError("Eurostat response dimensions and sizes differ")

        code_by_position: dict[str, dict[int, str]] = {}
        label_by_code: dict[str, dict[str, str]] = {}
        for dimension in dimensions:
            category = document["dimension"][dimension]["category"]
            indexes = category.get("index", {})
            if not isinstance(indexes, dict):
                raise ValueError(f"Eurostat dimension {dimension} has an unsupported index")
            code_by_position[dimension] = {
                int(position): str(code) for code, position in indexes.items()
            }
            label_by_code[dimension] = {
                str(code): str(label) for code, label in category.get("label", {}).items()
            }

        values = document.get("value", {})
        statuses = document.get("status", {})
        if isinstance(values, list):
            value_items = enumerate(values)
        elif isinstance(values, dict):
            value_items = values.items()
        else:
            raise ValueError("Eurostat response has an unsupported value structure")

        result: list[dict[str, Any]] = []
        for raw_index, raw_value in value_items:
            if raw_value is None:
                continue
            index = int(raw_index)
            positions = cls._positions(index, [int(size) for size in sizes])
            coordinates = {
                dimension: code_by_position[dimension][position]
                for dimension, position in zip(dimensions, positions, strict=True)
            }
            labels = {
                dimension: label_by_code[dimension].get(code, code)
                for dimension, code in coordinates.items()
            }
            result.append(
                {
                    "coordinates": coordinates,
                    "labels": labels,
                    "time": coordinates["time"],
                    "value": Decimal(str(raw_value)),
                    "status": statuses.get(str(raw_index)) if isinstance(statuses, dict) else None,
                }
            )
        return result

    @staticmethod
    def _positions(index: int, sizes: list[int]) -> list[int]:
        positions = []
        for size in reversed(sizes):
            positions.append(index % size)
            index //= size
        if index:
            raise ValueError("Eurostat value index exceeds dimension sizes")
        return list(reversed(positions))

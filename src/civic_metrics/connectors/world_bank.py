from __future__ import annotations

import json
import logging
from typing import Any

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext, lookback_periods
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import parse_decimal, period_from_label

LOGGER = logging.getLogger(__name__)


class WorldBankIndicatorConnector(Connector):
    """Read a country's annual series from the World Bank Indicators API."""

    connector_name = "world_bank_indicator"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        response = context.http.get(
            dataset.endpoint,
            params={"format": "json", "per_page": 20000},
        )
        return context.http.payload(
            dataset.code,
            dataset.source,
            response,
            {"lookback_periods": lookback_periods(context, "annual")},
        )

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        document: Any = json.loads(payload.body.decode("utf-8-sig"))
        if not isinstance(document, list) or len(document) < 2 or not isinstance(document[1], list):
            raise ValueError(f"Unexpected World Bank API response for {dataset.code}")
        rows = [row for row in document[1] if isinstance(row, dict)]
        if rows and rows[0].get("indicator", {}).get("id") != dataset.config.get("indicator_id"):
            raise ValueError(f"World Bank response does not match dataset {dataset.code}")

        results: list[ObservationCandidate] = []
        for indicator in indicators:
            selected = sorted(
                (row for row in rows if row.get("value") is not None),
                key=lambda row: int(row["date"]),
                reverse=True,
            )[: int(payload.metadata.get("lookback_periods", 0))]
            for row in reversed(selected):
                value = parse_decimal(row["value"])
                results.append(
                    ObservationCandidate(
                        indicator_code=indicator.code,
                        source_code=dataset.source,
                        dataset_code=dataset.code,
                        period=period_from_label(str(row["date"]), "annual"),
                        value=value,
                        unit=indicator.unit,
                        source_series=str(row.get("indicator", {}).get("id") or ""),
                        source_url=payload.source_url,
                        metadata={
                            "indicator_name": row.get("indicator", {}).get("value"),
                            "country": row.get("country", {}).get("value"),
                            "source_id": document[0].get("sourceid") if document[0] else None,
                            "last_updated": document[0].get("lastupdated") if document[0] else None,
                        },
                    )
                )
        return results

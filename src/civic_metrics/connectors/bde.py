from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from dateutil import parser as date_parser

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import parse_decimal, period_from_datetime

LOGGER = logging.getLogger(__name__)


class BdeSeriesConnector(Connector):
    connector_name = "bde_series"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        series = dataset.config.get("series", [])
        if not series:
            raise ValueError(f"Dataset {dataset.code} has no Banco de España series")
        endpoint = dataset.endpoint or (
            "https://app.bde.es/bierest/resources/srdatosapp/listaSeries"
        )
        response = context.http.get(
            endpoint,
            params={
                "idioma": "es",
                "series": ",".join(series),
                "rango": dataset.config.get("range", "30Q"),
            },
        )
        return context.http.payload(
            dataset.code,
            dataset.source,
            response,
            {"series_requested": series},
        )

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        document = json.loads(payload.body.decode("utf-8-sig"))
        series_items = self._series_items(document)
        by_code = {
            str(item.get("serie") or item.get("code")): item
            for item in series_items
        }
        results: list[ObservationCandidate] = []
        for indicator in indicators:
            code = indicator.extraction.series_code
            if not code or code not in by_code:
                LOGGER.warning("BdE response did not contain series %s for %s", code, indicator.code)
                continue
            item = by_code[code]
            dates = item.get("fechas") or item.get("dates") or []
            values = item.get("valores") or item.get("values") or []
            multiplier = Decimal(indicator.extraction.multiplier)
            for raw_date, raw_value in zip(dates, values, strict=False):
                if raw_value in (None, "", "-"):
                    continue
                parsed_date = date_parser.parse(str(raw_date))
                period = period_from_datetime(parsed_date, indicator.frequency)
                results.append(
                    ObservationCandidate(
                        indicator_code=indicator.code,
                        source_code=dataset.source,
                        dataset_code=dataset.code,
                        period=period,
                        value=parse_decimal(raw_value) * multiplier,
                        unit=indicator.unit,
                        source_series=code,
                        source_url=payload.source_url,
                        metadata={
                            "description": item.get("descripcion"),
                            "short_description": item.get("descripcionCorta"),
                            "frequency_code": item.get("codFrecuencia"),
                            "symbol": item.get("simbolo"),
                        },
                    )
                )
        return results

    @staticmethod
    def _series_items(document: Any) -> list[dict[str, Any]]:
        if isinstance(document, list):
            return [item for item in document if isinstance(item, dict)]
        if isinstance(document, dict):
            for key in ("series", "listaSeries", "data", "result"):
                value = document.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if "serie" in document:
                return [document]
        raise ValueError("Unexpected Banco de España API response structure")

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import normalise_text, parse_decimal, period_from_ine_date

LOGGER = logging.getLogger(__name__)


class IneTableConnector(Connector):
    connector_name = "ine_table"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        table_id = str(dataset.config["table_id"])
        periods = int(dataset.config.get("nult", context.settings.max_history_periods))
        base_url = dataset.endpoint or "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA"
        response = context.http.get(f"{base_url.rstrip('/')}/{table_id}", params={"nult": periods})
        return context.http.payload(
            dataset.code,
            dataset.source,
            response,
            {"table_id": table_id, "periods_requested": periods},
        )

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        document = json.loads(payload.body.decode("utf-8-sig"))
        raw_series = document if isinstance(document, list) else document.get("Data", [])
        results: list[ObservationCandidate] = []
        available_names = [str(item.get("Nombre") or item.get("name") or "") for item in raw_series]

        for indicator in indicators:
            matches = [
                (self._score(item, indicator), item)
                for item in raw_series
                if self._matches(item, indicator)
            ]
            if not matches:
                LOGGER.warning(
                    "INE selector matched no series indicator=%s table=%s examples=%s",
                    indicator.code,
                    dataset.config.get("table_id"),
                    available_names[:8],
                )
                continue
            matches.sort(
                key=lambda pair: (
                    pair[0],
                    -len(str(pair[1].get("Nombre") or pair[1].get("name") or "")),
                ),
                reverse=True,
            )
            if len(matches) > 1:
                LOGGER.info(
                    "INE selector matched %s series for %s; selected=%r alternatives=%r",
                    len(matches),
                    indicator.code,
                    matches[0][1].get("Nombre"),
                    [item.get("Nombre") for _, item in matches[1:4]],
                )
            series = matches[0][1]
            series_name = str(series.get("Nombre") or series.get("name") or indicator.code)
            points = series.get("Data") or series.get("data") or []
            multiplier = Decimal(indicator.extraction.multiplier)
            for point in points:
                if point.get("Valor") is None and point.get("value") is None:
                    continue
                value = parse_decimal(point.get("Valor", point.get("value"))) * multiplier
                try:
                    period = period_from_ine_date(
                        point.get("Fecha", point.get("date")),
                        indicator.frequency,
                        point.get("Anyo", point.get("year")),
                    )
                except ValueError:
                    LOGGER.exception("Could not parse INE period for %s", indicator.code)
                    continue
                results.append(
                    ObservationCandidate(
                        indicator_code=indicator.code,
                        source_code=dataset.source,
                        dataset_code=dataset.code,
                        period=period,
                        value=value,
                        unit=indicator.unit,
                        source_series=str(series.get("COD") or series.get("code") or series_name),
                        source_url=payload.source_url,
                        metadata={
                            "series_name": series_name,
                            "ine_unit_id": series.get("FK_Unidad"),
                            "ine_scale_id": series.get("FK_Escala"),
                            "ine_data_type_id": point.get("FK_TipoDato"),
                            "ine_period_id": point.get("FK_Periodo"),
                        },
                    )
                )
        return results

    @staticmethod
    def _haystack(series: dict[str, Any]) -> str:
        return normalise_text(
            " | ".join(str(series.get(key, "")) for key in ("Nombre", "name", "COD", "code"))
        )

    @classmethod
    def _matches(cls, series: dict[str, Any], indicator: IndicatorDefinition) -> bool:
        haystack = cls._haystack(series)
        includes = [normalise_text(item) for item in indicator.extraction.include]
        excludes = [normalise_text(item) for item in indicator.extraction.exclude]
        return all(term in haystack for term in includes) and not any(
            term in haystack for term in excludes
        )

    @classmethod
    def _score(cls, series: dict[str, Any], indicator: IndicatorDefinition) -> int:
        haystack = cls._haystack(series)
        prefer = [normalise_text(item) for item in indicator.extraction.prefer]
        return sum(100 for term in prefer if term in haystack)

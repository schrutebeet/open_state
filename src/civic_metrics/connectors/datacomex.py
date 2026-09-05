from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import normalise_text, parse_decimal, period_from_label
from civic_metrics.security import get_datacomex_credentials

LOGGER = logging.getLogger(__name__)


class MissingCredentialsError(RuntimeError):
    pass


class DataComexConnector(Connector):
    connector_name = "datacomex"

    LOGIN_URL = "https://comercio.serviciosmin.gob.es/DatacomexAPI/IniciarSesion"
    DATA_URL = "https://comercio.serviciosmin.gob.es/DatacomexAPI/ObtenerDatos"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        credentials = get_datacomex_credentials(
            context.settings.datacomex_username,
            context.settings.datacomex_password,
        )
        if credentials is None:
            raise MissingCredentialsError(
                "DataComex credentials are missing. Run scripts/set_secret.py for "
                "datacomex_username and datacomex_password, or set OS environment variables."
            )
        login = context.http.post(
            self.LOGIN_URL,
            json_body={"Usuario": credentials.username, "Pass": credentials.password},
            use_cache=False,
        )
        token_document = json.loads(login.body.decode("utf-8-sig"))
        token = self._extract_token(token_document)
        params = {
            "f": dataset.config.get("flow", "I/E"),
            "pe": dataset.config.get("period", "LastM"),
            "pa": dataset.config.get("country", "TOTAL"),
            "ta": dataset.config.get("taric", "TOTAL"),
            "pr": dataset.config.get("province", "TOTAL"),
        }
        data_url = f"{dataset.endpoint or self.DATA_URL}?{urlencode({'access_token': token, **params})}"
        response = context.http.get(
            data_url,
            params=None,
            json_body=None,
            headers=None,
        )
        return context.http.payload(dataset.code, dataset.source, response, {"query": params})

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        document = json.loads(payload.body.decode("utf-8-sig"))
        rows = document if isinstance(document, list) else document.get("data", document.get("Resultados", []))
        if not isinstance(rows, list):
            raise ValueError("Unexpected DataComex response structure")

        results: list[ObservationCandidate] = []
        for indicator in indicators:
            # Indicator codes stay English; aliases accommodate DataComex labels.
            expected_source_flow = normalise_text(indicator.extraction.field)
            expected_flows = {
                expected_source_flow,
                *(normalise_text(alias) for alias in indicator.extraction.flow_aliases),
            }
            multiplier = Decimal(indicator.extraction.multiplier)
            matching_rows = [
                row
                for row in rows
                if normalise_text(row.get("flujo", "")) in expected_flows
            ]
            if not matching_rows:
                LOGGER.warning("No DataComex row found for %s", indicator.code)
                continue
            for row in matching_rows:
                period = period_from_label(str(row.get("periodo", "")), indicator.frequency)
                results.append(
                    ObservationCandidate(
                        indicator_code=indicator.code,
                        source_code=dataset.source,
                        dataset_code=dataset.code,
                        period=period,
                        value=parse_decimal(row["euros"]) * multiplier,
                        unit=indicator.unit,
                        is_provisional="provisional" in normalise_text(row.get("mensaje", "")),
                        source_series=f"{row.get('flujo')}:{row.get('id_pais')}:{row.get('taric')}:{row.get('id_prov')}",
                        source_url=payload.source_url,
                        metadata={
                            "flow": row.get("flujo"),
                            "country": row.get("pais"),
                            "province": row.get("prov"),
                            "taric": row.get("taric"),
                            "kilograms": row.get("kilos"),
                            "message": row.get("mensaje"),
                        },
                    )
                )
        return results

    @staticmethod
    def _extract_token(document: Any) -> str:
        def clean(value: Any) -> str:
            token = str(value).strip().strip('"')
            if token.lower().startswith("token:"):
                token = token.split(":", 1)[1]
            return token.strip()

        if isinstance(document, str):
            return clean(document)
        if isinstance(document, dict):
            for key in ("token", "access_token", "Token", "resultado"):
                if document.get(key):
                    return clean(document[key])
        raise ValueError("Could not find token in DataComex login response")

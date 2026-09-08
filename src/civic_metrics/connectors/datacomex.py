from __future__ import annotations

import json
import logging
import hashlib
import random
import time
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext, lookback_periods
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
    MAX_LATEST_PERIOD_PROBE = 24

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
            "pa": dataset.config.get("country", "TOTAL"),
            "ta": dataset.config.get("taric", "TOTAL"),
            "pr": dataset.config.get("province", "TOTAL"),
        }
        lookback = lookback_periods(context, context.frequency or "monthly")
        periods = self._lookback_months(date.today(), lookback)
        rows: list[Any] = []
        endpoint = dataset.endpoint or self.DATA_URL
        last_response = None
        request_count = 0

        # DataComex usually publishes with a delay. Find the newest month that
        # actually has data first, then take the requested history from there.
        latest_period = None
        latest_rows: list[Any] = []
        probe_periods = self._lookback_months(
            date.today(), self.MAX_LATEST_PERIOD_PROBE
        )
        for period in reversed(probe_periods):
            query = {**params, "pe": period}
            data_url = f"{endpoint}?{urlencode({'access_token': token, **query})}"
            response = context.http.get(
                data_url,
                params=None,
                json_body=None,
                headers=None,
            )
            request_count += 1
            last_response = response
            document = json.loads(response.body.decode("utf-8-sig"))
            period_rows = document if isinstance(document, list) else document.get(
                "data", document.get("Resultados", [])
            )
            if period_rows and period_rows[0]["euros"] is not None:
                latest_period = period
                latest_rows = period_rows
                break
            time.sleep(random.uniform(1, 3))  # Avoid rate limiting on DataComex API

        if latest_period is not None:
            latest_year = int(latest_period[:4])
            latest_month = int(latest_period[4:])
            periods = self._lookback_months(
                date(latest_year, latest_month, 1), lookback
            )
            rows.extend(latest_rows)

            # The newest period has already been fetched during the probe.
            for period in periods:
                if period == latest_period:
                    continue
                query = {**params, "pe": period}
                data_url = f"{endpoint}?{urlencode({'access_token': token, **query})}"
                response = context.http.get(
                    data_url,
                    params=None,
                    json_body=None,
                    headers=None,
                )
                request_count += 1
                last_response = response
                document = json.loads(response.body.decode("utf-8-sig"))
                period_rows = document if isinstance(document, list) else document.get(
                    "data", document.get("Resultados", [])
                )
                rows.extend(period_rows)
                time.sleep(random.uniform(1, 3))  # Avoid rate limiting on DataComex API
        body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        if last_response is None:
            raise ValueError("DataComex lookback produced no requests")
        return DatasetPayload(
            dataset_code=dataset.code,
            source_code=dataset.source,
            fetched_at=context.http.payload(dataset.code, dataset.source, last_response).fetched_at,
            source_url=last_response.source_url,
            content_type=last_response.content_type,
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            metadata={
                "query": params,
                "periods_requested": periods,
                "request_count": request_count,
                "latest_available_period": latest_period,
            },
        )

    @staticmethod
    def _lookback_months(as_of: date, count: int) -> list[str]:
        values: list[str] = []
        year, month = as_of.year, as_of.month
        for _ in range(count):
            values.append(f"{year}{month:02d}")
            month -= 1
            if month == 0:
                year -= 1
                month = 12
        return list(reversed(values))

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

from __future__ import annotations

import logging
from decimal import Decimal

from bs4 import BeautifulSoup

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.parsers.common import normalise_text, parse_decimal, period_from_label

LOGGER = logging.getLogger(__name__)


class HtmlTableConnector(Connector):
    connector_name = "html_table"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        response = context.http.get(dataset.endpoint)
        return context.http.payload(dataset.code, dataset.source, response)

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        soup = BeautifulSoup(payload.body, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            raise LookupError(f"No HTML table found in {payload.source_url}")
        table_index = int(dataset.config.get("table_index", 0))
        table = tables[table_index]
        matrix = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        matrix = [row for row in matrix if row]
        if len(matrix) < 2:
            raise LookupError(f"HTML table in {payload.source_url} has no data rows")

        period_column = int(dataset.config.get("period_column", 0))
        parsed_rows: list[tuple[int, list[str], Period]] = []
        for index, row in enumerate(matrix):
            if period_column >= len(row):
                continue
            try:
                period = period_from_label(row[period_column], indicators[0].frequency)
            except ValueError:
                continue
            parsed_rows.append((index, row, period))
        if not parsed_rows:
            preview = [row[:7] for row in matrix[:5]]
            raise LookupError(
                f"No data row with a parseable period was found in {payload.source_url}; "
                f"preview={preview}"
            )

        # The INE press table is newest-first, but selecting by parsed period also
        # works if the publisher changes the ordering.
        row_index, row, period = max(parsed_rows, key=lambda item: item[2].end)
        headers = self._find_headers(matrix, row_index, indicators, period_column)

        results: list[ObservationCandidate] = []
        for indicator in indicators:
            field = normalise_text(indicator.extraction.field)
            matching_indexes = [
                index for index, header in enumerate(headers) if field and field in normalise_text(header)
            ]
            if not matching_indexes:
                LOGGER.warning(
                    "HTML header %s not found for %s: %s",
                    field,
                    indicator.code,
                    headers,
                )
                continue
            index = matching_indexes[0]
            if index >= len(row):
                continue
            results.append(
                ObservationCandidate(
                    indicator_code=indicator.code,
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=period,
                    value=parse_decimal(row[index]) * Decimal(indicator.extraction.multiplier),
                    unit=indicator.unit,
                    source_series=headers[index],
                    source_url=payload.source_url,
                    metadata={"headers": headers, "raw_period": row[period_column]},
                )
            )
        return results

    @staticmethod
    def _find_headers(
        matrix: list[list[str]],
        data_row_index: int,
        indicators: list[IndicatorDefinition],
        period_column: int,
    ) -> list[str]:
        fields = [normalise_text(item.extraction.field) for item in indicators if item.extraction.field]
        for index in range(data_row_index - 1, max(-1, data_row_index - 5), -1):
            row = matrix[index]
            normalised = [normalise_text(cell) for cell in row]
            matches = sum(any(field in cell for cell in normalised) for field in fields)
            if matches >= max(1, min(2, len(fields))):
                headers = list(row)
                while len(headers) < len(matrix[data_row_index]):
                    headers.append("")
                return headers

        # Last-resort fixed mapping for simple summary tables.
        width = len(matrix[data_row_index])
        headers = [f"column_{index}" for index in range(width)]
        if period_column < width:
            headers[period_column] = "period"
        return headers

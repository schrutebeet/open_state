from __future__ import annotations

import logging
from decimal import Decimal

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.direct_file import DirectFileConnector
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import parse_decimal, period_from_label
from civic_metrics.parsers.excel import WorkbookMatrix

LOGGER = logging.getLogger(__name__)


class AeatTaxRevenueConnector(DirectFileConnector):
    """Extract net monthly AEAT tax revenue from its machine-readable series sheet."""

    connector_name = "aeat_tax_revenue"
    _SHEET_NAME = "Ingresos tributarios"
    _FIELDS: dict[str, tuple[int, Decimal]] = {
        # Zero-based columns in the sheet's "Miles de euros" table.
        # Source values are thousands of euros; divide by 1,000 to obtain
        # millions of euros.
        "tax_revenue_total": (6, Decimal("0.001")),
        "tax_revenue_irpf": (29, Decimal("0.001")),
        "tax_revenue_corporate": (65, Decimal("0.001")),
        "tax_revenue_vat": (107, Decimal("0.001")),
        # The source reports paid refunds as a negative amount; this indicator
        # represents the (positive) amount refunded.
        "tax_refunds": (4, Decimal("-0.001")),
    }

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        table_sheet = str(dataset.config.get("data_sheet", self._SHEET_NAME))
        workbook = WorkbookMatrix.from_bytes(
            payload.body,
            payload.content_type,
            payload.source_url,
            sheet_names=(table_sheet,),
        )
        rows = workbook.sheets.get(table_sheet)
        if rows is None:
            raise LookupError(f"Worksheet {table_sheet!r} was not found")

        latest = self._latest_available_row(rows)
        if latest is None:
            raise LookupError(f"No populated monthly data rows found in {table_sheet!r}")
        row_number, period, row = latest

        results: list[ObservationCandidate] = []
        for indicator in indicators:
            field = self._FIELDS.get(indicator.code)
            if field is None:
                LOGGER.warning("No AEAT tax revenue field configured for %s", indicator.code)
                continue
            column, multiplier = field
            try:
                raw_value = parse_decimal(row[column])
            except (IndexError, TypeError, ValueError):
                LOGGER.warning(
                    "No numeric value for %s in %s row %s column %s",
                    indicator.code,
                    table_sheet,
                    row_number,
                    column + 1,
                )
                continue
            results.append(
                ObservationCandidate(
                    indicator_code=indicator.code,
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=period,
                    value=raw_value * multiplier,
                    unit=indicator.unit,
                    source_series=f"{table_sheet}!R{row_number}C{column + 1}",
                    source_url=payload.source_url,
                    metadata={
                        "sheet": table_sheet,
                        "row": row_number,
                        "column": column + 1,
                        "source_unit": "thousand_eur",
                        "source_value": str(raw_value),
                        "sign_convention": (
                            "source_negative_output_positive_amount_refunded"
                            if indicator.code == "tax_refunds"
                            else "unchanged"
                        ),
                    },
                )
            )
        return results

    @staticmethod
    def _latest_available_row(rows: list[list[object]]) -> tuple[int, object, list[object]] | None:
        candidates: list[tuple[int, object, list[object]]] = []
        for row_index, row in enumerate(rows):
            try:
                year = int(parse_decimal(row[0]))
                month = int(parse_decimal(row[1]))
                # Net total tax revenue is the completeness check for a month.
                parse_decimal(row[6])
                period = period_from_label(f"{year}-{month:02d}", "monthly")
            except (IndexError, TypeError, ValueError):
                continue
            candidates.append((row_index + 1, period, row))
        return max(candidates, key=lambda item: item[1].end) if candidates else None

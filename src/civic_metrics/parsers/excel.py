from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Any

import openpyxl

from civic_metrics.domain import Period
from civic_metrics.parsers.common import normalise_text, parse_decimal, period_from_label


@dataclass(frozen=True)
class CellMatch:
    sheet: str
    row: int
    column: int
    label: str
    value: Any
    column_label: str | None = None


class WorkbookMatrix:
    def __init__(self, sheets: dict[str, list[list[Any]]]) -> None:
        self.sheets = sheets

    @classmethod
    def from_bytes(
        cls,
        body: bytes,
        content_type: str,
        source_url: str,
        *,
        sheet_names: Iterable[str] | None = None,
    ) -> WorkbookMatrix:
        lowered = source_url.lower()
        if lowered.endswith(".csv") or content_type in {"text/csv", "application/csv"}:
            text = body.decode("utf-8-sig", errors="replace")
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            return cls({"data": [list(row) for row in csv.reader(StringIO(text), dialect)]})
        if body[:2] != b"PK" and (
            ".xls" in lowered or content_type == "application/vnd.ms-excel"
        ):
            try:
                import xlrd  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "Reading legacy .xls files requires the optional 'xlrd' package"
                ) from exc
            book = xlrd.open_workbook(file_contents=body)
            sheets: dict[str, list[list[Any]]] = {}
            for sheet in book.sheets():
                if sheet_names is not None and sheet.name not in sheet_names:
                    continue
                sheets[sheet.name] = [sheet.row_values(index) for index in range(sheet.nrows)]
            return cls(sheets)

        book = openpyxl.load_workbook(BytesIO(body), data_only=True, read_only=True)
        sheets = {
            sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
            for sheet in book.worksheets
            if sheet_names is None or sheet.title in sheet_names
        }
        return cls(sheets)

    def find_value(
        self,
        *,
        sheet_include: Iterable[str] = (),
        row_include: Iterable[str],
        column_include: Iterable[str] = (),
    ) -> CellMatch:
        sheet_terms = [normalise_text(item) for item in sheet_include]
        row_terms = [normalise_text(item) for item in row_include]
        column_terms = [normalise_text(item) for item in column_include]
        candidates: list[CellMatch] = []
        inspected_sheets: list[str] = []

        for sheet_name, rows in self.sheets.items():
            normalised_sheet = normalise_text(sheet_name)
            if sheet_terms and not all(term in normalised_sheet for term in sheet_terms):
                continue
            inspected_sheets.append(sheet_name)
            for row_index, row in enumerate(rows):
                row_text = " | ".join(normalise_text(cell) for cell in row if cell is not None)
                if not row_text or not all(term in row_text for term in row_terms):
                    continue
                headers = self._column_contexts(rows, row_index)
                for column_index, value in enumerate(row):
                    try:
                        parsed = parse_decimal(value)
                    except (ValueError, TypeError):
                        continue
                    column_label = headers[column_index] if column_index < len(headers) else None
                    normalised_column = normalise_text(column_label)
                    if column_terms and not all(term in normalised_column for term in column_terms):
                        continue
                    candidates.append(
                        CellMatch(
                            sheet=sheet_name,
                            row=row_index,
                            column=column_index,
                            label=row_text,
                            value=parsed,
                            column_label=str(column_label) if column_label is not None else None,
                        )
                    )

        if not candidates:
            sample_rows = self._sample_rows(inspected_sheets or list(self.sheets), row_terms)
            raise LookupError(
                "No Excel value matched "
                f"sheet={list(sheet_include)}, row={list(row_include)}, "
                f"column={list(column_include)}; sheets={list(self.sheets)}; "
                f"nearby_rows={sample_rows}"
            )
        # Latest periods and current-year columns are normally furthest right;
        # when several totals match in the same column, the grand total is
        # normally the lowest row.
        return max(candidates, key=lambda item: (item.column, item.row))

    def infer_latest_period(self, frequency: str) -> Period | None:
        periods: list[Period] = []
        for rows in self.sheets.values():
            for row in rows:
                for cell in row:
                    if not isinstance(cell, str):
                        continue
                    text = normalise_text(cell)
                    if not re.search(r"\b(?:19|20)\d{2}\b", text):
                        continue
                    try:
                        period = period_from_label(cell, frequency)
                    except ValueError:
                        continue
                    if period.frequency == frequency:
                        periods.append(period)
        return max(periods, key=lambda item: item.end) if periods else None

    @staticmethod
    def _column_contexts(rows: list[list[Any]], row_index: int) -> list[str]:
        width = max((len(row) for row in rows[: row_index + 1]), default=0)
        contexts: list[list[str]] = [[] for _ in range(width)]
        start = max(0, row_index - 20)
        for header_row in rows[start:row_index]:
            # Excel merged headings are represented only in the leftmost cell.
            # Forward-filling text across empty cells reconstructs enough context
            # for labels such as "2026 / derechos reconocidos netos".
            carried: str | None = None
            for column in range(width):
                value = header_row[column] if column < len(header_row) else None
                if value is not None and str(value).strip():
                    carried = str(value).strip()
                if carried is None:
                    continue
                normalised = normalise_text(carried)
                if not normalised or normalised in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                    continue
                if normalised not in {normalise_text(item) for item in contexts[column]}:
                    contexts[column].append(carried)
        return [" | ".join(items[-6:]) if items else "" for items in contexts]

    def _sample_rows(self, sheet_names: list[str], terms: list[str]) -> list[str]:
        samples: list[str] = []
        for sheet_name in sheet_names[:5]:
            for row in self.sheets.get(sheet_name, []):
                text = " | ".join(
                    str(cell) for cell in row if cell is not None and str(cell).strip()
                )
                normalised = normalise_text(text)
                if not text:
                    continue
                if not terms or any(term in normalised for term in terms):
                    samples.append(f"{sheet_name}: {text[:220]}")
                if len(samples) >= 8:
                    return samples
        return samples

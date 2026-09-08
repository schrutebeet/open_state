from __future__ import annotations

import calendar
import csv
import hashlib
import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import openpyxl
from bs4 import BeautifulSoup

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.html_excel import HtmlExcelConnector
from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.parsers.common import normalise_text, parse_decimal, period_from_label

LOGGER = logging.getLogger(__name__)
PENSIONERS_PAGE = (
    "https://www.seg-social.es/wps/portal/wss/internet/"
    "EstadisticasPresupuestosEstudios/Estadisticas/EST23/"
    "26bce586-014b-4c4e-9484-009cee21e271"
)
PENSIONERS_ROOT = (
    "https://www.seg-social.es/wps/portal/wss/internet/"
    "EstadisticasPresupuestosEstudios/Estadisticas/EST23"
)


class SocialSecurityPensionsConnector(HtmlExcelConnector):
    """Dedicated parser for the INSS monthly pension workbooks.

    These books use hierarchical headers and time-series layouts that cannot be
    mapped safely with the generic Excel label matcher.
    """

    connector_name = "social_security_pensions"
    STATIC_INDICATORS = {
        "social_security_pensioners": "pensioner_count",
        "social_security_pension_payroll": "pension_monthly_payroll",
        "social_security_minimum_supplements": "pensions_with_minimum_supplement",
    }

    def collect(self, dataset, context, indicators):
        if dataset.code in {
            "social_security_pension_payroll",
            "social_security_minimum_supplements",
        }:
            return self._collect_static_and_latest(dataset, context, indicators)
        if dataset.code != "social_security_pensioners":
            return super().collect(dataset, context, indicators)
        documents = [self._seed_pensioner_document(dataset, indicators)]
        try:
            page = self._get_pensioners_page(context)
            excel_url, label = self._find_pensioners_excel(page)
            response = context.http.get(excel_url)
            payload = context.http.payload(dataset.code, dataset.source, response, {
                "listing_url": page.source_url,
                "selected_link_text": label,
                "history_periods": context.settings.lookback_period,
            })
            seeded_candidates = documents[0][1]
            live_candidates = self.extract(dataset, payload, indicators)
            seeded_labels = {candidate.period.label for candidate in seeded_candidates}
            latest_seeded_end = max(
                (candidate.period.end for candidate in seeded_candidates),
                default=date.min,
            )
            live_candidates = [
                candidate for candidate in live_candidates
                if (
                    candidate.period.label in seeded_labels
                    or candidate.period.end > latest_seeded_end
                )
            ]
            live_periods = {candidate.period.label for candidate in live_candidates}
            documents[0] = (
                documents[0][0],
                [
                    candidate for candidate in seeded_candidates
                    if candidate.period.label not in live_periods
                ],
            )
            if live_candidates:
                documents.append((payload, live_candidates))
        except (LookupError, OSError, ValueError) as exc:
            LOGGER.warning("Could not refresh current pensioner workbook: %s", exc)
        return documents

    def _collect_static_and_latest(self, dataset, context, indicators):
        """Seed history, then replace its newest period with the live XLSX.

        The monthly statistics page publishes only the current workbook for
        these two reports.  Rewriting the filename is unsafe because the
        portal can serve the current cached workbook for an older-looking URL.
        We therefore discover the actual link on the page every run.
        """
        seed_payload, seeded = SocialSecurityPensionsConnector._seed_csv_document(
            dataset, indicators
        )
        try:
            listing = context.http.get(dataset.endpoint)
            prefix = {
                "social_security_pension_payroll": "ICONCEPTOS",
                "social_security_minimum_supplements": "MIN",
            }[dataset.code]
            pattern = re.compile(rf"/{prefix}(20\d{{2}}(?:0[1-9]|1[0-2]))\.xlsx(?:\?|$)", re.I)
            links = []
            soup = BeautifulSoup(listing.body, "html.parser")
            for anchor in soup.find_all("a", href=True):
                url = urljoin(listing.source_url, str(anchor["href"]))
                match = pattern.search(url)
                if match:
                    links.append((int(match.group(1)), url, " ".join(anchor.stripped_strings)))
            if not links:
                raise LookupError(f"No current {prefix} XLSX link found at {listing.source_url}")
            _, excel_url, label = max(links)
            response = context.http.get(excel_url)
            payload = context.http.payload(dataset.code, dataset.source, response, {
                "listing_url": listing.source_url,
                "selected_link_text": label,
            })
            latest = self.extract(dataset, payload, indicators)
            latest_periods = {candidate.period.label for candidate in latest}
            seeded = [
                candidate for candidate in seeded
                if candidate.period.label not in latest_periods
            ]
            return [(seed_payload, seeded), (payload, latest)]
        except (LookupError, OSError, ValueError, KeyError) as exc:
            LOGGER.warning("Could not refresh current %s workbook: %s", dataset.code, exc)
            return [(seed_payload, seeded)]

    @staticmethod
    def _data_file() -> Path:
        return Path(__file__).resolve().parents[3] / "data" / "social_security_data.csv"

    @classmethod
    def _seed_csv_document(cls, dataset, indicators):
        data_file = cls._data_file()
        body = data_file.read_bytes()
        payload = DatasetPayload(
            dataset_code=dataset.code,
            source_code=dataset.source,
            fetched_at=datetime.now(UTC),
            source_url=PENSIONERS_PAGE,
            content_type="text/csv",
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            metadata={"listing_url": PENSIONERS_PAGE, "selected_link_text": data_file.name},
        )
        candidates = []
        with data_file.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("indicator_code") != cls.STATIC_INDICATORS.get(dataset.code):
                    continue
                year, month = int(row["period"][:4]), int(row["period"][4:])
                period = Period(
                    start=date(year, month, 1),
                    end=date(year, month, calendar.monthrange(year, month)[1]),
                    label=f"{year}-{month:02d}",
                    frequency="monthly",
                )
                values = {
                    row["indicator_code"]: (
                        Decimal(row["value"]), row.get("source_series", "CSV!value")
                    )
                }
                for candidate in cls._build_candidates(
                    dataset, payload, indicators, period, values
                ):
                    candidate = replace(candidate, source_url=row["source_url"])
                    candidate.metadata["official_source_url"] = row["source_url"]
                    candidate.metadata["official_source_document"] = row["source_document"]
                    candidate.metadata["official_source_type"] = row["source_type"]
                    candidates.append(candidate)
        return payload, candidates

    @classmethod
    def _seed_pensioner_document(cls, dataset, indicators):
        return cls._seed_csv_document(dataset, indicators)

    @staticmethod
    def _get_pensioners_page(context):
        try:
            page = context.http.get(PENSIONERS_PAGE)
            if "pensionista" in normalise_text(page.body.decode("utf-8", errors="ignore")):
                return page
        except Exception as exc:
            LOGGER.info("Official Pensionistas shortcut unavailable: %s", exc)
        root = context.http.get(PENSIONERS_ROOT)
        soup = BeautifulSoup(root.body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            if normalise_text(" ".join(anchor.stripped_strings)) == "pensionistas":
                return context.http.get(urljoin(root.source_url, str(anchor["href"])))
        raise LookupError("Could not find the official Pensionistas page")

    @staticmethod
    def _find_pensioners_excel(page):
        soup = BeautifulSoup(page.body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = urljoin(page.source_url, str(anchor["href"]))
            text = normalise_text(
                " ".join([*anchor.stripped_strings, str(anchor.get("title", "")), href])
            )
            if ".xlsx" in href.lower() and "pensionista" in text:
                return href, " ".join(anchor.stripped_strings)
        raise LookupError("The official Pensionistas page has no XLSX link")

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        workbook = openpyxl.load_workbook(BytesIO(payload.body), data_only=True, read_only=True)
        handlers = {
            "social_security_pension_series": self._extract_series,
            "social_security_pension_payroll": self._extract_payroll,
            "social_security_pensioners": self._extract_pensioners,
        }
        handler = handlers.get(dataset.code)
        if handler is None:
            raise ValueError(f"Unsupported pension workbook dataset {dataset.code}")
        if dataset.code == "social_security_pension_series":
            if (
                dataset.config.get("data_sheet") == "CA_Total sistema"
                and "CA_Total sistema" in workbook.sheetnames
            ):
                period = self._period_from_workbook(workbook) or self._period_from_payload(payload)
                return self._build_candidates(
                    dataset, payload, indicators, period, self._extract_ca_total(workbook)
                )
            rows = self._extract_series_history(workbook)
            history_periods = int(payload.metadata.get("history_periods", 12))
            results: list[ObservationCandidate] = []
            for period, values in rows[-history_periods:]:
                results.extend(self._build_candidates(dataset, payload, indicators, period, values))
            return results
        if dataset.code == "social_security_pensioners":
            if "history_periods" not in payload.metadata:
                period = self._period_from_workbook(workbook) or self._period_from_payload(payload)
                return self._build_candidates(
                    dataset, payload, indicators, period, self._extract_pensioners(workbook)
                )
            rows = self._extract_pensioner_history(workbook)
            history_periods = int(payload.metadata.get("history_periods", 12))
            results = []
            for period, values in rows[-history_periods:]:
                results.extend(self._build_candidates(dataset, payload, indicators, period, values))
            return results
        period = self._period_from_workbook(workbook) or self._period_from_payload(payload)
        values = handler(workbook)
        return self._build_candidates(dataset, payload, indicators, period, values)

    @staticmethod
    def _period_from_workbook(workbook: openpyxl.Workbook) -> Period | None:
        """Read the publication period stated in the workbook headings."""
        pattern = re.compile(
            r"\b1\s+de\s+([a-záéíóúüñ]+)\s+de\s+(20\d{2})\b", re.IGNORECASE
        )
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    match = pattern.search(str(value))
                    if match:
                        return period_from_label(
                            f"{match.group(1)} {match.group(2)}", "monthly"
                        )
        return None

    @staticmethod
    def _period_from_payload(payload: DatasetPayload) -> Period:
        combined = " ".join(
            [
                payload.source_url,
                str(payload.metadata.get("selected_link_text", "")),
            ]
        )
        match = re.search(r"(?:CA|PTAS|ICONCEPTOS|S)(20\d{2})(0[1-9]|1[0-2])", combined, re.I)
        if not match:
            raise ValueError(f"Could not infer pension workbook period from {combined!r}")
        year, month = int(match.group(1)), int(match.group(2))
        return Period(
            start=date(year, month, 1),
            end=date(year, month, calendar.monthrange(year, month)[1]),
            label=f"{year}-{month:02d}",
            frequency="monthly",
        )

    @staticmethod
    def _extract_series(workbook: openpyxl.Workbook) -> dict[str, tuple[Decimal, str]]:
        sheet = workbook["S_Total"]
        latest_row: tuple[int, list[object]] | None = None
        current_year: int | None = None
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            # The workbook contains a second percentage-change table below the
            # absolute series. Stop when that section begins.
            if values and normalise_text(values[0]) == "periodo" and row_number > 10:
                break
            if values and isinstance(values[0], (int, float)):
                current_year = int(values[0])
            month_label = values[1] if len(values) > 1 else None
            total_count = values[2] if len(values) > 2 else None
            if (
                current_year is None
                or not isinstance(month_label, str)
                or total_count in (None, "")
            ):
                continue
            latest_row = (row_number, values)
        if latest_row is None:
            raise LookupError("No populated monthly row found in S_Total")
        row_number, values = latest_row
        return {
            "pension_count": (parse_decimal(values[2]), f"S_Total!R{row_number}C3"),
            "average_pension": (parse_decimal(values[4]), f"S_Total!R{row_number}C5"),
            "average_retirement_pension": (
                parse_decimal(values[10]),
                f"S_Total!R{row_number}C11",
            ),
        }

    @classmethod
    def _extract_series_history(
        cls, workbook: openpyxl.Workbook
    ) -> list[tuple[Period, dict[str, tuple[Decimal, str]]]]:
        """Extract the contiguous monthly block for the latest available year.

        The workbook keeps only December observations for older years and then
        lists the current year month by month. Those December rows are annual
        reference points, not a complete monthly history, so mixing them into
        the current-year block would produce a misleading monthly series.
        """
        sheet = workbook["S_Total"]
        rows: list[tuple[Period, dict[str, tuple[Decimal, str]]]] = []
        current_year: int | None = None
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if values and normalise_text(values[0]) == "periodo" and row_number > 10:
                break
            if values and isinstance(values[0], (int, float)):
                current_year = int(values[0])
            month_label = values[1] if len(values) > 1 else None
            if current_year is None or not isinstance(month_label, str):
                continue
            if len(values) <= 10 or values[2] in (None, ""):
                continue
            try:
                period = period_from_label(f"{month_label} {current_year}", "monthly")
                values_by_indicator = {
                    "pension_count": (parse_decimal(values[2]), f"S_Total!R{row_number}C3"),
                    "average_pension": (parse_decimal(values[4]), f"S_Total!R{row_number}C5"),
                    "average_retirement_pension": (
                        parse_decimal(values[10]), f"S_Total!R{row_number}C11"
                    ),
                }
            except (TypeError, ValueError):
                continue
            rows.append((period, values_by_indicator))
        if not rows:
            return []
        latest_year = max(period.start.year for period, _ in rows)
        return sorted(
            [item for item in rows if item[0].start.year == latest_year],
            key=lambda item: item[0].end,
        )

    @staticmethod
    def _extract_payroll(workbook: openpyxl.Workbook) -> dict[str, tuple[Decimal, str]]:
        sheet = workbook["Importe"]
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if values and normalise_text(values[0]) == "total sistema":
                # Under the first header group, "Total pensiones", the first
                # numeric column is the total payroll in millions of euros.
                return {
                    "pension_monthly_payroll": (
                        parse_decimal(values[1]),
                        f"Importe!R{row_number}C2",
                    )
                }
        raise LookupError("Could not find Total sistema in pension payroll workbook")

    @staticmethod
    def _extract_ca_total(workbook: openpyxl.Workbook) -> dict[str, tuple[Decimal, str]]:
        """Extract the national total from the CCAA/provinces monthly book.

        In ``CA_Total sistema`` row 6 is the national total.  The columns are
        stable: B is total pension count, D total average, and J retirement
        average.  We locate the row by label and keep the explicit cell
        references for auditability.
        """
        sheet = workbook["CA_Total sistema"]
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if not values or normalise_text(values[0]) != "total sistema":
                continue
            if len(values) <= 9:
                continue
            try:
                return {
                    "pension_count": (
                        parse_decimal(values[1]), f"CA_Total sistema!R{row_number}C2"
                    ),
                    "average_pension": (
                        parse_decimal(values[3]), f"CA_Total sistema!R{row_number}C4"
                    ),
                    "average_retirement_pension": (
                        parse_decimal(values[9]), f"CA_Total sistema!R{row_number}C10"
                    ),
                }
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Ignoring incomplete Total sistema row %s in CA_Total sistema", row_number
                )
                continue
        raise LookupError("Could not find Total sistema in CA_Total sistema workbook")

    @staticmethod
    def _extract_pensioners(workbook: openpyxl.Workbook) -> dict[str, tuple[Decimal, str]]:
        sheet = workbook["Resumen de datos"]
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if values and normalise_text(values[0]) == "numero de pensionistas":
                return {
                    "pensioner_count": (
                        parse_decimal(values[1]),
                        f"Resumen de datos!R{row_number}C2",
                    )
                }
        raise LookupError("Could not find Número de pensionistas in workbook")

    @staticmethod
    def _extract_pensioner_history(workbook: openpyxl.Workbook):
        sheet = workbook["Pnes y ptas"]
        rows = []
        current_year = None
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if values and isinstance(values[0], (int, float)):
                current_year = int(values[0])
            if current_year is None or len(values) < 3 or not isinstance(values[1], str):
                continue
            if values[2] in (None, ""):
                continue
            try:
                period = period_from_label(f"{values[1]} {current_year}", "monthly")
                rows.append((period, {"pensioner_count": (
                    parse_decimal(values[2]), f"Pnes y ptas!R{row_number}C3"
                )}))
            except (TypeError, ValueError):
                continue
        return sorted(rows, key=lambda item: item[0].end)

    @staticmethod
    def _build_candidates(
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
        period: Period,
        values: dict[str, tuple[Decimal, str]],
    ) -> list[ObservationCandidate]:
        results: list[ObservationCandidate] = []
        for indicator in indicators:
            matched = values.get(indicator.code)
            if matched is None:
                continue
            value, source_series = matched
            results.append(
                ObservationCandidate(
                    indicator_code=indicator.code,
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=period,
                    value=value,
                    unit=indicator.unit,
                    source_series=source_series,
                    source_url=payload.source_url,
                    metadata={
                        "listing_url": payload.metadata.get("listing_url"),
                        "selected_link_text": payload.metadata.get("selected_link_text"),
                        "parser": "social_security_pensions_v1",
                    },
                )
            )
        return results

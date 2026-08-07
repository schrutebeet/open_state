from __future__ import annotations

import logging
import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.parsers.common import SPANISH_MONTHS, normalise_text, period_from_label
from civic_metrics.parsers.excel import WorkbookMatrix

LOGGER = logging.getLogger(__name__)


class HtmlExcelConnector(Connector):
    connector_name = "html_excel"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        listing = context.http.get(dataset.endpoint)
        soup = BeautifulSoup(listing.body, "html.parser")
        include_patterns = [
            re.compile(item, re.IGNORECASE) for item in dataset.config.get("link_include", [])
        ]
        exclude_patterns = [
            re.compile(item, re.IGNORECASE) for item in dataset.config.get("link_exclude", [])
        ]
        extensions = tuple(dataset.config.get("extensions", [".xlsx", ".xls", ".csv"]))
        candidates: list[tuple[str, str, tuple[int, int, int], int]] = []
        for position, anchor in enumerate(soup.find_all("a", href=True)):
            href = str(anchor.get("href"))
            anchor_text = " ".join(anchor.stripped_strings)
            context_text = self._link_context(anchor)
            combined = f"{context_text} {anchor_text} {href}"
            if include_patterns and not all(pattern.search(combined) for pattern in include_patterns):
                continue
            if any(pattern.search(combined) for pattern in exclude_patterns):
                continue
            if extensions and not self._href_has_allowed_extension(href, extensions):
                # Some portals use opaque download URLs without a file suffix. They can
                # opt into the older text-based fallback explicitly. It is disabled by
                # default because a table row may contain adjacent PDF and XLS links;
                # inspecting the whole row would then incorrectly accept the PDF link.
                if not dataset.config.get("allow_opaque_links", False):
                    continue
                if not any(
                    word in normalise_text(combined)
                    for word in ("xls", "xlsx", "csv", "cuadros", "series", "descarga")
                ):
                    continue
            candidates.append(
                (
                    urljoin(listing.source_url, href),
                    context_text or anchor_text,
                    self._publication_score(combined),
                    position,
                )
            )
        if not candidates:
            examples = [
                f"{' '.join(a.stripped_strings)} :: {a.get('href')}"
                for a in soup.find_all("a", href=True)[:12]
            ]
            raise LookupError(
                f"No downloadable file matched dataset {dataset.code} on {dataset.endpoint}; "
                f"link examples={examples}"
            )
        if any(item[2] != (0, 0, 0) for item in candidates):
            download_url, link_text, _, _ = max(candidates, key=lambda item: (item[2], -item[3]))
        else:
            download_url, link_text, _, _ = candidates[0]
        file_response = context.http.get(download_url)
        return context.http.payload(
            dataset.code,
            dataset.source,
            file_response,
            {
                "listing_url": listing.source_url,
                "selected_link_text": link_text,
                "candidate_count": len(candidates),
            },
        )

    @staticmethod
    def _link_context(anchor: Tag) -> str:
        for parent_name in ("tr", "li"):
            parent = anchor.find_parent(parent_name)
            if parent is not None:
                return " ".join(parent.stripped_strings)
        parent = anchor.parent
        return " ".join(parent.stripped_strings) if isinstance(parent, Tag) else ""

    @staticmethod
    def _href_has_allowed_extension(href: str, extensions: tuple[str, ...]) -> bool:
        path = urlparse(href).path.lower()
        return any(path.endswith(extension.lower()) for extension in extensions)

    @staticmethod
    def _publication_score(text: str) -> tuple[int, int, int]:
        normalised = normalise_text(text)
        year_matches = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", normalised)]
        year = max(year_matches, default=0)
        month = 0
        for name, number in SPANISH_MONTHS.items():
            if len(name) >= 3 and re.search(rf"\b{re.escape(name)}\b", normalised):
                month = max(month, number)
        quarter_match = re.search(r"(?:q|t|trimestre)\s*([1-4])", normalised)
        quarter = int(quarter_match.group(1)) if quarter_match else 0
        return year, month, quarter

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        workbook = WorkbookMatrix.from_bytes(payload.body, payload.content_type, payload.source_url)
        results: list[ObservationCandidate] = []
        for indicator in indicators:
            extraction = indicator.extraction
            try:
                match = workbook.find_value(
                    sheet_include=extraction.sheet_include,
                    row_include=extraction.row_include,
                    column_include=extraction.column_include,
                )
            except LookupError:
                LOGGER.exception("Could not extract %s from %s", indicator.code, dataset.code)
                continue

            period = self._infer_period(dataset, payload, workbook, match.column_label, match.sheet, indicator.frequency)
            if period is None:
                LOGGER.error(
                    "Could not infer period for %s from link=%r column=%r sheet=%r",
                    indicator.code,
                    payload.metadata.get("selected_link_text"),
                    match.column_label,
                    match.sheet,
                )
                continue

            results.append(
                ObservationCandidate(
                    indicator_code=indicator.code,
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=period,
                    value=Decimal(str(match.value)) * Decimal(extraction.multiplier),
                    unit=indicator.unit,
                    source_series=f"{match.sheet}!R{match.row + 1}C{match.column + 1}",
                    source_url=payload.source_url,
                    metadata={
                        "sheet": match.sheet,
                        "row": match.row + 1,
                        "column": match.column + 1,
                        "matched_label": match.label,
                        "column_label": match.column_label,
                        "listing_url": payload.metadata.get("listing_url"),
                    },
                )
            )
        return results

    @staticmethod
    def _infer_period(
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        workbook: WorkbookMatrix,
        column_label: str | None,
        sheet: str,
        frequency: str,
    ) -> Period | None:
        contexts = [
            str(payload.metadata.get("selected_link_text", "")),
            column_label or "",
            sheet,
            str(dataset.config.get("period_fallback", "")),
        ]
        for context in contexts:
            if not context:
                continue
            try:
                return period_from_label(context, frequency)
            except ValueError:
                pass
        return workbook.infer_latest_period(frequency)

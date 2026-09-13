from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal
from urllib.parse import parse_qs, parse_qsl, unquote_plus, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate, Period
from civic_metrics.parsers.common import SPANISH_MONTHS, normalise_text, period_from_label
from civic_metrics.parsers.excel import WorkbookMatrix

LOGGER = logging.getLogger(__name__)


class HtmlExcelConnector(Connector):
    connector_name = "html_excel"

    def collect(self, dataset, context, indicators):
        if not dataset.config.get("historical_files"):
            return super().collect(dataset, context, indicators)
        if dataset.config.get("historical_year_tree"):
            return self._collect_historical_year_tree(dataset, context, indicators)
        documents = []
        seen_urls = set()
        seen_content_hashes = set()
        periods = {item.code: set() for item in indicators}
        target = context.settings.lookback_period
        listing_urls = [dataset.endpoint]
        visited = set()
        while listing_urls:
            listing_url = listing_urls.pop(0)
            if listing_url in visited:
                continue
            visited.add(listing_url)
            listing = context.http.get(listing_url)
            soup = BeautifulSoup(listing.body, "html.parser")
            links = []
            archives = []
            for anchor in soup.find_all("a", href=True):
                url = urljoin(listing.source_url, str(anchor["href"]))
                label = self._link_context(anchor)
                combined = unquote_plus(f"{label} {url}")
                archive_pattern = dataset.config.get("archive_link_pattern")
                if archive_pattern and re.search(archive_pattern, url, re.I):
                    if urlparse(url).netloc == urlparse(listing.source_url).netloc:
                        archives.append(url)
                if not self._href_has_allowed_extension(url, tuple(dataset.config.get("extensions", [".xlsx"]))):
                    continue
                if not all(re.search(p, combined, re.I) for p in dataset.config.get("link_include", [])):
                    continue
                if any(re.search(p, combined, re.I) for p in dataset.config.get("link_exclude", [])):
                    continue
                links.append((url, label, self._publication_score(combined)))
            if dataset.config.get("generate_historical_files"):
                links = self._generated_historical_links(links, target)
            for url, label, _ in sorted(links, key=lambda item: item[2], reverse=True):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                response = context.http.get(url)
                content_hash = hashlib.sha256(response.body).hexdigest()
                if content_hash in seen_content_hashes:
                    LOGGER.warning("Skipping duplicate historical document %s", url)
                    continue
                seen_content_hashes.add(content_hash)
                payload = context.http.payload(dataset.code, dataset.source, response, {
                    "listing_url": listing.source_url, "selected_link_text": label,
                    "history_periods": target,
                })
                try:
                    candidates = self.extract(dataset, payload, indicators)
                except (KeyError, LookupError, TypeError, ValueError) as exc:
                    LOGGER.warning("Skipping unparseable historical file %s: %s", url, exc)
                    continue
                unique = []
                for candidate in sorted(candidates, key=lambda item: item.period.end, reverse=True):
                    bucket = periods[candidate.indicator_code]
                    key = (candidate.period.start, candidate.period.end)
                    if key not in bucket and len(bucket) < target:
                        bucket.add(key)
                        unique.append(candidate)
                documents.append((payload, unique))
                if all(len(bucket) >= target for bucket in periods.values()):
                    return documents
            listing_urls.extend(sorted(set(archives) - visited, reverse=True))
        missing = {
            code: target - len(bucket)
            for code, bucket in periods.items()
            if len(bucket) < target
        }
        if missing:
            self._warn_incomplete_history(dataset.code, target, periods)
        if not documents:
            raise LookupError(f"No historical files found for {dataset.code}")
        return documents

    def _collect_historical_year_tree(self, dataset, context, indicators):
        """Collect real monthly files from an annual portal tree.

        When ``historical_navigation`` is configured, the portal is followed by
        the visible labels rather than by crawling every descendant.  This is
        important here because each annual page contains several reports with
        similarly named Excel files. A navigation step may provide multiple
        accepted labels when the portal uses alternate titles across years.
        """
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        target = context.settings.lookback_period
        root = context.http.get(dataset.endpoint)
        root_soup = BeautifulSoup(root.body, "html.parser")
        year_links = []
        for anchor in root_soup.find_all("a", href=True):
            label = " ".join(anchor.stripped_strings)
            match = re.fullmatch(r"a[nñ]o\s+(20\d{2})", normalise_text(label), re.I)
            if match:
                year_links.append((int(match.group(1)), urljoin(root.source_url, str(anchor["href"]))))
        if not year_links:
            raise LookupError(f"No annual links found at {dataset.endpoint}")

        documents = []
        seen_files = set()
        seen_content_hashes = set()
        periods = {item.code: set() for item in indicators}
        max_pages_per_year = int(dataset.config.get("max_historical_pages_per_year", 40))
        navigation_labels = dataset.config.get("historical_navigation", [])

        for year, year_url in sorted(set(year_links), reverse=True):
            page = context.http.get(year_url)
            if navigation_labels:
                for label in navigation_labels:
                    page = self._get_named_page(page, label, context)
                soup = BeautifulSoup(page.body, "html.parser")
                files = self._file_links(soup, page.source_url, dataset)
            else:
                # Backwards-compatible fallback for other annual trees whose
                # structure is not configured yet.
                files = self._files_from_descendants(
                    page, dataset, context, max_pages_per_year
                )

            for url, label, _ in sorted(files, key=lambda item: item[2], reverse=True):
                if url in seen_files:
                    continue
                seen_files.add(url)
                try:
                    response = context.http.get(url)
                except Exception as exc:
                    LOGGER.warning("Could not download historical file %s: %s", url, exc)
                    continue
                content_hash = hashlib.sha256(response.body).hexdigest()
                if content_hash in seen_content_hashes:
                    LOGGER.warning("Skipping duplicate historical document %s", url)
                    continue
                seen_content_hashes.add(content_hash)
                payload = context.http.payload(dataset.code, dataset.source, response, {
                    "listing_url": page.source_url,
                    "selected_link_text": label,
                    "history_periods": target,
                    "source_year": year,
                })
                try:
                    candidates = self.extract(dataset, payload, indicators)
                except (KeyError, LookupError, TypeError, ValueError) as exc:
                    LOGGER.warning("Skipping unparseable historical file %s: %s", url, exc)
                    continue
                unique = []
                for candidate in sorted(candidates, key=lambda item: item.period.end, reverse=True):
                    bucket = periods[candidate.indicator_code]
                    key = (candidate.period.start, candidate.period.end)
                    if key not in bucket and len(bucket) < target:
                        bucket.add(key)
                        unique.append(candidate)
                if unique:
                    documents.append((payload, unique))
                if all(len(bucket) >= target for bucket in periods.values()):
                    return documents

        if not documents:
            raise LookupError(f"No historical files found for {dataset.code}")
        self._warn_incomplete_history(dataset.code, target, periods)
        return documents

    @staticmethod
    def _warn_incomplete_history(
        dataset_code: str,
        requested_periods: int,
        periods: dict[str, set[tuple[object, object]]],
    ) -> None:
        """Report a short official archive without failing the whole dataset.

        Historical portals often publish fewer distinct workbooks than requested.
        The rows that were found remain valid and are persisted; this is an
        availability warning, not a parsing or transport error.
        """
        found = {
            indicator_code: len(indicator_periods)
            for indicator_code, indicator_periods in periods.items()
            if len(indicator_periods) < requested_periods
        }
        if found:
            LOGGER.warning(
                "Historical source has fewer periods than requested for dataset=%s: "
                "requested=%s, found=%s. Continuing with the available official data.",
                dataset_code,
                requested_periods,
                found,
            )

    @staticmethod
    def _get_named_page(page, expected_label: str | list[str], context):
        soup = BeautifulSoup(page.body, "html.parser")
        expected_labels = (
            [expected_label] if isinstance(expected_label, str) else expected_label
        )
        expected = {normalise_text(label) for label in expected_labels}
        for anchor in soup.find_all("a", href=True):
            text = normalise_text(" ".join(anchor.stripped_strings))
            if text in expected:
                url = urljoin(page.source_url, str(anchor["href"]))
                if HtmlExcelConnector._is_auxiliary_navigation_url(urlparse(url)):
                    continue
                return context.http.get(url)
        raise LookupError(
            f"Could not find navigation link matching {expected_labels!r} at {page.source_url}"
        )

    @staticmethod
    def _is_auxiliary_navigation_url(parsed_url):
        """Reject language, fragment and other global-navigation links."""
        query_keys = {key.lower() for key in parse_qs(parsed_url.query)}
        return bool(parsed_url.fragment) or "changelanguage" in query_keys

    def _file_links(self, soup, source_url, dataset):
        files = []
        for anchor in soup.find_all("a", href=True):
            url = urljoin(source_url, str(anchor["href"]))
            if not self._href_has_allowed_extension(url, tuple(dataset.config.get("extensions", [".xlsx"]))):
                continue
            label = self._link_context(anchor)
            combined = unquote_plus(f"{label} {' '.join(anchor.stripped_strings)} {url}")
            if not all(re.search(pattern, combined, re.I) for pattern in dataset.config.get("link_include", [])):
                continue
            if any(re.search(pattern, combined, re.I) for pattern in dataset.config.get("link_exclude", [])):
                continue
            files.append((url, label, self._publication_score(combined)))
        return files

    def _files_from_descendants(self, first_page, dataset, context, limit):
        """Compatibility crawler used only when no semantic route is configured."""
        root = urlparse(first_page.source_url)
        queue = [first_page]
        visited = set()
        files = []
        while queue and len(visited) < limit:
            page = queue.pop(0)
            if page.source_url in visited:
                continue
            visited.add(page.source_url)
            soup = BeautifulSoup(page.body, "html.parser")
            files.extend(self._file_links(soup, page.source_url, dataset))
            for anchor in soup.find_all("a", href=True):
                url = urljoin(page.source_url, str(anchor["href"]))
                parsed_url = urlparse(url)
                if self._is_auxiliary_navigation_url(parsed_url):
                    continue
                if (parsed_url.netloc == root.netloc
                        and parsed_url.path.startswith(root.path.rstrip("/") + "/")
                        and url not in visited):
                    queue.append(context.http.get(url))
        return files

    @classmethod
    def _generated_historical_links(cls, links, target):
        """Add prior monthly filenames when a portal only publishes its latest link."""
        expanded = list(links)
        seen = {url for url, _, _ in links}
        period_pattern = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)")
        for url, label, score in links:
            match = period_pattern.search(url)
            if match is None:
                continue
            year, month = int(match.group(1)), int(match.group(2))
            for _ in range(1, max(0, target)):
                month -= 1
                if month == 0:
                    year -= 1
                    month = 12
                period_code = f"{year}{month:02d}"
                historical_url = period_pattern.sub(period_code, url, count=1)
                if historical_url in seen:
                    continue
                seen.add(historical_url)
                parsed_url = urlparse(historical_url)
                query = urlencode(
                    [item for item in parse_qsl(parsed_url.query) if item[0].lower() == "mod"]
                )
                historical_url = urlunparse(parsed_url._replace(query=query, fragment=""))
                historical_label = period_pattern.sub(period_code, label, count=1)
                expanded.append((historical_url, historical_label, (year, month, score[2])))
        return expanded

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
                "history_periods": context.settings.lookback_period,
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
        path = unquote_plus(urlparse(href).path).lower()
        return any(path.endswith(extension.lower()) for extension in extensions)

    @staticmethod
    def _publication_score(text: str) -> tuple[int, int, int]:
        normalised = normalise_text(unquote_plus(text))
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
            except LookupError as exc:
                # A historic archive can contain similarly named workbooks with a
                # different layout. It is expected while scanning candidate files.
                LOGGER.warning(
                    "Skipping document for indicator=%s dataset=%s: %s",
                    indicator.code,
                    dataset.code,
                    exc,
                )
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

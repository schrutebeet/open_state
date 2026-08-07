from __future__ import annotations

import logging
import re
from decimal import Decimal
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector, ConnectorContext
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.parsers.common import parse_decimal, period_from_label

LOGGER = logging.getLogger(__name__)


class HtmlRegexConnector(Connector):
    connector_name = "html_regex"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        listing = context.http.get(dataset.endpoint)
        follow = bool(dataset.config.get("follow_link", False))
        if not follow:
            return context.http.payload(dataset.code, dataset.source, listing)

        soup = BeautifulSoup(listing.body, "html.parser")
        include_patterns = [re.compile(item, re.IGNORECASE) for item in dataset.config.get("link_include", [])]
        exclude_patterns = [re.compile(item, re.IGNORECASE) for item in dataset.config.get("link_exclude", [])]
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.stripped_strings)
            href = str(anchor.get("href"))
            combined = f"{text} {href}"
            if include_patterns and not all(pattern.search(combined) for pattern in include_patterns):
                continue
            if any(pattern.search(combined) for pattern in exclude_patterns):
                continue
            article = context.http.get(urljoin(listing.source_url, href))
            return context.http.payload(
                dataset.code,
                dataset.source,
                article,
                {"listing_url": listing.source_url, "selected_link_text": text},
            )
        raise LookupError(f"No article link matched dataset {dataset.code}")

    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        soup = BeautifulSoup(payload.body, "html.parser")
        text = " ".join(soup.stripped_strings)
        period_regex = dataset.config.get(
            "period_regex",
            r"(?P<period>(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)(?:\s+de)?\s+20\d{2})",
        )
        period_matches = list(re.finditer(period_regex, text, re.IGNORECASE))
        parsed_periods = []
        for period_match in period_matches:
            period_text = period_match.groupdict().get("period") or period_match.group(0)
            try:
                parsed_periods.append(period_from_label(period_text, indicators[0].frequency))
            except ValueError:
                continue
        inferred_period = max(parsed_periods, key=lambda item: item.end) if parsed_periods else None
        fallback_period_text = str(payload.metadata.get("selected_link_text", ""))

        results: list[ObservationCandidate] = []
        for indicator in indicators:
            pattern = indicator.extraction.value_regex
            if not pattern:
                continue
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                LOGGER.warning("Regex did not match %s in %s", indicator.code, payload.source_url)
                continue
            raw_value = match.groupdict().get("value") or match.group(1)
            if inferred_period is not None:
                period = inferred_period
            else:
                try:
                    period = period_from_label(fallback_period_text, indicator.frequency)
                except ValueError:
                    LOGGER.exception("Could not parse HTML article period for %s", indicator.code)
                    continue
            results.append(
                ObservationCandidate(
                    indicator_code=indicator.code,
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=period,
                    value=parse_decimal(raw_value) * Decimal(indicator.extraction.multiplier),
                    unit=indicator.unit,
                    source_series=indicator.code,
                    source_url=payload.source_url,
                    metadata={
                        "matched_text": match.group(0),
                        "listing_url": payload.metadata.get("listing_url"),
                    },
                )
            )
        return results

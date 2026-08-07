from __future__ import annotations

from civic_metrics.catalog import DatasetDefinition
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.connectors.html_excel import HtmlExcelConnector
from civic_metrics.domain import DatasetPayload


class DirectFileConnector(HtmlExcelConnector):
    """Download a stable official CSV/XLS/XLSX URL directly.

    Some institutions update a fixed file URL in place. This is preferable to
    scraping a publication page; the artefact hash still records each revision.
    """

    connector_name = "direct_file"

    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        if not dataset.endpoint:
            raise ValueError(f"Dataset {dataset.code} requires an endpoint")
        response = context.http.get(dataset.endpoint)
        return context.http.payload(
            dataset.code,
            dataset.source,
            response,
            {
                "listing_url": dataset.config.get("source_page"),
                "selected_link_text": dataset.config.get("period_hint", ""),
            },
        )

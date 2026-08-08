from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from civic_metrics.catalog import DatasetDefinition, load_catalog
from civic_metrics.connectors.html_regex import HtmlRegexConnector
from civic_metrics.domain import DatasetPayload


def test_html_regex_uses_latest_explicit_month_not_first_historical_reference() -> None:
    catalog = load_catalog(Path("config"))
    dataset = DatasetDefinition(
        code="social_security_affiliation_article",
        source="social_security",
        connector="html_regex",
    )
    indicator = catalog.indicator_by_code["social_security_affiliates_avg"].model_copy(
        update={
            "extraction": catalog.indicator_by_code[
                "social_security_affiliates_avg"
            ].extraction.model_copy(
                update={
                    "kind": "html_regex",
                    "value_regex": (
                        r"afiliacion media registra (?P<value>[0-9]{1,3}(?:\.[0-9]{3})+)"
                    ),
                }
            )
        }
    )
    html = b"""
    <html><body>
      <p>El ritmo es el mayor desde abril de 2023.</p>
      <p>La afiliacion media registra 22.508.065 afiliados medios en julio.</p>
      <p>Si se descuenta la estacionalidad y el efecto calendario, la afiliacion alcanza
      22.288.120 ocupados.</p>
      <a>Afiliados julio 2026</a>
    </body></html>
    """
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/article",
        content_type="text/html",
        body=html,
        sha256=hashlib.sha256(html).hexdigest(),
        metadata={},
    )
    results = HtmlRegexConnector().extract(dataset, payload, [indicator])
    assert {item.period.label for item in results} == {"2026-07"}

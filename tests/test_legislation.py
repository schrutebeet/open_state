from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from civic_metrics.http import CachedResponse
from civic_metrics.legislation import (
    BOE_ELI_DAILY_URL,
    BOE_LEGISLATION_API_URL,
    BOE_SUMMARY_API_URL,
    CONGRESS_OPEN_DATA_URL,
    EVENT_CONGRESS_APPROVAL,
    EVENT_FINAL_APPROVAL,
    EVENT_PUBLICATION,
    EVENT_STARTED,
    SENATE_BULK_DATA_URL,
    DailyLawReport,
    Ley,
    OfficialLawSources,
    extract_boe_summary_items,
    is_ordinary_law,
    parse_tabular_payload,
)

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = date(2026, 9, 15)


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, params=None, headers=None):
        del headers
        self.calls.append(url)
        if url == CONGRESS_OPEN_DATA_URL:
            body = (FIXTURES / "congress_initiatives.html").read_bytes()
        elif "IniciativasLegislativasAprobadas__test.json" in url:
            body = (FIXTURES / "congress_approved.json").read_bytes()
        elif "ProyectosDeLey__test.json" in url:
            body = (FIXTURES / "congress_projects.json").read_bytes()
        elif "ProposicionesDeLey__test.json" in url:
            body = (FIXTURES / "congress_proposals.json").read_bytes()
        elif url == "https://www.senado.es/web/relacionesciudadanos/datosabiertos/catalogodatos/iniciativas/index.html?legis=15":
            body = (
                b'<a href="/web/ficopendataservlet?legis=15&tipoFich=9">'
                b'Descargar datos abiertos</a>'
            )
        elif url.startswith(SENATE_BULK_DATA_URL):
            body = (FIXTURES / "senate_initiatives.xml").read_bytes()
        elif url == BOE_LEGISLATION_API_URL:
            body = (FIXTURES / "boe_legislation.json").read_bytes()
        elif url.startswith(BOE_ELI_DAILY_URL):
            body = (FIXTURES / "boe_eli_daily.html").read_bytes()
        elif url.startswith("https://www.boe.es/diario_boe/xml.php?id=BOE-A-2026-9999"):
            body = (FIXTURES / "boe_document.xml").read_bytes()
        elif url.startswith(BOE_SUMMARY_API_URL):
            body = (FIXTURES / "boe_summary.json").read_bytes()
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        content_type = "text/html" if body.lstrip().startswith(b"<!") else "application/json"
        if body.lstrip().startswith(b"<?xml"):
            content_type = "application/xml"
        return CachedResponse(body, url, content_type, {})


class CongressUnavailableHttp(FakeHttp):
    def get(self, url: str, *, params=None, headers=None):
        if url == CONGRESS_OPEN_DATA_URL:
            raise RuntimeError("503 Service Unavailable")
        return super().get(url, params=params, headers=headers)


class BoeSummaryMissingHttp(FakeHttp):
    def get(self, url: str, *, params=None, headers=None):
        if url.startswith(BOE_SUMMARY_API_URL):
            request = httpx.Request("GET", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)
        if url == BOE_LEGISLATION_API_URL:
            return CachedResponse(
                b'{"status":{"code":"200"},"data":[]}',
                url,
                "application/json",
                {},
            )
        return super().get(url, params=params, headers=headers)


def test_ordinary_law_filter_excludes_organic_laws() -> None:
    assert is_ordinary_law("Proyecto de Ley de transparencia", "Proyecto de ley")
    assert not is_ordinary_law("Proyecto de Ley Orgánica de prueba", "Proyecto de ley orgánica")


def test_boe_summary_keeps_state_department_context() -> None:
    payload = json.loads((FIXTURES / "boe_summary.json").read_text(encoding="utf-8"))
    items = extract_boe_summary_items(payload)
    assert len(items) == 1
    assert items[0]["department_name"] == "Jefatura del Estado"


def test_tabular_parser_supports_json_xml_and_csv() -> None:
    assert len(parse_tabular_payload(b'[{"TIPO":"Proyecto de ley"}]', "feed.json")) == 1
    assert (
        len(parse_tabular_payload(b"<root><item><titulo>Ley</titulo></item></root>", "feed.xml"))
        == 1
    )
    csv_rows = parse_tabular_payload(
        b"TIPO,OBJETO\nProyecto de ley,Ley de prueba\n", "feed.csv"
    )
    assert csv_rows[0]["TIPO"] == "Proyecto de ley"


def test_congress_approved_law_feed_is_supported() -> None:
    law = Ley.from_congress_record(
        {
            "TIPO": "Leyes",
            "TITULO_LEY": "Ley 1/2025, de 1 de abril, de prueba.",
            "NUMERO_LEY": "1",
            "FECHA_LEY": "01/04/2025",
            "FECHA_BOLETIN": "02/04/2025",
        },
        dataset_kind="approved",
    )

    assert law is not None
    assert law.title.startswith("Ley 1/2025")
    assert law.metadata["numero_oficial"] == "1"


def test_congress_final_phase_date_is_used_for_definitive_approval() -> None:
    law = Ley.from_congress_record(
        {
            "TIPO": "Proyecto de ley",
            "OBJETO": "Proyecto de Ley de prueba.",
            "NUMEXPEDIENTE": "121/000001/0000",
            "FECHAPRESENTACION": "01/01/2025",
            "TRAMITACIONSEGUIDA": (
                "Senado\n"
                "desde 01/02/2025 hasta 10/03/2025\n"
                "Concluido - (Aprobado con modificaciones)\n"
                "desde 10/03/2025 hasta 20/03/2025"
            ),
            "RESULTADOTRAMITACION": "Aprobado con modificaciones\n20/03/2025",
        },
        dataset_kind="projects",
    )

    assert law is not None
    assert law.events_on("2025-03-10", EVENT_FINAL_APPROVAL)
    assert not law.events_on("2025-03-20", EVENT_FINAL_APPROVAL)


def test_official_sources_collect_and_deduplicate_structured_events() -> None:
    client = FakeHttp()
    report = OfficialLawSources(client, enrich_boe_xml=True).collect(TARGET)

    assert isinstance(report, DailyLawReport)
    assert len(report.initiated) == 3
    assert len(report.approved) == 1
    assert len(report.sanctioned_promulgated) == 1
    assert len(report.published) == 1
    assert len(report.entered_into_force) == 1
    assert len(report.published[0].events_on(TARGET, EVENT_PUBLICATION)) == 1
    assert all("Orgánica" not in law.title for law in report.laws)
    assert all(
        any(event.event_date == TARGET for event in law.events) for law in report.laws
    )
    assert report.source_status["boe_eli"] == "ok"
    assert report.sanctioned_promulgated[0].metadata["url_eli"].endswith("/2026/09/15/1")

    approved = report.approved[0]
    assert approved.events_on(TARGET, EVENT_FINAL_APPROVAL)
    assert approved.events_on(TARGET, EVENT_STARTED)
    assert approved.events_on(TARGET, EVENT_CONGRESS_APPROVAL)
    assert any(urlsplit(url).netloc == "www.congreso.es" for url in client.calls)

    serialized = report.to_dict()
    assert serialized["counts"]["aprobadas_definitivamente"] == 0
    assert serialized["candidate_counts"]["aprobadas_definitivamente"] == 1
    assert serialized["counts"]["sancionadas_promulgadas"] == 1
    assert serialized["candidate_counts"]["sancionadas_promulgadas"] == 0
    assert any("no cubre todas las leyes recientes" in warning for warning in report.warnings)


def test_unavailable_source_never_looks_like_a_confirmed_zero() -> None:
    report = OfficialLawSources(CongressUnavailableHttp()).collect(TARGET)

    assert report.source_status["congreso"] == "unavailable"
    serialized = report.to_dict()
    assert serialized["counts"]["iniciadas"] is None
    assert serialized["counts"]["aprobadas_definitivamente"] is None
    assert serialized["coverage"]["iniciadas"]["status"] == "incomplete"


def test_missing_boe_summary_is_an_empty_official_day() -> None:
    report = OfficialLawSources(BoeSummaryMissingHttp()).collect(TARGET)

    assert report.source_status["boe_sumario"] == "empty"
    serialized = report.to_dict()
    assert serialized["counts"]["publicadas_boe"] == 0
    assert serialized["coverage"]["publicadas_boe"]["status"] == "complete"
    assert any("no existe sumario oficial" in warning for warning in report.warnings)

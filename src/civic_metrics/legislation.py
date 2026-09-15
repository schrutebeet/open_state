"""API-first collection of the Spanish state ordinary-law lifecycle.

The module deliberately consumes only structured official material: JSON, XML,
CSV, HTML and RSS.  It does not download or parse PDF files.  Parliamentary
documents that are only available as PDF are retained as links and reported as
verification gaps instead of being guessed from.

The public entry point is :class:`Ley`.  A law is represented as a set of
dated, source-backed events and ``Ley.resumen_dia`` combines the official
Congress, Senate and BOE feeds for a target date.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from civic_metrics.http import HttpClient

LOGGER = logging.getLogger(__name__)

CONGRESS_OPEN_DATA_URL = "https://www.congreso.es/es/opendata/iniciativas"
SENATE_OPEN_DATA_URL = (
    "https://www.senado.es/web/relacionesciudadanos/datosabiertos/"
    "catalogodatos/iniciativas/index.html"
)
SENATE_BULK_DATA_URL = "https://www.senado.es/web/ficopendataservlet"
BOE_LEGISLATION_API_URL = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
BOE_SUMMARY_API_URL = "https://www.boe.es/datosabiertos/api/boe/sumario"
BOE_ELI_DAILY_URL = "https://www.boe.es/eli/es/l"
MONCLOA_RSS_PAGE = "https://www.lamoncloa.gob.es/paginas/varios/rss.aspx"

EVENT_STARTED = "iniciada"
EVENT_QUALIFIED = "calificada"
EVENT_CONSIDERATION = "toma_en_consideracion"
EVENT_CONGRESS_APPROVAL = "aprobacion_inicial_congreso"
EVENT_SENATE_APPROVAL = "aprobacion_senado"
EVENT_FINAL_APPROVAL = "aprobacion_definitiva"
EVENT_SANCTION_PROMULGATION = "sancion_promulgacion"
EVENT_PUBLICATION = "publicacion_boe"
EVENT_ENTRY_INTO_FORCE = "entrada_en_vigor"
EVENT_GOVERNMENT_APPROVAL = "aprobacion_gobierno"

_DATE_PATTERNS = (
    re.compile(r"\b(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})\b"),
    re.compile(r"\b(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})\b"),
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"),
)
_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_TITLE_DATE_PATTERN = re.compile(
    r"\bde\s+(?P<day>\d{1,2})\s+de\s+(?P<month>[a-záéíóú]+)\s+de\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

_METRIC_SOURCES = {
    "iniciadas": ("congreso", "senado"),
    "aprobadas_definitivamente": ("congreso",),
    "sancionadas_promulgadas": ("boe_eli",),
    "publicadas_boe": ("boe_sumario",),
    "entradas_en_vigor": ("boe_legislacion",),
    "aprobadas_por_gobierno": ("moncloa",),
}
_METRIC_EVENTS = {
    "iniciadas": EVENT_STARTED,
    "aprobadas_definitivamente": EVENT_FINAL_APPROVAL,
    "sancionadas_promulgadas": EVENT_SANCTION_PROMULGATION,
    "publicadas_boe": EVENT_PUBLICATION,
    "entradas_en_vigor": EVENT_ENTRY_INTO_FORCE,
    "aprobadas_por_gobierno": EVENT_GOVERNMENT_APPROVAL,
}
_AVAILABLE_SOURCE_STATUSES = frozenset({"ok", "empty"})


class ResponseLike(Protocol):
    body: bytes
    source_url: str
    content_type: str


@dataclass(frozen=True)
class LegislativeEvent:
    """One dated event with enough provenance to audit its extraction."""

    kind: str
    event_date: date
    source: str
    source_url: str
    confidence: str = "high"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "date": self.event_date.isoformat(),
            "source": self.source,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "details": self.details,
        }


@dataclass
class Ley:
    """A Spanish state ordinary law and its machine-readable lifecycle.

    ``initiative_id`` is normally a Congress or Senate parliamentary
    expediente.  ``boe_id`` is the BOE document identifier when the law has
    been published.  A law may be created initially from one source and later
    enriched from another source by :meth:`merge`.
    """

    title: str
    initiative_id: str | None = None
    boe_id: str | None = None
    legislature: str | None = None
    origin: str | None = None
    events: list[LegislativeEvent] = field(default_factory=list)
    source_ids: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def resumen_dia(
        cls,
        target_date: date | str,
        *,
        client: HttpClient | None = None,
        legislature: str = "15",
        include_government: bool = False,
    ) -> DailyLawReport:
        """Collect all structured official events known for ``target_date``.

        The default scope is state ordinary laws.  ``include_government`` adds
        an optional heuristic list of Council of Ministers project approvals;
        it is deliberately not mixed into the parliamentary ``iniciadas`` or
        definitive ``aprobadas`` counters.
        """
        parsed_date = parse_date(target_date)
        owns_client = client is None
        http = client or HttpClient(45)
        try:
            sources = OfficialLawSources(
                http,
                legislature=legislature,
                enrich_boe_xml=True,
            )
            return sources.collect(parsed_date, include_government=include_government)
        finally:
            if owns_client:
                http.close()

    @classmethod
    def from_congress_record(
        cls,
        record: Mapping[str, Any],
        *,
        dataset_kind: str,
        legislature: str | None = None,
        source_url: str = CONGRESS_OPEN_DATA_URL,
    ) -> Ley | None:
        """Convert a Congress open-data row into a law candidate."""
        title = _first_value(record, "OBJETO", "TITULO", "TÍTULO", "titulo")
        initiative_type = _first_value(record, "TIPO", "tipo")
        approved_law_row = bool(_first_value(record, "TITULO_LEY", "titulo_ley"))
        title = title or _first_value(record, "TITULO_LEY", "titulo_ley")
        if not title or (
            not is_ordinary_law(title, initiative_type)
            and not (approved_law_row and _is_official_ordinary_law_title(title))
        ):
            return None

        initiative_id = _clean_id(_first_value(record, "NUMEXPEDIENTE", "EXPEDIENTE"))
        origin = _first_value(record, "AUTOR", "ORIGEN", "CAMARAORIGEN")
        document_links = _extract_document_links(record)
        metadata: dict[str, Any] = {
            "dataset_kind": dataset_kind,
            "initiative_type": initiative_type,
            "status": _first_value(record, "SITUACIONACTUAL", "RESULTADOTRAMITACION"),
        }
        if approved_law_row:
            metadata.update(
                {
                    "numero_oficial": _first_value(record, "NUMERO_LEY", "numero_ley"),
                    "fecha_ley": _first_value(record, "FECHA_LEY", "fecha_ley"),
                    "numero_boletin_congreso": _first_value(
                        record, "NUMERO_BOLETIN", "numero_boletin"
                    ),
                    "fecha_boletin_congreso": _first_value(
                        record, "FECHA_BOLETIN", "fecha_boletin"
                    ),
                }
            )
        if document_links:
            metadata["document_links"] = document_links
        law = cls(
            title=title,
            initiative_id=initiative_id,
            legislature=legislature or _first_value(record, "LEGISLATURA"),
            origin=origin,
            metadata=metadata,
        )
        if initiative_id:
            law.source_ids.add(initiative_id)

        presentation = parse_date_from_value(
            _first_value(record, "FECHAPRESENTACION", "FECHA_PRESENTACION")
        )
        if presentation:
            law.add_event(
                LegislativeEvent(
                    EVENT_STARTED,
                    presentation,
                    "congreso_open_data",
                    source_url,
                    details={"field": "FECHAPRESENTACION", "dataset_kind": dataset_kind},
                )
            )
        qualification = parse_date_from_value(
            _first_value(record, "FECHACALIFICACION", "FECHA_CALIFICACION")
        )
        if qualification:
            law.add_event(
                LegislativeEvent(
                    EVENT_QUALIFIED,
                    qualification,
                    "congreso_open_data",
                    source_url,
                    details={"field": "FECHACALIFICACION", "dataset_kind": dataset_kind},
                )
            )

        process = _first_value(record, "TRAMITACIONSEGUIDA", "TRAMITACIÓNSEGUIDA")
        if process:
            _add_process_events(law, process, source_url, dataset_kind)

        result = _first_value(record, "RESULTADOTRAMITACION", "RESULTADO_TRAMITACION")
        result_date = parse_date_from_value(result)
        if result_date and _contains_approval(result) and not any(
            event.kind == EVENT_FINAL_APPROVAL for event in law.events
        ):
            law.add_event(
                LegislativeEvent(
                    EVENT_FINAL_APPROVAL,
                    result_date,
                    "congreso_open_data",
                    source_url,
                    confidence="medium",
                    details={
                        "field": "RESULTADOTRAMITACION",
                        "raw_result": result,
                        "dataset_kind": dataset_kind,
                        "requires_document_verification": True,
                    },
                )
            )
        return law

    def add_event(self, event: LegislativeEvent) -> None:
        for index, existing in enumerate(self.events):
            if existing.kind != event.kind or existing.event_date != event.event_date:
                continue
            if (
                event.source == "boe_document_xml"
                and existing.source != "boe_document_xml"
                and _confidence_rank(event.confidence) >= _confidence_rank(existing.confidence)
            ) or _confidence_rank(event.confidence) > _confidence_rank(existing.confidence):
                self.events[index] = event
            return
        self.events.append(event)
        if event.kind == EVENT_PUBLICATION and self.boe_id is None:
            match = re.search(r"BOE-[A-Z]-\d{4}-\d+", event.details.get("identifier", ""))
            if match:
                self.boe_id = match.group(0)

    def merge(self, other: Ley) -> None:
        """Merge a duplicate representation from another official source."""
        if not self.title and other.title:
            self.title = other.title
        self.initiative_id = self.initiative_id or other.initiative_id
        self.boe_id = self.boe_id or other.boe_id
        self.legislature = self.legislature or other.legislature
        self.origin = self.origin or other.origin
        self.source_ids.update(other.source_ids)
        self.metadata.update({key: value for key, value in other.metadata.items() if value})
        for event in other.events:
            self.add_event(event)

    def events_on(self, target_date: date | str, kind: str | None = None) -> list[LegislativeEvent]:
        parsed_date = parse_date(target_date)
        return [
            event
            for event in self.events
            if event.event_date == parsed_date and (kind is None or event.kind == kind)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "initiative_id": self.initiative_id,
            "boe_id": self.boe_id,
            "legislature": self.legislature,
            "origin": self.origin,
            "source_ids": sorted(self.source_ids),
            "metadata": self.metadata,
            "events": [
                event.to_dict()
                for event in sorted(self.events, key=lambda item: item.event_date)
            ],
        }


@dataclass
class DailyLawReport:
    target_date: date
    laws: list[Ley]
    warnings: list[str] = field(default_factory=list)
    government_approvals: list[Ley] = field(default_factory=list)
    source_status: dict[str, str] = field(default_factory=dict)
    # The congressional feeds contain historic events as well as events for
    # ``target_date``.  Keep them separately so the local history can retain
    # them without making the daily JSON response enormous.
    discovered_laws: list[Ley] = field(default_factory=list)

    def by_event(self, kind: str) -> list[Ley]:
        return [law for law in self.laws if law.events_on(self.target_date, kind)]

    @property
    def initiated(self) -> list[Ley]:
        return self.by_event(EVENT_STARTED)

    @property
    def approved(self) -> list[Ley]:
        return self.by_event(EVENT_FINAL_APPROVAL)

    @property
    def sanctioned_promulgated(self) -> list[Ley]:
        return self.by_event(EVENT_SANCTION_PROMULGATION)

    @property
    def published(self) -> list[Ley]:
        return self.by_event(EVENT_PUBLICATION)

    @property
    def entered_into_force(self) -> list[Ley]:
        return self.by_event(EVENT_ENTRY_INTO_FORCE)

    def by_event_confidence(self, kind: str, confidence: str) -> list[Ley]:
        """Return laws with an event of ``kind`` and the requested confidence."""
        return [
            law
            for law in self.laws
            if any(
                event.kind == kind
                and event.event_date == self.target_date
                and event.confidence == confidence
                for event in law.events
            )
        ]

    def verified_by_event(self, kind: str) -> list[Ley]:
        return self.by_event_confidence(kind, "high")

    def candidate_by_event(self, kind: str) -> list[Ley]:
        verified = {id(law) for law in self.verified_by_event(kind)}
        return [
            law
            for law in self.by_event_confidence(kind, "medium")
            if id(law) not in verified
        ]

    def coverage(self, metric: str) -> dict[str, Any]:
        """Describe whether a metric has all of its required sources available."""
        if metric not in _METRIC_SOURCES:
            raise KeyError(f"Métrica desconocida: {metric}")
        required = _METRIC_SOURCES[metric]
        statuses = {
            source: self.source_status.get(source, "unavailable") for source in required
        }
        if (
            metric == "aprobadas_por_gobierno"
            and self.source_status.get("moncloa") == "not_requested"
        ):
            status = "not_requested"
        else:
            status = (
                "complete"
                if all(value in _AVAILABLE_SOURCE_STATUSES for value in statuses.values())
                else "incomplete"
            )
        return {"status": status, "sources": statuses}

    def _count_for(self, metric: str, confidence: str) -> int | None:
        if self.coverage(metric)["status"] != "complete":
            return None
        kind = _METRIC_EVENTS[metric]
        if metric == "aprobadas_por_gobierno":
            laws = self.government_approvals
            return sum(
                1
                for law in laws
                if any(
                    event.kind == kind
                    and event.event_date == self.target_date
                    and event.confidence == confidence
                    for event in law.events
                )
            )
        laws = (
            self.verified_by_event(kind)
            if confidence == "high"
            else self.candidate_by_event(kind)
        )
        return len(laws)

    def verified_counts(self) -> dict[str, int | None]:
        """Counts safe to expose as confirmed; ``None`` means incomplete coverage."""
        return {
            metric: self._count_for(metric, "high") for metric in _METRIC_SOURCES
        }

    def candidate_counts(self) -> dict[str, int | None]:
        """Counts extracted with medium confidence, never mixed with confirmed counts."""
        return {
            metric: self._count_for(metric, "medium") for metric in _METRIC_SOURCES
        }

    def observed_counts(self) -> dict[str, int]:
        """Raw extracted counts, which may be lower bounds when coverage is incomplete."""
        return {
            "iniciadas": len(self.initiated),
            "aprobadas_definitivamente": len(self.approved),
            "sancionadas_promulgadas": len(self.sanctioned_promulgated),
            "publicadas_boe": len(self.published),
            "entradas_en_vigor": len(self.entered_into_force),
            "aprobadas_por_gobierno": len(self.government_approvals),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.target_date.isoformat(),
            "counts": self.verified_counts(),
            "candidate_counts": self.candidate_counts(),
            "observed_counts": self.observed_counts(),
            "coverage": {
                metric: self.coverage(metric) for metric in _METRIC_SOURCES
            },
            "source_status": self.source_status,
            "laws": [law.to_dict() for law in self.laws],
            "government_approvals": [law.to_dict() for law in self.government_approvals],
            "warnings": self.warnings,
        }


class OfficialLawSources:
    """Fetch and reconcile the official structured sources."""

    def __init__(
        self,
        client: HttpClient,
        *,
        legislature: str = "15",
        congress_landing_url: str = CONGRESS_OPEN_DATA_URL,
        senate_landing_url: str = SENATE_OPEN_DATA_URL,
        enrich_boe_xml: bool = False,
    ) -> None:
        self.client = client
        self.legislature = legislature
        self.congress_landing_url = congress_landing_url
        self.senate_landing_url = senate_landing_url
        self.enrich_boe_xml = enrich_boe_xml

    def collect(self, target_date: date, *, include_government: bool = False) -> DailyLawReport:
        laws: dict[str, Ley] = {}
        warnings: list[str] = []
        source_status: dict[str, str] = {}

        try:
            congress_laws = self._collect_congress()
            for law in congress_laws:
                _merge_law(laws, law)
            source_status["congreso"] = _source_status_for_event(
                congress_laws, EVENT_STARTED
            )
            if source_status["congreso"] == "partial":
                warnings.append(
                    "congreso: hay expedientes ordinarios sin fecha de inicio reconocible"
                )
        except Exception as exc:  # noqa: BLE001 - one unavailable official source should not hide others
            LOGGER.exception("Congress source failed")
            source_status["congreso"] = "unavailable"
            warnings.append(f"congreso: {type(exc).__name__}: {exc}")

        try:
            senate_laws = self._collect_senate()
            for law in senate_laws:
                _merge_law(laws, law)
            source_status["senado"] = _source_status_for_event(senate_laws, EVENT_STARTED)
            if source_status["senado"] == "partial":
                warnings.append(
                    "senado: hay expedientes ordinarios sin fecha de inicio reconocible"
                )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Senate source failed")
            source_status["senado"] = "unavailable"
            warnings.append(f"senado: {type(exc).__name__}: {exc}")

        try:
            for law in self._collect_boe_by_disposition(target_date):
                _merge_law(laws, law)
            # The consolidated-legislation API does not contain every newly
            # published ordinary law, so a successful response is useful
            # evidence but never complete coverage for a daily zero.
            source_status["boe_legislacion"] = "partial"
            warnings.append(
                "boe_legislacion: la API de legislación consolidada no cubre "
                "todas las leyes recientes; no confirma ceros de sanción ni vigencia"
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("BOE legislation source failed")
            source_status["boe_legislacion"] = "unavailable"
            warnings.append(f"boe_legislacion: {type(exc).__name__}: {exc}")

        try:
            eli_laws = self._collect_boe_eli_by_disposition(target_date)
            for law in eli_laws:
                _merge_law(laws, law)
            source_status["boe_eli"] = "ok" if eli_laws else "empty"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                source_status["boe_eli"] = "empty"
            else:
                LOGGER.exception("BOE ELI source failed")
                source_status["boe_eli"] = "unavailable"
                warnings.append(f"boe_eli: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("BOE ELI source failed")
            source_status["boe_eli"] = "unavailable"
            warnings.append(f"boe_eli: {type(exc).__name__}: {exc}")

        try:
            for law in self._collect_boe_publications(target_date):
                _merge_law(laws, law)
            source_status["boe_sumario"] = "ok"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                source_status["boe_sumario"] = "empty"
                warnings.append(
                    f"boe_sumario: no existe sumario oficial para {target_date.isoformat()}; "
                    "se interpreta como día sin publicación en BOE"
                )
            else:
                LOGGER.exception("BOE summary source failed")
                source_status["boe_sumario"] = "unavailable"
                warnings.append(f"boe_sumario: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("BOE summary source failed")
            source_status["boe_sumario"] = "unavailable"
            warnings.append(f"boe_sumario: {type(exc).__name__}: {exc}")

        government: list[Ley] = []
        if include_government:
            try:
                government = self._collect_government_approvals(target_date)
                source_status["moncloa"] = "ok"
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Moncloa source failed")
                source_status["moncloa"] = "unavailable"
                warnings.append(f"moncloa: {type(exc).__name__}: {exc}")
        else:
            source_status["moncloa"] = "not_requested"

        unique_laws: list[Ley] = []
        seen_objects: set[int] = set()
        for law in laws.values():
            if id(law) not in seen_objects:
                unique_laws.append(law)
                seen_objects.add(id(law))
        daily_laws = [
            law
            for law in unique_laws
            if any(event.event_date == target_date for event in law.events)
        ]
        report = DailyLawReport(
            target_date,
            daily_laws,
            warnings,
            government,
            source_status,
            unique_laws,
        )
        self._attach_warnings(report)
        return report

    def _collect_boe_eli_by_disposition(self, target_date: date) -> list[Ley]:
        """Read BOE's daily ELI index for laws dated on ``target_date``.

        This is HTML published by the BOE, rather than a PDF.  An ELI URL is a
        stable official identifier, and the daily index is more complete for
        newly promulgated laws than the consolidated-legislation API.
        """
        url = "/".join(
            (
                BOE_ELI_DAILY_URL,
                f"{target_date.year:04d}",
                f"{target_date.month:02d}",
                f"{target_date.day:02d}",
            )
        )
        response = self.client.get(url, headers={"Accept": "text/html"})
        soup = BeautifulSoup(response.body, "html.parser")
        laws: list[Ley] = []
        seen_urls: set[str] = set()
        pattern = re.compile(
            rf"/eli/es/l/{target_date.year:04d}/{target_date.month:02d}/"
            rf"{target_date.day:02d}/\d+/?$",
            re.I,
        )
        for anchor in soup.find_all("a", href=True):
            eli_url = urljoin(response.source_url, str(anchor["href"]))
            if eli_url in seen_urls or not pattern.search(eli_url.split("?", 1)[0]):
                continue
            container = anchor.find_parent("li") or anchor.parent or anchor
            title = _clean_text(container.get_text(" ", strip=True))
            title = _clean_text(title.split("Permalink ELI:", 1)[0])
            if not _is_official_ordinary_law_title(title):
                continue
            seen_urls.add(eli_url)
            law = Ley(title=title, metadata={"url_eli": eli_url})
            law.add_event(
                LegislativeEvent(
                    EVENT_SANCTION_PROMULGATION,
                    target_date,
                    "boe_eli_daily",
                    eli_url,
                    details={
                        "basis": "indice diario ELI oficial del BOE",
                        "eli_url": eli_url,
                        "date_role": "fecha_de_disposicion",
                    },
                )
            )
            laws.append(law)
        return laws

    def _collect_congress(self) -> list[Ley]:
        response = self.client.get(self.congress_landing_url)
        soup = BeautifulSoup(response.body, "html.parser")
        links: dict[str, str] = {}
        patterns = {
            "approved": re.compile(
                r"IniciativasLegislativasAprobadas__[^/]+\.(?:json|xml|csv)$", re.I
            ),
            "projects": re.compile(r"ProyectosDeLey__[^/]+\.(?:json|xml|csv)$", re.I),
            "proposals": re.compile(r"ProposicionesDeLey__[^/]+\.(?:json|xml|csv)$", re.I),
        }
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.source_url, str(anchor["href"]))
            for kind, pattern in patterns.items():
                if pattern.search(href):
                    # The landing page publishes CSV, JSON and XML variants;
                    # JSON is the least lossy and must win over the CSV link.
                    current = links.get(kind)
                    if current is None or (
                        _data_link_preference(href) < _data_link_preference(current)
                    ):
                        links[kind] = href

        if not links:
            raise LookupError("No se encontraron enlaces JSON/XML/CSV de iniciativas del Congreso")

        laws: list[Ley] = []
        parsed_record_count = 0
        for kind, url in links.items():
            response = self.client.get(url, headers={"Accept": _accept_for_url(url)})
            records = parse_tabular_payload(response.body, url)
            parsed_record_count += len(records)
            for record in records:
                law = Ley.from_congress_record(
                    record,
                    dataset_kind=kind,
                    legislature=self.legislature,
                    source_url=url,
                )
                if law:
                    laws.append(law)
        if parsed_record_count == 0:
            raise LookupError("Los ficheros del Congreso no contienen registros parseables")
        return laws

    def _collect_senate(self) -> list[Ley]:
        response = self.client.get(f"{self.senate_landing_url}?legis={self.legislature}")
        soup = BeautifulSoup(response.body, "html.parser")
        bulk_url = None
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.source_url, str(anchor["href"]))
            label = _normalise_text(anchor.get_text(" ", strip=True))
            if "ficopendataservlet" in href.lower() and (
                "descargar" in label or "datos abiertos" in label
            ):
                bulk_url = href
                break
        if bulk_url is None:
            bulk_url = f"{SENATE_BULK_DATA_URL}?legis={self.legislature}&tipoFich=9"

        response = self.client.get(bulk_url, headers={"Accept": "application/xml"})
        records = parse_senate_xml(response.body)
        if not records:
            raise LookupError("El fichero del Senado no contiene registros XML parseables")
        laws: list[Ley] = []
        for record in records:
            title = _first_value(record, "title", "titulo", "objeto", "descripcion")
            initiative_type = _first_value(record, "type", "tipo")
            if not title or not is_ordinary_law(title, initiative_type):
                continue
            initiative_id = _clean_id(
                _first_value(record, "initiative_id", "expediente", "numero_expediente", "id")
            )
            law = Ley(
                title=title,
                initiative_id=initiative_id,
                legislature=self.legislature,
                origin="Senado",
                metadata={"dataset_kind": "senate_initiatives", "initiative_type": initiative_type},
            )
            if initiative_id:
                law.source_ids.add(initiative_id)
            presentation = parse_date_from_value(
                _first_value(record, "presentation_date", "fecha_presentacion", "fecha_entrada")
            )
            if presentation:
                law.add_event(
                    LegislativeEvent(
                        EVENT_STARTED,
                        presentation,
                        "senado_open_data",
                        bulk_url,
                        details={"record": record},
                    )
                )
            qualification = parse_date_from_value(
                _first_value(record, "qualification_date", "fecha_calificacion")
            )
            if qualification:
                law.add_event(
                    LegislativeEvent(
                        EVENT_QUALIFIED,
                        qualification,
                        "senado_open_data",
                        bulk_url,
                        details={"record": record},
                    )
                )
            laws.append(law)
        return laws

    def _collect_boe_by_disposition(self, target_date: date) -> list[Ley]:
        query = {
            "query": {
                "query_string": {
                    "query": "ambito@codigo:1 and rango@codigo:1300",
                },
                "range": {
                    "fecha_disposicion": {
                        "gte": target_date.strftime("%Y%m%d"),
                        "lte": target_date.strftime("%Y%m%d"),
                    }
                },
            },
            "sort": [{"fecha_disposicion": "asc"}],
        }
        response = self.client.get(
            BOE_LEGISLATION_API_URL,
            params={"query": json.dumps(query, ensure_ascii=False), "limit": -1},
            headers={"Accept": "application/json"},
        )
        payload = json.loads(response.body.decode("utf-8-sig"))
        rows = _extract_data_rows(payload)
        laws: list[Ley] = []
        for row in rows:
            if not isinstance(row, Mapping) or not _is_boe_ordinary_law(row):
                continue
            law = _law_from_boe_row(row, response.source_url)
            if law:
                laws.append(law)
        return laws

    def _collect_boe_publications(self, target_date: date) -> list[Ley]:
        url = f"{BOE_SUMMARY_API_URL}/{target_date.strftime('%Y%m%d')}"
        response = self.client.get(url, headers={"Accept": "application/json"})
        payload = json.loads(response.body.decode("utf-8-sig"))
        items = extract_boe_summary_items(payload)
        laws: list[Ley] = []
        for item in items:
            if not _is_boe_summary_ordinary_state_law(item):
                continue
            law = Ley(
                title=str(item.get("titulo", "")).strip(),
                boe_id=str(item.get("identificador")),
                metadata={"publication_number": item.get("numero_diario")},
            )
            law.add_event(
                LegislativeEvent(
                    EVENT_PUBLICATION,
                    target_date,
                    "boe_summary_api",
                    str(item.get("url_html") or item.get("url_xml") or url),
                    details={
                        "identifier": item.get("identificador"),
                        "url_xml": item.get("url_xml"),
                        "url_pdf": item.get("url_pdf"),
                    },
                )
            )
            disposition_date = parse_date_from_value(item.get("disposition_date"))
            if disposition_date is None:
                disposition_date = parse_spanish_title_date(law.title)
            if disposition_date:
                law.add_event(
                    LegislativeEvent(
                        EVENT_SANCTION_PROMULGATION,
                        disposition_date,
                        "boe_summary_api",
                        str(item.get("url_xml") or item.get("url_html") or url),
                        confidence="medium",
                        details={
                            "basis": "fecha_disposicion or date in title",
                            "requires_original_xml_verification": True,
                        },
                    )
                )
            if self.enrich_boe_xml and item.get("url_xml"):
                try:
                    xml_law = self._collect_boe_document_xml(str(item["url_xml"]))
                except Exception as exc:  # noqa: BLE001 - keep the daily summary usable
                    LOGGER.warning(
                        "BOE XML enrichment failed for %s: %s",
                        item.get("identificador"),
                        exc,
                    )
                else:
                    if xml_law:
                        law.merge(xml_law)
            laws.append(law)
        return laws

    def _collect_boe_document_xml(self, url: str) -> Ley | None:
        """Read BOE's structured document XML, never its PDF representation."""
        response = self.client.get(url, headers={"Accept": "application/xml"})
        root = ET.fromstring(response.body)
        metadata_node = next(
            (node for node in root.iter() if _local_name(node.tag).lower() == "metadatos"),
            None,
        )
        if metadata_node is None:
            return None
        fields = {
            _local_name(node.tag).lower(): _clean_text(node.text)
            for node in metadata_node
            if node.text and _clean_text(node.text)
        }
        identifier = fields.get("identificador")
        title = fields.get("titulo")
        if not identifier or not title:
            return None
        law = Ley(
            title=title,
            boe_id=identifier,
            metadata={
                "rango": fields.get("rango", ""),
                "ambito": fields.get("origen_legislativo", ""),
                "numero_oficial": fields.get("numero_oficial"),
                "fecha_publicacion": fields.get("fecha_publicacion"),
                "url_eli": fields.get("url_eli"),
                "xml_metadata": True,
            },
        )
        disposition_date = parse_date_from_value(fields.get("fecha_disposicion"))
        if disposition_date:
            law.add_event(
                LegislativeEvent(
                    EVENT_SANCTION_PROMULGATION,
                    disposition_date,
                    "boe_document_xml",
                    response.source_url,
                    confidence="medium",
                    details={
                        "field": "fecha_disposicion",
                        "identifier": identifier,
                        "basis": "metadatos del XML oficial del documento",
                    },
                )
            )
        publication_date = parse_date_from_value(fields.get("fecha_publicacion"))
        if publication_date:
            law.add_event(
                LegislativeEvent(
                    EVENT_PUBLICATION,
                    publication_date,
                    "boe_document_xml",
                    response.source_url,
                    details={
                        "field": "fecha_publicacion",
                        "identifier": identifier,
                    },
                )
            )
        effective_date = parse_date_from_value(fields.get("fecha_vigencia"))
        if effective_date:
            law.add_event(
                LegislativeEvent(
                    EVENT_ENTRY_INTO_FORCE,
                    effective_date,
                    "boe_document_xml",
                    response.source_url,
                    details={
                        "field": "fecha_vigencia",
                        "identifier": identifier,
                    },
                )
            )
        return law

    def _collect_government_approvals(self, target_date: date) -> list[Ley]:
        """Extract a conservative list from the official Moncloa RSS/HTML.

        This is supplementary only: the RSS item date is the publication date
        of the government reference, not a substitute for the parliamentary
        presentation date.
        """
        response = self.client.get(MONCLOA_RSS_PAGE)
        soup = BeautifulSoup(response.body, "html.parser")
        feeds = [
            urljoin(response.source_url, str(anchor["href"]))
            for anchor in soup.find_all("a", href=True)
            if "rss" in str(anchor["href"]).lower() or "feed" in str(anchor["href"]).lower()
        ]
        if not feeds:
            raise LookupError("No se encontraron feeds RSS de La Moncloa")

        laws: list[Ley] = []
        for feed_url in dict.fromkeys(feeds):
            feed_response = self.client.get(
                feed_url,
                headers={"Accept": "application/rss+xml, application/xml"},
            )
            for item in parse_rss_items(feed_response.body):
                item_date = parse_rss_date(item.get("pubDate") or item.get("date"))
                if item_date != target_date:
                    continue
                article_url = item.get("link")
                article_text = ""
                if article_url:
                    article_response = self.client.get(
                        article_url,
                        headers={"Accept": "text/html"},
                    )
                    article_text = BeautifulSoup(
                        article_response.body, "html.parser"
                    ).get_text(" ", strip=True)
                haystack = f"{item.get('title', '')} {item.get('description', '')} {article_text}"
                if not re.search(
                    r"aprob(?:ado|ada|ó|óse).*?(?:proyecto|anteproyecto)\s+de\s+ley",
                    haystack,
                    re.I,
                ):
                    continue
                for title in extract_government_law_titles(haystack):
                    law = Ley(title=title, origin="Gobierno")
                    law.add_event(
                        LegislativeEvent(
                            EVENT_GOVERNMENT_APPROVAL,
                            target_date,
                            "moncloa_rss_html",
                            article_url or feed_url,
                            confidence="medium",
                            details={"feed_url": feed_url},
                        )
                    )
                    laws.append(law)
        return laws

    @staticmethod
    def _attach_warnings(report: DailyLawReport) -> None:
        if any(
            event.confidence == "medium"
            and event.kind == EVENT_FINAL_APPROVAL
            for law in report.laws
            for event in law.events
        ):
            report.warnings.append(
                "Las aprobaciones definitivas procedentes del estado estructurado del Congreso "
                "son candidatas y requieren el BOCG/Diario de Sesiones para auditoría plena."
            )
        if any(
            event.kind == EVENT_SANCTION_PROMULGATION and event.confidence == "medium"
            for law in report.laws
            for event in law.events
        ):
            report.warnings.append(
                "La sanción/promulgación se ha fechado con fecha_disposicion o la fecha del "
                "título; "
                "el XML original del BOE es la evidencia definitiva."
            )


def is_ordinary_law(title: str, initiative_type: str | None = None) -> bool:
    """Return whether a parliamentary record represents a state ordinary law."""
    haystack = _normalise_text(f"{initiative_type or ''} {title}")
    if "proyecto de ley" not in haystack and "proposicion de ley" not in haystack:
        return False
    return "ley organica" not in haystack


def _is_official_ordinary_law_title(title: str) -> bool:
    """Recognise the Congress approved-laws feed, whose type is ``Leyes``."""
    haystack = _normalise_text(title)
    return bool(re.match(r"^ley\s+\d+/\d+\b", haystack)) and "ley organica" not in haystack


def parse_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_date_from_value(value)
    if parsed is None:
        raise ValueError(f"Fecha no reconocida: {value!r}")
    return parsed


def parse_date_from_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean_text(value)
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
    return None


def parse_spanish_title_date(title: str) -> date | None:
    match = _TITLE_DATE_PATTERN.search(_normalise_text(title))
    if not match:
        return None
    month = _SPANISH_MONTHS.get(match.group("month"))
    if month is None:
        return None
    try:
        return date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def parse_tabular_payload(body: bytes, source_url: str = "") -> list[dict[str, Any]]:
    """Parse JSON, XML or CSV open-data payloads without source-specific I/O."""
    text = body.decode("utf-8-sig")
    lower_url = source_url.lower()
    if lower_url.endswith(".csv") or (
        text.lstrip() and not text.lstrip().startswith(("{", "[", "<"))
    ):
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    if lower_url.endswith(".xml") or text.lstrip().startswith("<"):
        return parse_generic_xml_records(body)
    payload = json.loads(text)
    return [dict(row) for row in _extract_records(payload)]


def parse_senate_xml(body: bytes) -> list[dict[str, str]]:
    """Parse Senate bulk XML while tolerating namespace and naming variants."""
    root = ET.fromstring(body)
    containers = [
        element
        for element in root.iter()
        if _local_name(element.tag).lower() in {"iniciativa", "item", "expediente", "registro"}
    ]
    if not containers:
        containers = [root]
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for container in containers:
        record = _xml_record(container)
        title = _first_value(record, "title", "titulo", "objeto", "descripcion")
        if not title:
            continue
        fingerprint = _normalise_text(title) + "|" + _normalise_text(
            _first_value(record, "initiative_id", "expediente", "numero_expediente", "id")
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        records.append(record)
    return records


def parse_generic_xml_records(body: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(body)
    containers = [
        element
        for element in root.iter()
        if _local_name(element.tag).lower() in {"item", "iniciativa", "record", "registro"}
    ]
    if not containers:
        containers = [root]
    return [_xml_record(element) for element in containers if _xml_record(element)]


def extract_boe_summary_items(payload: Any) -> list[dict[str, Any]]:
    """Flatten the nested BOE sumario while retaining section/department context."""
    root = payload.get("data", payload) if isinstance(payload, Mapping) else payload
    sumario = root.get("sumario", root) if isinstance(root, Mapping) else root
    results: list[dict[str, Any]] = []

    def visit(node: Any, section: str | None = None, department: str | None = None) -> None:
        if isinstance(node, Mapping):
            identifier = node.get("identificador")
            title = node.get("titulo")
            if identifier and title:
                item = dict(node)
                item["section_name"] = section
                item["department_name"] = department
                results.append(item)
            for key, value in node.items():
                if key in {"identificador", "titulo", "url_html", "url_xml", "url_pdf"}:
                    continue
                if key == "seccion" and isinstance(value, list):
                    for child in value:
                        child_section = (
                            str(child.get("nombre", section))
                            if isinstance(child, Mapping)
                            else section
                        )
                        visit(child, child_section, department)
                elif key == "departamento" and isinstance(value, list):
                    for child in value:
                        child_department = (
                            str(child.get("nombre", department))
                            if isinstance(child, Mapping)
                            else department
                        )
                        visit(child, section, child_department)
                else:
                    visit(value, section, department)
        elif isinstance(node, list):
            for value in node:
                visit(value, section, department)

    visit(sumario)
    unique: dict[str, dict[str, Any]] = {}
    for item in results:
        identifier = str(item.get("identificador"))
        unique.setdefault(identifier, item)
    return list(unique.values())


def parse_rss_items(body: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(body)
    items: list[dict[str, str]] = []
    for element in root.iter():
        if _local_name(element.tag).lower() not in {"item", "entry"}:
            continue
        record: dict[str, str] = {}
        for child in element:
            name = _local_name(child.tag)
            value = "".join(child.itertext()).strip()
            if name == "link" and not value:
                value = str(child.attrib.get("href", ""))
            record[name] = value
        items.append(record)
    return items


def extract_government_law_titles(text: str) -> list[str]:
    matches = re.findall(
        r"(?:proyecto|anteproyecto)\s+de\s+(?:ley\s+)?(?:orgánica\s+)?(.{8,180}?)(?=\.|;|\s+que\s+se|\s+para\s+su|$)",
        unescape(text),
        flags=re.IGNORECASE,
    )
    return [f"Proyecto de Ley {match.strip()}" for match in matches[:20]]


def parse_rss_date(value: str | None) -> date | None:
    if not value:
        return None
    direct = parse_date_from_value(value)
    if direct:
        return direct
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _law_from_boe_row(row: Mapping[str, Any], source_url: str) -> Ley | None:
    title = _first_value(row, "titulo", "title")
    identifier = _first_value(row, "identificador", "id")
    if not title or not identifier:
        return None
    law = Ley(
        title=title,
        boe_id=identifier,
        metadata={
            "rango": _nested_text(row.get("rango")),
            "ambito": _nested_text(row.get("ambito")),
            "numero_oficial": row.get("numero_oficial"),
            "fecha_publicacion": row.get("fecha_publicacion"),
            "url_eli": row.get("url_eli"),
        },
    )
    disposition_date = parse_date_from_value(row.get("fecha_disposicion"))
    publication_date = parse_date_from_value(row.get("fecha_publicacion"))
    if disposition_date:
        law.add_event(
            LegislativeEvent(
                EVENT_SANCTION_PROMULGATION,
                disposition_date,
                "boe_legislation_api",
                source_url,
                confidence="medium",
                details={"field": "fecha_disposicion", "identifier": identifier},
            )
        )
    if publication_date:
        law.add_event(
            LegislativeEvent(
                EVENT_PUBLICATION,
                publication_date,
                "boe_legislation_api",
                source_url,
                details={"field": "fecha_publicacion", "identifier": identifier},
            )
        )
    effective_date = parse_date_from_value(row.get("fecha_vigencia"))
    if effective_date:
        law.add_event(
            LegislativeEvent(
                EVENT_ENTRY_INTO_FORCE,
                effective_date,
                "boe_legislation_api",
                source_url,
                details={"field": "fecha_vigencia", "identifier": identifier},
            )
        )
    return law


def _is_boe_ordinary_law(row: Mapping[str, Any]) -> bool:
    range_text = _normalise_text(_nested_text(row.get("rango")))
    scope_text = _normalise_text(_nested_text(row.get("ambito")))
    title = _normalise_text(_first_value(row, "titulo", "title"))
    return (
        (range_text == "ley" or range_text.endswith(" ley"))
        and (scope_text in {"estatal", "1"} or row.get("ambito@codigo") == "1")
        and "ley organica" not in title
    )


def _is_boe_summary_ordinary_state_law(item: Mapping[str, Any]) -> bool:
    title = _normalise_text(str(item.get("titulo", "")))
    department = _normalise_text(str(item.get("department_name", "")))
    return (
        str(item.get("identificador", "")).startswith("BOE-A-")
        and title.startswith("ley ")
        and not title.startswith("ley organica")
        and department == "jefatura del estado"
    )


def _add_process_events(law: Ley, process: str, source_url: str, dataset_kind: str) -> None:
    current_phase = ""
    for line in [part.strip() for part in process.splitlines() if part.strip()]:
        normalised = _normalise_text(line)
        if not _DATE_PATTERNS[0].search(line) and not _DATE_PATTERNS[1].search(line):
            if len(line) < 100 and not re.search(r"\bdesde\b|\bhasta\b", normalised):
                current_phase = line
            continue
        parsed = parse_date_from_value(line)
        if not parsed:
            continue
        context = _normalise_text(f"{current_phase} {line}")
        kind = None
        if "concluido" in context and "aprobado" in context:
            kind = EVENT_FINAL_APPROVAL
        elif "toma en consideracion" in context:
            kind = EVENT_CONSIDERATION
        elif "aprobacion" in context and "senado" not in context:
            kind = EVENT_CONGRESS_APPROVAL
        elif "aprobacion" in context and "senado" in context:
            kind = EVENT_SENATE_APPROVAL
        if kind:
            law.add_event(
                LegislativeEvent(
                    kind,
                    parsed,
                    "congreso_open_data",
                    source_url,
                    confidence="medium",
                    details={
                        "phase": current_phase,
                        "raw_line": line,
                        "dataset_kind": dataset_kind,
                    },
                )
            )


def _merge_law(index: dict[str, Ley], law: Ley) -> None:
    key = law.boe_id or law.initiative_id or _normalise_text(law.title)
    title_key = _normalise_text(law.title)
    eli_url = str(law.metadata.get("url_eli") or "")
    existing = (
        index.get(key)
        or (index.get(f"eli:{eli_url}") if eli_url else None)
        or (
            next(
                (
                    candidate
                    for candidate in index.values()
                    if eli_url and candidate.metadata.get("url_eli") == eli_url
                ),
                None,
            )
        )
        or index.get(f"title:{title_key}")
    )
    if existing is None:
        index[key] = law
        index.setdefault(f"title:{title_key}", law)
        if eli_url:
            index.setdefault(f"eli:{eli_url}", law)
        return
    existing.merge(law)
    index.setdefault(key, existing)
    if eli_url:
        index.setdefault(f"eli:{eli_url}", existing)
    existing_eli_url = str(existing.metadata.get("url_eli") or "")
    if existing_eli_url:
        index.setdefault(f"eli:{existing_eli_url}", existing)


def _source_status_for_event(laws: list[Ley], event_kind: str) -> str:
    if any(not any(event.kind == event_kind for event in law.events) for law in laws):
        return "partial"
    return "ok"


def _extract_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "items", "records", "results", "iniciativas"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        return [payload]
    return []


def _extract_data_rows(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        data = payload.get("data", payload)
        if isinstance(data, list):
            return data
        if isinstance(data, Mapping):
            return [data] if "identificador" in data else []
    return []


def _xml_record(element: ET.Element) -> dict[str, str]:
    record: dict[str, str] = {}
    aliases = {
        "titulo": "title",
        "objeto": "title",
        "descripcion": "description",
        "tipo": "type",
        "tipoiniciativa": "type",
        "tipo_iniciativa": "type",
        "procedimiento": "procedure",
        "expediente": "initiative_id",
        "numeroexpediente": "initiative_id",
        "numero_expediente": "initiative_id",
        "identificador": "initiative_id",
        "id": "initiative_id",
        "fechapresentacion": "presentation_date",
        "fecha_presentacion": "presentation_date",
        "fechaentrada": "presentation_date",
        "fecha_entrada": "presentation_date",
        "fechacalificacion": "qualification_date",
        "fecha_calificacion": "qualification_date",
    }
    for child in element.iter():
        if child is element:
            continue
        raw_name = _local_name(child.tag).lower()
        name = aliases.get(raw_name, raw_name)
        value = " ".join(part.strip() for part in child.itertext() if part.strip())
        if value and name not in record:
            record[name] = value
    return record


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _nested_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(
            value.get("texto")
            or value.get("text")
            or value.get("nombre")
            or value.get("name")
            or ""
        )
    return str(value or "")


def _first_value(record: Mapping[str, Any], *keys: str) -> str:
    lowered = {str(key).lower(): value for key, value in record.items()}
    for key in keys:
        value = record.get(key, lowered.get(key.lower()))
        if value is None:
            continue
        text = _nested_text(value).strip()
        if text:
            return text
    return ""


def _clean_text(value: Any) -> str:
    return " ".join(str(value).replace("\xa0", " ").split())


def _clean_id(value: str) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _extract_document_links(record: Mapping[str, Any]) -> list[str]:
    links: list[str] = []
    for field_name in ("ENLACESBOCG", "ENLACESDS", "DOCUMENTOS", "ENLACES"):
        value = _first_value(record, field_name)
        for link in re.findall(r"https?://[^\s]+", value):
            if link not in links:
                links.append(link)
    return links


def _normalise_text(value: Any) -> str:
    raw = _clean_text(value).lower()
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(character)
    )


def _contains_approval(value: str) -> bool:
    normalised = _normalise_text(value)
    return "aprobado" in normalised or "aprobada" in normalised


def _confidence_rank(value: str) -> int:
    return {"high": 2, "medium": 1}.get(value, 0)


def _accept_for_url(url: str) -> str:
    lower = url.lower()
    if lower.endswith(".xml"):
        return "application/xml"
    if lower.endswith(".csv"):
        return "text/csv"
    return "application/json"


def _data_link_preference(url: str) -> int:
    lower = url.lower()
    if lower.endswith(".json"):
        return 0
    if lower.endswith(".xml"):
        return 1
    if lower.endswith(".csv"):
        return 2
    return 99


__all__ = [
    "DailyLawReport",
    "EVENT_CONGRESS_APPROVAL",
    "EVENT_CONSIDERATION",
    "EVENT_ENTRY_INTO_FORCE",
    "EVENT_FINAL_APPROVAL",
    "EVENT_GOVERNMENT_APPROVAL",
    "EVENT_PUBLICATION",
    "EVENT_SANCTION_PROMULGATION",
    "EVENT_SENATE_APPROVAL",
    "EVENT_STARTED",
    "EVENT_QUALIFIED",
    "Ley",
    "LegislativeEvent",
    "OfficialLawSources",
    "extract_boe_summary_items",
    "is_ordinary_law",
    "parse_date",
    "parse_date_from_value",
    "parse_senate_xml",
    "parse_tabular_payload",
]

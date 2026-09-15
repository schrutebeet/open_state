"""SQLite history for machine-readable Spanish ordinary-law events.

This store deliberately keeps the raw official source URL and extraction
details with every event.  It only joins records where an official identifier
proves they are the same law; similar titles are never used as a crosswalk.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from civic_metrics.legislation import DailyLawReport, LegislativeEvent, Ley


class LawHistoryStore(AbstractContextManager["LawHistoryStore"]):
    """Persist and retrieve the official lifecycle of state ordinary laws."""

    def __init__(self, database: Path | str) -> None:
        self.path = Path(database)
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> LawHistoryStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        return self

    def __exit__(self, *args: object) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("LawHistoryStore debe usarse dentro de un bloque with")
        return self.connection

    def _create_tables(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS legislation_laws (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                initiative_id TEXT,
                boe_id TEXT,
                eli_url TEXT,
                legislature TEXT,
                origin TEXT,
                official_key TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS legislation_laws_boe_id_unique
                ON legislation_laws(boe_id) WHERE boe_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS legislation_laws_eli_url_unique
                ON legislation_laws(eli_url) WHERE eli_url IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS legislation_laws_official_key_unique
                ON legislation_laws(official_key) WHERE official_key IS NOT NULL;

            CREATE TABLE IF NOT EXISTS legislation_identifiers (
                law_id INTEGER NOT NULL REFERENCES legislation_laws(id) ON DELETE CASCADE,
                identifier_type TEXT NOT NULL,
                identifier_value TEXT NOT NULL,
                PRIMARY KEY (identifier_type, identifier_value),
                UNIQUE (law_id, identifier_type, identifier_value)
            );

            CREATE TABLE IF NOT EXISTS legislation_events (
                id INTEGER PRIMARY KEY,
                law_id INTEGER NOT NULL REFERENCES legislation_laws(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                event_date TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                confidence TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE (law_id, kind, event_date, source, source_url)
            );
            CREATE INDEX IF NOT EXISTS legislation_events_by_date
                ON legislation_events(event_date, kind);

            CREATE TABLE IF NOT EXISTS legislation_collection_runs (
                target_date TEXT PRIMARY KEY,
                source_status_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                collected_at TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def record_report(self, report: DailyLawReport) -> None:
        """Upsert all discovered records and retain the audit result of a run."""
        laws = report.discovered_laws or report.laws
        for law in laws:
            law_id = self._upsert_law(law)
            for event in law.events:
                self.db.execute(
                    """
                    INSERT INTO legislation_events
                        (law_id, kind, event_date, source, source_url, confidence, details_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(law_id, kind, event_date, source, source_url) DO UPDATE SET
                        confidence=excluded.confidence,
                        details_json=excluded.details_json
                    """,
                    (
                        law_id,
                        event.kind,
                        event.event_date.isoformat(),
                        event.source,
                        event.source_url,
                        event.confidence,
                        _json(event.details),
                    ),
                )
        self.db.execute(
            """
            INSERT INTO legislation_collection_runs
                (target_date, source_status_json, warnings_json, collected_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target_date) DO UPDATE SET
                source_status_json=excluded.source_status_json,
                warnings_json=excluded.warnings_json,
                collected_at=excluded.collected_at
            """,
            (
                report.target_date.isoformat(),
                _json(report.source_status),
                _json(report.warnings),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.commit()

    def report_for_day(self, target_date: date) -> DailyLawReport:
        """Build a daily report from events already verified and stored locally."""
        rows = self.db.execute(
            """
            SELECT law.id AS law_id, law.title, law.initiative_id, law.boe_id,
                   law.legislature, law.origin, law.metadata_json,
                   event.kind, event.event_date, event.source, event.source_url,
                   event.confidence, event.details_json
            FROM legislation_events AS event
            JOIN legislation_laws AS law ON law.id = event.law_id
            WHERE event.event_date = ?
            ORDER BY law.id, event.event_date, event.id
            """,
            (target_date.isoformat(),),
        ).fetchall()
        laws: dict[int, Ley] = {}
        for row in rows:
            law = laws.setdefault(
                row["law_id"],
                Ley(
                    title=row["title"],
                    initiative_id=row["initiative_id"],
                    boe_id=row["boe_id"],
                    legislature=row["legislature"],
                    origin=row["origin"],
                    metadata=_from_json(row["metadata_json"]),
                ),
            )
            law.add_event(
                LegislativeEvent(
                    row["kind"],
                    date.fromisoformat(row["event_date"]),
                    row["source"],
                    row["source_url"],
                    row["confidence"],
                    _from_json(row["details_json"]),
                )
            )
        run = self.db.execute(
            "SELECT source_status_json, warnings_json "
            "FROM legislation_collection_runs WHERE target_date=?",
            (target_date.isoformat(),),
        ).fetchone()
        return DailyLawReport(
            target_date=target_date,
            laws=list(laws.values()),
            warnings=_from_json(run["warnings_json"]) if run else [],
            source_status=_from_json(run["source_status_json"]) if run else {},
        )

    def _upsert_law(self, law: Ley) -> int:
        now = datetime.now(UTC).isoformat()
        eli_url = str(law.metadata.get("url_eli") or "") or None
        official_key = _official_key(law)
        candidates: list[tuple[str, str]] = []
        if law.boe_id:
            candidates.append(("boe_id", law.boe_id))
        if eli_url:
            candidates.append(("eli_url", eli_url))
        if official_key:
            candidates.append(("official_key", official_key))
        if law.initiative_id:
            candidates.append(("initiative", law.initiative_id))
        for identifier in sorted(law.source_ids):
            candidates.append(("initiative", identifier))

        law_id: int | None = None
        for kind, value in candidates:
            if kind == "initiative":
                row = self.db.execute(
                    "SELECT law_id FROM legislation_identifiers "
                    "WHERE identifier_type=? AND identifier_value=?",
                    (kind, value),
                ).fetchone()
            else:
                row = self.db.execute(
                    f"SELECT id FROM legislation_laws WHERE {kind}=?", (value,)
                ).fetchone()
            if row:
                law_id = int(row[0])
                break

        if law_id is None:
            cursor = self.db.execute(
                """
                INSERT INTO legislation_laws
                    (title, initiative_id, boe_id, eli_url, legislature, origin,
                     official_key, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    law.title,
                    law.initiative_id,
                    law.boe_id,
                    eli_url,
                    law.legislature,
                    law.origin,
                    official_key,
                    _json(law.metadata),
                    now,
                    now,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite no devolvió el identificador de la ley insertada")
            law_id = int(cursor.lastrowid)
        else:
            current = self.db.execute(
                "SELECT metadata_json FROM legislation_laws WHERE id=?", (law_id,)
            ).fetchone()
            metadata = _from_json(current["metadata_json"]) if current else {}
            metadata.update(
                {key: value for key, value in law.metadata.items() if value is not None}
            )
            self.db.execute(
                """
                UPDATE legislation_laws
                SET title=?, initiative_id=COALESCE(initiative_id, ?), boe_id=COALESCE(boe_id, ?),
                    eli_url=COALESCE(eli_url, ?), legislature=COALESCE(legislature, ?),
                    origin=COALESCE(origin, ?), official_key=COALESCE(official_key, ?),
                    metadata_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    law.title,
                    law.initiative_id,
                    law.boe_id,
                    eli_url,
                    law.legislature,
                    law.origin,
                    official_key,
                    _json(metadata),
                    now,
                    law_id,
                ),
            )
        for identifier in {value for kind, value in candidates if kind == "initiative"}:
            self.db.execute(
                "INSERT OR IGNORE INTO legislation_identifiers "
                "(law_id, identifier_type, identifier_value) VALUES (?, ?, ?)",
                (law_id, "initiative", identifier),
            )
        return law_id


def _official_key(law: Ley) -> str | None:
    """Return the statutory number/year identity, never a fuzzy title match."""
    match = re.match(r"^ley\s+(\d+)/(\d{4})\b", law.title.strip(), re.IGNORECASE)
    if not match:
        return None
    return f"ley:{int(match.group(1))}/{match.group(2)}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _from_json(value: str) -> Any:
    loaded = json.loads(value)
    if isinstance(loaded, (dict, list)):
        return loaded
    return {}

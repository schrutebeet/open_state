from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.create_snapshot_db import create_snapshot


def test_snapshot_keeps_latest_distinct_periods_per_indicator(tmp_path: Path) -> None:
    source = tmp_path / "history.db"
    output = tmp_path / "snapshot.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE indicators (id INTEGER PRIMARY KEY, frequency TEXT NOT NULL);
            INSERT INTO indicators VALUES (1, 'monthly'), (2, 'monthly');
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                indicator_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                status TEXT NOT NULL,
                frequency TEXT NOT NULL DEFAULT 'monthly'
            );
            CREATE TABLE observation_dependencies (
                id INTEGER PRIMARY KEY,
                observation_id INTEGER NOT NULL,
                depends_on_observation_id INTEGER NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO observations (id, indicator_id, period_start, period_end, retrieved_at, status) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "2026-01-01", "2026-01-31", "2026-02-01", "published"),
                (2, 1, "2026-02-01", "2026-02-28", "2026-03-01", "published"),
                (3, 1, "2026-02-01", "2026-02-28", "2026-03-02", "published"),
                (4, 1, "2026-03-01", "2026-03-31", "2026-04-01", "published"),
                (5, 2, "2026-01-01", "2026-01-31", "2026-02-01", "published"),
                (6, 2, "2026-02-01", "2026-02-28", "2026-03-01", "published"),
                (7, 2, "2026-03-01", "2026-03-31", "2026-04-01", "published"),
                (8, 2, "2026-04-01", "2026-04-30", "2026-05-01", "published"),
            ],
        )
        connection.execute("""
            INSERT INTO observations VALUES
            (9, 1, '2026-01-01', '2026-12-31', '2026-09-01', 'published', 'annual')
        """)

    assert create_snapshot(source, output, 2) == 4
    with sqlite3.connect(output) as connection:
        assert connection.execute(
            "SELECT indicator_id, COUNT(*) FROM observations GROUP BY indicator_id"
        ).fetchall() == [(1, 2), (2, 2)]

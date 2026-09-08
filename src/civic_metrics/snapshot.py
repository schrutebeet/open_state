"""Build a bounded SQLite copy without modifying the historical database."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def validate_snapshot_paths(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve() or (
        source.exists() and output.exists() and source.samefile(output)
    ):
        raise ValueError("History and snapshot must be different database files")


def create_snapshot(source: Path, output: Path, lookback: int) -> int:
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    validate_snapshot_paths(source, output)
    if not source.is_file():
        raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin,
            closing(sqlite3.connect(temporary)) as target,
        ):
            origin.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
            target.execute("PRAGMA foreign_keys = ON")
            target.execute("""
                CREATE TEMP TABLE retained_observations AS
                WITH latest_per_period AS (
                    SELECT o.id, o.indicator_id, o.period_end, o.retrieved_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY o.indicator_id, o.period_start, o.period_end
                               ORDER BY o.retrieved_at DESC, o.id DESC
                           ) AS revision_rank
                    FROM observations AS o
                    JOIN indicators AS i ON i.id = o.indicator_id
                    WHERE o.status = 'published' AND o.frequency = i.frequency
                ), ranked_periods AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY indicator_id
                        ORDER BY period_end DESC, retrieved_at DESC, id DESC
                    ) AS period_rank
                    FROM latest_per_period WHERE revision_rank = 1
                )
                SELECT id FROM ranked_periods WHERE period_rank <= ?
            """, (lookback,))
            target.execute("""
                DELETE FROM observation_dependencies
                WHERE observation_id NOT IN (SELECT id FROM retained_observations)
                   OR depends_on_observation_id NOT IN (SELECT id FROM retained_observations)
            """)
            target.execute(
                "DELETE FROM observations WHERE id NOT IN (SELECT id FROM retained_observations)"
            )
            target.commit()
            count = int(target.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        # Close connections before replacing the file (required on Windows).
        # A failed build leaves the previous snapshot intact.
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    LOGGER.info("Created snapshot %s: %s observations (lookback=%s)", output, count, lookback)
    return count

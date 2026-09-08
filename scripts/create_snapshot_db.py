#!/usr/bin/env python3
"""Rebuild the bounded snapshot without rerunning ingestion."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy.engine import make_url  # noqa: E402

from civic_metrics.settings import Settings  # noqa: E402
from civic_metrics.snapshot import create_snapshot  # noqa: E402, F401


def parse_args() -> argparse.Namespace:
    settings = Settings(project_root=ROOT, _env_file=ROOT / ".env")
    database = make_url(settings.resolved_database_url())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=os.environ.get("HISTORY_DB_PATH"))
    parser.add_argument("--output", type=Path, default=settings.resolved_snapshot_db_path())
    parser.add_argument("--lookback", type=int, default=settings.lookback_period)
    args = parser.parse_args()
    if args.source is None:
        if database.get_backend_name() != "sqlite" or database.database in (None, "", ":memory:"):
            parser.error("--source must name a SQLite database file")
        args.source = Path(database.database)
    return args


def main() -> None:
    args = parse_args()
    count = create_snapshot(args.source, args.output, args.lookback)
    print(f"Created snapshot {args.output}: {count} observations (lookback={args.lookback})")


if __name__ == "__main__":
    main()

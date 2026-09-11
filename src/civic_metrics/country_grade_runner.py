"""Command-line modes for materialising country grades in either database."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from filelock import FileLock
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.models import CountryGrade
from civic_metrics.processors.country_grade import materialise_country_grade, previous_month
from civic_metrics.settings import Settings


def parse_month(token: str) -> list[date]:
    """Expand YYYYMM or an inclusive YYYYMM-YYYYMM range into month starts."""
    values = token.split("-", 1)
    if any(len(value) != 6 or not value.isdigit() for value in values):
        raise argparse.ArgumentTypeError(f"Invalid month or range: {token!r}; use YYYYMM[-YYYYMM]")

    def to_month(value: str) -> date:
        year, month = int(value[:4]), int(value[4:])
        if not 1 <= month <= 12:
            raise argparse.ArgumentTypeError(f"Invalid month: {value!r}")
        return date(year, month, 1)

    start, end = to_month(values[0]), to_month(values[-1])
    if end < start:
        raise argparse.ArgumentTypeError(f"Range must be ascending: {token!r}")
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return months


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--historic", nargs="+", metavar="YYYYMM[-YYYYMM]")
    modes.add_argument("--snapshot", action="store_true")
    return parser


def _sqlite_path(settings: Settings, snapshot: bool) -> Path:
    if snapshot:
        return settings.resolved_snapshot_db_path()
    database = make_url(settings.resolved_database_url())
    if database.get_backend_name() != "sqlite" or database.database in (None, "", ":memory:"):
        raise RuntimeError("Country-grade CLI requires a file-backed SQLite database")
    return Path(database.database).resolve()


def run(argv: list[str] | None = None, *, project_root: Path) -> list[dict[str, object]]:
    args = build_parser().parse_args(argv)
    settings = Settings(project_root=project_root, _env_file=project_root / ".env")
    snapshot_mode = bool(args.snapshot)
    database_path = _sqlite_path(settings, snapshot_mode)
    if snapshot_mode and not database_path.is_file():
        raise FileNotFoundError(f"Snapshot database does not exist: {database_path}")
    lock_path = project_root / "data" / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with FileLock(lock_path, timeout=0):
        engine = create_database_engine(f"sqlite+pysqlite:///{database_path}")
        try:
            init_database(engine)
            with make_session_factory(engine).begin() as session:
                if snapshot_mode:
                    start, end = previous_month()
                    session.execute(delete(CountryGrade))
                    results.append(
                        materialise_country_grade(
                            session,
                            period_start=start,
                            period_end=end,
                            replace_existing=True,
                        )
                    )
                else:
                    requested = [month for token in args.historic for month in parse_month(token)]
                    for start in requested:
                        next_month = date(
                            start.year + (start.month == 12),
                            start.month % 12 + 1,
                            1,
                        )
                        end = date.fromordinal(next_month.toordinal() - 1)
                        results.append(
                            materialise_country_grade(
                                session,
                                period_start=start,
                                period_end=end,
                                replace_existing=True,
                            )
                        )
        finally:
            engine.dispose()
    return results


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    print(json.dumps(run(project_root=project_root), indent=2, ensure_ascii=False, default=str))

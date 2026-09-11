from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

from filelock import FileLock, Timeout
from sqlalchemy.engine import make_url

from civic_metrics.catalog import load_catalog
from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.logging_config import configure_logging
from civic_metrics.orchestrator import PipelineOrchestrator
from civic_metrics.processors.country_grade import materialise_country_grade
from civic_metrics.settings import Settings
from civic_metrics.snapshot import create_snapshot, validate_snapshot_paths

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="civic-metrics",
        description="Fetch official Spanish public data and materialise the indicator catalog.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Run only this dataset code. May be passed more than once.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code when any source fails.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the run summary as JSON.",
    )
    parser.add_argument(
        "--generate-country-grade",
        action="store_true",
        help="Generate the country conditions grade without downloading source data.",
    )
    return parser


def run_pipeline(
    argv: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    root = (project_root or Path.cwd()).resolve()
    if args.generate_country_grade:
        grade = generate_contry_grade(project_root=root)
        print(json.dumps(grade, indent=2, ensure_ascii=False, default=str))
        return 0
    settings = Settings(project_root=root, _env_file=root / ".env")
    configure_logging(settings.log_level)
    database_url = make_url(settings.resolved_database_url())
    history_path = (
        Path(database_url.database)
        if database_url.get_backend_name() == "sqlite"
        and database_url.database not in (None, "", ":memory:")
        else None
    )
    snapshot_path = settings.resolved_snapshot_db_path()
    if history_path is not None:
        validate_snapshot_paths(history_path, snapshot_path)

    lock_path = settings.project_root / "data" / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path, timeout=0)
    try:
        with lock:
            catalog = load_catalog(settings.resolved_config_dir())
            engine = create_database_engine(settings.resolved_database_url())
            try:
                init_database(engine)
                factory = make_session_factory(engine)
                result = PipelineOrchestrator(settings, catalog, factory).run(args.dataset)
                with factory.begin() as session:
                    result.country_grade = materialise_country_grade(session)
                if history_path is not None:
                    create_snapshot(history_path, snapshot_path, settings.lookback_period)
                    snapshot_counts = _snapshot_indicator_counts(snapshot_path)
                    for dataset in result.datasets:
                        dataset.snapshot_by_indicator = {
                            indicator.code: snapshot_counts.get(
                                (dataset.dataset, indicator.code), 0
                            )
                            for indicator in catalog.indicators
                            if indicator.dataset == dataset.dataset and indicator.enabled
                        }
                else:
                    LOGGER.warning("Automatic snapshot requires a file-backed SQLite database")
            finally:
                engine.dispose()
    except Timeout:
        LOGGER.error("Another pipeline run already holds %s", lock_path)
        return 3

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        _print_summary(result.to_dict())

    incomplete = [
        item
        for item in result.datasets
        if item.required and item.status in {"failed", "partial", "empty", "skipped"}
    ]
    if result.status == "failed":
        return 2
    if args.strict and (incomplete or result.derived_errors):
        return 1
    return 0


def generate_contry_grade(
    *,
    project_root: Path | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Generate and persist the grade for the calendar month before *today*.

    The spelling is retained as the public function requested by the project.
    ``generate_country_grade`` below is the correctly spelled alias.
    """
    root = (project_root or Path.cwd()).resolve()
    settings = Settings(project_root=root, _env_file=root / ".env")
    database_url = make_url(settings.resolved_database_url())
    history_path = (
        Path(database_url.database)
        if database_url.get_backend_name() == "sqlite"
        and database_url.database not in (None, "", ":memory:")
        else None
    )
    snapshot_path = settings.resolved_snapshot_db_path()
    if history_path is not None:
        validate_snapshot_paths(history_path, snapshot_path)
    lock_path = settings.project_root / "data" / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(lock_path, timeout=0):
            engine = create_database_engine(settings.resolved_database_url())
            try:
                init_database(engine)
                with make_session_factory(engine).begin() as session:
                    grade = materialise_country_grade(session, today=today)
                if history_path is not None:
                    create_snapshot(history_path, snapshot_path, settings.lookback_period)
                return grade
            finally:
                engine.dispose()
    except Timeout as exc:
        raise RuntimeError(f"Another pipeline run already holds {lock_path}") from exc


generate_country_grade = generate_contry_grade


def _print_summary(summary: dict[str, object]) -> None:
    print(f"Civic Metrics run #{summary['run_id']}: {summary['status']}")
    datasets = summary.get("datasets", [])
    assert isinstance(datasets, list)
    for item in datasets:
        assert isinstance(item, dict)
        extracted = item.get("extracted_by_indicator", {})
        history = item.get("history_by_indicator", {})
        snapshot = item.get("snapshot_by_indicator", {})
        indicator_codes = list(extracted) if isinstance(extracted, dict) else []
        if not indicator_codes:
            indicator_codes = [str(item.get("dataset", ""))]
        if item.get("error"):
            detail = str(item["error"])
            print(f"  [{str(item.get('status', '')).upper():7}] {item.get('dataset')}: {detail}")
        else:
            for indicator_code in indicator_codes:
                extracted_count = (
                    extracted.get(indicator_code, 0) if isinstance(extracted, dict) else 0
                )
                history_count = history.get(indicator_code, 0) if isinstance(history, dict) else 0
                snapshot_count = (
                    snapshot.get(indicator_code, 0) if isinstance(snapshot, dict) else 0
                )
                print(
                    f"  [{str(item.get('status', '')).upper():7}] {indicator_code} | "
                    f"{item.get('dataset')} | extracted: {extracted_count} values "
                    f"(LOOKBACK_PERIOD) | history DB: {history_count} written | "
                    f"snapshot DB: {snapshot_count} written |"
                )
        validation = item.get("genai_validation")
        if isinstance(validation, dict):
            print(
                "             GenAI validation: "
                f"{validation.get('status')} - "
                f"{validation.get('description') or validation.get('error') or ''}"
            )
        elif isinstance(validation, list):
            statuses = ", ".join(
                str(entry.get("status")) for entry in validation if isinstance(entry, dict)
            )
            print(f"             GenAI validation: {statuses}")
    print(
        "  Derived: "
        f"{summary.get('derived_inserted', 0)} materialised, "
        f"{summary.get('derived_skipped', 0)} skipped"
    )
    skipped_details = summary.get("derived_skipped_details", [])
    if isinstance(skipped_details, list):
        for detail in skipped_details:
            print(f"    Derived skipped: {detail}")
    grade = summary.get("country_grade")
    if isinstance(grade, dict):
        print(
            "  Country grade: "
            f"{grade.get('status')} | {grade.get('period_start')}..{grade.get('period_end')} | "
            f"result: {grade.get('result')} | coverage: {grade.get('coverage')}"
        )


def _snapshot_indicator_counts(path: Path) -> dict[tuple[str, str], int]:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT o.dataset_code, i.code, COUNT(*)
            FROM observations AS o
            JOIN indicators AS i ON i.id = o.indicator_id
            GROUP BY o.dataset_code, i.code
            """
        ).fetchall()
    return {(str(dataset), str(indicator)): int(count) for dataset, indicator, count in rows}


def main() -> None:
    raise SystemExit(run_pipeline(sys.argv[1:]))

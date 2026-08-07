from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from filelock import FileLock, Timeout

from civic_metrics.catalog import load_catalog
from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.logging_config import configure_logging
from civic_metrics.orchestrator import PipelineOrchestrator
from civic_metrics.settings import Settings

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
    return parser


def run_pipeline(
    argv: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(project_root=(project_root or Path.cwd()).resolve())
    configure_logging(settings.log_level)

    lock_path = settings.project_root / "data" / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path, timeout=0)
    try:
        with lock:
            catalog = load_catalog(settings.resolved_config_dir())
            engine = create_database_engine(settings.resolved_database_url())
            init_database(engine)
            factory = make_session_factory(engine)
            result = PipelineOrchestrator(settings, catalog, factory).run(args.dataset)
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


def _print_summary(summary: dict[str, object]) -> None:
    print(f"Civic Metrics run #{summary['run_id']}: {summary['status']}")
    datasets = summary.get("datasets", [])
    assert isinstance(datasets, list)
    for item in datasets:
        assert isinstance(item, dict)
        detail = (
            f"{item.get('fetched_observations', 0)} extracted, "
            f"{item.get('inserted_observations', 0)} inserted"
        )
        if item.get("error"):
            detail = str(item["error"])
        print(f"  [{str(item.get('status', '')).upper():7}] {item.get('dataset')}: {detail}")
        validation = item.get("genai_validation")
        if isinstance(validation, dict):
            print(
                "             GenAI validation: "
                f"{validation.get('status')} - "
                f"{validation.get('summary') or validation.get('error') or ''}"
            )
    print(
        "  Derived: "
        f"{summary.get('derived_inserted', 0)} materialised, "
        f"{summary.get('derived_skipped', 0)} skipped"
    )


def main() -> None:
    raise SystemExit(run_pipeline(sys.argv[1:]))

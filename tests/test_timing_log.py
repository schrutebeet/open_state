import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from civic_metrics.cli import _print_summary, _write_run_report
from civic_metrics.orchestrator import DatasetResult, PipelineOrchestrator


def test_timing_log_is_written_under_data_with_per_dataset_timings(tmp_path: Path) -> None:
    result = DatasetResult(
        dataset="demo_dataset",
        status="success",
        http_request_count=2,
        http_elapsed_seconds=1.25,
        dataset_elapsed_seconds=1.5,
        http_request_timings=[
            {
                "method": "GET",
                "url": "https://example.test/data",
                "elapsed_seconds": 0.5,
                "status_code": 503,
                "error": None,
            },
            {
                "method": "GET",
                "url": "https://example.test/data",
                "elapsed_seconds": 0.75,
                "status_code": 200,
                "error": None,
            },
        ],
    )

    log_path = PipelineOrchestrator._write_timing_log(
        tmp_path,
        17,
        datetime(2026, 9, 13, tzinfo=UTC),
        [result],
    )

    assert log_path is not None
    path = Path(log_path)
    assert path.parent == tmp_path / "data"
    with path.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["run_id"] == "17"
    assert row["dataset"] == "demo_dataset"
    assert row["http_request_attempts"] == "2"
    assert row["http_elapsed_seconds"] == "1.25"
    assert row["dataset_elapsed_seconds"] == "1.5"
    details = json.loads(row["http_attempt_details"])
    assert len(details) == 2
    assert details[0]["url"] == "https://example.test/data"


def test_run_summary_prints_dataset_timing_and_log_path(capsys) -> None:
    _print_summary(
        {
            "run_id": 17,
            "status": "success",
            "datasets": [
                {
                    "dataset": "demo_dataset",
                    "status": "success",
                    "extracted_by_indicator": {"demo_indicator": 1},
                    "history_by_indicator": {"demo_indicator": 1},
                    "snapshot_by_indicator": {"demo_indicator": 1},
                    "http_request_count": 2,
                    "http_elapsed_seconds": 1.25,
                    "dataset_elapsed_seconds": 1.5,
                }
            ],
            "timing_log_path": "data/dataset_timings_run_17.csv",
        }
    )

    output = capsys.readouterr().out
    assert "Timing: 2 HTTP attempts / 1.250s waiting for responses" in output
    assert "1.500s total for dataset" in output
    assert "Dataset timing log: data/dataset_timings_run_17.csv" in output


def test_run_report_explains_short_source_history_and_ends_with_source_url(tmp_path: Path) -> None:
    report_path = _write_run_report(
        tmp_path,
        50,
        {
            "run_id": 17,
            "status": "success",
            "started_at": "2026-09-13T10:00:00+00:00",
            "finished_at": "2026-09-13T10:00:03+00:00",
            "datasets": [
                {
                    "dataset": "demo_dataset",
                    "status": "success",
                    "extracted_by_indicator": {"demo_indicator": 20},
                    "requested_by_indicator": {"demo_indicator": 50},
                    "history_by_indicator": {"demo_indicator": 20},
                    "snapshot_by_indicator": {"demo_indicator": 20},
                    "warnings": ["Only 20 of 50 requested periods were available from the source."],
                    "http_request_count": 2,
                    "http_elapsed_seconds": 1.25,
                    "dataset_elapsed_seconds": 1.5,
                    "source_urls": ["https://example.test/source.xlsx"],
                }
            ],
        },
    )

    assert report_path is not None
    report = Path(report_path).read_text(encoding="utf-8")
    assert "LOOKBACK_PERIOD requested: 50 observations per indicator" in report
    assert "requested 50; extracted 20; history DB written 20" in report
    assert "Warning: Only 20 of 50 requested periods" in report
    assert "HTTP request duration: 0 mins, 1 segs." in report
    assert "Source URL(s):\n  https://example.test/source.xlsx\n\nDERIVED INDICATORS" in report

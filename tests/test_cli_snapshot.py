import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from civic_metrics.catalog import load_catalog
from civic_metrics.cli import run_pipeline
from civic_metrics.http import CachedResponse, HttpClient
from civic_metrics.snapshot import create_snapshot


def test_cli_refreshes_snapshot_and_preserves_history(tmp_path, monkeypatch, capsys):
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "config")
    catalog.indicators = [i for i in catalog.indicators if i.code == "public_debt_total"]
    catalog.datasets = [d for d in catalog.datasets if d.code == "bde_public_debt"]
    monkeypatch.setattr("civic_metrics.cli.load_catalog", lambda _: catalog)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", "data/snapshot.db")
    monkeypatch.setenv("GENAI_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("FAIL_FAST", "false")
    document = [{
        "serie": "DTNPDE2010_P00000_PS_APU",
        "fechas": [f"{year}-{month:02d}-01" for year in range(2022, 2026)
                   for month in (1, 4, 7, 10)],
        "valores": list(range(100, 116)),
    }]

    def response(self, method, url, params=None, json_body=None, headers=None):
        assert params["rango"] == "MAX"
        return CachedResponse(json.dumps(document).encode(), url, "application/json", {})

    monkeypatch.setattr(HttpClient, "_request", response)
    for lookback in (12, 3, 15):
        monkeypatch.setenv("LOOKBACK_PERIOD", str(lookback))
        assert run_pipeline(["--strict", "--json"], project_root=tmp_path) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "success"
        with closing(sqlite3.connect(tmp_path / "history.db")) as db:
            assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 16
        snapshot = tmp_path / "data/snapshot.db"
        with closing(sqlite3.connect(snapshot)) as db:
            assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == lookback
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not Path(str(snapshot) + "-wal").exists()

    def fail(*args, **kwargs):
        raise ValueError("source unavailable")

    monkeypatch.setattr(HttpClient, "_request", fail)
    monkeypatch.setenv("LOOKBACK_PERIOD", "2")
    assert run_pipeline(["--strict"], project_root=tmp_path) == 2
    with closing(sqlite3.connect(snapshot)) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_snapshot_rejects_history_as_destination(tmp_path):
    history = tmp_path / "history.db"
    history.write_bytes(b"original history")
    with pytest.raises(ValueError, match="different database files"):
        create_snapshot(history, history, 12)
    assert history.read_bytes() == b"original history"


def test_failed_snapshot_build_preserves_previous_output(tmp_path):
    history = tmp_path / "history.db"
    snapshot = tmp_path / "snapshot.db"
    with closing(sqlite3.connect(history)) as db:
        db.execute("CREATE TABLE unrelated (id INTEGER)")
        db.commit()
    snapshot.write_bytes(b"previous snapshot")
    with pytest.raises(sqlite3.OperationalError):
        create_snapshot(history, snapshot, 12)
    assert snapshot.read_bytes() == b"previous snapshot"
    assert list(tmp_path.glob("*.tmp")) == []

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from civic_metrics.domain import DatasetPayload
from civic_metrics.models import IngestionRun, RawArtifact, SourceDataset


def _extension(content_type: str, source_url: str) -> str:
    lowered = source_url.lower()
    for extension in (".xlsx", ".xls", ".csv", ".json", ".html", ".xml"):
        if extension in lowered:
            return extension
    return {
        "application/json": ".json",
        "text/html": ".html",
        "text/csv": ".csv",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }.get(content_type, ".bin")


def store_artifact(
    session: Session,
    root: Path,
    run: IngestionRun,
    dataset: SourceDataset,
    payload: DatasetPayload,
) -> RawArtifact:
    day = payload.fetched_at.date().isoformat()
    directory = root / dataset.source.code / dataset.code / day
    directory.mkdir(parents=True, exist_ok=True)
    safe_code = re.sub(r"[^a-zA-Z0-9_.-]+", "_", dataset.code)
    path = directory / f"{safe_code}-{payload.sha256[:16]}{_extension(payload.content_type, payload.source_url)}"
    if not path.exists():
        path.write_bytes(payload.body)

    artifact = RawArtifact(
        run_id=run.id,
        dataset_id=dataset.id,
        fetched_at=payload.fetched_at,
        source_url=payload.source_url,
        content_type=payload.content_type,
        sha256=payload.sha256,
        local_path=str(path),
        metadata_json=payload.metadata,
    )
    session.add(artifact)
    session.flush()
    return artifact

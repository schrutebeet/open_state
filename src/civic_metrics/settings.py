from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Secrets are intentionally not read from committed files. Pydantic reads the
    project `.env` file and OS environment variables; the DataComex connector
    also supports the OS keyring.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    project_root: Path = Field(default_factory=lambda: Path.cwd())
    database_url: str = "sqlite+pysqlite:///./data/history.db"
    snapshot_db_path: Path = Path("data/snapshot.db")
    budget_history_path: Path = Path("data/budget_execution_history.json")
    budget_audit_summary_path: Path = Path("data/budget_execution_history_audit.summary.json")
    data_dir: Path = Path("data")
    runtime_dir: Path = Path(".runtime")
    save_files_locally: bool = True
    config_dir: Path = Path("config")
    artifacts_dir: Path = Path("artifacts")
    log_level: str = "INFO"
    http_timeout_seconds: float = 45.0
    lookback_period: int = Field(default=12, ge=1)
    fail_fast: bool = False
    genai_validation_enabled: bool = False
    genai_validation_dataset_ids: Annotated[tuple[int, ...] | None, NoDecode] = None
    genai_validation_model: str = "gpt-5.6-luna"
    genai_validation_max_payload_chars: int = 100_000
    genai_validation_strict: bool = False
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    datacomex_username: str | None = None
    datacomex_password: str | None = None

    @field_validator("genai_validation_dataset_ids", mode="before")
    @classmethod
    def parse_genai_validation_dataset_ids(cls, value: Any) -> tuple[int, ...] | None:
        """Accept one ID, comma-separated IDs, or a JSON list; unset means all."""
        if value is None:
            return None
        if isinstance(value, str):
            raw = value.strip()
            if not raw or raw.lower() == "all":
                return None
            if raw.startswith("["):
                value = json.loads(raw)
            else:
                value = raw.split(",")
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            ids = tuple(int(item) for item in value)
            if any(dataset_id < 1 for dataset_id in ids):
                raise ValueError("GENAI_VALIDATION_DATASET_IDS must contain positive integers")
            return ids
        dataset_id = int(value)
        if dataset_id < 1:
            raise ValueError("GENAI_VALIDATION_DATASET_IDS must contain positive integers")
        return (dataset_id,)

    def should_validate_dataset(self, dataset_id: int) -> bool:
        """Return whether this database dataset ID is selected for GenAI validation."""
        return (
            self.genai_validation_dataset_ids is None
            or dataset_id in self.genai_validation_dataset_ids
        )

    def resolved_config_dir(self) -> Path:
        return self._resolve(self.config_dir)

    def resolved_artifacts_dir(self) -> Path:
        if self.save_files_locally:
            return self._resolve(self.artifacts_dir)
        return self._resolve(self.runtime_dir / "artifacts").resolve()

    def resolved_snapshot_db_path(self) -> Path:
        return self._resolve_runtime_path(self.snapshot_db_path)

    def resolved_data_dir(self) -> Path:
        """Return the directory for generated databases, JSON, logs, and reports."""
        return self._resolve(self.data_dir if self.save_files_locally else self.runtime_dir).resolve()

    def resolved_budget_history_path(self) -> Path:
        return self._resolve_runtime_path(self.budget_history_path)

    def resolved_budget_audit_summary_path(self) -> Path:
        return self._resolve_runtime_path(self.budget_audit_summary_path)

    def resolved_database_url(self) -> str:
        if self.database_url.endswith(":memory:") or not self.database_url.startswith("sqlite"):
            return self.database_url
        marker = "///"
        if marker not in self.database_url:
            return self.database_url
        prefix, raw_path = self.database_url.split(marker, maxsplit=1)
        path = Path(raw_path)
        if path.is_absolute():
            return self.database_url
        absolute = self._resolve_runtime_path(path)
        return f"{prefix}{marker}{absolute}"

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.project_root / path

    def _resolve_runtime_path(self, path: Path) -> Path:
        absolute = self._resolve(path).resolve()
        if self.save_files_locally:
            return absolute
        data_root = (self.project_root / "data").resolve()
        try:
            relative = absolute.relative_to(data_root)
        except ValueError:
            return absolute
        return (self._resolve(self.runtime_dir) / relative).resolve()

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_url: str = "sqlite+pysqlite:///./data/civic_metrics.db"
    config_dir: Path = Path("config")
    artifacts_dir: Path = Path("artifacts")
    log_level: str = "INFO"
    http_timeout_seconds: float = 45.0
    max_history_periods: int = 12
    fail_fast: bool = False
    genai_validation_enabled: bool = False
    genai_validation_model: str = "gpt-5.6-luna"
    genai_validation_max_payload_chars: int = 100_000
    genai_validation_strict: bool = False
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    datacomex_username: str | None = None
    datacomex_password: str | None = None

    def resolved_config_dir(self) -> Path:
        return self._resolve(self.config_dir)

    def resolved_artifacts_dir(self) -> Path:
        return self._resolve(self.artifacts_dir)

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
        absolute = (self.project_root / path).resolve()
        return f"{prefix}{marker}{absolute}"

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.project_root / path

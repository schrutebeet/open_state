from pathlib import Path

from civic_metrics.settings import Settings


def test_settings_loads_genai_configuration_from_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GENAI_VALIDATION_ENABLED=true\n"
        "GENAI_VALIDATION_MODEL=gpt-5.6-luna\n"
        "GENAI_VALIDATION_MAX_PAYLOAD_CHARS=1234\n"
        "GENAI_VALIDATION_STRICT=true\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv)

    assert settings.genai_validation_enabled is True
    assert settings.genai_validation_model == "gpt-5.6-luna"
    assert settings.genai_validation_max_payload_chars == 1234
    assert settings.genai_validation_strict is True
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-key"


def test_settings_can_load_dotenv_outside_the_current_directory(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("GENAI_VALIDATION_ENABLED=true\n", encoding="utf-8")

    settings = Settings(project_root=tmp_path, _env_file=tmp_path / ".env")

    assert settings.genai_validation_enabled is True

from pathlib import Path

from civic_metrics.settings import Settings


def test_settings_loads_genai_configuration_from_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GENAI_VALIDATION_ENABLED=true\n"
        "GENAI_VALIDATION_DATASET_IDS=12,13\n"
        "GENAI_VALIDATION_MODEL=gpt-5.6-luna\n"
        "GENAI_VALIDATION_MAX_PAYLOAD_CHARS=1234\n"
        "GENAI_VALIDATION_STRICT=true\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv)

    assert settings.genai_validation_enabled is True
    assert settings.genai_validation_dataset_ids == (12, 13)
    assert settings.should_validate_dataset(12) is True
    assert settings.should_validate_dataset(14) is False
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


def test_settings_defaults_to_validating_all_datasets(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path, _env_file=None)

    assert settings.genai_validation_dataset_ids is None
    assert settings.should_validate_dataset(1) is True


def test_settings_accepts_a_single_or_json_list_of_genai_dataset_ids(tmp_path: Path) -> None:
    single = Settings(
        project_root=tmp_path,
        _env_file=None,
        genai_validation_dataset_ids="12",
    )
    json_list = Settings(
        project_root=tmp_path,
        _env_file=None,
        genai_validation_dataset_ids="[12, 13]",
    )

    assert single.genai_validation_dataset_ids == (12,)
    assert json_list.genai_validation_dataset_ids == (12, 13)

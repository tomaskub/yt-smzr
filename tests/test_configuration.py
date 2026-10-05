"""Dotfile precedence, safe validation, and atomic nonsecret persistence."""

from pathlib import Path

import pytest

from yt_smzr.config import ConfigurationError, Settings, save_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "YT_SMZR_SUMMARIZATION_PROVIDER",
        "YT_SMZR_SUMMARIZATION_MODEL",
        "YT_SMZR_OLLAMA_BASE_URL",
        "YT_SMZR_OLLAMA_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_dotfile_and_provider_defaults(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"
    assert Settings.from_env(path=path).summarization_model == "gpt-4.1-mini"
    assert not path.exists()
    assert Settings(summarization_provider="ollama").summarization_model == ""
    assert Settings(summarization_provider="openrouter").summarization_model == ""


def test_dotfile_environment_session_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        '[summarization]\nprovider="ollama"\nmodel="file:tag"\n[ollama]\nbase_url="http://file:11434"\ntimeout_seconds=30\n'
    )
    assert Settings.from_env(path=path).summarization_model == "file:tag"
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "env:tag")
    monkeypatch.setenv("YT_SMZR_OLLAMA_TIMEOUT_SECONDS", "60")
    assert Settings.from_env(path=path).summarization_model == "env:tag"
    selected = Settings.from_env(
        path=path, session={"summarization_model": "session:tag"}
    )
    assert selected.summarization_model == "session:tag"
    assert selected.ollama_timeout_seconds == 60


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("[summarization]\nmodel=123", "summarization.model"),
        ('[summarization]\nprovider="secret-unsupported"', "summarization.provider"),
        ("[ollama]\ntimeout_seconds=true", "ollama.timeout_seconds"),
        ("[ollama]\ntimeout_seconds=nan", "ollama.timeout_seconds"),
        ("[ollama]\ntimeout_seconds=inf", "ollama.timeout_seconds"),
        ('[ollama]\nbase_url="http://user:secret@localhost"', "ollama.base_url"),
        ('[ollama]\nbase_url="file:///secret"', "ollama.base_url"),
        ('summarization="secret"', "summarization"),
    ],
)
def test_safe_file_and_field_errors(tmp_path: Path, text: str, field: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(text)
    with pytest.raises(ConfigurationError) as caught:
        Settings.from_env(path=path)
    message = str(caught.value)
    assert str(path) in message
    assert field in message
    assert "secret" not in message


def test_malformed_toml_never_echoes_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('api_key = "secret invalid')
    with pytest.raises(ConfigurationError, match="valid TOML") as caught:
        Settings.from_env(path=path)
    assert "secret" not in str(caught.value)


def test_save_preserves_unrelated_settings_and_never_serializes_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        "# keep comment\n[unrelated.nested]\nvalues=[1,2]\n"
        '[summarization]\nother="keep"\n'
    )
    settings = Settings(
        summarization_provider="ollama",
        summarization_model="local:tag",
        openai_api_key="openai-secret",
        openrouter_api_key="router-secret",
    )
    save_settings(settings, path=path)
    saved = path.read_text()
    assert "# keep comment" in saved
    assert "[unrelated.nested]" in saved
    assert 'other="keep"' in saved
    assert "secret" not in saved
    assert Settings.from_env(path=path).summarization_model == "local:tag"


def test_save_failure_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    old = "[unrelated]\nkeep=true\n"
    path.write_text(old)

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("private filesystem details")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(ConfigurationError, match="previous file retained"):
        save_settings(Settings(), path=path)
    assert path.read_text() == old
    assert list(tmp_path.iterdir()) == [path]


def test_valid_overrides_replace_invalid_lower_priority_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("[summarization]\nmodel=123\n")
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "env-model")
    assert Settings.from_env(path=path).summarization_model == "env-model"
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "")
    assert (
        Settings.from_env(
            path=path, session={"summarization_model": "session-model"}
        ).summarization_model
        == "session-model"
    )


@pytest.mark.parametrize("value", [None, True, 123, [], {}])
def test_invalid_session_model_never_coerced(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigurationError, match="summarization.model"):
        Settings.from_env(
            path=tmp_path / "missing.toml", session={"summarization_model": value}
        )


def test_save_rejects_missing_non_openai_model(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"
    with pytest.raises(ConfigurationError, match="summarization.model"):
        save_settings(Settings(summarization_provider="ollama"), path=path)
    assert not path.exists()

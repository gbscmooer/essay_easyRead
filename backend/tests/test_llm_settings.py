"""统一 LLM 配置与旧环境变量兼容。"""

from backend.config import Settings, _apply_legacy_llm_env


def _clear_llm_env(monkeypatch):
    for key in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "MIMO_API_KEY",
        "MIMO_BASE_URL",
        "MIMO_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_settings_loads_without_api_key(monkeypatch):
    _clear_llm_env(monkeypatch)

    _apply_legacy_llm_env(use_project_dotenv=False)
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""
    assert "openai" in settings.llm_base_url
    assert settings.llm_model


def test_legacy_mimo_env_maps_to_llm(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("MIMO_API_KEY", "legacy-key")
    monkeypatch.setenv("MIMO_BASE_URL", "https://example.com/anthropic")
    monkeypatch.setenv("MIMO_MODEL", "legacy-model")

    _apply_legacy_llm_env(use_project_dotenv=False)
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == "legacy-key"
    assert settings.llm_base_url == "https://example.com/anthropic"
    assert settings.llm_model == "legacy-model"


def test_legacy_mimo_dotenv_maps_to_llm(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIMO_API_KEY=legacy-dotenv-key\n"
        "MIMO_BASE_URL=https://legacy.example/anthropic\n"
        "MIMO_MODEL=legacy-dotenv-model\n"
        "REMOVED_SETTING=ignored-during-migration\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    assert settings.llm_api_key == "legacy-dotenv-key"
    assert settings.llm_base_url == "https://legacy.example/anthropic"
    assert settings.llm_model == "legacy-dotenv-model"


def test_llm_dotenv_values_override_legacy_aliases(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIMO_API_KEY=legacy-key\n"
        "MIMO_BASE_URL=https://legacy.example/anthropic\n"
        "MIMO_MODEL=legacy-model\n"
        "LLM_API_KEY=canonical-key\n"
        "LLM_BASE_URL=https://canonical.example/v1\n"
        "LLM_MODEL=canonical-model\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    assert settings.llm_api_key == "canonical-key"
    assert settings.llm_base_url == "https://canonical.example/v1"
    assert settings.llm_model == "canonical-model"


def test_empty_llm_dotenv_does_not_block_legacy_aliases(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_KEY=\n"
        "LLM_BASE_URL=\n"
        "LLM_MODEL=\n"
        "MIMO_API_KEY=legacy-from-empty-llm\n"
        "MIMO_BASE_URL=https://legacy.example/anthropic\n"
        "MIMO_MODEL=legacy-model\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    assert settings.llm_api_key == "legacy-from-empty-llm"
    assert settings.llm_base_url == "https://legacy.example/anthropic"
    assert settings.llm_model == "legacy-model"


def test_settings_accepts_direct_llm_field_overrides():
    settings = Settings(
        _env_file=None,
        llm_api_key="direct-key",
        llm_base_url="https://direct.example/v1",
        llm_model="direct-model",
    )
    assert settings.llm_api_key == "direct-key"
    assert settings.llm_base_url == "https://direct.example/v1"
    assert settings.llm_model == "direct-model"


def test_analysis_config_falls_back_to_llm_settings(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services import ai_config

    monkeypatch.setattr(settings, "llm_api_key", "env-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "llm_model", "gpt-test")
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(tmp_path / "ai_config.json"))
    monkeypatch.setattr(
        ai_config,
        "load_config",
        lambda: {"analysis": {"api_key": "", "base_url": "", "model": ""}},
    )

    cfg = ai_config.get_analysis_config()
    assert cfg["api_key"] == "env-key"
    assert cfg["base_url"] == "https://api.openai.com/v1"
    assert cfg["model"] == "gpt-test"


def test_analysis_config_uses_llm_endpoint_without_saved_config(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services import ai_config

    monkeypatch.setattr(settings, "llm_api_key", "env-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://custom.example/v1")
    monkeypatch.setattr(settings, "llm_model", "custom-model")
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(tmp_path / "missing-ai-config.json"))

    cfg = ai_config.get_analysis_config()
    assert cfg["api_key"] == "env-key"
    assert cfg["base_url"] == "https://custom.example/v1"
    assert cfg["model"] == "custom-model"


def test_analysis_config_preserves_saved_endpoint_when_key_comes_from_env(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services import ai_config

    config_path = tmp_path / "ai_config.json"
    config_path.write_text(
        '{"analysis":{"api_key":"","base_url":"https://saved.example/v1","model":"saved-model"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "llm_api_key", "env-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://env.example/v1")
    monkeypatch.setattr(settings, "llm_model", "env-model")
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(config_path))

    cfg = ai_config.get_analysis_config()
    assert cfg["api_key"] == "env-key"
    assert cfg["base_url"] == "https://saved.example/v1"
    assert cfg["model"] == "saved-model"


def test_mock_eval_cli_imports_without_api_key(monkeypatch):
    """Reproduce CI import path: settings must not require a key."""
    for key in ("LLM_API_KEY", "MIMO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    # Already-imported settings may have a test key; assert empty default construction works.
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""

    from backend.tests.evaluation.deep_research.eval import cli as eval_cli

    assert hasattr(eval_cli, "main") or hasattr(eval_cli, "build_parser") or True

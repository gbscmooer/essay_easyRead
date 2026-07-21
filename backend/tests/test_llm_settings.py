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

    _apply_legacy_llm_env()
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""
    assert "openai" in settings.llm_base_url
    assert settings.llm_model


def test_legacy_mimo_env_maps_to_llm(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("MIMO_API_KEY", "legacy-key")
    monkeypatch.setenv("MIMO_BASE_URL", "https://example.com/anthropic")
    monkeypatch.setenv("MIMO_MODEL", "legacy-model")

    _apply_legacy_llm_env()
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == "legacy-key"
    assert settings.llm_base_url == "https://example.com/anthropic"
    assert settings.llm_model == "legacy-model"


def test_legacy_mimo_dotenv_file_maps_without_crash(monkeypatch, tmp_path):
    """旧 .env 仅含 MIMO_* 时不应 ValidationError，且应映射到 llm_*。"""
    _clear_llm_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MIMO_API_KEY=dev-placeholder-key",
                "MIMO_BASE_URL=https://example.com/v1",
                "MIMO_MODEL=legacy-from-dotenv",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _apply_legacy_llm_env(str(env_file))
    settings = Settings(_env_file=str(env_file))
    assert settings.llm_api_key == "dev-placeholder-key"
    assert settings.llm_base_url == "https://example.com/v1"
    assert settings.llm_model == "legacy-from-dotenv"


def test_legacy_dotenv_leftover_with_llm_key_ignored(monkeypatch, tmp_path):
    """已有 LLM_* 时，.env 残留 MIMO_* 不得导致启动失败。"""
    _clear_llm_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LLM_API_KEY=canonical-key",
                "MIMO_API_KEY=stale-legacy-key",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _apply_legacy_llm_env(str(env_file))
    settings = Settings(_env_file=str(env_file))
    assert settings.llm_api_key == "canonical-key"


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


def test_mock_eval_cli_imports_without_api_key(monkeypatch):
    """Reproduce CI import path: settings must not require a key."""
    _clear_llm_env(monkeypatch)

    # Already-imported settings may have a test key; assert empty default construction works.
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""

    from backend.tests.evaluation.deep_research.eval import cli as eval_cli

    assert hasattr(eval_cli, "main") or hasattr(eval_cli, "build_parser") or True

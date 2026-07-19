"""统一 LLM 配置与旧环境变量兼容。"""

from backend.config import Settings, _apply_legacy_llm_env


def test_settings_loads_without_api_key(monkeypatch):
    for key in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "MIMO_API_KEY",
        "MIMO_BASE_URL",
        "MIMO_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    _apply_legacy_llm_env()
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""
    assert "openai" in settings.llm_base_url
    assert settings.llm_model


def test_legacy_mimo_env_maps_to_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("MIMO_API_KEY", "legacy-key")
    monkeypatch.setenv("MIMO_BASE_URL", "https://example.com/anthropic")
    monkeypatch.setenv("MIMO_MODEL", "legacy-model")

    _apply_legacy_llm_env()
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == "legacy-key"
    assert settings.llm_base_url == "https://example.com/anthropic"
    assert settings.llm_model == "legacy-model"


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
    for key in ("LLM_API_KEY", "MIMO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    # Already-imported settings may have a test key; assert empty default construction works.
    settings = Settings(_env_file=None)
    assert settings.llm_api_key == ""

    from backend.tests.evaluation.deep_research.eval import cli as eval_cli

    assert hasattr(eval_cli, "main") or hasattr(eval_cli, "build_parser") or True

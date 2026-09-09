"""测试配置文件自动发现、YAML/TOML/JSON 读写、导出与导入及 API 端点。"""

import io
import json
import os
import copy
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import ai_config


def test_parse_config_content():
    yaml_text = """
translate:
  model: "gpt-4o"
  temperature: 0.5
"""
    parsed_yaml = ai_config.parse_config_content(yaml_text, fmt="yaml")
    assert parsed_yaml["translate"]["model"] == "gpt-4o"
    assert parsed_yaml["translate"]["temperature"] == 0.5

    json_text = '{"analysis": {"model": "claude-3-7-sonnet"}}'
    parsed_json = ai_config.parse_config_content(json_text, fmt="json")
    assert parsed_json["analysis"]["model"] == "claude-3-7-sonnet"

    toml_text = """
[translate]
model = "deepseek-chat"
temperature = 0.7
"""
    parsed_toml = ai_config.parse_config_content(toml_text, fmt="toml")
    assert parsed_toml["translate"]["model"] == "deepseek-chat"
    assert parsed_toml["translate"]["temperature"] == 0.7


def test_export_config_formats():
    content_yaml, filename_yaml, media_yaml = ai_config.export_config(fmt="yaml", mask_keys=True)
    assert filename_yaml == "config.yaml"
    assert "text/yaml" in media_yaml
    parsed = yaml.safe_load(content_yaml)
    assert isinstance(parsed, dict)
    assert "llm" in parsed
    assert "translate" not in parsed
    assert "analysis" not in parsed

    content_json, filename_json, media_json = ai_config.export_config(fmt="json", mask_keys=True)
    assert filename_json == "config.json"
    assert "application/json" in media_json
    parsed_json = json.loads(content_json)
    assert isinstance(parsed_json, dict)

    content_toml, filename_toml, media_toml = ai_config.export_config(fmt="toml", mask_keys=True)
    assert filename_toml == "config.toml"
    assert "application/toml" in media_toml


def test_import_config_preserving_keys(tmp_path, monkeypatch):
    test_yaml_path = tmp_path / "config.yaml"
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(test_yaml_path))

    # 初始化配置，保存真实 api_key
    initial = copy.deepcopy(ai_config.DEFAULT_CONFIG)
    initial["llm"] = {**ai_config.DEFAULT_CONFIG["llm"], "api_key": "sk-real-secret"}
    ai_config.save_config(initial, path=str(test_yaml_path))

    # 用户导入包含掩码的配置（例如从脱敏导出文件导入，修改了 model）
    import_text = """
llm:
  api_key: "***cret"
  model: "new-gpt-model"
"""
    ai_config.import_config(import_text, fmt="yaml")
    loaded = ai_config.load_config(str(test_yaml_path))
    # 验证真 key 被保护，未被 ***cret 覆盖
    assert loaded["llm"]["api_key"] == "sk-real-secret"
    # 验证新字段已更新
    assert loaded["llm"]["model"] == "new-gpt-model"


def test_api_config_file_info():
    client = TestClient(app)
    resp = client.get("/api/analysis/config/file-info")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_path" in data
    assert "format" in data
    assert "supported_formats" in data
    assert "yaml" in data["supported_formats"]


def test_api_exposes_one_llm_config_and_provider_catalog():
    client = TestClient(app)
    response = client.get("/api/analysis/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["llm"]["api_format"] in ("openai_chat", "anthropic_messages")
    assert "analysis" not in payload
    assert "translate" not in payload

    providers = client.get("/api/analysis/llm/providers")
    assert providers.status_code == 200
    rows = providers.json()["providers"]
    assert {row["id"] for row in rows} >= {"openai", "anthropic", "custom"}
    assert all(row["api_format"] in ("openai_chat", "anthropic_messages") for row in rows)


def test_llm_config_preflight_allows_frontend_origin():
    client = TestClient(app)
    response = client.options(
        "/api/analysis/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_api_config_export_and_import(tmp_path, monkeypatch):
    test_yaml_path = tmp_path / "config.yaml"
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(test_yaml_path))
    monkeypatch.setattr("backend.services.ai_config.CONFIG_PATH", str(test_yaml_path))

    client = TestClient(app)

    # 导出 yaml
    export_resp = client.get("/api/analysis/config/export?format=yaml&include_secrets=true")
    assert export_resp.status_code == 200
    assert "attachment; filename=\"config.yaml\"" in export_resp.headers["content-disposition"]
    yaml_content = export_resp.text
    assert "llm:" in yaml_content
    assert "translate:" not in yaml_content

    # 通过 JSON payload 导入
    new_payload = {
        "content": "ui:\n  highlight_duration: 4500\n",
        "format": "yaml",
    }
    import_resp = client.post("/api/analysis/config/import", json=new_payload)
    assert import_resp.status_code == 200
    assert import_resp.json()["message"] == "配置导入成功并已即时生效"

    # 通过文件上传导入
    file_bytes = b"ui:\n  highlight_duration: 5000\n"
    upload_resp = client.post(
        "/api/analysis/config/import",
        files={"file": ("config.yaml", io.BytesIO(file_bytes), "application/x-yaml")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["message"] == "配置导入成功并已即时生效"


def test_config_key_save_and_load(tmp_path, monkeypatch):
    test_key_path = tmp_path / "config_key"
    monkeypatch.setattr(ai_config, "CONFIG_KEY_PATH", str(test_key_path))
    monkeypatch.setattr("backend.services.ai_config.CONFIG_KEY_PATH", str(test_key_path))

    llm_data = {
        "api_key": "sk-test-secret-key-12345",
        "base_url": "https://api.example.com/v1",
        "model": "deepseek-chat",
        "temperature": 0.4,
        "max_tokens": 8192,
        "thinking_effort": "high",
    }
    saved_path = ai_config.save_config_key(llm_data, path=str(test_key_path))
    assert os.path.isfile(saved_path)

    loaded = ai_config.load_config_key(str(test_key_path))
    assert loaded["api_key"] == "sk-test-secret-key-12345"
    assert set(loaded) == {"api_key"}


def test_load_config_merges_config_key(tmp_path, monkeypatch):
    test_yaml_path = tmp_path / "config.yaml"
    test_key_path = tmp_path / "config_key"
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(test_yaml_path))
    monkeypatch.setattr("backend.services.ai_config.CONFIG_PATH", str(test_yaml_path))
    monkeypatch.setattr(ai_config, "CONFIG_KEY_PATH", str(test_key_path))
    monkeypatch.setattr("backend.services.ai_config.CONFIG_KEY_PATH", str(test_key_path))

    # 旧版主配置文件还没有统一 llm 段。
    main_cfg = copy.deepcopy(ai_config.DEFAULT_CONFIG)
    main_cfg.pop("llm")
    ai_config.dump_file_config(main_cfg, str(test_yaml_path))

    # config_key 中包含独立存储的 key 与模型
    # 兼容迁移：旧 config_key 可能还包含非密钥 provider 字段。
    ai_config.dump_file_config({"llm": {
        "api_key": "sk-isolated-key-9999",
        "base_url": "https://token.example.com/anthropic",
        "model": "claude-3-7-sonnet",
        "thinking_effort": "medium",
    }}, str(test_key_path))

    loaded = ai_config.load_config(str(test_yaml_path))
    assert loaded["llm"]["api_key"] == "sk-isolated-key-9999"
    assert loaded["llm"]["base_url"] == "https://token.example.com/anthropic"
    assert loaded["llm"]["model"] == "claude-3-7-sonnet"
    assert loaded["analysis"]["api_key"] == "sk-isolated-key-9999"
    assert loaded["translate"]["api_key"] == "sk-isolated-key-9999"


def test_secret_only_config_key_does_not_reset_saved_llm_provider(tmp_path, monkeypatch):
    test_json_path = tmp_path / "ai_config.json"
    test_key_path = tmp_path / "config_key"
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(test_json_path))
    monkeypatch.setattr(ai_config, "CONFIG_KEY_PATH", str(test_key_path))

    main_cfg = copy.deepcopy(ai_config.DEFAULT_CONFIG)
    main_cfg["llm"] = {
        **main_cfg["llm"],
        "provider": "deepseek",
        "api_format": "openai_chat",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "temperature": 0.4,
    }
    ai_config.dump_file_config(main_cfg, str(test_json_path))
    ai_config.dump_file_config({"llm": {"api_key": "sk-secret-only"}}, str(test_key_path))

    loaded = ai_config.load_config(str(test_json_path))

    assert loaded["llm"]["provider"] == "deepseek"
    assert loaded["llm"]["api_format"] == "openai_chat"
    assert loaded["llm"]["base_url"] == "https://api.deepseek.com"
    assert loaded["llm"]["model"] == "deepseek-chat"
    assert loaded["llm"]["temperature"] == 0.4
    assert loaded["llm"]["api_key"] == "sk-secret-only"

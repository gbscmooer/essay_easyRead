"""测试配置文件自动发现、YAML/TOML/JSON 读写、导出与导入及 API 端点。"""

import io
import json
import os
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
    assert "translate" in parsed

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
    initial = ai_config.DEFAULT_CONFIG.copy()
    initial["translate"] = {**ai_config.DEFAULT_CONFIG["translate"], "api_key": "sk-real-secret"}
    ai_config.save_config(initial, path=str(test_yaml_path))

    # 用户导入包含掩码的配置（例如从脱敏导出文件导入，修改了 model）
    import_text = """
translate:
  api_key: "***cret"
  model: "new-gpt-model"
"""
    ai_config.import_config(import_text, fmt="yaml")
    loaded = ai_config.load_config(str(test_yaml_path))
    # 验证真 key 被保护，未被 ***cret 覆盖
    assert loaded["translate"]["api_key"] == "sk-real-secret"
    # 验证新字段已更新
    assert loaded["translate"]["model"] == "new-gpt-model"


def test_api_config_file_info():
    client = TestClient(app)
    resp = client.get("/api/analysis/config/file-info")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_path" in data
    assert "format" in data
    assert "supported_formats" in data
    assert "yaml" in data["supported_formats"]


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
    assert "translate:" in yaml_content

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

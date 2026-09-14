"""LLM 配置档案：CRUD、启用物化、掩码密钥保护与整份保存保留。"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import ai_config


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    """把主配置与 config_key 指到临时目录，避免读写真实根目录文件。"""
    cfg_path = tmp_path / "ai_config.json"
    key_path = tmp_path / "config_key"
    monkeypatch.setattr(ai_config, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(ai_config, "CONFIG_KEY_PATH", str(key_path))
    # 屏蔽环境变量 key：否则 get_llm_config 的 env 回退会被误判为「已配置」
    from backend.config import settings
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_provider", "")
    monkeypatch.setattr(settings, "llm_api_format", "")
    return cfg_path, key_path


def _make_profile(name: str, base_url: str, model: str, api_key: str) -> dict:
    return ai_config.upsert_llm_profile({
        "name": name, "base_url": base_url, "model": model, "api_key": api_key,
    })


def test_profile_crud_and_activate_materializes_llm(profile_env):
    cfg_path, _ = profile_env
    first = ai_config.upsert_llm_profile({
        "name": "猫肥 API",
        "base_url": "https://api.neko.example/v1",
        "model": "neko-chat",
        "api_key": "sk-neko-123",
        "proxy": {"enabled": True, "base_url": "http://localhost:9000/v1"},
    })
    _make_profile("Zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-4.7", "sk-zhipu-456")

    state = ai_config.get_llm_profiles()
    assert state["active_id"] == ""
    assert [p["name"] for p in state["profiles"]] == ["猫肥 API", "Zhipu"]
    # api_key 只掩码回显，不泄露真 key
    assert state["profiles"][0]["api_key"].startswith("***")
    assert "sk-neko-123" not in json.dumps(state)

    activated = ai_config.activate_llm_profile(first["id"])
    assert activated["active_id"] == first["id"]

    llm = ai_config.get_llm_config()
    assert llm["api_key"] == "sk-neko-123"
    assert llm["base_url"] == "https://api.neko.example/v1"
    assert llm["model"] == "neko-chat"
    assert llm["proxy"] == {"enabled": True, "base_url": "http://localhost:9000/v1"}

    # 档案密钥映射存 config_key，全局生效密钥也写在其 llm 段（密钥唯一真源）
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["llm_profiles"]["active_id"] == first["id"]
    assert ai_config.load_config_key().get("api_key") == "sk-neko-123"
    assert ai_config.load_llm_profile_keys()[first["id"]] == "sk-neko-123"


def test_legacy_profile_without_proxy_gets_disabled_default(profile_env):
    cfg_path, _ = profile_env
    cfg_path.write_text(json.dumps({
        "llm_profiles": {
            "active_id": "legacy",
            "profiles": [{
                "id": "legacy",
                "name": "旧档案",
                "base_url": "https://legacy.example/v1",
                "model": "legacy-model",
            }],
        },
    }), encoding="utf-8")

    profile = ai_config.get_llm_profiles()["profiles"][0]

    assert profile["proxy"] == {"enabled": False, "base_url": ""}


def test_profile_update_masked_key_preserved_and_active_synced(profile_env):
    pid = _make_profile("A", "https://a.example/v1", "m1", "sk-aaa-111")["id"]
    ai_config.activate_llm_profile(pid)

    masked = ai_config.get_llm_profiles()["profiles"][0]["api_key"]
    assert ai_config.is_masked_key(masked)
    # 用掩码回显更新其他字段：真 key 必须保留，同时启用中的档案同步生效
    ai_config.upsert_llm_profile({
        "id": pid, "name": "A2", "base_url": "https://a2.example/v1",
        "model": "m2", "api_key": masked,
    })
    llm = ai_config.get_llm_config()
    assert llm["api_key"] == "sk-aaa-111"
    assert llm["model"] == "m2"
    assert llm["base_url"] == "https://a2.example/v1"


def test_whole_config_save_keeps_profiles(profile_env):
    """设置页 POST /config 的整份保存不携带档案段，盘上档案必须保留。"""
    pid = _make_profile("A", "https://a.example/v1", "m1", "sk-aaa-111")["id"]
    ai_config.activate_llm_profile(pid)

    cfg = ai_config.load_config()
    cfg.pop("llm_profiles")
    cfg["ui"]["theme"] = "dark"
    ai_config.save_config(cfg)

    state = ai_config.get_llm_profiles()
    assert len(state["profiles"]) == 1
    assert state["active_id"] == pid


def test_direct_llm_edit_syncs_back_to_active_profile(profile_env):
    """直接改全局 llm 段并保存，等价于编辑启用中的档案。"""
    pid = _make_profile("A", "https://a.example/v1", "m1", "sk-aaa-111")["id"]
    ai_config.activate_llm_profile(pid)

    cfg = ai_config.load_config()
    cfg["llm"]["model"] = "m-new"
    ai_config.save_config(cfg)

    state = ai_config.get_llm_profiles()
    assert state["profiles"][0]["model"] == "m-new"
    assert state["active_id"] == pid


def test_delete_active_profile_keeps_global_config(profile_env):
    pid = _make_profile("A", "https://a.example/v1", "m1", "sk-aaa-111")["id"]
    ai_config.activate_llm_profile(pid)

    ai_config.delete_llm_profile(pid)

    state = ai_config.get_llm_profiles()
    assert state["profiles"] == []
    assert state["active_id"] == ""
    # 生效配置保持不变，仅解除绑定
    llm = ai_config.get_llm_config()
    assert llm["model"] == "m1"
    assert llm["api_key"] == "sk-aaa-111"
    # config_key 中该档案的密钥一并清理
    assert pid not in ai_config.load_llm_profile_keys()


def test_profile_api_endpoints(profile_env):
    client = TestClient(app)

    # 名称缺失 → 400
    resp = client.post("/api/analysis/llm-profiles", json={"base_url": "https://x.example/v1"})
    assert resp.status_code == 400

    resp = client.post("/api/analysis/llm-profiles", json={
        "name": "P1", "base_url": "https://x.example/v1", "model": "mx", "api_key": "sk-p1-9999",
    })
    assert resp.status_code == 200
    body = resp.json()
    pid = body["profiles"][0]["id"]
    assert body["profiles"][0]["api_key"].startswith("***")

    resp = client.post(f"/api/analysis/llm-profiles/{pid}/activate")
    assert resp.status_code == 200
    assert resp.json()["active_id"] == pid

    # GET /config 的 llm 段反映启用档案，且掩码
    resp = client.get("/api/analysis/config")
    assert resp.status_code == 200
    assert resp.json()["llm"]["model"] == "mx"
    assert resp.json()["llm"]["api_key"].startswith("***")

    resp = client.put(f"/api/analysis/llm-profiles/{pid}", json={"name": "P1b"})
    assert resp.status_code == 200
    assert resp.json()["profiles"][0]["name"] == "P1b"

    resp = client.delete(f"/api/analysis/llm-profiles/{pid}")
    assert resp.status_code == 200
    assert resp.json()["active_id"] == ""

    assert client.delete(f"/api/analysis/llm-profiles/{pid}").status_code == 404
    assert client.post(f"/api/analysis/llm-profiles/{pid}/activate").status_code == 404
    assert client.put(f"/api/analysis/llm-profiles/{pid}", json={"name": "x"}).status_code == 404


def test_profile_key_reveal_endpoint(profile_env):
    """「显示 / 复制」取回完整密钥：真钥回真值、未存密钥回空串、未知档案 404。"""
    pid = _make_profile("A", "https://a.example/v1", "m1", "sk-aaa-111")["id"]
    keyless = _make_profile("B", "https://b.example/v1", "m2", "")["id"]
    client = TestClient(app)

    resp = client.get(f"/api/analysis/llm-profiles/{pid}/key")
    assert resp.status_code == 200
    assert resp.json() == {"api_key": "sk-aaa-111"}
    assert resp.headers["cache-control"] == "no-store"

    resp = client.get(f"/api/analysis/llm-profiles/{keyless}/key")
    assert resp.status_code == 200
    assert resp.json() == {"api_key": ""}

    assert client.get("/api/analysis/llm-profiles/lp_missing/key").status_code == 404

    remote = TestClient(app, client=("192.168.1.50", 50000))
    denied = remote.get(
        f"/api/analysis/llm-profiles/{pid}/key",
        headers={"x-forwarded-for": "127.0.0.1"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "LOCAL_ACCESS_REQUIRED"


def test_profile_test_connection_uses_profile_key(profile_env, monkeypatch):
    import backend.services.llm as llm_core

    pid = _make_profile("P", "https://p.example/v1", "mp", "sk-profile-real")["id"]
    captured: dict = {}

    async def fake_test_connection(cfg):
        captured.update(cfg)
        return {"ok": True, "message": "ok", "latency_ms": 1, "request_id": None, "api_format": cfg.get("api_format")}

    monkeypatch.setattr(llm_core, "test_connection", fake_test_connection)

    client = TestClient(app)
    resp = client.post("/api/analysis/test-connection", json={
        "type": "llm", "profile_id": pid, "api_key": "***aliz", "base_url": "https://p.example/v1", "model": "mp",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # 掩码被替换为档案真实密钥
    assert captured["api_key"] == "sk-profile-real"


def test_profile_test_connection_without_key_reports_missing(profile_env, monkeypatch):
    import backend.services.llm as llm_core

    pid = _make_profile("P", "https://p.example/v1", "mp", "")["id"]
    captured: dict = {}

    async def fake_test_connection(cfg):
        captured.update(cfg)
        return {"ok": False, "message": cfg.get("api_key", ""), "latency_ms": None, "request_id": None, "api_format": cfg.get("api_format")}

    monkeypatch.setattr(llm_core, "test_connection", fake_test_connection)

    client = TestClient(app)
    client.post("/api/analysis/test-connection", json={
        "type": "llm", "profile_id": pid, "base_url": "https://p.example/v1", "model": "mp",
    })
    # 档案没存密钥时置空串，明确缺失而不是误用全局密钥
    assert captured["api_key"] == ""


def test_ensure_default_migrates_existing_global_config(profile_env):
    """老用户升级：已配置的全局 llm 首次被收编为「使用中」档案。"""
    from backend.config import settings

    ai_config.save_config({
        **ai_config.load_config(),
        "llm": {**ai_config.DEFAULT_CONFIG["llm"], "provider": "deepseek", "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat", "api_key": "sk-legacy-real"},
    })
    resolved = ai_config.get_llm_config()
    assert resolved["api_key"] == "sk-legacy-real"

    state = ai_config.ensure_default_llm_profile()
    assert len(state["profiles"]) == 1
    assert state["active_id"] == state["profiles"][0]["id"]
    assert state["profiles"][0]["name"] == "DeepSeek"
    assert state["profiles"][0]["model"] == "deepseek-chat"
    assert state["profiles"][0]["api_key"].startswith("***")
    # 幂等：再次调用不重复创建
    again = ai_config.ensure_default_llm_profile()
    assert len(again["profiles"]) == 1
    assert again["profiles"][0]["id"] == state["profiles"][0]["id"]
    # 全局配置保持可用
    assert ai_config.get_llm_config()["api_key"] == "sk-legacy-real"
    assert settings.llm_api_key == ""  # fixture 生效，env 未泄漏


def test_ensure_default_skips_unconfigured_install(profile_env):
    """全新安装（未配置 key/端点）不生成空档案。"""
    state = ai_config.ensure_default_llm_profile()
    assert state == {"active_id": "", "profiles": []}

def test_get_config(client):
    r = client.get("/api/analysis/config")
    assert r.status_code == 200
    data = r.json()
    assert "translate" in data
    assert "analysis" in data
    assert "ui" in data
    assert "embedding" in data
    assert "research" in data


def test_save_and_get_config(client):
    r = client.get("/api/analysis/config")
    cfg = r.json()

    cfg["ui"]["highlight_duration"] = 5000
    r2 = client.post("/api/analysis/config", json=cfg)
    assert r2.status_code == 200

    r3 = client.get("/api/analysis/config")
    assert r3.json()["ui"]["highlight_duration"] == 5000

    cfg["ui"]["highlight_duration"] = 3000
    client.post("/api/analysis/config", json=cfg)


def test_get_builtin_prompt(client):
    r = client.get("/api/analysis/builtin-analysis-prompt")
    assert r.status_code == 200
    assert "content" in r.json()
    assert len(r.json()["content"]) > 0


def test_text_action_empty_text(client):
    r = client.post("/api/analysis/text-action", json={"text": "", "action": "translate"})
    assert r.status_code == 400


def test_text_action_invalid_action(client):
    r = client.post("/api/analysis/text-action", json={"text": "hello", "action": "invalid"})
    assert r.status_code == 422


def test_get_analysis_not_found(client):
    r = client.get("/api/analysis/99999")
    assert r.status_code == 404


def test_analysis_progress_not_found(client):
    r = client.get("/api/analysis/99999/progress")
    assert r.status_code == 404


def test_similarity_providers(client):
    r = client.get("/api/analysis/similarity/providers")
    assert r.status_code == 200
    assert "providers" in r.json()

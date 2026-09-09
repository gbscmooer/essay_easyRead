def test_get_config(client):
    r = client.get("/api/analysis/config")
    assert r.status_code == 200
    data = r.json()
    assert "llm" in data
    assert "translate" not in data
    assert "analysis" not in data
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


def test_relation_channels(client):
    r = client.get("/api/analysis/relations/channels")
    assert r.status_code == 200
    channels = r.json()["channels"]
    names = {c["name"] for c in channels}
    assert {"topic", "purpose", "method", "term", "bibcoupling"} <= names
    assert all("default_weight" in c and "group" in c for c in channels)


def test_export_bibtex_uses_analysis_metadata(client, session, create_paper):
    from backend.services.database import save_analysis_result, update_paper_metadata

    paper_id = create_paper("bibtex.pdf")
    update_paper_metadata(session, paper_id, "Fallback Title", "Fallback Venue", 2023, "Fallback Author")
    save_analysis_result(session, paper_id, {
        "title": "Graph Neural Networks",
        "authors": "Alice Smith, Bob Lee",
        "venue": "ICLR",
        "year": 2024,
        "one_sentence": "",
        "problem": "",
        "key_contributions": [],
        "outline": [],
        "method": {},
        "experiments": {},
        "code_mapping": [],
        "interview_qa": [],
        "limitations": "",
        "related_papers": [],
    })

    r = client.get(f"/api/analysis/{paper_id}/bibtex")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "@inproceedings" in r.text
    assert "title = {Graph Neural Networks}" in r.text
    assert "author = {Alice Smith and Bob Lee}" in r.text
    assert "booktitle = {ICLR}" in r.text
    assert "year = {2024}" in r.text

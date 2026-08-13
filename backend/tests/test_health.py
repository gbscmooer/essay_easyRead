def test_health_check(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "5.0.1"
    assert "running" in data["message"]

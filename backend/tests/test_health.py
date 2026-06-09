def test_health_check(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "1.8.1"
    assert "running" in data["message"]

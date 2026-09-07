from backend.main import APP_VERSION


def test_health_check(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == APP_VERSION
    assert client.get("/openapi.json").json()["info"]["version"] == APP_VERSION
    assert "running" in data["message"]

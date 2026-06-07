def test_list_workspaces_empty(client):
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_workspace(client):
    r = client.post("/api/workspaces", json={"name": "Test Workspace"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Workspace"
    assert data["paper_count"] == 0
    assert data["id"] > 0


def test_get_workspace(client):
    r = client.post("/api/workspaces", json={"name": "Get Test"})
    ws_id = r.json()["id"]

    r2 = client.get(f"/api/workspaces/{ws_id}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Get Test"


def test_get_workspace_not_found(client):
    r = client.get("/api/workspaces/99999")
    assert r.status_code == 404


def test_delete_workspace(client):
    r = client.post("/api/workspaces", json={"name": "Delete Me"})
    ws_id = r.json()["id"]

    r2 = client.delete(f"/api/workspaces/{ws_id}")
    assert r2.status_code == 200

    r3 = client.get(f"/api/workspaces/{ws_id}")
    assert r3.status_code == 404


def test_rename_workspace(client):
    r = client.post("/api/workspaces", json={"name": "Old Name"})
    ws_id = r.json()["id"]

    r2 = client.put(f"/api/workspaces/{ws_id}", json={"name": "New Name"})
    assert r2.status_code == 200
    assert r2.json()["name"] == "New Name"

import io


def test_upload_pdf(client):
    buf = io.BytesIO(b"%PDF-1.4 fake pdf content for testing")
    r = client.post(
        "/api/papers/upload",
        files={"file": ("test_paper.pdf", buf, "application/pdf")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["filename"] == "test_paper.pdf"
    assert data["status"] == "pending"
    assert data["id"] > 0


def test_upload_non_pdf_rejected(client):
    buf = io.BytesIO(b"not a pdf")
    r = client.post(
        "/api/papers/upload",
        files={"file": ("readme.txt", buf, "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_FORMAT"


def test_list_papers_empty(client):
    r = client.get("/api/papers")
    assert r.status_code == 200
    data = r.json()
    assert "papers" in data
    assert "total" in data


def test_get_paper_not_found(client):
    r = client.get("/api/papers/99999")
    assert r.status_code == 404


def test_upload_then_get(client):
    buf = io.BytesIO(b"%PDF-1.4 another test pdf")
    r = client.post(
        "/api/papers/upload",
        files={"file": ("get_test.pdf", buf, "application/pdf")},
    )
    paper_id = r.json()["id"]

    r2 = client.get(f"/api/papers/{paper_id}")
    assert r2.status_code == 200
    assert r2.json()["filename"] == "get_test.pdf"


def test_upload_then_delete(client):
    buf = io.BytesIO(b"%PDF-1.4 delete me")
    r = client.post(
        "/api/papers/upload",
        files={"file": ("del_test.pdf", buf, "application/pdf")},
    )
    paper_id = r.json()["id"]

    r2 = client.delete(f"/api/papers/{paper_id}")
    assert r2.status_code == 200
    assert "deleted" in r2.json()["message"]

    r3 = client.get(f"/api/papers/{paper_id}")
    assert r3.status_code == 404


def test_delete_not_found(client):
    r = client.delete("/api/papers/99999")
    assert r.status_code == 404


def test_list_papers_pagination(client):
    for i in range(3):
        buf = io.BytesIO(f"%PDF-1.4 page test {i}".encode())
        client.post(
            "/api/papers/upload",
            files={"file": (f"page_{i}.pdf", buf, "application/pdf")},
        )

    r = client.get("/api/papers?page=1&per_page=2")
    assert r.status_code == 200
    data = r.json()
    assert len(data["papers"]) <= 2
    assert data["total"] >= 3

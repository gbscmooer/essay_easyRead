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
    assert r.json()["code"] == "INVALID_FORMAT"
    assert r.json()["detail"] == "Only PDF files are accepted"


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


# ============================================================
# 排序与筛选值（论文库视图）
# ============================================================

def _seed_library(session):
    """造一批带年份 / 方向 / 标题的论文，供排序与 facets 断言。"""
    from backend.models.paper import Paper

    rows = [
        Paper(file_size=100, filename="b.pdf", pdf_path="/tmp/b.pdf", title="Beta", year=2019, category="cs.LG", source="uploaded"),
        Paper(file_size=100, filename="a.pdf", pdf_path="/tmp/a.pdf", title="Alpha", year=2025, category="cs.CV", source="uploaded"),
        Paper(file_size=100, filename="c.pdf", pdf_path="/tmp/c.pdf", title="Gamma", year=None, category=None, source="uploaded"),
        Paper(file_size=100, filename="x.pdf", pdf_path="/tmp/x.pdf", title="Expanded", year=2001, category="cs.AI", source="expanded"),
    ]
    for row in rows:
        session.add(row)
    session.commit()


def test_list_papers_sort_by_year(client, session):
    _seed_library(session)

    desc = client.get("/api/papers?sort=year_desc").json()["papers"]
    assert [p["year"] for p in desc] == [2025, 2019, None]

    asc = client.get("/api/papers?sort=year_asc").json()["papers"]
    # 空年份两个方向都排在最后，否则「按年份排序」的第一屏全是没有年份的论文
    assert [p["year"] for p in asc] == [2019, 2025, None]


def test_list_papers_sort_by_title(client, session):
    _seed_library(session)

    titles = [p["title"] for p in client.get("/api/papers?sort=title").json()["papers"]]
    assert titles == ["Alpha", "Beta", "Gamma"]


def test_list_papers_unknown_sort_falls_back(client, session):
    _seed_library(session)

    fallback = client.get("/api/papers?sort=bogus").json()["papers"]
    default = client.get("/api/papers").json()["papers"]
    assert [p["id"] for p in fallback] == [p["id"] for p in default]


def test_paper_facets_cover_whole_library(client, session):
    _seed_library(session)

    facets = client.get("/api/papers/facets").json()
    # 分页只影响列表本身；筛选值必须覆盖全库（这里一页只放得下两条）
    assert facets["categories"] == ["cs.CV", "cs.LG"]
    assert facets["years"] == [2025, 2019]
    assert len(facets["dates"]) >= 1
    assert [s["value"] for s in facets["sorts"]][0] == "recent"


def test_paper_facets_respect_source(client, session):
    _seed_library(session)

    uploaded = client.get("/api/papers/facets?source=uploaded").json()
    assert "cs.AI" not in uploaded["categories"]

    every = client.get("/api/papers/facets?source=all").json()
    assert "cs.AI" in every["categories"]


# ============================================================
# 智能搜索
# ============================================================

def _seed_searchable(session):
    from backend.models.paper import Paper

    rows = [
        Paper(
            file_size=100, filename="attention.pdf", pdf_path="/tmp/a.pdf",
            title="Attention Is All You Need", authors="Vaswani", venue="NeurIPS",
            year=2017, source="uploaded",
            full_text="line one\nthe transformer uses scaled dot-product attention\nline three",
        ),
        Paper(
            file_size=100, filename="resnet.pdf", pdf_path="/tmp/r.pdf",
            title="Deep Residual Learning", authors="He", venue="CVPR",
            year=2016, source="uploaded",
            full_text="residual connections mitigate degradation",
        ),
    ]
    for row in rows:
        session.add(row)
    session.commit()


def test_smart_search_matches_metadata(client, session):
    _seed_searchable(session)

    hits = client.get("/api/papers/smart-search?q=vaswani").json()
    assert hits["total"] == 1
    assert hits["results"][0]["title"] == "Attention Is All You Need"
    assert [m["field"] for m in hits["results"][0]["matched_fields"]] == ["authors"]


def test_smart_search_skips_fulltext_by_default(client, session):
    _seed_searchable(session)

    # "degradation" 只出现在正文里；不开全文就不该命中
    assert client.get("/api/papers/smart-search?q=degradation").json()["total"] == 0


def test_smart_search_fulltext_only_hit(client, session):
    _seed_searchable(session)

    hits = client.get("/api/papers/smart-search?q=degradation&global=true").json()
    assert hits["total"] == 1
    matched = hits["results"][0]["matched_fields"]
    assert [m["field"] for m in matched] == ["fulltext"]
    assert "degradation" in matched[0]["snippet"]
    assert matched[0]["line"] == 1


def test_smart_search_fulltext_reports_line_number(client, session):
    _seed_searchable(session)

    hits = client.get("/api/papers/smart-search?q=scaled dot-product&global=true").json()
    matched = hits["results"][0]["matched_fields"]
    assert matched[0]["field"] == "fulltext"
    assert matched[0]["line"] == 2


def test_smart_search_combines_metadata_and_fulltext(client, session):
    _seed_searchable(session)

    hits = client.get("/api/papers/smart-search?q=attention&global=true").json()
    fields = [m["field"] for m in hits["results"][0]["matched_fields"]]
    assert fields == ["title", "fulltext"]


def test_smart_search_respects_filters_on_fulltext(client, session):
    _seed_searchable(session)

    # 年份筛选必须同样作用在正文这一路查询上
    assert client.get("/api/papers/smart-search?q=degradation&global=true&year=2017").json()["total"] == 0
    assert client.get("/api/papers/smart-search?q=degradation&global=true&year=2016").json()["total"] == 1

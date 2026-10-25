"""arXiv 本地镜像：OAI-PMH 解析、断点续传与增量、FTS 字面检索、向量检索与召回接口。"""

import asyncio

import numpy as np

from backend.services.deep_research import arxiv_mirror as am


def _page(records, token=None, error=None):
    if error:
        return f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="{error}">x</error></OAI-PMH>'
    body = "".join(
        f"""<record><header{' status="deleted"' if r.get('deleted') else ''}>
        <identifier>oai:arXiv.org:{r['id']}</identifier><datestamp>{r['date']}</datestamp></header>
        <metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>{r['id']}</id>
        <authors><author><keyname>Lee</keyname><forenames>Ann</forenames></author></authors>
        <title>{r['title']}</title><categories>cs.RO</categories>
        <abstract>  {r['abstract']}  </abstract></arXiv></metadata></record>""" for r in records)
    tok = f"<resumptionToken>{token}</resumptionToken>" if token else "<resumptionToken/>"
    return f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>{body}{tok}</ListRecords></OAI-PMH>'


_P1 = [{"id": "2101.00001", "date": "2021-01-02", "title": "Narrow passage sampling for RRT planners",
        "abstract": "We bias samples toward narrow passages in configuration space."},
       {"id": "cs/9912001", "date": "2021-01-03", "title": "Old paper", "abstract": "Legacy identifiers."}]
_P2 = [{"id": "2102.00002", "date": "2021-02-01", "title": "Bridge test for motion planning",
        "abstract": "The bridge test finds narrow corridors."},
       {"id": "2102.00003", "date": "2021-02-02", "title": "Deleted", "abstract": "", "deleted": True}]


def test_year_from_id_handles_new_and_old_identifiers():
    assert am.year_from_id("2301.00001") == 2023 and am.year_from_id("cs/9912001") == 1999
    assert am.year_from_id("math.GT/0309136") == 2003 and am.year_from_id("weird") is None


def test_harvest_resumes_from_token_then_switches_to_incremental(tmp_path):
    conn = am.connect(tmp_path)
    calls = []
    pages = {None: (_P1, "tok1"), "tok1": (_P2, None)}

    def fetch(params):
        calls.append(dict(params))
        if len(calls) == 2:
            return 503, {"Retry-After": "7"}, ""
        recs, nxt = pages[params.get("resumptionToken")]
        return 200, {}, _page(recs, nxt)

    waits = []
    out = am.harvest(conn, "cs", fetch=fetch, sleep=waits.append, max_pages=1)
    assert out["complete"] is False and am._get_state(conn, "harvest:cs:token") == "tok1"
    out = am.harvest(conn, "cs", fetch=fetch, sleep=waits.append)  # 续传，先遇到 503
    assert out["complete"] is True and 7.0 in waits
    assert calls[-1] == {"verb": "ListRecords", "resumptionToken": "tok1"}
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3  # 已删除记录跳过
    row = conn.execute("SELECT authors, year, abstract FROM papers WHERE arxiv_id='cs/9912001'").fetchone()
    assert row == ("Ann Lee", 1999, "Legacy identifiers.")
    # 增量：从最新 datestamp 起列
    am.harvest(conn, "cs", fetch=lambda p: (calls.append(dict(p)) or (200, {}, _page([], None, "noRecordsMatch"))),
               sleep=waits.append)
    assert calls[-1]["from"] == "2021-02-01"


def test_expired_token_restarts_from_newest_datestamp(tmp_path):
    conn = am.connect(tmp_path)
    am._set_state(conn, "harvest:cs:token", "stale")
    am._set_state(conn, "harvest:cs:newest", "2021-01-03")
    calls = []

    def fetch(params):
        calls.append(dict(params))
        if "resumptionToken" in params:
            return 200, {}, _page([], None, "badResumptionToken")
        return 200, {}, _page(_P2, None)

    assert am.harvest(conn, "cs", fetch=fetch, sleep=lambda s: None)["complete"] is True
    assert calls[1] == {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": "cs", "from": "2021-01-03"}


def _fake_encoder(texts):
    vocab = ["narrow", "bridge", "legacy", "corridor"]
    vecs = np.array([[t.lower().count(w) + 0.01 for w in vocab] for t in texts], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def test_lexical_and_dense_search_and_reembed_on_change(tmp_path):
    conn = am.connect(tmp_path)
    am.upsert(conn, [{**r, "arxiv_id": r["id"], "authors": "", "categories": "cs.RO",
                      "year": am.year_from_id(r["id"]), "doi": None, "journal_ref": None, "datestamp": r["date"]}
                     for r in _P1 + _P2[:1]])
    conn.commit()
    assert am.embed(conn, tmp_path, encoder=_fake_encoder, batch=2, dim=4) == {"embedded": 3, "vectors": 3}
    mirror = am.ArxivMirror(tmp_path, encoder=_fake_encoder)
    am.EMBED_DIM, saved = 4, am.EMBED_DIM
    try:
        assert [r["arxiv_id"] for r in mirror.lexical("narrow passage planners", 5)][0] == "2101.00001"
        assert mirror.lexical("of the", 5) == []
        assert mirror.dense("corridor bridge", 1)[0]["arxiv_id"] == "2102.00002"
        # 摘要变化 → 清掉向量行号，重嵌入后旧行不再被返回
        am.upsert(conn, [{"arxiv_id": "2102.00002", "title": "Bridge test for motion planning",
                          "abstract": "Now about legacy systems.", "authors": "", "categories": "cs.RO",
                          "year": 2021, "doi": None, "journal_ref": None, "datestamp": "2021-03-01"}])
        conn.commit()
        assert am.embed(conn, tmp_path, encoder=_fake_encoder, dim=4)["vectors"] == 4
        fresh = am.ArxivMirror(tmp_path, encoder=_fake_encoder)
        hits = fresh.dense("legacy", 3)
        assert len({h["arxiv_id"] for h in hits}) == len(hits)
        cands = asyncio.run(fresh.as_search_one()("arxiv", "narrow passages", 2, "relevance"))
        assert cands[0].arxiv_id == "2101.00001" and "channel:lexical" in cands[0].provenance
        assert cands[0].abstract and len({c.arxiv_id for c in cands}) == len(cands)
    finally:
        am.EMBED_DIM = saved


def test_match_by_doi_or_exact_title(tmp_path):
    conn = am.connect(tmp_path)
    base = {"authors": "", "categories": "cs.NI", "year": 2020, "journal_ref": None, "datestamp": "2020-01-01"}
    am.upsert(conn, [
        {**base, "arxiv_id": "2001.00001", "title": "Link Outages and TCP:\n A Study", "abstract": "About outages.",
         "doi": "10.1109/X.1"},
        {**base, "arxiv_id": "2001.00002", "title": "Link Outages and TCP Revisited", "abstract": "Later work.",
         "doi": None}])
    conn.commit()
    rows = am.ArxivMirror(tmp_path).match([
        ("10.1109/x.1", "whatever"),                 # DOI 不区分大小写
        (None, "Link outages and TCP — a study"),    # 题名规范化后完全一致
        (None, "Link Outages and TCP"),              # 只是前缀：不算命中
    ])
    assert [r and r["arxiv_id"] for r in rows] == ["2001.00001", "2001.00001", None]


def test_network_errors_are_retried_with_backoff(tmp_path):
    conn = am.connect(tmp_path)
    calls, waits = [], []

    def fetch(params):
        calls.append(params)
        if len(calls) < 3:
            raise ConnectionError("SSL: UNEXPECTED_MESSAGE")
        return 200, {}, _page(_P1, None)

    assert am.harvest(conn, "cs", fetch=fetch, sleep=waits.append)["records"] == 2
    assert waits[:2] == [10.0, 20.0]


def test_mirror_takes_over_only_after_first_complete_harvest(tmp_path):
    conn = am.connect(tmp_path)
    am.harvest(conn, "cs", fetch=lambda p: (200, {}, _page(_P1, "more")), sleep=lambda s: None, max_pages=1)
    assert am.ArxivMirror.available(tmp_path) is False  # 抓取进行中
    am.harvest(conn, "cs", fetch=lambda p: (200, {}, _page(_P2, None)), sleep=lambda s: None)
    assert am.ArxivMirror.available(tmp_path) is True
    assert am.ArxivMirror(tmp_path).dense_ready() is False  # 还没嵌入：只用字面通道

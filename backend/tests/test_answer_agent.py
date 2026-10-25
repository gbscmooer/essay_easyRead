"""指针式作答：证据库（BM25/grep/read/find）、指针还原为逐字摘录、起草→检索→定稿全流程。"""

import asyncio

import pytest

from backend.services.deep_research import answer_agent as ag
from backend.services.deep_research import evidence_search as es


@pytest.fixture(autouse=True)
def _short_texts_ok(monkeypatch):
    """用例里的草稿与定稿都很短：关掉截断保护（另有专门用例测它）。"""
    monkeypatch.setattr(ag, "MIN_DRAFT_WORDS", 0)
    monkeypatch.setattr(ag, "SHORT_FINAL_RATIO", 0)

_PAPERS = [
    {"key": "a", "title": "TypeEvalPy: A Micro-benchmarking Framework for Python Type Inference Tools", "year": 2024,
     "abstract": "We present TypeEvalPy, a micro-benchmarking framework for evaluating type inference tools. "
                 "TypeEvalPy contains 154 code snippets with 845 type annotations across 18 categories. "
                 "It standardizes the evaluation of type inference across tools."},
    {"key": "b", "title": "ManyTypes4Py: A Benchmark Python Dataset for Machine Learning-based Type Inference",
     "year": 2021,
     "abstract": "ManyTypes4Py is a large Python dataset for machine learning based type inference. "
                 "The dataset contains 5,382 Python projects with more than 869K type annotations. "
                 "Duplicate source files were removed to eliminate the negative effect of duplication."},
    {"key": "c", "title": "Untitled", "year": 2020, "abstract": ""},   # 无可用句子：不收入
]


def _no_rerank(monkeypatch):
    async def none(query, documents):
        return None
    monkeypatch.setattr(es, "rerank_scores", none)


def test_index_bm25_grep_and_read():
    idx = es.EvidenceIndex()
    assert [idx.add_paper(p) for p in _PAPERS] == [3, 3, 0]
    assert idx.add_paper({**_PAPERS[0], "key": "dup"}) == 0          # 同题名只收一次
    top = idx.bm25("how many projects and type annotations does ManyTypes4Py have", 2)
    assert top[0].key == "b" and "5,382 Python projects" in top[0].text
    assert [p.pid for p in idx.grep("154 code snippets")] == ["S2"]
    ctx = idx.read("S2")
    assert [p.pid for p in ctx] == ["S1", "S2", "S3"]
    assert idx.get("S99") is None and idx.get("x") is None


def test_find_adds_entity_matches_beyond_bm25(monkeypatch):
    _no_rerank(monkeypatch)
    idx = es.EvidenceIndex()
    for p in _PAPERS:
        idx.add_paper(p)
    hits = asyncio.run(idx.find("The benchmark has 869K annotations", k=10))
    assert any("869K type annotations" in p.text for p in hits)
    assert es.entities("TypeEvalPy covers 154 snippets; GPT-4 scored 85.3% in 2023") == ["TypeEvalPy", "GPT-4", "85.3%"]


def test_same_title_tolerates_missing_subtitle_but_not_other_papers():
    assert es._same_title("TypeEvalPy", "TypeEvalPy: A Micro-benchmarking Framework")      # 冒号前的系统名一致
    assert not es._same_title("Deep Learning", "Deep Learning: A Survey of Methods")         # 泛化前缀不算
    assert es._same_title("ManyTypes4Py: A Benchmark Python Dataset",
                          "ManyTypes4Py: A Benchmark Python Dataset for Machine Learning-based Type Inference")
    assert not es._same_title("Deep learning for type inference", "Type inference for deep learning frameworks")


def test_render_pointers_maps_to_numbers_and_verbatim_excerpts():
    idx = es.EvidenceIndex()
    for p in _PAPERS:
        idx.add_paper(p)
    text = ("ManyTypes4Py has 869K annotations [S5]. TypeEvalPy has 154 snippets [S2, S1]. "
            "Both are public [S5][S2][S77].")
    out, refs, invalid = ag.render_pointers(text, idx)
    assert out == ("ManyTypes4Py has 869K annotations [1]. TypeEvalPy has 154 snippets [2]. "
                   "Both are public [1][2].")
    assert invalid == ["S77"]
    assert [r["key"] for r in refs] == ["b", "a"]
    assert refs[1]["excerpts"][0].startswith("TypeEvalPy contains 154 code snippets")   # 原文逐字
    assert refs[0]["excerpt_source"] == "abstract"


class _FakeMirror:
    """match 只认精确题名；lexical 返回近似题名；as_search_one 返回一条检索结果。"""

    import threading
    _lock = threading.Lock()

    def match(self, items):
        return [None for _ in items]

    def lexical(self, query, limit):
        return [{"arxiv_id": "2401.00001", "title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference "
                 "for Python", "abstract": "Type4Py is a deep similarity learning model for type inference. "
                 "It achieves an MRR of 77.1% on the ManyTypes4Py dataset.", "year": 2022, "authors": "A", "doi": None}]

    def as_search_one(self):
        from backend.models.recall import RecallSource, build_recall_candidate

        async def search_one(source, keyword, limit, order):
            cand = build_recall_candidate(
                title="Static type inference for Python with PyType", arxiv_id="2402.00002", year=2023,
                abstract="We evaluate static type inference tools such as Pyre and PyType on real projects.",
                source=RecallSource.SEARCH, provenance=["channel:lexical"])
            return [cand]
        return search_one


def test_write_with_pointers_end_to_end(monkeypatch):
    _no_rerank(monkeypatch)
    calls = []

    async def llm_json(system, user):
        calls.append(user)
        if "## 任务" in user and "draft" in user:
            return {"draft": "**Datasets.** ManyTypes4Py is a large dataset with 869K annotations. "
                             "TypeEvalPy is a micro-benchmark with 154 snippets. Type4Py reaches an MRR of 77.1%.\n\n"
                             "**Limitations.** Type inference for Python remains hard because of dynamic features.",
                    "works": [{"title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference", "year": 2022}],
                    "queries": ["static type inference tools python evaluation"]}
        assert "[S" in user and "候选证据" in user
        return {"answer": "**Datasets.** ManyTypes4Py contains 5,382 projects with 869K annotations [S5]. "
                          "TypeEvalPy has 154 snippets across 18 categories [S2]. "
                          "Type4Py achieves an MRR of 77.1% on ManyTypes4Py [S8].\n\n"
                          "**Limitations.** Python's dynamic features make type inference hard."}

    out = asyncio.run(ag.write_with_pointers("What datasets evaluate Python type inference?", _PAPERS,
                                             llm_json=llm_json, mirror=_FakeMirror()))
    assert out["status"] == "ok" and out["writer"] == "agent"
    assert out["agent"]["works_found"] == 1 and out["agent"]["search_added"] == 1
    assert {r["origin"] for r in out["references"]} == {"recall", "named_work"}
    assert "[1]" in out["text"] and "[S" not in out["text"]
    assert out["check"]["cited_share"] == 0.75 and len(calls) == 2
    t4 = next(r for r in out["references"] if r["origin"] == "named_work")
    assert "77.1%" in t4["excerpt"]


def test_low_citation_share_triggers_one_rewrite_with_pointer_feedback(monkeypatch):
    _no_rerank(monkeypatch)
    answers = iter([
        {"answer": "ManyTypes4Py is large. TypeEvalPy is small. Both are popular. They differ in scope [S2]."},
        {"answer": "ManyTypes4Py is large [S5]. TypeEvalPy is small [S2]. They differ in scope [S2]."},
    ])
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        if len(prompts) == 1:
            return {"draft": "ManyTypes4Py is large. TypeEvalPy is small.", "works": [], "queries": []}
        return next(answers)

    out = asyncio.run(ag.write_with_pointers("Which Python type inference datasets exist?", _PAPERS,
                                             llm_json=llm_json, mirror=None))
    assert out["attempts"] == 2 and out["status"] == "ok"
    assert "没有指针" in prompts[2] and "上一稿（供修改）" in prompts[2]


def test_full_text_upgrade_keeps_pointers_and_prefers_named_works():
    idx = es.EvidenceIndex()
    idx.add_paper({**_PAPERS[0], "arxiv_id": "2312.0001"}, origin="recall")
    idx.add_paper({"key": "t4", "title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference", "year": 2022,
                   "arxiv_id": "2101.0001", "abstract": "Type4Py is a deep similarity learning model for type inference."},
                  origin="named_work")
    before = idx.get("S1").text
    fetched = []

    async def fake_fetch(arxiv_id):
        fetched.append(arxiv_id)
        return ("Type4Py is a deep similarity learning model for type inference.\n\n"
                "On the ManyTypes4Py dataset, Type4Py achieves an MRR of 77.1% for all types.\n") * 1

    out = asyncio.run(ag._fetch_full_texts(idx, 1, fake_fetch))
    assert fetched == ["2101.0001"] and out == {"tried": 1, "ok": 1, "sentences": 1}   # 点名代表作优先；重复句不再收
    assert idx.get("S1").text == before                                                  # 已有指针不变
    new = idx.passages[-1]
    assert new.source == "full_text" and "77.1%" in new.text and idx.papers["t4"]["source"] == "full_text"
    assert [p.pid for p in idx.read(new.pid, before=1, after=0)] == [f"S{len(idx) - 1}", new.pid]


def test_attach_mode_pure_draft_and_claim_search(monkeypatch):
    _no_rerank(monkeypatch)
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        if len(prompts) == 1:
            return {"draft": "Static checkers such as Pyre and PyType are evaluated on real projects. "
                             "Type inference remains hard for Python.", "works": [], "queries": []}
        return {"answer": "Static checkers such as Pyre and PyType are evaluated on real projects [S7]. "
                          "Type inference remains hard for Python."}

    out = asyncio.run(ag.write_with_pointers("How are Python type checkers evaluated?", _PAPERS, llm_json=llm_json,
                                             mirror=_FakeMirror(), draft_titles=0, claim_search=1,
                                             final_mode="attach"))
    assert "已检索到的部分文献" not in prompts[0]                  # 纯凭知识起草，不展示召回题名
    assert out["agent"]["claim_search_added"] == 1                # 草稿逐句到镜像检索补来论文
    assert "以草稿为准" in prompts[1] and out["attempts"] == 1        # 引用率 0.5 达到 attach 模式下限，不重写
    assert out["references"][0]["origin"] == "claim_search" and out["draft"].startswith("Static checkers")


def test_inline_quotes_must_be_verbatim_or_lose_quote_marks():
    idx = es.EvidenceIndex()
    for p in _PAPERS:
        idx.add_paper(p)
    text = ('ManyTypes4Py "contains 5,382 Python projects with more than 869K type annotations" [1]. '
            'TypeEvalPy is described as "a benchmark suite with thousands of real-world programs and labels" [2]. '
            'It is "short quote" here.')
    out, audit = ag.verify_inline_quotes(text, idx)
    assert '"contains 5,382 Python projects with more than 869K type annotations"' in out     # 逐字可查：保留
    assert "a benchmark suite with thousands" in out and '"a benchmark suite' not in out          # 查不到：去引号
    assert '"short quote"' in out and audit == {"verified": 1, "unverified_stripped": 1}           # 太短不处理


def test_truncated_draft_is_requested_again_and_short_final_gets_feedback(monkeypatch):
    _no_rerank(monkeypatch)
    monkeypatch.setattr(ag, "MIN_DRAFT_WORDS", 30)
    monkeypatch.setattr(ag, "SHORT_FINAL_RATIO", 0.4)
    long_draft = " ".join(["ManyTypes4Py is a large dataset for type inference."] * 8)
    replies = iter([
        {"draft": "ManyTypes4Py is", "works": [], "queries": []},           # 截断的草稿
        {"draft": long_draft, "works": [], "queries": []},
        {"answer": "ManyTypes4Py is large [S5]."},                          # 定稿过短
        {"answer": " ".join(["ManyTypes4Py is a large dataset for type inference [S4]."] * 8)},
    ])
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        return next(replies)

    out = asyncio.run(ag.write_with_pointers("Which datasets?", _PAPERS, llm_json=llm_json, mirror=None))
    assert out["agent"]["draft_tries"] == 2 and out["attempts"] == 2
    assert "明显短于草稿" in prompts[3]


def test_followup_round_searches_for_weakest_claims(monkeypatch):
    _no_rerank(monkeypatch)
    prompts = []

    async def llm_json(system, user):
        prompts.append((system, user))
        if len(prompts) == 1:
            return {"draft": "Type4Py reaches an MRR of 77.1% on ManyTypes4Py. ManyTypes4Py has 869K annotations.",
                    "works": [], "queries": []}
        if system == ag._FOLLOWUP_SYSTEM:
            return {"searches": [{"claim": 1, "query": "Type4Py deep similarity type inference MRR",
                                  "title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference for Python"}]}
        return {"answer": "Type4Py reaches an MRR of 77.1% on ManyTypes4Py [S8]. ManyTypes4Py has 869K annotations [S5]."}

    out = asyncio.run(ag.write_with_pointers("How good is Type4Py?", _PAPERS, llm_json=llm_json, mirror=_FakeMirror(),
                                             followup=1, final_mode="attach"))
    fu = out["agent"]["followup"]
    assert fu["claims"] == 1 and fu["titles"] == 1 and fu["added"] >= 1
    assert "还没有找到支撑它的论文原文" in prompts[1][1]
    assert any(r["origin"] == "followup" for r in out["references"])


def test_enrich_mode_prompt_allows_evidence_backed_additions():
    idx = es.EvidenceIndex()
    for p in _PAPERS:
        idx.add_paper(p)
    evidence = [("ManyTypes4Py is large.", [idx.get("S4")])]
    prompt = ag._write_prompt("q", "ManyTypes4Py is large.", evidence, "en", mode="enrich")
    assert "草稿没有写到的重要发现" in prompt and "每段最多补 3 句" in prompt and "不要删除草稿内容" in prompt
    assert "[S4]" in prompt and "候选证据" in prompt

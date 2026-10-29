"""指针式作答：证据库（BM25/grep/read/find）、指针还原为逐字摘录、起草→检索→定稿全流程。"""

import asyncio
import os
import re

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


class _FakeWeb:
    """在线通道：as_search_one 返回一篇期刊论文；lookup_titles 只认镜像里找不到的点名论文。"""

    def __init__(self):
        self.looked_up = []

    def as_search_one(self):
        from backend.models.recall import RecallSource, build_recall_candidate

        async def search_one(source, keyword, limit, order):
            return [build_recall_candidate(
                title="Nosocomial infections during extracorporeal membrane oxygenation", doi="10.1016/x.9", year=2022,
                abstract="Nosocomial infection occurs in a large share of adults supported by extracorporeal membrane "
                         "oxygenation.", source=RecallSource.SEARCH, provenance=["channel:scopus"])]
        return search_one

    async def lookup_titles(self, titles):
        self.looked_up += titles
        return [{"key": "doi:10.1097/x", "title": t, "year": 2006, "doi": "10.1097/x", "requested_title": t,
                 "source_query": "method:title_lookup",
                 "abstract": "This meta-analysis of 32 studies reports elevated prostate cancer risk among firefighters."}
                for t in titles if "firefighters" in t]


def test_web_search_channel_nominates_alongside_mirror_and_finds_named_journal_papers(monkeypatch):
    _no_rerank(monkeypatch)

    async def llm_json(system, user):
        if "## 任务" in user and "draft" in user:
            return {"draft": "**Evidence.** Firefighters show an elevated prostate cancer risk in meta-analyses. "
                             "Type inference tools such as PyType are evaluated on real projects.",
                    "works": [{"title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference"},
                              {"title": "Cancer risk among firefighters: a review and meta-analysis of 32 studies"}],
                    "queries": ["firefighter prostate cancer meta-analysis"]}
        return {"answer": "**Evidence.** Firefighters show an elevated prostate cancer risk [S1]."}

    web = _FakeWeb()
    out = asyncio.run(ag.write_with_pointers("Which occupations raise prostate cancer risk?", [], llm_json=llm_json,
                                             mirror=_FakeMirror(), web_search=web, claim_search=1))
    assert web.looked_up == ["Cancer risk among firefighters: a review and meta-analysis of 32 studies"]   # 镜像找到的不再查
    assert out["agent"]["works_found"] == 2 and out["agent"]["works_web_found"] == 1
    assert out["agent"]["search_added"] == 2      # 镜像与在线通道各提名一篇


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


def test_full_text_sources_default_arxiv_only_and_multi_source_uses_doi(monkeypatch):
    """默认只给带 arXiv 编号的论文补全文；开启 pmc/elsevier 后只有 DOI 的期刊论文也按顺序试各来源。"""
    body = "Firefighting is associated with prostate cancer risk in several cohorts.\n\n" * 40
    calls = []

    async def fake_pmc(doi):
        calls.append(("pmc", doi))
        return ""

    async def fake_els(doi):
        calls.append(("elsevier", doi))
        return body

    monkeypatch.setattr(es, "fetch_pmc_full_text", fake_pmc)
    monkeypatch.setattr(es, "fetch_elsevier_full_text", fake_els)

    def index():
        idx = es.EvidenceIndex()
        idx.add_paper({"key": "j1", "title": "Cancer risk among firefighters", "year": 2006, "doi": "10.1016/x.1",
                       "abstract": "This meta-analysis of 32 studies reports elevated prostate cancer risk among firefighters."}, origin="recall")
        return idx

    async def no_arxiv(arxiv_id):
        raise AssertionError("不应请求 arXiv")

    out = asyncio.run(ag._fetch_full_texts(index(), 3, no_arxiv))
    assert out["tried"] == 0 and calls == []
    idx = index()
    out = asyncio.run(ag._fetch_full_texts(idx, 3, no_arxiv, ("arxiv", "pmc", "elsevier")))
    assert calls == [("pmc", "10.1016/x.1"), ("elsevier", "10.1016/x.1")]
    assert out["ok"] == 1 and out["by_source"] == {"elsevier": 1} and idx.papers["j1"]["source"] == "full_text"


def test_full_text_rerank_order_lets_relevant_recall_papers_in(monkeypatch):
    """order="rerank"：名额按与问题的相关度分配，不再先给镜像检索到的论文；重排不可用时退回来源先后。"""
    def index():
        idx = es.EvidenceIndex()
        idx.add_paper({"key": "cs1", "title": "Imitation learning for clinical decision support", "arxiv_id": "2601.0001",
                       "abstract": "We train a reinforcement learning agent to imitate clinicians in the intensive care unit."},
                      origin="question_search")
        idx.add_paper({"key": "med", "title": "Nosocomial infections during extracorporeal membrane oxygenation",
                       "doi": "10.1016/x.2",
                       "abstract": "Nosocomial infection affects many adults supported by extracorporeal membrane oxygenation."},
                      origin="recall")
        return idx

    fetched = []

    async def fake_any(meta, sources, arxiv_fetch=None):
        fetched.append(meta["key"])
        return "", ""

    async def fake_rerank(query, docs):
        return [0.9 if "Nosocomial" in d else 0.1 for d in docs]

    monkeypatch.setattr(ag, "fetch_full_text_any", fake_any)
    monkeypatch.setattr(ag, "rerank_scores", fake_rerank)
    srcs = ("arxiv", "pmc", "elsevier")
    asyncio.run(ag._fetch_full_texts(index(), 1, None, srcs, question="ECMO infection risk", order="origin"))
    asyncio.run(ag._fetch_full_texts(index(), 1, None, srcs, question="ECMO infection risk", order="rerank"))

    async def no_rerank(query, docs):
        return None

    monkeypatch.setattr(ag, "rerank_scores", no_rerank)
    asyncio.run(ag._fetch_full_texts(index(), 1, None, srcs, question="ECMO infection risk", order="rerank"))
    assert fetched == ["cs1", "med", "cs1"]


def test_publisher_xml_parsers_keep_body_only():
    jats = ("<article><front><abstract><p>Abstract text.</p></abstract></front><body><sec><title>Introduction</title>"
            "<p>ECMO raises <italic>infection</italic> risk.</p></sec></body><back><ref-list><p>Ref</p></ref-list></back></article>")
    assert es.jats_body_text(jats) == "Introduction\n\nECMO raises infection risk."
    ns = 'xmlns:ce="http://www.elsevier.com/xml/common/dtd"'
    els = (f'<full-text-retrieval-response {ns}><ce:abstract><ce:para>Abstract.</ce:para></ce:abstract>'
           "<body><ce:sections><ce:section><ce:section-title>Methods</ce:section-title>"
           "<ce:para>We enrolled 40 patients.</ce:para></ce:section></ce:sections></body></full-text-retrieval-response>")
    assert es.elsevier_body_text(els) == "Methods\n\nWe enrolled 40 patients."
    assert es.elsevier_body_text(f"<r {ns}><ce:abstract><ce:para>Only abstract.</ce:para></ce:abstract></r>") == ""


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
    assert fetched == ["2101.0001"] and out == {"tried": 1, "ok": 1, "sentences": 1,   # 点名代表作优先；重复句不再收
                                                 "by_source": {"arxiv": 1}}
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


def test_same_paper_from_recall_and_mirror_is_indexed_once():
    idx = es.EvidenceIndex()
    assert idx.add_paper({"key": "doi:10.1/x", "doi": "10.1/X", "arxiv_id": "2101.00001", "title": "Type4Py",
                          "abstract": "Type4Py is a deep similarity learning model for type inference."})
    assert idx.add_paper({"key": "arxiv:2101.00001", "arxiv_id": "2101.00001", "title": "Type4Py: Practical",
                          "abstract": "Type4Py is a deep similarity learning model for type inference."}) == 0


def _no_rerank_anywhere(monkeypatch):
    async def none(query, documents):
        return None
    monkeypatch.setattr(es, "rerank_scores", none)
    monkeypatch.setattr(ag, "rerank_scores", none)


def test_reading_shows_question_relevant_passages_beyond_claim_candidates(monkeypatch):
    """原文阅读：按问题检索到、但不在逐句候选里的原文句交给模型补充；模型引用了几句记入审计。"""
    _no_rerank_anywhere(monkeypatch)
    prompts = []
    papers = _PAPERS[:2] + [{
        "key": "d", "title": "Typilus: Neural Type Hints", "year": 2020,
        "abstract": "Typilus is accompanied by a dataset that contains 600 Python projects. "
                    "Raychev et al. published the Python-150K dataset, which contains 8,422 Python projects."}]

    async def llm_json(system, user):
        prompts.append(user)
        if len(prompts) == 1:
            return {"draft": "**Datasets.** TypeEvalPy is a micro-benchmark with 154 snippets.", "works": [],
                    "queries": ["Python type inference dataset projects"]}
        return {"answer": "**Datasets.** TypeEvalPy is a micro-benchmark with 154 snippets [S2]. "
                          "Python-150K contains 8,422 Python projects [S8]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets are used to evaluate Python type inference?", papers,
                                             llm_json=llm_json, mirror=None, final_mode="attach", reading=4))
    write_prompt = prompts[1]
    assert "## 原文阅读" in write_prompt and "补充（原文阅读）" in write_prompt
    reading_block = write_prompt.split("## 原文阅读", 1)[1].split("## 写作要求", 1)[0]
    candidates = write_prompt.split("## 草稿各句的候选证据", 1)[1].split("## 原文阅读", 1)[0]
    shown = {pid for pid in __import__("re").findall(r"\[(S\d+)\]", reading_block)}
    assert shown and not shown & set(__import__("re").findall(r"S\d+", candidates))   # 不重复逐句候选
    assert "S8" in shown and out["agent"]["reading"]["cited"] == 1 and out["agent"]["reading"]["shown"] == 4


def test_question_search_adds_papers_and_full_text_alternates_with_named_works(monkeypatch):
    _no_rerank_anywhere(monkeypatch)
    fetched = []

    async def fake_fetch(arxiv_id):
        fetched.append(arxiv_id)
        return ""

    async def llm_json(system, user):
        if "## 任务" in user:
            return {"draft": "**Tools.** Type4Py reaches an MRR of 77.1%.",
                    "works": [{"title": "Type4Py: Practical Deep Similarity Learning-Based Type Inference"}],
                    "queries": ["type inference tools"]}
        return {"answer": "**Tools.** Type4Py reaches an MRR of 77.1% [S1]."}

    out = asyncio.run(ag.write_with_pointers("How are Python type checkers evaluated?", [], llm_json=llm_json,
                                             mirror=_FakeMirror(), final_mode="attach", question_search=2,
                                             fetch_full_text=2, fetch=fake_fetch))
    assert out["agent"]["question_search_added"] == 1 and out["agent"]["search_added"] == 0   # 同一篇只收一次
    assert fetched == ["2401.00001", "2402.00002"]           # 点名代表作与按问题检索的论文轮流补全文


_BODY = [f"Section {i} explains how the {w} programs were compiled with the Microsoft C compiler and labeled with types."
         for i, w in enumerate(["open-source", "synthetic", "instrumented", "balanced", "real", "generated"])]
_TABLE_TEXT = ("\n".join(_BODY[:3]) + "\nProject\nFunctions\nLoC\narcadia\n121\n3,590\nbgrep\n5\n252\njansson\n176\n7,020\n"
               "a https://github.com/kimtg/arcadia\nTable 3: Open-source C projects used.\n"
               "Table 3 shows the different open source C projects we used to create the dataset of decompiled code.\n"
               + "\n".join(_BODY[3:]) + "\n")


def _table_index():
    idx = es.EvidenceIndex(tables=True)
    idx.add_paper({"key": "e", "title": "Improving type information inferred by decompilers", "year": 2021,
                   "abstract": "We improve the type information inferred by decompilers with machine learning."})
    idx.add_full_text("e", _TABLE_TEXT)
    return idx


def test_full_text_tables_become_passages_linked_from_mentions():
    """表格在切句时会被当作碎片丢掉：按表题取一侧的短行拼成表格块，正文提到 Table N 时能找到它。"""
    blocks = es.table_blocks(_TABLE_TEXT)
    assert [no for no, _ in blocks] == ["3"]
    assert blocks[0][1].startswith("Table 3: Open-source C projects used. — Project | Functions")
    assert "arcadia | 121 | 3,590" in blocks[0][1] and "https://github.com/kimtg/arcadia" in blocks[0][1]
    idx = _table_index()
    table = [p for p in idx.passages if p.table]
    mention = next(p for p in idx.passages if p.text.startswith("Table 3 shows"))
    assert len(table) == 1 and table[0].table == "3" and idx.tables_mentioned(mention) == table
    assert idx.tables_mentioned(table[0]) == []
    plain = es.EvidenceIndex()                       # 默认不收表格：现行作答的证据库与之前一致
    plain.add_paper({"key": "e", "title": "x paper", "abstract": "We improve the type information of decompilers."})
    plain.add_full_text("e", _TABLE_TEXT)
    assert not any(p.table for p in plain.passages)


def test_paper_reading_model_picks_passages_whole_tables_and_falls_back_to_rerank(monkeypatch):
    idx = _table_index()

    async def scores(query, documents):
        return [float(i % 3) for i in range(len(documents))]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    table = next(p for p in idx.passages if p.table)
    sent = next(p for p in idx.passages if "instrumented programs" in p.text)
    prompts = []

    async def reader(system, user):
        prompts.append((system, user))
        return {"picks": [{"pid": table.pid, "note": "Projects used: arcadia, bgrep, jansson."},
                          {"pid": sent.pid, "note": "Instrumented programs compiled with MSVC."},
                          {"pid": "S999", "note": "not shown to the reader"}]}

    out, stats, notes, objects = asyncio.run(ag._read_papers(idx, "Which datasets?", 6, asyncio.Semaphore(2), reader))
    assert prompts[0][0] == ag._READ_SYSTEM and "### 表格" in prompts[0][1] and f"[{table.pid}]" in prompts[0][1]
    assert {p.pid for p in out} == {table.pid, sent.pid}
    assert set(notes) == {table.pid, sent.pid} and stats["picked"] == 2 and stats["tables"] == 1

    async def broken(system, user):
        raise RuntimeError("model down")

    out, stats, notes, objects = asyncio.run(ag._read_papers(idx, "Which datasets?", 1, asyncio.Semaphore(2), broken))
    assert stats["fallback"] == 1 and not notes and 1 <= len(out) <= ag.PAPER_WINDOW + ag.PAPER_TABLE_LINKS


def test_paper_reading_reads_only_the_most_relevant_papers_when_many_have_full_text(monkeypatch):
    """库内作答时全文论文很多：只精读按题名与摘要和问题最相关的前几篇。"""
    idx = es.EvidenceIndex()
    for i, topic in enumerate(["spurious correlations in Waterbirds", "graph neural networks", "spurious features in CelebA"]):
        idx.add_paper({"key": f"lib:{i}", "title": f"Paper about {topic}", "abstract": f"We study {topic}.",
                       "source_text": (f"We study {topic} in detail with many experiments and analyses. " * 40)})

    async def scores(query, documents):
        return [float("spurious" in d) for d in documents]
    monkeypatch.setattr(ag, "rerank_scores", scores)

    async def reader(system, user):
        return {"picks": []}

    _, stats, _, _ = asyncio.run(ag._read_papers(idx, "Which datasets test spurious correlations?", 2,
                                              asyncio.Semaphore(2), reader, max_papers=2))
    assert stats["candidates"] == 3 and stats["papers"] == 2 and set(stats["selected"]) == {"lib:0", "lib:2"}


def test_write_with_pointers_paper_reading_records_shown_and_cited(monkeypatch):
    """精读的原文交给定稿；审计记录送到模型面前的指针与定稿引用的指针，区分“没送到”与“送到没用”。"""
    _no_rerank(monkeypatch)

    async def scores(query, documents):
        return [1.0 if "Table 3" in d else 0.0 for d in documents]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    paper = {"key": "e", "title": "Improving type information inferred by decompilers", "year": 2021,
             "source_text": _TABLE_TEXT * 6}
    calls = []

    async def llm_json(system, user):
        calls.append(system)
        if system == ag._DRAFT_SYSTEM:
            return {"draft": "**Datasets.** Decompiler studies compile open-source C programs.", "works": [],
                    "queries": []}
        if system == ag._READ_SYSTEM:
            pid = __import__("re").search(r"\[(S\d+)\] Table 3: Open-source", user).group(1)
            return {"picks": [{"pid": pid, "note": "arcadia, bgrep and jansson"}]}
        pid = __import__("re").search(r"\[(S\d+)\] \"Table 3: Open-source", user).group(1)
        return {"answer": f"**Datasets.** Decompiler studies compile open-source C programs such as arcadia [{pid}]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets are used?", [paper], llm_json=llm_json, mirror=None,
                                             final_mode="attach", paper_reading=2))
    pr, shown = out["agent"]["paper_reading"], out["agent"]["shown"]
    assert calls.count(ag._READ_SYSTEM) == 1 and pr["picked"] == 1 and pr["cited"] == 1
    assert shown["paper_reading"] and set(shown["paper_reading"]) & set(out["agent"]["cited_pids"])
    assert "arcadia" in out["references"][0]["excerpts"][0]


def test_inventory_of_asked_objects_is_listed_and_missing_ones_trigger_one_rewrite(monkeypatch):
    """精读标出问题所问的对象（如数据集）→ 清单交给定稿；定稿漏写的对象由程序核对后要求补上（整篇重写方式）。"""
    _no_rerank(monkeypatch)

    async def scores(query, documents):
        return [1.0 if "Table 3" in d else 0.5 for d in documents]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    paper = {"key": "e", "title": "Improving type information inferred by decompilers", "year": 2021,
             "source_text": _TABLE_TEXT * 6}
    prompts = []

    async def llm_json(system, user):
        if system == ag._DRAFT_SYSTEM:
            return {"draft": "**Datasets.** Studies compile open-source C programs.", "works": [], "queries": []}
        if system == ag._READ_SYSTEM:
            pid = __import__("re").search(r"\[(S\d+)\] Table 3: Open-source", user).group(1)
            return {"picks": [{"pid": pid, "note": "projects"}],
                    "objects": [{"name": n, "pids": [pid]} for n in ("arcadia", "jansson", "bgrep")]}
        prompts.append(user)
        pid = __import__("re").search(r"\[(S\d+)\] \"Table 3: Open-source", user).group(1)
        if len(prompts) == 1:
            return {"answer": f"**Datasets.** Studies compile open-source C programs such as arcadia and jansson [{pid}]."}
        return {"answer": f"**Datasets.** Studies compile open-source C programs such as arcadia, jansson and bgrep [{pid}]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets are used?", [paper], llm_json=llm_json, mirror=None,
                                             final_mode="attach", paper_reading=2, missing_fix="rewrite",
                                             paper_use="cover"))
    inv = out["agent"]["inventory"]
    assert set(inv["items"]) == {"arcadia", "jansson", "bgrep"} and "## 问题所问对象清单" in prompts[0]
    assert inv["missing_by_attempt"] == [["bgrep"], []] and "对象清单里有 1 个没写到：bgrep" in prompts[1]
    assert out["agent"]["write_problems"] == [["missing_items"], []]
    assert out["attempts"] == 2 and "bgrep" in out["text"]
    assert ag._missing_items("We use CivilComments and Colored MNIST.",
                             [{"name": "CivilComments-WILDS"}, {"name": "Colored MNIST"}, {"name": "MNIST-CIFAR"}]) \
        == ["CivilComments-WILDS", "MNIST-CIFAR"]


def test_missing_inventory_items_are_supplemented_into_paragraphs_without_rewrite(monkeypatch):
    """定稿只差清单里的对象时：只把漏写的对象和出处原文交给模型补句，接回指定段落，不整篇重写。"""
    _no_rerank(monkeypatch)

    async def scores(query, documents):
        return [1.0 if "Table 3" in d else 0.5 for d in documents]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    paper = {"key": "e", "title": "Improving type information inferred by decompilers", "year": 2021,
             "source_text": _TABLE_TEXT * 6}
    calls = []

    async def llm_json(system, user):
        calls.append((system, user))
        if system == ag._DRAFT_SYSTEM:
            return {"draft": "**Datasets.** Studies compile open-source C programs.\n\n**Limits.** Labels are noisy.",
                    "works": [], "queries": []}
        if system == ag._READ_SYSTEM:
            pid = re.search(r"\[(S\d+)\] Table 3: Open-source", user).group(1)
            return {"picks": [pid], "objects": [{"name": n, "pids": [pid]} for n in ("arcadia", "jansson", "bgrep")]}
        pid = re.search(r"\[(S\d+)\] \"Table 3: Open-source", user).group(1)
        if system == ag._SUPPLEMENT_SYSTEM:
            return {"add": [{"item": "bgrep", "para": 1, "sentence": f"bgrep has 5 functions [{pid}]."}]}
        return {"answer": f"**Datasets.** Studies compile open-source C programs such as arcadia and jansson [{pid}]."
                          f"\n\n**Limits.** Labels of these projects are noisy [{pid}]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets are used?", [paper], llm_json=llm_json, mirror=None,
                                             final_mode="attach", paper_reading=2, paper_use="cover"))
    systems = [s for s, _ in calls]
    assert systems.count(ag._WRITE_SYSTEM) == 1 and systems.count(ag._SUPPLEMENT_SYSTEM) == 1
    sup = next(u for s, u in calls if s == ag._SUPPLEMENT_SYSTEM)
    assert "### bgrep" in sup and "### arcadia" not in sup and "1. **Datasets.**" in sup
    assert out["agent"]["inventory"]["missing_by_attempt"] == [["bgrep"], []] and out["attempts"] == 1
    assert out["agent"]["supplement"] == {"missing": 1, "added": 1, "words": 5, "still_missing": 0}
    assert out["agent"]["write_problems"] == [["missing_items"]]
    first, second = out["text"].split("\n\n")
    assert "bgrep has 5 functions" in first and "noisy" in second


def test_support_use_of_paper_reading_keeps_the_draft_length_and_lists_no_objects(monkeypatch):
    """支撑用法（默认）：精读不列对象、定稿提示限定篇幅与新增句数，也没有对象清单与补写（E17）。"""
    _no_rerank(monkeypatch)

    async def scores(query, documents):
        return [1.0 if "Table 3" in d else 0.5 for d in documents]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    paper = {"key": "e", "title": "Improving type information inferred by decompilers", "year": 2021,
             "source_text": _TABLE_TEXT * 6}
    calls = []

    async def llm_json(system, user):
        calls.append((system, user))
        if system == ag._DRAFT_SYSTEM:
            return {"draft": "**Datasets.** Studies compile open-source C programs. " * 20, "works": [], "queries": []}
        if system == ag._READ_SYSTEM:
            pid = re.search(r"\[(S\d+)\] Table 3: Open-source", user).group(1)
            return {"picks": [pid], "objects": [{"name": "arcadia", "pids": [pid]}]}
        pid = re.search(r"\[(S\d+)\] \"Table 3: Open-source", user).group(1)
        return {"answer": f"**Datasets.** Studies compile open-source C programs such as arcadia [{pid}]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets are used?", [paper], llm_json=llm_json, mirror=None,
                                             final_mode="attach", paper_reading=2))
    read = next(u for s, u in calls if s == ag._READ_SYSTEM)
    write = next(u for s, u in calls if s == ag._WRITE_SYSTEM)
    assert "objects" not in read and "尽量每个都选到" not in read
    assert "草稿约 120 词，定稿不超过 138 词" in write and "全篇最多补 2 句" in write
    assert "对象清单" not in write and "inventory" not in out["agent"] and "supplement" not in out["agent"]
    assert out["agent"]["paper_reading"]["use"] == "support" and out["attempts"] == 1


def test_apply_supplement_appends_to_lists_headings_or_a_new_paragraph():
    raw = "## Datasets\n\n- **Waterbirds**: birds [S1].\n- **CelebA**: hair [S2].\n\nPlain paragraph [S3]."
    out, added = ag._apply_supplement(raw, {"add": [
        {"item": "MultiNLI", "para": 2, "sentence": "**MultiNLI**: negation words [S4]."},
        {"item": "BAR", "para": 1, "sentence": "Action recognition data [S5]."},
        {"item": "NICO", "para": 9, "sentence": "NICO varies contexts [S6]."},
        {"item": "x", "para": 3, "sentence": "  "}], "heading": "**Other datasets**", "after": 2})
    paras = out.split("\n\n")
    assert len(added) == 3 and paras[0] == "## Datasets\nAction recognition data [S5]."
    assert paras[1].endswith("\n- **MultiNLI**: negation words [S4].") and paras[3] == "Plain paragraph [S3]."
    assert paras[2] == "**Other datasets.** NICO varies contexts [S6]."


def test_paper_reading_prompts_share_a_cacheable_opening_and_can_skip_notes(monkeypatch):
    """各篇精读提示的开头（任务说明 + 问题）相同，能命中前缀缓存；notes=False 时只要指针，定稿提示里也没有要点。"""
    idx = _table_index()
    rows = [p for p in idx.passages if not p.table]
    a = ag._paper_read_prompt("Which datasets?", "Paper A", 2020, rows[:2], [], 6)
    b = ag._paper_read_prompt("Which datasets?", "Paper B", 2021, rows[2:4], [], 6)
    shared = os.path.commonprefix([a, b])
    assert shared.startswith("## 任务") and "## 问题\nWhich datasets?" in shared and "Paper A" not in shared
    assert "写一句要点" not in a and '"picks": ["S12", "S40"]' in a
    noted = ag._paper_read_prompt("Which datasets?", "Paper A", 2020, rows[:2], [], 6, notes=True)
    assert "写一句要点" in noted and '"note": "..."' in noted

    async def scores(query, documents):
        return [float(i % 3) for i in range(len(documents))]
    monkeypatch.setattr(ag, "rerank_scores", scores)
    table = next(p for p in idx.passages if p.table)

    async def reader(system, user):
        assert "写一句要点" not in user
        return {"picks": [table.pid], "objects": [{"name": "arcadia", "pids": [table.pid]}]}

    out, stats, notes, objects = asyncio.run(ag._read_papers(idx, "Which datasets?", 6, asyncio.Semaphore(2), reader,
                                                             write_notes=False))
    assert stats["picked"] == 1 and notes == {table.pid: ""} and objects == {table.pid: ["arcadia"]}
    prompt = ag._write_prompt("Which datasets?", "Draft.", [], "en", mode="attach", papers_read=out, notes=notes)
    assert "（要点：" not in prompt and "要点是阅读笔记" not in prompt and f"[{table.pid}]" in prompt


def test_write_prompt_lists_passages_picked_by_paper_reading_once():
    """逐句候选里也被精读挑中的句子只在精读里列一次，候选对应关系照列。"""
    idx = _table_index()
    sent = next(p for p in idx.passages if "instrumented programs" in p.text)
    other = next(p for p in idx.passages if "synthetic programs" in p.text)
    prompt = ag._write_prompt("Which datasets?", "Draft.", [("Claim one.", [sent, other])], "en", mode="attach",
                              papers_read=[sent])
    assert prompt.count(f"[{sent.pid}] \"") == 1 and prompt.count(f"[{other.pid}] \"") == 1
    assert f"「Claim one.」→ {sent.pid}、{other.pid}" in prompt


def test_inline_claim_map_marks_candidates_in_the_draft_instead_of_listing_claims():
    """候选指针就地标在草稿句末，不再逐句抄写草稿；找不到原句位置的句子照旧另列，定稿里抄回的标记会被去掉。"""
    idx = _table_index()
    sent = next(p for p in idx.passages if "instrumented programs" in p.text)
    other = next(p for p in idx.passages if "synthetic programs" in p.text)
    draft = "**Data.** Studies compile open-source programs. They also use synthetic code.\n\nNo evidence here."
    evidence = [("Studies compile open-source programs.", [sent]), ("They also use synthetic code.", [other]),
                ("No evidence here.", []), ("A sentence that is not in the draft.", [sent])]
    prompt = ag._write_prompt("Which datasets?", draft, evidence, "en", mode="attach", claim_map="inline")
    assert f"Studies compile open-source programs. ⟦候选 {sent.pid}⟧ They also use synthetic code. ⟦候选 {other.pid}⟧" \
        in prompt and "No evidence here.\n" in prompt
    assert "「Studies compile" not in prompt and f"1. 「A sentence that is not in the draft.」→ {sent.pid}" in prompt
    listed = ag._write_prompt("Which datasets?", draft, evidence, "en", mode="attach")
    assert "⟦候选" not in listed and "「Studies compile open-source programs.」" in listed
    assert ag._CANDIDATE_MARK.sub("", f"Studies compile programs [{sent.pid}]. ⟦候选 {sent.pid}⟧ Next. ⟦{sent.pid}⟧") \
        == f"Studies compile programs [{sent.pid}]. Next."


def test_opening_draft_prompt_asks_for_background_first():
    with_opening = ag._draft_prompt("What is RMQ?", [], "en", 0, opening=True)
    assert "先交代背景" in with_opening and "先用 1~2 句直接回答" not in with_opening
    assert "先用 1~2 句直接回答" in ag._draft_prompt("What is RMQ?", [], "en", 0)


def test_draft_context_shows_most_relevant_recall_abstracts(monkeypatch):
    async def fake_rerank(query, docs):
        return [0.9 if "Nosocomial" in d else 0.1 for d in docs]

    monkeypatch.setattr(ag, "rerank_scores", fake_rerank)
    papers = [{"key": "x", "title": "Imitation learning in the ICU", "abstract": "We train an agent to imitate clinicians."},
              {"key": "m", "title": "Nosocomial infections during ECMO", "year": 2022,
               "abstract": "Infection occurred in 20% of adult ECMO runs; duration was the main risk factor."},
              {"key": "n", "title": "No abstract paper"}]
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        return {"draft": "", "works": [], "queries": []}

    out = asyncio.run(ag.write_with_pointers("How does ECMO affect infection risk?", papers, llm_json=llm_json,
                                             mirror=None, draft_context=1))
    assert out["agent"]["draft_context"] == 1
    assert "duration was the main risk factor" in prompts[0] and "imitate clinicians" not in prompts[0]


def test_mark_mode_keeps_draft_verbatim_and_attaches_pointers(monkeypatch):
    _no_rerank(monkeypatch)
    draft = ("**Datasets.** ManyTypes4Py is a large dataset with 869K annotations. "
             "TypeEvalPy is a micro-benchmark with 999 snippets.\n\n"
             "**Limitations.** Type inference for Python remains hard because of dynamic features.")
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        if "## 任务" in user and "draft" in user:
            return {"draft": draft, "works": [], "queries": []}
        assert "已按句编号" in user and "[2] TypeEvalPy is a micro-benchmark with 999 snippets." in user
        return {"marks": [{"i": 1, "pointers": ["S5"], "fix": ""},
                          {"i": 2, "pointers": ["S2", "S99"], "fix": "TypeEvalPy is a micro-benchmark with 154 snippets."}]}

    out = asyncio.run(ag.write_with_pointers("What datasets evaluate Python type inference?", _PAPERS,
                                             llm_json=llm_json, mirror=None, final_mode="mark"))
    assert out["status"] == "ok" and len(prompts) == 2              # 起草 + 一次挑指针，不进入重写
    assert "**Datasets.** ManyTypes4Py is a large dataset with 869K annotations [1]." in out["text"]
    assert "154 snippets [2]." in out["text"] and "999" not in out["text"]   # 矛盾句按证据改正
    assert "Type inference for Python remains hard because of dynamic features." in out["text"]   # 无证据句原样保留
    assert out["agent"]["mark"] == {"sentences": 3, "marked": 2, "fixed": 1}


def test_mark_mode_falls_back_to_attach_when_no_marks(monkeypatch):
    _no_rerank(monkeypatch)

    async def llm_json(system, user):
        if "## 任务" in user and "draft" in user:
            return {"draft": "ManyTypes4Py is a large dataset with 869K annotations for type inference.",
                    "works": [], "queries": []}
        if "已按句编号" in user:
            return {"marks": "oops"}
        return {"answer": "ManyTypes4Py is a large dataset with 869K annotations for type inference [S5]."}

    out = asyncio.run(ag.write_with_pointers("Which datasets?", _PAPERS, llm_json=llm_json, mirror=None,
                                             final_mode="mark"))
    assert out["status"] == "ok" and out["agent"]["mark_fallback"] and "[1]" in out["text"]


def test_multiple_drafts_are_merged_and_their_works_and_queries_united(monkeypatch):
    _no_rerank(monkeypatch)
    drafts = iter([
        {"draft": "**Chemical routes.** Co-precipitation and thermal decomposition dominate.",
         "works": [{"title": "Monodisperse magnetite nanoparticles", "year": 2002}], "queries": ["co-precipitation size"]},
        {"draft": "**Top-down vs bottom-up.** Ball milling breaks bulk material; chemistry builds atom by atom.",
         "works": [{"title": "Ball milling of iron oxide powders", "year": 2010}],
         "queries": ["ball milling nanoparticles", "Co-precipitation size"]},
    ])
    prompts = []

    async def llm_json(system, user):
        prompts.append(user)
        if "## 草稿 1" in user:
            return {"draft": "**Top-down vs bottom-up.** Ball milling breaks bulk material; bottom-up chemistry "
                             "(co-precipitation, thermal decomposition) builds particles atom by atom."}
        if '"works"' in user:
            return next(drafts)
        return {"answer": "Top-down ball milling breaks bulk material; bottom-up chemistry builds particles."}

    out = asyncio.run(ag.write_with_pointers("What are the two main methods for making magnetic nanoparticles?",
                                             _PAPERS, llm_json=llm_json, mirror=None, final_mode="attach",
                                             drafts=2))
    merge = [p for p in prompts if "## 草稿 1" in p][0]
    assert "Co-precipitation and thermal decomposition" in merge and "Ball milling breaks bulk" in merge
    assert "分类方式不同时，都写进来" in merge
    assert out["draft"].startswith("**Top-down vs bottom-up.**") and out["agent"]["merge"]["status"] == "ok"
    assert out["agent"]["works"] == ["Monodisperse magnetite nanoparticles", "Ball milling of iron oxide powders"]
    assert out["agent"]["queries"] == ["co-precipitation size", "ball milling nanoparticles"]   # 交替合并、不分大小写去重


def test_short_merge_falls_back_to_first_draft(monkeypatch):
    _no_rerank(monkeypatch)
    first = "**Chemical routes.** Co-precipitation and thermal decomposition dominate the synthesis literature."

    async def llm_json(system, user):
        if "## 草稿 1" in user:
            return {"draft": "Two methods."}                          # 合并稿丢了大半内容
        if '"works"' in user:
            return {"draft": first, "works": [], "queries": []}
        return {"answer": first}

    out = asyncio.run(ag.write_with_pointers("What are the two main methods?", _PAPERS, llm_json=llm_json,
                                             mirror=None, final_mode="attach", drafts=2))
    assert out["draft"] == first and out["agent"]["merge"]["status"] == "short"


def test_failed_second_draft_falls_back_to_the_one_that_succeeded(monkeypatch):
    _no_rerank(monkeypatch)
    calls = []
    text = "**Chemical routes.** Co-precipitation and thermal decomposition dominate the synthesis literature."

    async def llm_json(system, user):
        if '"works"' in user:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("content filter")
            return {"draft": text, "works": [], "queries": []}
        assert "## 草稿 1" not in user                                 # 只有一份时不合并
        return {"answer": text}

    out = asyncio.run(ag.write_with_pointers("What are the two main methods?", _PAPERS, llm_json=llm_json,
                                             mirror=None, final_mode="attach", drafts=2))
    assert out["draft"] == text and out["agent"]["merge"] == {"drafts": 1, "status": "single"}

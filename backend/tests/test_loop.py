"""单循环（PaSa 式 Crawler + Selector）的离线测试：不联网、不调真模型。"""

import asyncio
import json

from backend.services.deep_research.loop import crawler as crawler_mod
from backend.services.deep_research.loop import runner
from backend.services.deep_research.loop.config import config_for
from backend.services.deep_research.loop.papers import aliases, dedupe_candidates
from backend.services.deep_research.loop.runner import LoopState, ResearchLoop
from backend.services.deep_research.loop.sections import PaperSections, Reference, parse_html
from backend.services.deep_research.loop.selector import Selector, parse_labels

LLM_CFG = {"api_key": "k", "model": "m"}


def _paper(i: int, title: str, abstract: str = "abstract text") -> dict:
    return {"key": f"doi:10.1/{i}", "doi": f"10.1/{i}", "title": title, "abstract": abstract}


# ── Selector ────────────────────────────────────────────────────────────────

def test_parse_labels_accepts_string_or_list_and_rejects_wrong_count():
    assert parse_labels({"labels": "2 0 1"}, 3) == [2, 0, 1]
    assert parse_labels({"labels": [2, "1", 0]}, 3) == [2, 1, 0]
    assert parse_labels({"labels": "2 0"}, 3) is None


def test_selector_floor_skips_model_and_caches_verdicts():
    calls = []

    async def llm(system, user):
        calls.append(user)
        n = user.count("\n[")
        return {"labels": " ".join(["2"] * n)}

    async def rerank(q, docs):
        return [0.9 if "dense" in d.lower() else 0.001 for d in docs]

    cfg = config_for("standard", {"judge_batch": 10, "rerank_floor": 0.02})
    sel = Selector("dense retrieval", llm, cfg, rerank=rerank)
    papers = [_paper(1, "Dense retrieval at scale"), _paper(2, "Cooking recipes"), _paper(3, "dense passage")]
    out = asyncio.run(sel.judge(papers))
    assert out["llm_calls"] == 1 and sel.stats["floor_rejected"] == 1
    assert [p["label"] for p in papers] == [2, 0, 2]
    assert papers[0]["score"] > papers[1]["score"]
    asyncio.run(sel.judge(papers))          # 已判过：不再调模型
    assert len(calls) == 1


def test_selector_splits_failed_batch_and_marks_unknown():
    async def llm(system, user):
        n = user.count("\n[")
        if n > 1:
            return {"labels": "bad"}
        return {"labels": "1"} if "keep" in user else {"labels": "x"}

    sel = Selector("q", llm, config_for("standard", {"judge_batch": 4}), rerank=None)
    papers = [_paper(1, "keep me"), _paper(2, "unparsable")]
    asyncio.run(sel.judge(papers))
    assert papers[0]["label"] == 1 and papers[1]["label"] is None
    assert sel.stats["failed"] == 1 and not sel.selected(papers[1])


def test_selector_forced_seed_and_state_roundtrip():
    sel = Selector("q", None, config_for("standard"), rerank=None)
    seed = _paper(1, "my seed paper")
    asyncio.run(sel.judge([seed], forced={seed["key"]}))
    assert seed["label"] == 2
    again = Selector("q", None, config_for("standard"))
    again.load(json.loads(json.dumps(sel.dump())))
    assert again.verdicts == {seed["key"]: 2}


# ── 论文身份 ────────────────────────────────────────────────────────────────

def test_aliases_merge_same_paper_across_sources():
    a = {"key": "doi:10.48550/arxiv.2401.00001", "doi": "10.48550/arXiv.2401.00001", "title": "A Paper"}
    b = {"key": "arxiv:2401.00001", "arxiv_id": "2401.00001", "title": "A paper"}
    assert set(aliases(a)) & set(aliases(b))
    assert len(dedupe_candidates([dict(b), dict(b)])) == 1


# ── 章节解析 ────────────────────────────────────────────────────────────────

HTML = """
<section class="ltx_section"><h2 class="ltx_title"><span class="ltx_tag">1 </span>Introduction</h2>
<p>Intro <cite><a href="#bib.bib1">A</a></cite><br class="ltx_break"></p></section>
<section class="ltx_section"><h2 class="ltx_title"><span class="ltx_tag">2 </span>Related Work</h2>
<section class="ltx_subsection"><h3 class="ltx_title"><span class="ltx_tag">2.1 </span>Dense Retrieval</h3>
<p>See <a href="#bib.bib2">B</a> and <a href="#bib.bib1">A</a>.</p></section></section>
<section class="ltx_bibliography"><h2 class="ltx_title">References</h2><ul>
<li id="bib.bib1" class="ltx_bibitem"><span class="ltx_bibblock">A. Smith and B. Jones.</span>
<span class="ltx_bibblock">Attention is all you need for retrieval.</span>
<span class="ltx_bibblock">NeurIPS, 2017. arXiv:1706.03762</span></li>
<li id="bib.bib2" class="ltx_bibitem"><span class="ltx_bibblock">Doe, J. Dense passage retrieval for open-domain question answering. In EMNLP, 2020.</span></li>
</ul></section>"""


def test_parse_html_maps_sections_to_cited_references():
    parsed = parse_html(HTML)
    assert parsed.names() == ["Introduction", "Related Work / Dense Retrieval"]
    refs = parsed.cited(["Related Work / Dense Retrieval"], 10)
    assert [r.title for r in refs] == ["Dense passage retrieval for open-domain question answering",
                                       "Attention is all you need for retrieval"]
    assert parsed.references["bib.bib1"].arxiv_id == "1706.03762"


# ── Crawler ─────────────────────────────────────────────────────────────────

class _FakeSources:
    enabled = ("arxiv",)
    custom = None

    def __init__(self, resolved=None, refs=None):
        self.resolved, self.refs = resolved or {}, refs or []

    async def resolve_titles(self, titles):
        from backend.services.deep_research.loop.papers import title_key
        return {title_key(t): self.resolved[t] for t in titles if t in self.resolved}

    async def references(self, paper, limit):
        return self.refs

    async def fill_abstracts(self, papers):
        return 0


def test_expand_follows_chosen_sections(monkeypatch):
    parsed = PaperSections(sections={"Introduction": ["b1"], "Related Work": ["b2"]},
                           references={"b1": Reference("b1", "", "Intro paper title here"),
                                       "b2": Reference("b2", "", "Related paper title here")})

    async def fake_fetch(arxiv_id):
        return parsed

    async def llm(system, user):
        return {"sections": ["related work"]}

    monkeypatch.setattr(crawler_mod, "fetch_sections", fake_fetch)
    src = _FakeSources(resolved={"Related paper title here": _paper(9, "Related paper title here")})
    c = crawler_mod.Crawler("q", llm, src, config_for("standard"))
    rows, audit = asyncio.run(c.expand({"key": "arxiv:1", "arxiv_id": "2401.1", "title": "Seed", "depth": 0}))
    assert audit["chosen"] == ["Related Work"] and [r["title"] for r in rows] == ["Related paper title here"]
    assert rows[0]["parent"] == "arxiv:1" and rows[0]["via"] == "Related Work" and rows[0]["depth"] == 1


def test_expand_falls_back_to_reference_list_by_term_overlap(monkeypatch):
    async def no_html(arxiv_id):
        return None

    monkeypatch.setattr(crawler_mod, "fetch_sections", no_html)
    refs = [_paper(1, "Gardening tips"), _paper(2, "Dense retrieval for question answering")]
    c = crawler_mod.Crawler("dense retrieval", None, _FakeSources(refs=refs), config_for("standard"))
    rows, audit = asyncio.run(c.expand({"key": "k", "arxiv_id": "1", "title": "Retrieval survey"}))
    assert audit["mode"] == "reference_list" and [r["title"] for r in rows] == ["Dense retrieval for question answering"]


# ── ResearchLoop（本地语料，不联网） ────────────────────────────────────────

def test_research_loop_crawl_on_local_corpus(monkeypatch):
    corpus = {
        "dense retrieval": [_paper(1, "Dense retrieval at scale"), _paper(2, "Unrelated cooking")],
        "retrieval survey": [_paper(1, "Dense retrieval at scale"), _paper(3, "A survey of neural retrieval")],
    }

    async def search_one(source, query, limit, order):
        return [dict(p) for p in corpus.get(query, [])]

    def fake_caller(cfg, *, max_tokens, **kw):
        async def call(system, user):
            if "检索式" in user and "queries" in user and "已经找到" not in user:
                return {"queries": ["dense retrieval", "retrieval survey"]}
            if "已经找到" in user:
                return {"queries": []}
            titles = [line for line in user.splitlines() if line.startswith("[")]
            return {"labels": " ".join("0" if "cooking" in t.lower() else "2" for t in titles)}
        return call

    monkeypatch.setattr(runner, "json_caller", fake_caller)
    cfg = config_for("lite", {"sources": ["local"], "rerank_floor": 0})
    loop = ResearchLoop("dense retrieval?", cfg, llm_cfg=LLM_CFG, search_one=search_one,
                        options={"exclusions": {"titles": ["A survey of neural retrieval"]}})
    chosen = asyncio.run(loop.crawl())
    assert [p["title"] for p in chosen] == ["Dense retrieval at scale"]
    assert loop.state.queries_done == ["dense retrieval", "retrieval survey"]
    assert loop.state.excluded == 1 and loop.state.stop_reason == "no_expansion"
    report = loop.crawl_report()
    assert report["candidates"] == 2 and report["selected"] == 1

    restored = LoopState.restore(json.loads(json.dumps(loop.dump_state())))
    assert restored.queries_done == loop.state.queries_done and restored.selector["verdicts"]


def test_research_loop_pause_raises_before_next_action(monkeypatch):
    async def search_one(source, query, limit, order):
        return []

    loop = ResearchLoop("q", config_for("lite", {"sources": ["local"]}), llm_cfg=None, search_one=search_one,
                        should_pause=lambda: True, state=LoopState(seeded=True, queries_todo=["a", "b"]))
    try:
        asyncio.run(loop.crawl())
        raise AssertionError("expected Paused")
    except runner.Paused:
        assert loop.state.queries_todo == ["a", "b"]


# ── 作答：指针还原 ──────────────────────────────────────────────────────────

def test_render_pointers_numbers_papers_and_drops_invalid():
    from backend.services.deep_research.evidence_search import EvidenceIndex
    from backend.services.deep_research.loop.answer import render_pointers

    index = EvidenceIndex()
    index.add_paper({"key": "p1", "title": "Paper one", "abstract": (
        "Dense retrieval with dual encoders improves top-20 passage recall by nine points on Natural Questions. "
        "The approach also scales to corpora with twenty million passages using approximate nearest neighbours.")})
    pid = index.passages[0].pid
    text, refs, invalid = render_pointers(f"Claim one [{pid}][S999].", index)
    assert text == "Claim one [1]." and invalid == ["S999"] and refs[0]["key"] == "p1"


# ── run_research：落库外壳（假模型 + 本地检索 + 假作答） ────────────────────

def test_run_research_persists_result_and_clears_checkpoint(monkeypatch, engine):
    from sqlmodel import Session

    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research.loop import sources as sources_mod

    corpus = [_paper(1, "Dense retrieval at scale"), _paper(2, "Unrelated cooking")]

    async def search_one(source, query, limit, order):
        return [dict(p) for p in corpus]

    class LocalSources(sources_mod.Sources):
        def __init__(self, **kw):
            super().__init__(**{**kw, "search_one": search_one})

    def fake_caller(cfg, *, max_tokens, **kw):
        async def call(system, user):
            if "已经找到" in user:
                return {"queries": []}
            if '"queries"' in user:
                return {"queries": ["dense retrieval"]}
            titles = [line for line in user.splitlines() if line.startswith("[")]
            return {"labels": " ".join("0" if "cooking" in t.lower() else "2" for t in titles)}
        return call

    async def fake_preflight(online, warnings):
        return [], set()

    async def fake_answer(question, papers, llm_cfg, options, online, paper_filter=None):
        return {"status": "ok", "text": "Answer [1].", "references": [{"n": 1, "key": papers[0]["key"]}]}

    monkeypatch.setattr(runner, "get_engine", lambda: engine)
    monkeypatch.setattr(runner, "Sources", LocalSources)
    monkeypatch.setattr(runner, "json_caller", fake_caller)
    monkeypatch.setattr(runner, "_preflight", fake_preflight)
    monkeypatch.setattr(runner, "answer_question", fake_answer)
    monkeypatch.setattr("backend.services.ai_config.get_analysis_config", lambda: LLM_CFG)
    with Session(engine) as s:
        run = ExplorationRun(question="dense retrieval?", depth="lite",
                             options={"enrich": False, "loop": {"sources": ["local"], "rerank_floor": 0}})
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    asyncio.run(runner.run_research(run_id))
    with Session(engine) as s:
        run = s.get(ExplorationRun, run_id)
        assert run.status == "done", run.error_message
        assert run.checkpoint is None and run.decompose == {"queries": ["dense retrieval"]}
        result = run.result
    assert [p["title"] for p in result["papers"]] == ["Dense retrieval at scale"]
    assert result["answer"]["status"] == "ok" and result["metrics"]["total"] == 1
    assert [row["stage"] for row in result["sources"]["stage_timeline"]] == ["search", "expand", "landscape", "answer"]
    assert result["crawl"]["selected"] == 1 and result["crawl"]["candidates"] == 2

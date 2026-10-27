"""本地语料检索：替代联网检索、返回格式相同，可直接注入召回引擎。"""

import asyncio
import json

from backend.eval import public_benchmarks as pb
from backend.models.recall import RecallCandidate
from backend.services.deep_research.local_search import LOCAL_SOURCE, LocalCorpusIndex, tokenize
from backend.services.deep_research.recall_engine import run_target_recall

_RECORDS = [
    {"id": 1, "title": "Concealed Data Poisoning Attacks on NLP Models",
     "abstract": "Adversarial attacks insert a few poisoned examples into training data.", "citations": []},
    {"id": 2, "title": "Backdoor Attacks with Few Poisoned Samples",
     "abstract": "Poisoning attacks on neural networks.", "citations": [1]},
    {"id": 3, "title": "Scaling Coreference to Long Documents",
     "abstract": "Within-document coreference resolution for long texts.", "citations": [1, 2]},
    {"id": 4, "title": "", "abstract": "untitled records are skipped"},
]


def test_tokenize_drops_stopwords_and_normalizes_plurals():
    assert tokenize("Studies of the Models and Attacks") == ["model", "attack"]


def test_bm25_ranks_title_matches_first_and_cited_order_uses_corpus_citations():
    index = LocalCorpusIndex(_RECORDS, name="toy")
    assert len(index) == 3  # 无标题记录跳过
    ranked = [doc["id"] for doc, _ in index.search("data poisoning attacks", limit=5)]
    assert ranked[:2] == ["1", "2"] and "3" not in ranked
    # 语料内被引：#1 被引 2 次、#2 被引 1 次
    cited = [doc["id"] for doc, _ in index.search("poisoning", limit=5, order="cited")]
    assert cited == ["1", "2"]
    assert index.search("quantum chromodynamics", limit=5) == []


def test_search_one_matches_online_contract_and_only_serves_local_source():
    search_one = LocalCorpusIndex(_RECORDS, name="toy").as_search_one()
    out = asyncio.run(search_one(LOCAL_SOURCE, "coreference long documents", 3, "relevance"))
    assert isinstance(out[0], RecallCandidate) and out[0].title.startswith("Scaling Coreference")
    assert out[0].extra == {"local_id": "3", "local_corpus": "toy"}
    assert "provider:local" in out[0].provenance and out[0].abstract
    assert asyncio.run(search_one("openalex", "coreference", 3, "relevance")) == []


def test_recall_engine_runs_offline_on_local_corpus():
    index = LocalCorpusIndex(_RECORDS, name="toy")

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return {"accepted": [item["key"] for item in json.loads(user)["papers"]], "rejected": [], "uncertain": []}

    papers, audit = asyncio.run(run_target_recall(
        "data poisoning with few examples", ["data poisoning attacks"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=index.as_search_one(), sources=(LOCAL_SOURCE,),
        llm_json=llm_json, backfill_abstracts=lambda dois: asyncio.sleep(0, result={})))
    assert {p["local_id"] for p in papers} == {"1", "2"}
    assert audit["keyword_stats"][0]["sources"] == {LOCAL_SOURCE: 2}
    assert audit["source_failures"] == {}


def test_failed_abstract_backfill_is_retried_before_final_ranking():
    base = LocalCorpusIndex(_RECORDS, name="toy").as_search_one()

    async def search_one(source, keyword, limit, order):
        out = await base(source, keyword, limit, order)
        for c in out:  # 模拟 Scopus：有 DOI 无摘要
            c.doi, c.abstract = f"10.1/{c.extra['local_id']}", None
        return out

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return {"accepted": [item["key"] for item in json.loads(user)["papers"]], "rejected": [], "uncertain": []}

    calls = []

    async def backfill(dois):
        calls.append(list(dois))
        if len(calls) == 1:
            raise RuntimeError("429")  # 首轮临时限流
        return {d.upper(): f"abstract of {d}" for d in dois}

    papers, audit = asyncio.run(run_target_recall(
        "data poisoning with few examples", ["data poisoning attacks"], depth="quick", now_year=2026,
        llm_cfg={"api_key": "k"}, search_one=search_one, sources=(LOCAL_SOURCE,),
        llm_json=llm_json, backfill_abstracts=backfill))
    bf = audit["abstract_backfill"]
    assert bf["filled"] == 0 and bf["retry_needed"] == bf["retry_filled"] == len(papers) == 2
    assert all(p["abstract"].startswith("abstract of 10.1/") for p in papers)


def test_match_local_reports_rank_filter_rejection_and_baseline():
    corpus = [{"local_id": "2", "title": "B", "relevance_status": "confirmed"},
              {"local_id": "1", "title": "A", "relevance_status": "unknown"}]
    rows = pb.match_local(["1", "2", "9"], corpus, rejected_keys={"titlefp:x"},
                          key_of={"9": "titlefp:x"}, baseline=["2", "7", "1"])
    assert [(r["found"], r["rank"], r["kept"]) for r in rows] == [(True, 2, False), (True, 1, True), (False, None, False)]
    assert [r["rejected_by_filter"] for r in rows] == [False, False, True]
    assert [r["baseline_rank"] for r in rows] == [3, 1, None]
    summary = pb.summarize_litsearch([{"targets": rows, "corpus_size": 2, "wall_s": 5}])
    assert summary["system_recall@5"] == round(2 / 3, 3) and summary["bm25_recall@5"] == round(2 / 3, 3)


def test_rerank_orders_final_and_keeps_recall_position():
    index = LocalCorpusIndex(_RECORDS, name="toy")

    async def llm_json(system, user):
        if "检索规划员" in system:
            return {"next_keywords": [], "done": True, "reason": "ok"}
        return {"accepted": [item["key"] for item in json.loads(user)["papers"]], "rejected": [], "uncertain": []}

    async def scores(question, texts):
        return [1.0 if "Backdoor" in t else 0.1 for t in texts]

    async def broken(question, texts):
        raise RuntimeError("down")

    def run(rerank):
        return asyncio.run(run_target_recall(
            "few poisoned samples", ["poisoning attacks"], depth="quick", now_year=2026,
            llm_cfg={"api_key": "k"}, search_one=index.as_search_one(), sources=(LOCAL_SOURCE,),
            llm_json=llm_json, backfill_abstracts=lambda dois: asyncio.sleep(0, result={}),
            rerank_scores=rerank))

    papers, audit = run(scores)
    assert papers[0]["local_id"] == "2" and papers[0]["rerank_score"] == 1.0
    assert audit["rerank"]["applied"] is True and {p["recall_pos"] for p in papers} == {0, 1}
    rows = pb.match_local(["2"], papers)
    assert rows[0]["rank"] == 1 and rows[0]["recall_rank"] == papers[0]["recall_pos"] + 1
    # 重排失败：保持原裁剪与顺序，审计记录未生效
    papers, audit = run(broken)
    assert audit["rerank"] == {"applied": False, "requested": True}
    assert all("rerank_score" not in p for p in papers)


def test_listwise_rerank_reorders_head_only_and_survives_bad_output():
    from backend.services.deep_research.recall_engine import listwise_rerank

    papers = [{"key": k, "title": k} for k in "abcd"]

    async def good(system, user):
        return {"ranking": [2, 0, 2, 9]}  # 重复与越界编号忽略，漏掉的按原顺序补在后面

    async def bad(system, user):
        raise ValueError("bad json")

    ranked, audit = asyncio.run(listwise_rerank("q", papers, good, top=3))
    assert [p["key"] for p in ranked] == ["c", "a", "b", "d"] and audit["appended"] == 1
    ranked, audit = asyncio.run(listwise_rerank("q", papers, bad, top=3))
    assert ranked == papers and audit == {"applied": False, "reason": "ValueError"}


def test_gap_search_reranks_per_query_and_skips_abstractless():
    from backend.models.recall import RecallSource, build_recall_candidate
    from backend.services.deep_research.explorer import gap_search

    def cand(i, abstract="Some abstract about the topic."):
        return build_recall_candidate(title=f"Paper {i}", arxiv_id=f"2401.0000{i}", abstract=abstract,
                                      source=RecallSource.SEARCH, provenance=["provider:arxiv"])

    async def search_one(source, kw, limit, order):
        assert source == "arxiv"
        if kw == "boom":
            raise RuntimeError("down")
        return [cand(1), cand(2, abstract=""), cand(3), cand(4)]

    async def score(query, texts):
        return [{"Paper 1": 0.1, "Paper 3": 0.9, "Paper 4": 0.5}[t.split(".")[0]] for t in texts]

    out = asyncio.run(gap_search("q", ["boom", "alpha", "beta"], search_one=search_one, score=score))
    # alpha：重排取前 2（3、4）；beta：已选的不重复，剩下 1
    assert [p["title"] for p in out] == ["Paper 3", "Paper 4", "Paper 1"]
    assert out[0]["source_query"] == "answer_gap:alpha"

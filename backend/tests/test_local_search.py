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

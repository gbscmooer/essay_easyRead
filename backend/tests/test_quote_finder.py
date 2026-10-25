"""原文摘录检索：全文切句、按方面并入原文句、逐句核查引用（模型只挑候选编号）。"""

import asyncio

from backend.services.deep_research import answer_writer as aw
from backend.services.deep_research import quote_finder as qf

_FULL = "\n\n".join([
    "--- Page 1 ---",
    "Abstract. We study sampling strategies for planning in narrow passages of configuration space.",
    "Bridge test sampling places milestones in narrow passages by checking that both ends of a short "
    "segment are in collision while its midpoint is free.",
    "On the corridor benchmark, bridge sampling reduced planning time by 63% compared with uniform sampling.",
    "A limitation is that the bridge test wastes samples in wide open regions of the space.",
    "--- Page 2 ---",
    "References",
    "[1] L. Kavraki et al. Probabilistic roadmaps for path planning in high-dimensional spaces. 1996.",
]) + "\n" + "x" * 2000

_PAPERS = [
    {"key": "a", "title": "Bridge test sampling", "year": 2003, "abstract": "We study sampling strategies for narrow passages.",
     "source_text": _FULL, "source_kind": "downloaded_full_text"},
    {"key": "b", "title": "Gaussian sampling", "year": 1999,
     "abstract": "Gaussian sampling concentrates milestones near obstacle boundaries. It improves coverage of "
                 "difficult regions in probabilistic roadmaps."},
]


def _no_rerank(monkeypatch):
    async def none(query, documents):
        return None
    monkeypatch.setattr(qf, "rerank_scores", none)


def test_split_sentences_drops_references_and_page_markers():
    sents = qf.split_sentences(_FULL)
    assert any(s.startswith("Bridge test sampling places milestones") for s in sents)
    assert not any("Kavraki" in s or "Page" in s for s in sents)


def test_pdf_lines_join_into_sentences_and_headings_break():
    pdf = ("Reviewing Papers With LLMs\nTianmai Zhang\nUniversity of Washington\ntianmai@uw.edu\nAbstract\n"
           "Recent advancements in large language models have sparked interest in using\n"
           "them to aid the peer review process. We assessed their perfor-\n"
           "mance and API costs for identifying critical errors in papers.\n1\nIntroduction\n"
           "Automated review generation has been studied for a long time by many groups.\n")
    sents = qf.split_sentences(pdf)
    assert sents[0] == ("Recent advancements in large language models have sparked interest in using them "
                        "to aid the peer review process.")
    assert "We assessed their performance and API costs for identifying critical errors in papers." in sents
    assert sents[-1].startswith("Automated review generation")      # 标题 Introduction 不并入句子
    assert not any("@" in s or "Washington" in s for s in sents)


def test_aspect_quotes_add_full_text_sentences_to_materials(monkeypatch):
    _no_rerank(monkeypatch)
    mats = aw.build_materials(_PAPERS, [])
    aspects = [{"title": "Limitations", "need": "where bridge test wastes samples in wide open regions"}]
    out, audit = asyncio.run(qf.add_aspect_quotes("narrow passage sampling", aspects, mats, _PAPERS))
    texts = [e["text"] for m in out for e in m["excerpts"]]
    assert any(t.startswith("A limitation is that the bridge test wastes samples") for t in texts)
    added = [e for m in out for e in m["excerpts"] if e["fact"] == "Limitations"]
    assert added and all(e["source"] in ("full_text", "abstract") for e in added)
    assert audit["full_text_papers"] == 1 and audit["added"] == len(added)
    assert all(len(m["excerpts"]) <= qf.MAX_EXCERPTS for m in out)


def test_verified_references_take_model_chosen_sentence_verbatim(monkeypatch):
    _no_rerank(monkeypatch)
    mats = aw.build_materials(_PAPERS, [])
    text = ("Bridge sampling reduced planning time by 63% on the corridor benchmark [1]. "
            "Gaussian sampling concentrates milestones near obstacle boundaries [2]. "
            "Quantum annealing solves path planning in constant time [2].")
    calls = []

    async def judge(system, user):
        calls.append(user)
        verdicts = []
        for i, block in enumerate(user.split("## 陈述 ")[1:], 1):
            claim, *cands = block.split("\n  候选 ")
            hit = [j for j, c in enumerate(cands, 1) if ("63%" in c and "63%" in claim)
                   or ("obstacle boundaries" in c and "obstacle boundaries" in claim)]
            verdicts.append({"claim": i, "support": hit})
        return {"verdicts": verdicts}

    refs, audit = asyncio.run(qf.verified_references(text, mats, _PAPERS, judge))
    by_n = {r["n"]: r for r in refs}
    assert by_n[1]["verified"] and "reduced planning time by 63%" in by_n[1]["excerpt"]
    assert by_n[2]["verified"] and "obstacle boundaries" in by_n[2]["excerpt"]
    assert audit["pairs"] == 3 and audit["unsupported_count"] == 1
    assert "Quantum annealing" in audit["unsupported"][0]["claim"]
    assert len(calls) == 1


def test_verification_failure_falls_back_to_top_candidate_unverified(monkeypatch):
    _no_rerank(monkeypatch)
    mats = aw.build_materials(_PAPERS, [])

    async def broken(system, user):
        raise RuntimeError("LLM down")

    refs, audit = asyncio.run(qf.verified_references(
        "Bridge sampling reduced planning time by 63% on the corridor benchmark [1].", mats, _PAPERS, broken))
    assert refs[0]["verified"] is False and refs[0]["excerpt"]
    assert audit["judge_failed"] == 1 and audit["unsupported_count"] == 0

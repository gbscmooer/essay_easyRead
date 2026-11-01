"""评测的发表时间截断：严格档 / 常规档，作答阶段所有入口共用过滤器。"""

import asyncio

from backend.services.deep_research import answer_agent as ag
from backend.services.deep_research import evidence_search as es
from backend.services.deep_research.time_machine import make_filter, question_year

Q = "What effects do soy oligosaccharides have on intestinal physiology in weaning piglets?"


def test_strict_keeps_only_papers_up_to_the_question_year_and_drops_same_year_reviews():
    f = make_filter(Q, 2010, "strict")
    assert f({"title": "Lactic acid bacteria in piglets", "year": 2008})
    assert f({"title": "Soy oligosaccharides in piglets: a feeding trial", "year": 2010})
    assert not f({"title": "Soy oligosaccharides and non-starch polysaccharides: A review", "year": 2010})
    assert not f({"title": "Later study citing the survey", "year": 2015})
    assert not f({"title": "Unknown year", "year": None})


def test_regular_drops_only_likely_source_surveys():
    f = make_filter(Q, 2010, "regular")
    assert f({"title": "Later study on fibre", "year": 2015}) and f({"title": "No year paper"})
    assert not f({"title": "Soy oligosaccharides and non-starch polysaccharides: A review", "year": 2010})
    assert not f({"title": "Effects of soy oligosaccharides on intestinal physiology in weaning piglets", "year": 2012})
    assert f({"title": "Effects of soy oligosaccharides on intestinal physiology in weaning piglets", "year": 2005})


def test_no_filter_without_tier_or_year():
    assert make_filter(Q, None, "strict") is None and make_filter(Q, 2010, "") is None
    assert question_year("2010-8-20") == 2010 and question_year("1971-12") == 1971 and question_year("") is None


def test_answer_agent_filter_applies_to_every_paper_source(monkeypatch):
    async def none(query, documents):
        return None

    monkeypatch.setattr(es, "rerank_scores", none)
    papers = [{"key": "old", "title": "Soy oligosaccharides raise ATP in piglets", "year": 2008,
               "abstract": "Soy oligosaccharides increased microbial ATP and VFA contents in piglets."},
              {"key": "new", "title": "A 2020 follow-up on piglet gut health", "year": 2020,
               "abstract": "Soy oligosaccharides increased microbial ATP and VFA contents in piglets."}]

    async def llm_json(system, user):
        if '"works"' in user:
            return {"draft": "Soy oligosaccharides increase microbial ATP in piglets.", "works": [], "queries": []}
        return {"answer": "Soy oligosaccharides increase microbial ATP in piglets [S1]."}

    out = asyncio.run(ag.write_with_pointers(Q, papers, llm_json=llm_json, mirror=None, final_mode="attach",
                                             paper_filter=make_filter(Q, 2010, "strict")))
    assert out["agent"]["filtered_out"] == 1 and [r["title"] for r in out["references"]] == [papers[0]["title"]]

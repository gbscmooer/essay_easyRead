"""ResearchQA 评测：分层抽题、评委档位解析与覆盖率计算与官方脚本一致。"""

import asyncio

from backend.eval import researchqa as rq


def _items():
    items = []
    for dom, k in rq.QUOTA.items():
        for f in range(3):
            for n in range(5):
                items.append({"id": f"{dom}-{f}-{n}", "general_domain": dom, "field": f"{dom}-field{f}",
                              "query": "q", "rubric": []})
    for f in sorted(rq.CS_FIELDS):
        for n in range(5):
            items.append({"id": f"cs-{f}-{n}", "general_domain": "Engineering", "field": f, "query": "q", "rubric": []})
    return items


def test_pick_is_stratified_deterministic_and_excludes_cs_fields():
    a = rq.pick_items(_items(), seed=0, n_noncs=40, n_cs=10)
    b = rq.pick_items(_items(), seed=0, n_noncs=40, n_cs=10)
    assert [x["id"] for x in a] == [x["id"] for x in b]
    non_cs = [x for x in a if x["group"] == "non_cs"]
    cs = [x for x in a if x["group"] == "cs"]
    assert len(non_cs) == 40 and len(cs) == 10
    assert not any(x["field"] in rq.CS_FIELDS for x in non_cs)
    assert all(x["field"] in rq.CS_FIELDS for x in cs)
    health = [x for x in non_cs if x["general_domain"] == "Health Sciences & Medicine"]
    assert len(health) == 12 and len({x["field"] for x in health}) == 3   # 大类内轮流取不同领域
    assert len({x["id"] for x in a}) == len(a)


def test_subset_rotates_domains_and_keeps_cs_controls():
    s = rq.subset(rq.pick_items(_items(), seed=0), 10)
    assert len(s) == 10
    assert sum(x["group"] == "cs" for x in s) == 2
    assert {x["general_domain"] for x in s if x["group"] == "non_cs"} == set(rq.QUOTA)


def test_parse_labels_tolerates_numbering_and_rejects_bad_counts():
    assert rq.parse_labels("1. Completely\n- mostly.\n\nNot at all", 3) == ["completely", "mostly", "not at all"]
    assert rq.parse_labels("Completely\nMostly", 3) is None
    assert rq.parse_labels("Completely\nGood\nMostly", 3) is None


def test_coverage_matches_official_normalisation_and_retries():
    calls = []

    async def complete_text(prompt):
        calls.append(prompt)
        return "Completely\nBarely" if len(calls) > 1 else "Completely"   # 第一次条数不对 → 重试

    rubric = [{"rubric_item": "r1", "type": ["Citation"]}, {"rubric_item": "r2", "type": ["Other", "Impact"]}]
    judged = asyncio.run(rq.CoverageJudge(complete_text).judge("answer", rubric))
    out = rq.coverage(judged)
    assert len(calls) == 2
    assert out["coverage"] == round((1.0 + 0.25) / 2, 4)
    assert out["by_type"] == {"Citation": 1.0, "Impact": 0.25, "Other": 0.25}
    assert "Response: answer" in calls[0] and "r1\nr2" in calls[0]


def test_paired_summary_by_group():
    recs = [{"group": "non_cs", "domain": "Social Sciences", "arms": {"bare": {"coverage": 0.5, "words": 10},
                                                                       "pipeline": {"coverage": 0.7, "words": 20}}},
            {"group": "non_cs", "domain": "Social Sciences", "arms": {"bare": {"coverage": 0.4, "words": 10},
                                                                       "pipeline": {"coverage": 0.5, "words": 20}}}]
    s = rq.summarize(recs, ["bare", "pipeline"])
    p = s["paired"]["pipeline-bare"]["non_cs"]
    assert p["n"] == 2 and p["wins"] == 2 and abs(p["diff"] - 0.15) < 1e-9
    assert s["paired"]["pipeline-bare"]["cs"] is None
    assert s["arms"]["pipeline"]["non_cs"]["coverage"] == 0.6


def test_repeated_judging_averages_each_item():
    answers = iter(["Completely\nNot at all", "Mostly\nNot at all"])

    async def complete_text(prompt):
        return next(answers)

    rubric = [{"rubric_item": "r1", "type": ["Other"]}, {"rubric_item": "r2", "type": ["Other"]}]
    judged = asyncio.run(rq.CoverageJudge(complete_text, repeats=2).judge("answer", rubric))
    assert [j["score"] for j in judged] == [(1.0 + 0.75) / 2, 0.0]
    assert judged[0]["labels"] == ["completely", "mostly"]


def test_resume_rederives_unfinished_questions_from_sample_params():
    items = _items()
    full = rq.subset(rq.pick_items(items, seed=0), 10)
    meta = {"seed": 0, "n_noncs": 40, "n_cs": 10}
    saved = [{"id": x["id"], "group": x["group"], "item": x} for x in full[:4]]
    out = rq.resume_items(items, meta, saved, subset_arg=10)
    assert [x["id"] for x in out] == [x["id"] for x in full] and meta["subset"] == 10

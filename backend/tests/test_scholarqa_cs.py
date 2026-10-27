"""ScholarQA-CS 评分：权重、长度分与组合规则与官方脚本一致。"""

import asyncio

from backend.eval import scholarqa_cs as sq

_CONFIG = {"question": "q", "other_properties": [
    {"name": "most_important_item_0", "criterion": "c0", "weight": 0.4, "evidence": ["e1", "e2"]},
    {"name": "nice_to_have_item_0", "criterion": "c1", "weight": 0.2, "evidence": []}]}


def test_weights_sum_to_one_and_length_penalty():
    w = sq.score_weights(_CONFIG)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["most_important_item_0"] == w["most_important_item_0_evidence"] == 0.2
    assert sq.length_score("w " * 250) == 1.0 and sq.length_score("w " * 450) == 0.5
    assert sq.length_score("w " * 900) == 0.0


def test_judge_zeroes_evidence_when_criterion_fails():
    async def llm_json(system, user):
        if "claims" in user:
            return {"claims": [{"claim_text": "a", "citations": [{"citation_text": "[1]", "excerpts": ["x"]}]},
                               {"claim_text": "b", "citations": []}]}
        if "<snippets>" in user:
            return {"score": 2}
        return {"score": 0 if "c0" in user else 10}

    out = asyncio.run(sq.RubricJudge(llm_json).score(_CONFIG, "short answer [1]"))
    assert out["citations"] == 0.5 and out["excerpts"] == 1.0
    assert out["most_important_item_0"] == 0.0 and out["most_important_item_0_evidence"] == 0.0
    assert out["nice_to_have_item_0"] == 1.0
    # 0.05 长度 + 0.05 专业 + 0.2×0.5 + 0.1×1 + 0.2×1（nice_to_have）
    assert out["score"] == round(0.05 + 0.05 + 0.1 + 0.1 + 0.2, 4)
    assert out["ann_score"] == round(0.2 / 0.6, 4)


def test_judge_sends_one_call_per_shared_prefix_before_the_rest():
    """各条细则共用“说明+问题+答案”前缀：第一条返回（缓存写入）前不发同前缀的其余调用；分数与齐发时相同。"""
    config = {"question": "q", "other_properties": [
        {"name": f"most_important_item_{i}", "criterion": f"c{i}", "weight": 0.1, "evidence": ["e1", "e2"]}
        for i in range(3)]}
    events: list[tuple[str, str]] = []

    async def llm_json(system, user):
        kind = "claims" if "claims" in user else ("evidence" if "<snippets>" in user else "prop")
        events.append(("start", kind))
        await asyncio.sleep(0.01)
        events.append(("end", kind))
        if kind == "claims":
            return {"claims": [{"claim_text": "a", "citations": [{"citation_text": "[1]", "excerpts": ["x"]}]}]}
        return {"score": 1 if kind == "evidence" else 10}

    scores = asyncio.run(sq.RubricJudge(llm_json).score_all([config, config], "answer [1]"))
    for kind in ("prop", "evidence"):
        seq = [e for e, k in events if k == kind]
        assert seq[:2] == ["start", "end"]   # 第一条单独发出并返回后，其余才发
    assert scores[0] == scores[1] and scores[0]["most_important_item_2_evidence"] == 0.5


def test_resplit_redoes_only_the_claims_split_and_writes_a_new_file(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    config = {**_CONFIG, "question": "q1"}
    old = sq.combine(config, {"length": 1.0, "expertise": 1.0, "citations": 0.0, "excerpts": 0.0,
                              "most_important_item_0": 1.0, "most_important_item_0_evidence": 0.5,
                              "nice_to_have_item_0": 1.0})
    src = tmp_path / "run.json"
    src.write_text(json.dumps({"meta": {}, "summary": {}, "records": [
        {"question": "q1", "system": {"response": "a [1]", "scores": [old]}},
        {"question": "q1", "OpenScholar": {"response": "b [1]", "scores": [old]}}]}), encoding="utf-8")

    async def claims(system, user):
        return {"claims": [{"claim_text": "a", "citations": [{"citation_text": "[1]", "excerpts": ["x"]}]}]}

    monkeypatch.setattr(sq, "load_cases", lambda: [{"question": "q1", "configs": [config]}])
    monkeypatch.setattr(sq, "make_judge", lambda args: sq.RubricJudge(None, claims))
    args = SimpleNamespace(file=str(src), arms=["OpenScholar"], concurrency=2, claims_model="m", judge_model="j")
    out = asyncio.run(sq.resplit(args))
    data = json.loads(out.read_text(encoding="utf-8"))
    new = data["records"][1]["OpenScholar"]["scores"][0]
    assert new["citations"] == new["excerpts"] == 1.0 and new["most_important_item_0_evidence"] == 0.5
    assert new["score"] == round(old["score"] + 0.3, 4) and new["ann_score"] == old["ann_score"]
    assert data["records"][0]["system"]["scores"][0] == old            # 未列入 --arms 的不动
    assert json.loads(src.read_text(encoding="utf-8"))["records"][1]["OpenScholar"]["scores"][0] == old


def test_pick_cases_keeps_seed_sample_and_excludes_it_for_new_questions():
    import random

    from backend.eval.scholarqa_cs import pick_cases

    cases = [{"question": f"q{i}"} for i in range(100)]
    base = pick_cases(cases, 20, 0)
    assert base == random.Random(0).sample(cases, 20)          # 不排除时与原抽样完全一致
    fresh = pick_cases(cases, 20, 1, exclude_seed=0)
    assert len(fresh) == 20 and not {c["question"] for c in base} & {c["question"] for c in fresh}

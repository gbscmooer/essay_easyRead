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


def test_pick_cases_keeps_seed_sample_and_excludes_it_for_new_questions():
    import random

    from backend.eval.scholarqa_cs import pick_cases

    cases = [{"question": f"q{i}"} for i in range(100)]
    base = pick_cases(cases, 20, 0)
    assert base == random.Random(0).sample(cases, 20)          # 不排除时与原抽样完全一致
    fresh = pick_cases(cases, 20, 1, exclude_seed=0)
    assert len(fresh) == 20 and not {c["question"] for c in base} & {c["question"] for c in fresh}

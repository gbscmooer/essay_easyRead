"""开放问答：材料装配、引用验收、参考文献摘录与重写反馈。"""

import asyncio

from backend.services.deep_research import answer_writer as aw

_PAPERS = [
    {"key": "a", "title": "ManyTypes4Py: A Benchmark Python Dataset", "year": 2021,
     "abstract": "We present ManyTypes4Py, a large dataset for machine learning based type inference. "
                 "It contains 5,382 Python projects with over 869K type annotations."},
    {"key": "b", "title": "TypeEvalPy: A Micro-benchmarking Framework", "year": 2023,
     "abstract": "TypeEvalPy contains 154 code snippets with 845 type annotations across 18 categories."},
    {"key": "c", "title": "No abstract paper", "year": 2020, "abstract": ""},
]
_RECORDS = [{"source": {"paper_key": "b"}, "fact": {"name": "TypeEvalPy", "kind": "benchmark"},
             "spans": [{"quote": "TypeEvalPy contains 154 code snippets with 845 type annotations."}]}]


def test_materials_prefer_extracted_evidence_then_abstracts():
    mats = aw.build_materials(_PAPERS, _RECORDS)
    assert [m["key"] for m in mats] == ["b", "a"]  # 有提取证据的在前；无摘要无证据的不进入
    assert mats[0]["excerpts"][0]["source"] == "extract" and mats[1]["excerpts"][0]["source"] == "abstract"
    assert aw.question_language("Python 类型推断用哪些数据集？") == "zh"
    assert aw.question_language("What datasets are used for type inference?") == "en"


def test_check_answer_strips_invalid_ids_and_moves_trailing_citations():
    mats = aw.build_materials(_PAPERS, _RECORDS)
    text = ("Type inference predicts variable types. [2] ManyTypes4Py has 869K annotations [2][9]. "
            "TypeEvalPy covers 18 categories [1]. Both are public.")
    check = aw.check_answer(text, mats, "en")
    assert check["invalid_citations"] == [9] and "[9]" not in check["text"]
    assert "types [2]." in check["text"]
    assert check["sentences"] == 4 and check["cited_share"] == 0.75 and check["problems"]
    refs = aw._references(check["text"], mats)
    assert [r["n"] for r in refs] == [1, 2] and "154 code snippets" in refs[0]["excerpt"]
    assert "References:" in aw.render_answer({"text": check["text"], "references": refs, "language": "en"})


def test_each_citing_sentence_gets_its_own_excerpt():
    papers = [{"key": "a", "title": "ManyTypes4Py", "year": 2021,
               "abstract": "We present ManyTypes4Py, a dataset for type inference. "
                           "It contains 5,382 Python projects. "
                           "Neural baselines reach 70% top-1 accuracy."}]
    mats = aw.build_materials(papers, [])
    text = ("ManyTypes4Py contains 5,382 Python projects [1]. Neural baselines reach 70% top-1 accuracy [1]. "
            "The 5,382 projects are public [1].")
    refs = aw._references(text, mats)
    assert refs[0]["excerpts"] == ["It contains 5,382 Python projects.", "Neural baselines reach 70% top-1 accuracy."]
    assert 'Excerpts: "It contains 5,382 Python projects." "Neural' in aw.render_answer(
        {"text": text, "references": refs, "language": "en"})


def test_write_answer_retries_with_feedback_then_passes():
    calls = []

    async def llm_json(system, user):
        calls.append(user)
        if len(calls) == 1:
            return {"answer": "Several datasets exist. ManyTypes4Py is large [2]."}
        return {"answer": "ManyTypes4Py and TypeEvalPy are the common choices [1][2]. "
                          "ManyTypes4Py has 5,382 projects [2]."}

    out = asyncio.run(aw.write_answer("What datasets are used for Python type inference?",
                                      _PAPERS, _RECORDS, llm_json=llm_json))
    assert out["status"] == "ok" and out["attempts"] == 2 and "没有 [n] 引用" in calls[1]
    assert out["check"]["cited_share"] == 1.0 and [r["key"] for r in out["references"]] == ["b", "a"]


def test_insufficient_material_is_reported_not_answered():
    async def llm_json(system, user):
        raise AssertionError("should not be called")

    out = asyncio.run(aw.write_answer("某个问题？", _PAPERS[2:], [], llm_json=llm_json))
    assert out["status"] == "insufficient" and "材料不足" in out["text"]


def test_inline_bold_headings_and_markup_are_not_counted():
    papers = [{"key": "a", "title": "Light Reflection-Guided RRT<inline-formula><tex-math>$^{*}$</tex-math></inline-formula>",
               "year": 2025, "abstract": "LRH-RRT<inline-formula>*</inline-formula> generates a reference path along obstacles."}]
    mats = aw.build_materials(papers, [])
    assert "<" not in mats[0]["title"] and "<" not in mats[0]["excerpts"][0]["text"]
    text = ("**Heuristic guidance.** LRH-RRT* generates a reference path along obstacles [1].\n"
            "- **Hybrid planners:** A DQN local planner handles constricted spaces [1].")
    check = aw.check_answer(text, mats, "en")
    assert check["sentences"] == 2 and check["cited_share"] == 1.0 and not check["problems"]


def test_aspect_plan_drives_gap_retrieval_and_outline():
    """要点规划：无材料支撑的方面补检索，补来的论文编号接在召回材料之后，提纲进入写作提示。"""
    asked, prompts = [], []

    async def plan_json(system, user):
        return {"aspects": [
            {"title": "Background", "need": "Define type inference", "covered_by": [], "query": "type inference survey"},
            {"title": "Benchmarks", "need": "List datasets", "covered_by": [1, 2, 99], "query": "type benchmarks"},
            {"title": ""}]}

    async def search_more(question, queries):
        asked.append(queries)
        return [{"key": "a", "title": "dup", "abstract": "Already a material paper with abstract text."},
                {"key": "s", "title": "A Survey of Type Inference", "year": 2022,
                 "abstract": "Type inference assigns static types to program variables automatically."},
                {"key": "t", "title": "No abstract", "abstract": ""}]

    async def llm_json(system, user):
        prompts.append(user)
        return {"answer": "Type inference assigns static types to program variables [3]. "
                          "TypeEvalPy covers 18 categories [1]. ManyTypes4Py has 869K annotations [2]."}

    out = asyncio.run(aw.write_answer("What datasets exist for type inference?", _PAPERS, _RECORDS,
                                      llm_json=llm_json, plan_json=plan_json, search_more=search_more))
    assert asked == [["type inference survey"]]  # 已覆盖的方面不补检索
    assert [a["covered_by"] for a in out["aspects"]] == [[], [1, 2]]  # 无效编号与空标题被剔除
    assert out["gap_retrieval"]["added"] == 1 and [p["key"] for p in out["gap_papers"]] == ["s"]
    assert "[3] A Survey of Type Inference" in prompts[0] and "1. Background：Define type inference" in prompts[0]
    assert out["status"] == "ok" and [r["key"] for r in out["references"]] == ["b", "a", "s"]
    # 重建参考文献时用同一装配规则得到同样的编号
    mats = aw.assemble_materials(_PAPERS, _RECORDS, out["gap_papers"])
    assert {r["n"]: r["key"] for r in aw._references(out["text"], mats)} == {1: "b", 2: "a", 3: "s"}


def test_gap_retrieval_can_rescue_question_without_material():
    async def plan_json(system, user):
        return {"aspects": [{"title": "Causes", "need": "x", "covered_by": [], "query": "link failure causes"}]}

    async def search_more(question, queries):
        return [{"key": f"k{i}", "title": f"Paper {i}", "abstract": f"Link layer failure mode number {i} is studied."}
                for i in range(3)]

    async def llm_json(system, user):
        return {"answer": "Link failures have several modes [1][2]. A third mode is also studied [3]."}

    out = asyncio.run(aw.write_answer("Why do links fail?", [_PAPERS[2]], [], llm_json=llm_json,
                                      plan_json=plan_json, search_more=search_more))
    assert out["status"] == "ok" and out["materials"] == 3


def test_knowledge_mode_lets_model_write_beyond_materials_without_fake_citations():
    prompts = []

    async def llm_json(system, user):
        prompts.append((system, user))
        # 一半句子是公认知识、不带编号：知识主导模式下验收通过，不再要求逐句引用
        return {"answer": "Type inference deduces the most general type of each expression. "
                          "Python is dynamically typed, so types are only known at runtime. "
                          "TypeEvalPy contains 154 code snippets [1]. ManyTypes4Py has 869K annotations [2]."}

    out = asyncio.run(aw.write_answer("What datasets exist for type inference?", _PAPERS, _RECORDS,
                                      llm_json=llm_json, knowledge=True))
    system, user = prompts[0]
    assert "资深研究者" in system and "只依据给定材料" not in system
    assert "不得凭记忆补充事实" not in user and "不得编造引用" in user
    assert out["status"] == "ok" and out["attempts"] == 1 and out["knowledge"] is True
    assert out["check"]["cited_share"] == 0.5
    # 默认（只依据材料）模式下同样的答案不通过验收，会带反馈重写
    grounded = aw.check_answer(out["text"], aw.build_materials(_PAPERS, _RECORDS), "en")
    assert grounded["problems"]

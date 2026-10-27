import asyncio
import json

from backend.services.deep_research.stage_agent import StageValidation, run_stage_agent


def test_finish_requires_program_validator_and_can_reenter_loop():
    decisions = iter([
        {"action": "finish", "finish": True, "rationale_summary": "看起来完成"},
        {"action": "add", "arguments": {"value": 2}, "rationale_summary": "补足缺口"},
        {"action": "finish", "finish": True, "rationale_summary": "再次申请完成"},
    ])

    async def model(_view):
        return next(decisions)

    async def add(args, state):
        state["count"] += int(args["value"])
        return {"count": state["count"]}

    def validate(state):
        return StageValidation(state["count"] >= 2, "数量不足" if state["count"] < 2 else "通过")

    result = asyncio.run(run_stage_agent(
        stage="test", state={"count": 0}, model_step=model, tools={"add": add},
        validator=validate, max_iterations=4, no_progress_limit=2,
        progress_marker=lambda state: state["count"],
    ))
    assert result.status == "completed"
    assert result.iterations == 3
    assert result.trace[0]["validation"]["passed"] is False
    assert result.trace[-1]["validation"]["passed"] is True


def test_unlisted_tool_is_blocked_and_no_progress_stops_loop():
    async def model(_view):
        return {"action": "shell", "arguments": {"cmd": "anything"}}

    result = asyncio.run(run_stage_agent(
        stage="test", state={}, model_step=model, tools={},
        validator=lambda _state: StageValidation(False, "not done"),
        max_iterations=5, no_progress_limit=2,
    ))
    assert result.status == "partial"
    assert result.stop_reason == "no_progress"
    assert result.iterations == 2
    assert result.trace[0]["observation"]["code"] == "TOOL_NOT_ALLOWED"


def test_iteration_budget_returns_partial_with_existing_state():
    async def model(_view):
        return {"action": "increment", "arguments": {}}

    async def increment(_args, state):
        state["count"] += 1
        return {"count": state["count"]}

    result = asyncio.run(run_stage_agent(
        stage="test", state={"count": 0}, model_step=model, tools={"increment": increment},
        validator=lambda _state: StageValidation(False), max_iterations=3,
        progress_marker=lambda state: state["count"],
    ))
    assert result.status == "partial"
    assert result.stop_reason == "iteration_budget_exhausted"
    assert result.state["count"] == 3


def test_model_error_returns_auditable_partial_result():
    async def model_step(_view):
        raise RuntimeError("gateway unavailable")

    result = asyncio.run(run_stage_agent(
        stage="test", state={"count": 0}, model_step=model_step, tools={},
        validator=lambda _state: StageValidation(True), max_iterations=3,
    ))
    assert result.status == "partial"
    assert result.stop_reason == "model_error:RuntimeError"
    assert result.trace[0]["observation"]["code"] == "RuntimeError"


def test_llm_error_code_is_kept_in_stop_reason_and_logged(caplog):
    from backend.services.llm.errors import LLMError

    async def model_step(_view):
        raise LLMError("LLM_RATE_LIMITED", "Error code: 429 - 已达到 5 小时的使用上限", status_code=429)

    with caplog.at_level("WARNING"):
        result = asyncio.run(run_stage_agent(
            stage="decompose", state={}, model_step=model_step, tools={},
            validator=lambda _state: StageValidation(True), max_iterations=3,
        ))
    # 错误码进入停止原因 → 运行失败信息带 LLM_RATE_LIMITED，评测层据此等待重置后重跑
    assert result.stop_reason == "model_error:LLM_RATE_LIMITED"
    assert result.trace[0]["observation"]["code"] == "LLM_RATE_LIMITED"
    assert "使用上限" in caplog.text


def test_model_timeout_stops_and_preserves_partial_state():
    async def model_step(_view):
        await asyncio.sleep(.05)
        return {"action": "finish", "finish": True}

    result = asyncio.run(run_stage_agent(
        stage="test", state={"saved": ["evidence-before-timeout"]},
        model_step=model_step, tools={}, validator=lambda _state: StageValidation(False),
        max_iterations=3, turn_timeout=.001,
    ))
    assert result.status == "partial"
    assert result.stop_reason == "model_error:TimeoutError"
    assert result.state["saved"] == ["evidence-before-timeout"]
    assert result.audit()["failure_count"] == 1


def test_repeated_read_without_new_state_stops_as_no_progress():
    async def model_step(_view):
        return {"action": "read_same", "arguments": {"evidence_id": "ev_1"}}

    async def read_same(_args, _state):
        return {"evidence_id": "ev_1", "cached": True}

    result = asyncio.run(run_stage_agent(
        stage="test", state={"evidence_ids": ["ev_1"]}, model_step=model_step,
        tools={"read_same": read_same}, validator=lambda _state: StageValidation(False),
        max_iterations=5, no_progress_limit=2,
        progress_marker=lambda state: tuple(state["evidence_ids"]),
    ))
    assert result.status == "partial"
    assert result.stop_reason == "no_progress"
    assert result.iterations == 2


# ── 上下文压缩：保结构（v7.4.1 研究 5 索引坍缩回归） ────────────────────────

def test_compact_keeps_every_evidence_id_structured():
    """12 条证据索引压缩后仍是结构化列表：每条 evidence_id 完整可见。

    旧实现在超限时把整个对象坍缩成 {"__truncated__": "..."}，模型看不到
    有效编号就会凭空生成不存在的 evidence_id。
    """
    from backend.services.deep_research.stage_agent import _compact

    index = {
        "evidence_version": "es_" + "a" * 24,
        "subquestion_id": "effect_comparison",
        "cursor": 0, "limit": 12, "total": 12, "next_cursor": None,
        "coverage": {f"subq{i}": {"evidence_count": 3, "verified_count": 2} for i in range(5)},
        "items": [{
            "evidence_id": f"ev_{i:024d}",
            "evidence_type": "taxonomy",
            "subquestion_ids": ["method_mechanism"],
            "paper_key": f"doi:10.1000/paper{i}",
            "fact_summary": "视觉基础模型 " + "细节描述" * 120,
            "missing_fields": ["hardware", "batch_size"],
            "comparability_status": "not_applicable",
            "verification_status": "relation_verified",
            "location_status": "resolved",
        } for i in range(12)],
    }
    # 模型视图的实际观察预算 6000：一整页 12 条的编号全部可见
    compacted = _compact(index, max_chars=6000)
    assert isinstance(compacted, dict)
    assert "__truncated__" not in compacted
    items = compacted.get("items")
    assert isinstance(items, list)
    ids = [row.get("evidence_id") for row in items if isinstance(row, dict)]
    assert f"ev_{0:024d}" in ids and f"ev_{11:024d}" in ids
    # 更紧的预算下也不坍缩：保留结构完整的前几条 + __dropped__ 计数，
    # 模型配合 next_cursor 分页仍能拿到全部编号
    tighter = _compact(index, max_chars=4000)
    assert isinstance(tighter.get("items"), list)
    kept_ids = [row.get("evidence_id") for row in tighter["items"]
                if isinstance(row, dict) and "evidence_id" in row]
    assert kept_ids and f"ev_{0:024d}" == kept_ids[0]
    assert any(isinstance(row, dict) and "__dropped__" in row for row in tighter["items"])


def test_compact_trims_list_tail_instead_of_collapsing_structure():
    """极端超限时裁列表尾部（带 __dropped__ 计数）而不是丢掉全部结构。"""
    from backend.services.deep_research.stage_agent import _compact

    payload = {"rows": [{"id": f"r{i}", "text": "x" * 500} for i in range(60)]}
    compacted = _compact(payload, max_chars=2000)
    assert isinstance(compacted, dict) and isinstance(compacted.get("rows"), list)
    kept = [row for row in compacted["rows"] if isinstance(row, dict) and "id" in row]
    assert len(kept) >= 1
    dropped = [row for row in compacted["rows"] if isinstance(row, dict) and "__dropped__" in row]
    assert dropped and dropped[0]["__dropped__"] >= 1


def test_view_orders_volatile_fields_last_for_prefix_cache():
    """网关前缀缓存按最长相同前缀命中：逐轮不变的键在前、轮次号殿后，
    否则 iteration 出现在载荷开头会让每一轮的前缀在第一个字节处断掉。"""
    views = []

    async def model(view):
        views.append(view)
        return {"action": "ping", "arguments": {}}

    async def ping(_args, state):
        state["calls"] = state.get("calls", 0) + 1
        return {"pong": True}

    asyncio.run(run_stage_agent(
        stage="test", state={"calls": 0}, model_step=model, tools={"ping": ping},
        validator=lambda _state: StageValidation(False),
        max_iterations=2, no_progress_limit=1,
        progress_marker=lambda state: state["calls"],
    ))

    keys = list(views[0])
    assert keys[0] == "stage"
    assert keys.index("available_tools") < keys.index("state")
    assert keys[-2:] == ["iteration", "max_iterations"]
    # 逐轮 view 前缀（iteration 之前的部分）字节稳定：state/工具表不随轮次重排
    first_prefix = json.dumps({k: views[0][k] for k in keys[:-2]}, ensure_ascii=False)
    second_prefix = json.dumps({k: views[1][k] for k in keys[:-2]}, ensure_ascii=False)
    assert first_prefix[:64] == second_prefix[:64]

import asyncio

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

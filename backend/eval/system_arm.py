"""评测的系统臂：在临时 SQLite 上完整跑一次研究循环（真模型 + 真检索源），保存 result 并提取可比字段。

scholarqa_cs / researchqa / interaction_case 共用。并发跑多题时用 shared_engine() 共享一个临时库。
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path


def _fresh_engine(td: str):
    from sqlmodel import SQLModel, create_engine

    # 显式导入全部表模型（与 tests/conftest.py 的建表集一致）
    from backend.models.analysis import AnalysisResult  # noqa: F401
    from backend.models.annotation import Annotation  # noqa: F401
    from backend.models.code_analysis import CodeAnalysis  # noqa: F401
    from backend.models.exploration import ExplorationRun  # noqa: F401
    from backend.models.note import Note  # noqa: F401
    from backend.models.paper import Paper  # noqa: F401
    from backend.models.research import PaperReference, ResearchGraph, ResearchRun  # noqa: F401
    from backend.models.tag import PaperTag, Tag  # noqa: F401
    from backend.models.workspace import Workspace, WorkspacePaper  # noqa: F401

    engine = create_engine(f"sqlite:///{td}/eval.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@contextlib.contextmanager
def shared_engine():
    """临时库：runner.get_engine 只替换一次，各题各自建 run 行。"""
    import tempfile

    from backend.services.deep_research.loop import runner

    with tempfile.TemporaryDirectory(prefix="system_arm_") as td:
        engine = _fresh_engine(td)
        original = runner.get_engine
        runner.get_engine = lambda: engine
        try:
            yield engine
        finally:
            runner.get_engine = original
            engine.dispose()


async def run_system_arm(question: str, depth: str = "standard", save_as: Path | None = None, *,
                         options: dict | None = None, engine=None) -> dict:
    from sqlmodel import Session

    from backend.models.exploration import ExplorationRun
    from backend.services.deep_research.loop import run_research

    with contextlib.ExitStack() as stack:
        if engine is None:
            engine = stack.enter_context(shared_engine())
        with Session(engine) as s:
            run = ExplorationRun(question=question, depth=depth or "standard", options=dict(options or {}))
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
        t0 = time.monotonic()
        await run_research(run_id)
        wall = round(time.monotonic() - t0, 1)
        with Session(engine) as s:
            run = s.get(ExplorationRun, run_id)
            result, status, error = run.result or {}, run.status, run.error_message
        if save_as is not None:
            save_as.parent.mkdir(parents=True, exist_ok=True)
            save_as.write_text(json.dumps({"question": question, "depth": depth, "status": status,
                                           "error_message": error, "result": result},
                                          ensure_ascii=False, default=str), encoding="utf-8")
    crawl = result.get("crawl") or {}
    usage = (result.get("sources") or {}).get("token_usage") or {}
    return {"total": len(result.get("papers") or []), "candidates": crawl.get("candidates"),
            "expanded": crawl.get("expanded"), "stop_reason": crawl.get("stop_reason"),
            "selector": crawl.get("selector"), "wall_s": wall, "tokens": usage.get("total"),
            "tokens_by_stage": {k: v for k, v in usage.items() if k != "total"},
            "answer_status": (result.get("answer") or {}).get("status"),
            "error": f"run {status}: {error or 'unknown'}"[:300] if status != "done" else None,
            "saved": str(save_as) if save_as else None}

"""典型案例：用户过程干预（任务书：对检索过程补充、修正、追问与干预）。

真实 LLM + 真实检索源，走真实 HTTP 路由（ASGITransport，同一事件循环里跑 worker）：
  1. 新建八阶段研究；召回完成后请求暂停；
  2. 暂停时剔除 2 篇文献并追加 1 条检索式（/adjust），续跑到完成；
  3. 对完成的研究提交修正（/revise）：补充条件 + 复用材料，跑完子版本；
  4. 读取版本链，汇总两次运行的判定、完整性提示与干预审计。

用法：python -m backend.eval.interaction_case [--depth quick] [--question ...]
结果写入 data/eval/interaction_case_{stamp}.json；数据库用临时库，不碰本地数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

from backend.eval.explore_eval import _fresh_engine

DEFAULT_QUESTION = "多模态大模型用于手术视频理解的研究现状如何？"
DEFAULT_CONDITION = "只关注腹腔镜手术场景"
DEFAULT_QUERY = "laparoscopic surgical video vision language model"
_POLL_S = 5.0


def _summary(run: dict) -> dict:
    result = run.get("result") or {}
    synthesis = result.get("synthesis") or {}
    verdict = synthesis.get("verdict") or {}
    sources = result.get("sources") or {}
    recall = sources.get("recall") or {}
    return {
        "id": run.get("id"), "status": run.get("status"), "question": run.get("question"),
        "verdict": verdict.get("status"), "confidence": verdict.get("confidence"),
        "total": (result.get("metrics") or {}).get("total"),
        "integrity": result.get("integrity"),
        "interventions": result.get("interventions"),
        "user_supplements": recall.get("user_supplements"),
        "reuse": recall.get("reuse"),
        "excluded": sources.get("excluded"),
        "stage_timeline": sources.get("stage_timeline"),
        "error_message": run.get("error_message"),
    }


async def _wait(client, run_id: int, *, until: tuple[str, ...], timeout_s: float = 7200) -> dict:
    t0 = time.monotonic()
    while True:
        run = (await client.get(f"/api/exploration/runs/{run_id}")).json()
        if run["status"] in until:
            return run
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"run {run_id} 停留在 {run['status']}/{run.get('current_stage')}")
        await asyncio.sleep(_POLL_S)


async def main_async(args) -> dict:
    import httpx
    from fastapi import FastAPI

    from backend.main import register_http_error_handlers
    from backend.routers.exploration import router
    from backend.services import database

    app = FastAPI()
    register_http_error_handlers(app)
    app.include_router(router)
    log: list[dict] = []

    def note(step: str, **data) -> None:
        row = {"t": round(time.monotonic() - t_start, 1), "step": step, **data}
        log.append(row)
        print(json.dumps(row, ensure_ascii=False, default=str)[:400], flush=True)

    t_start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="interaction_case_") as td:
        engine = _fresh_engine(td)
        original = database._shared_engine
        database._shared_engine = engine
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://case", timeout=60) as client:
                # 1) 新建研究，召回完成后暂停
                created = (await client.post("/api/exploration/runs", json={
                    "question": args.question, "depth": args.depth})).json()
                run_id = created["id"]
                note("created", run_id=run_id, workflow=created.get("workflow_version"))
                while True:
                    run = (await client.get(f"/api/exploration/runs/{run_id}")).json()
                    if run["status"] in ("done", "failed"):
                        raise RuntimeError(f"未能在召回后暂停：run 已 {run['status']}")
                    if run.get("current_stage") not in (None, "preflight", "protocol", "recall"):
                        break
                    await asyncio.sleep(1.0)
                paused = (await client.post(f"/api/exploration/runs/{run_id}/pause", json={})).json()
                note("pause_requested", stage=run.get("current_stage"), response=paused)
                run = await _wait(client, run_id, until=("paused", "done", "failed"))
                if run["status"] != "paused":
                    raise RuntimeError(f"暂停未生效：{run['status']}")
                papers = (run.get("result") or {}).get("papers") or []
                note("paused", stage=run.get("current_stage"), papers=len(papers))

                # 2) 暂停时干预：剔除两篇（取最早的两篇，模拟“过时材料不要”）+ 追加检索式
                victims = sorted((p for p in papers if p.get("key")), key=lambda p: p.get("year") or 9999)[:2]
                adjust = await client.post(f"/api/exploration/runs/{run_id}/adjust", json={
                    "exclude_keys": [p["key"] for p in victims], "add_queries": [args.query],
                    "note": "早期材料与问题无关；补充腹腔镜方向检索"})
                note("adjusted", code=adjust.status_code, body=adjust.json(),
                     excluded_titles=[p.get("title") for p in victims])
                resumed = await client.post(f"/api/exploration/runs/{run_id}/resume", json={})
                note("resumed", code=resumed.status_code)
                first = await _wait(client, run_id, until=("done", "failed"))
                note("first_done", **{k: v for k, v in _summary(first).items() if k in ("status", "verdict", "total")})
                excluded_keys = {p["key"] for p in victims}
                leaked = [p["key"] for p in (first.get("result") or {}).get("papers") or [] if p.get("key") in excluded_keys]

                # 3) 修正：补充条件 + 复用材料
                revise = await client.post(f"/api/exploration/runs/{run_id}/revise", json={
                    "question": args.question, "expected_state_version": first.get("state_version") or 0,
                    "additional_questions": [args.condition], "reuse_materials": True, "depth": args.depth})
                note("revise_submitted", code=revise.status_code)
                child_id = revise.json()["id"]
                child = await _wait(client, child_id, until=("done", "failed"))
                note("child_done", **{k: v for k, v in _summary(child).items() if k in ("status", "verdict", "total")})

                # 4) 版本链
                versions = (await client.get(f"/api/exploration/runs/{child_id}/versions")).json()
        finally:
            database._shared_engine = original
            engine.dispose()

    return {
        "meta": {"question": args.question, "condition": args.condition, "query": args.query,
                 "depth": args.depth, "wall_s": round(time.monotonic() - t_start, 1)},
        "checks": {
            "paused_after_recall": True,
            "excluded_absent_after_resume": not leaked,
            "intervention_recorded": bool(_summary(first)["interventions"]),
            "supplement_recall_ran": bool(_summary(first)["user_supplements"]),
            "child_reused_parent": bool((_summary(child)["reuse"] or {}).get("kept")),
            "version_chain": [row["id"] for row in versions.get("ancestors") or []] + [versions["current"]["id"]],
        },
        "first": _summary(first), "child": _summary(child), "versions": versions, "log": log,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--condition", default=DEFAULT_CONDITION)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--depth", default="quick")
    parser.add_argument("--thinking-effort", default="off")
    args = parser.parse_args()
    if args.thinking_effort:
        # 与 explore_eval 同一覆盖方式：所有阶段都经 ai_config.get_llm_config 取配置
        from backend.services import ai_config

        original_config = ai_config.get_llm_config
        ai_config.get_llm_config = lambda: {**original_config(), "thinking_effort": args.thinking_effort}
    out = asyncio.run(main_async(args))
    path = Path("data/eval") / f"interaction_case_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"完成 → {path}", flush=True)
    print(json.dumps(out["checks"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

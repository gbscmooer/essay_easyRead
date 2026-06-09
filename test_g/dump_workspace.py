"""
把真实工作区导出成 PaperFeatures JSON fixture —— "系统 ↔ 算法" 的桥

这是**唯一**碰 DB 的脚本（手动运行，不被 app import）。它把 Paper + AnalysisResult +
已持久化图谱里的 cites 边，抽取成 graphkit 能直接吃的纯数据，写到 fixtures/。
之后 run.py 就能完全离线地在这份真实数据上调算法。

用法：
    .venv/bin/python -m test_g.dump_workspace --ws 1 --out test_g/fixtures/ws1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlmodel import Session, create_engine, select

from backend.config import settings
from backend.models.paper import Paper
from backend.models.workspace import WorkspacePaper
from backend.models.analysis import AnalysisResult
from backend.services.deep_research.graphkit import PaperFeatures


def _engine():
    return create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})


def dump(ws_id: int) -> dict:
    eng = _engine()
    with Session(eng) as s:
        papers = s.exec(
            select(Paper).join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == ws_id)
        ).all()
        pids = set(p.id for p in papers)
        analyses = {a.paper_id: a for a in s.exec(
            select(AnalysisResult).where(AnalysisResult.paper_id.in_(list(pids)))
        ).all()}

    # 引用边：与生产 graph_builder 一致，由 parent_paper_id 推断"父 → 子"引用
    # （联网扩展时子论文是父论文的参考文献，故父 cites 子）。
    cites: dict[int, set[int]] = {pid: set() for pid in pids}
    for p in papers:
        parent = getattr(p, "parent_paper_id", None)
        if parent in pids:
            cites[parent].add(p.id)

    out = []
    for p in papers:
        a = analyses.get(p.id)
        if a is not None:
            method = a.method or {}
            exp = a.experiments or {}
            pf = PaperFeatures(
                id=p.id, title=a.title or p.title or "", year=a.year,
                is_analyzed=True,
                purpose_text=" ".join(filter(None, [a.problem, a.one_sentence, *(a.key_contributions or [])])),
                method_text=" ".join(filter(None, [method.get("overview", ""), method.get("pipeline", "")])),
                datasets=[d for d in (exp.get("datasets") or []) if str(d).strip()],
                baselines=[b for b in (exp.get("baselines") or []) if str(b).strip()],
                cites=cites.get(p.id, set()),
            )
        else:
            pf = PaperFeatures(id=p.id, title=p.title or "", is_analyzed=False, cites=cites.get(p.id, set()))
        out.append(pf.to_dict())

    return {"_note": f"workspace {ws_id} 导出", "papers": out}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    data = dump(args.ws)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(data['papers'])} 篇 → {args.out}")

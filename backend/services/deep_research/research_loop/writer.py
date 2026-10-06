"""Project supported frozen answers into chapters without introducing new facts."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from sqlmodel import select

from ....models.exploration import ExplorationRun
from ....models.research_core import ResearchAsset, ResearchReportRevision
from ...research_event_log import append_event
from .contracts import LOOP_VERSION, digest
from .dossier import retrieve
from .evidence import project_facts


def render_html(report: dict) -> str:
    def esc(text):
        return html.escape(str(text))
    blocks = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
              "<style>body{max-width:1000px;margin:40px auto;font:16px/1.7 system-ui;padding:20px}blockquote{border-left:3px solid #888;padding:12px}pre{white-space:pre-wrap}</style><body>",
              f"<h1>{esc(report['title'])}</h1><p>{esc(report['summary'])}</p>"]
    for chapter in report["chapters"]:
        if report.get("workflow_revision") in ("question_loop_v19", "question_loop_v20"):
            blocks.append(f"<h2>{esc(chapter['title'])}</h2>")
            for paragraph in chapter["text"].split("\n\n"):
                safe = re.sub(r"\[(\d+)\]", r'<a href="#ref-\1">[\1]</a>', esc(paragraph))
                if paragraph.startswith("### "):
                    blocks.append("<h3>" + safe[4:] + "</h3>")
                elif paragraph.startswith("**") and paragraph.endswith("**"):
                    blocks.append("<h3>" + safe[2:-2] + "</h3>")
                else:
                    blocks.append("<p>" + safe.replace("\n", "<br>") + "</p>")
        else:
            blocks += [f"<h2>{esc(chapter['title'])}</h2><pre>{esc(chapter['text'])}</pre>"]
        for evidence in chapter["evidence"]:
            blocks.append(f"<blockquote id='{esc(evidence['evidence_id'])}'>{esc(evidence['fact'])}<br><small>{esc(evidence['spans'][0]['quote'])}</small></blockquote>")
    blocks.append("<h2>参考材料</h2>")
    for idx, paper in enumerate(report["references"], 1):
        blocks.append(f"<p id='ref-{idx}'>[{idx}] {esc(paper.get('title', ''))} {esc(paper.get('year') or '')}</p>")
    return "\n".join(blocks) + "</body></html>"


def build_report(tools, dossier):
    manifest = dossier.manifest
    revision = tools.repo.protocol().get("execution_revision") or ((tools.repo.run().options or {}).get("workflow") or {}).get("loop_revision") or LOOP_VERSION
    papers = {}
    frozen_documents = {}
    for aid, content_hash in manifest["assets"].items():
        asset = tools.repo.asset(aid, readable=True)
        if asset.content_hash != content_hash or not tools.allowed((asset.inline_payload or {}).get("paper") or {}):
            raise ValueError("冻结材料版本或范围已变化")
        paper = (asset.inline_payload or {})["paper"]
        papers.setdefault(paper["key"], paper)
        frozen_documents[aid] = asset
    used_asset_ids = {e["asset_id"] for q in manifest["questions"] for e in retrieve(manifest, q["question_id"])}
    used_keys = {(frozen_documents[aid].inline_payload or {})["paper"]["key"] for aid in used_asset_ids}
    references = [p for p in papers.values() if p["key"] in used_keys]
    numbers = {p["key"]: idx + 1 for idx, p in enumerate(references)}
    asset_keys = {aid: (tools.repo.asset(aid, readable=True).inline_payload or {}).get("paper", {}).get("key") for aid in manifest["assets"]}
    chapters, findings, gaps = [], [], []
    for question in manifest["questions"]:
        answer = next(a for a in manifest["answers"] if a["question_id"] == question["question_id"])
        evidence = [{**e, "paper_key": asset_keys[e["asset_id"]]} for e in retrieve(manifest, question["question_id"])]
        citations = sorted({numbers[asset_keys[e["asset_id"]]] for e in evidence if asset_keys.get(e["asset_id"]) in numbers})
        conclusion = answer["conclusion"]
        conditions = answer["conditions"]
        fallback_projection = not conclusion and bool(evidence)
        if not conclusion and evidence and revision in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
            conclusion = "综合答案尚未通过支持检查；下列材料可展开原文核查，不能视为完整研究结论。"
            conditions = list((answer.get("assessment") or {}).get("scope_constraints", []))
        elif not conclusion and evidence:
            conclusion = "综合答案尚未通过支持检查。" + project_facts(evidence)[0]
            conditions = ["要点限定于所引材料的对象、条件和摘要/全文范围", *[
                f"{k}：{v}" for e in evidence for k, v in e["conditions"].items()]]
        text = re.sub(r"\[\d+(?:\s*[,;]\s*\d+)*\]", "", conclusion) or "现有材料不足以形成有依据的答案。"
        evidence_numbers = {e["evidence_id"]: numbers[asset_keys[e["asset_id"]]] for e in evidence}
        if revision in ("question_loop_v19", "question_loop_v20") and any(
            eid not in evidence_numbers for eid in re.findall(r"【(ev_[A-Za-z0-9_]+)】", conclusion)
        ):
            raise ValueError("正文引用不属于冻结证据版本")
        text = re.sub(r"【(ev_[A-Za-z0-9_]+)】", lambda m: f"[{evidence_numbers[m[1]]}]" if m[1] in evidence_numbers else "", text)
        text = re.sub(r"(?:\[\d+\]){2,}", lambda m: "".join(dict.fromkeys(re.findall(r"\[\d+\]", m[0]))), text)
        if not re.search(r"\[\d+\]", text):
            text += " " + " ".join(f"[{n}]" for n in citations)
        if revision in ("question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
            conditions = [re.sub(r"【(ev_[A-Za-z0-9_]+)】", lambda m: f"[{evidence_numbers[m[1]]}]" if m[1] in evidence_numbers else "", c)
                          for c in conditions]
        if conditions:
            text += ("\n\n**适用条件**\n\n" + "\n".join("- " + c for c in conditions)
                     if revision in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") else "\n适用条件：" + "；".join(conditions))
        if answer["conflicts"]:
            text += ("\n\n" if revision in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") else "\n") + ("原综合检查提示：" if fallback_projection else "冲突：") + "；".join(answer["conflicts"])
        if answer["gaps"]:
            text += ("\n\n**仍缺**\n\n" + "\n".join("- " + gap for gap in answer["gaps"])
                     if revision in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") else "\n仍缺：" + "；".join(answer["gaps"]))
        gaps.extend(answer["gaps"])
        chapters.append({"question_id": question["question_id"], "title": question["text"], "text": text,
                         "evidence": evidence, "answer_revision_id": answer["revision_id"], "status": answer["status"],
                         "relations": [r for r in manifest.get("relations", []) if r["subquestion_id"] == question["question_id"]
                             and r["conditions"].get("answer_revision_id") == answer["revision_id"]],
                         "requirement_ledger": answer.get("assessment", {}).get("requirement_ledger", [])})
        if answer["conclusion"]:
            findings.append(text)
    complete = all(a["status"] == "supported" for q, a in zip(manifest["questions"], manifest["answers"]) if q["required"])
    searches = [tools.repo.asset(aid, readable=True).inline_payload or {} for aid in manifest.get("search_records", {})]
    attempts = [row for search in searches for row in (search.get("attempts") or ([search] if search.get("provider") else []))]
    readable_keys = {(a.inline_payload or {})["paper"]["key"] for a in frozen_documents.values()
                     if (a.inline_payload or {}).get("text", "").strip()}
    fulltext_keys = {(a.inline_payload or {})["paper"]["key"] for a in frozen_documents.values()
                     if (a.inline_payload or {}).get("source_kind") != "abstract" and (a.inline_payload or {}).get("text", "").strip()}
    examined_keys = {(frozen_documents[aid].inline_payload or {})["paper"]["key"]
                     for aid in manifest.get("examined_assets", []) if aid in frozen_documents}
    search_summary = {"queries": list(dict.fromkeys(row.get("query", "") for row in attempts)), "sources": [],
        "attempts": [{"phase": "research", "round": idx + 1, "order": "relevance", "limit": 12, **row}
                     for idx, row in enumerate(attempts)], "attempt_count": len(attempts),
        "total_channel_hits": sum(row.get("hits", 0) for row in attempts), "directions": gaps,
        "stop_reason": dossier.stop_reason, "interpretation": "未命中、来源失败与政策拒绝均不能证明不存在相关实现。"}
    report = {"version": 4, "workflow_revision": revision, "title": tools.repo.run().question,
              "created_at": datetime.now(timezone.utc).isoformat(), "status": "completed" if complete else "partial",
              "quality_status": "internally_checked" if complete else "needs_review",
              "summary": "已按冻结问题组织答案与依据。内部支持检查不等同于人工评审。",
              "findings": findings, "limitations": gaps, "gaps": gaps,
              "chapters": chapters, "dossier_revision_id": dossier.revision_id, "dossier_hash": dossier.content_hash,
              "references": references, "materials": list(papers.values()),
              "warnings": ["未命中不能推断不存在；摘要证据仅支持摘要范围。"],
              "search_summary": search_summary,
              "coverage": {"corpus": len(papers), "examined": len(examined_keys), "used": len(used_keys),
                           "with_material": len(readable_keys), "selected": len(examined_keys),
                           "full_text": len(fulltext_keys),
                           "abstract_only": len(readable_keys - fulltext_keys),
                           "failed": sum(t["state"] == "failed" for t in manifest.get("material_tasks", [])),
                           "rejected_rows": sum(d.get("status") != "relevant" for s in searches for d in s.get("decisions", []))},
              "tables": {name: [] for name in ("frameworks", "metrics", "tasks", "taxonomy", "experimental_comparisons")},
              "bibliometrics": {"by_year": {}, "year_span": []},
              "matrices": {name: {"rows": [], "columns": [], "cells": []} for name in ("aspect_metric", "task_dataset")},
              "extractions": [], "sections": {}, "method": {"stop_reason": dossier.stop_reason,
                  "final_audit": {"status": "not_requested"}, "writer": "frozen_answer_projection_v1"},
              "delivery": {"delivery_status": "ready", "narrative_source": "frozen_answers"}}
    if revision in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
        report["method"]["stop_details"] = (tools.repo.run().checkpoint or {}).get("stop_details", [])
    if not frozen_documents:
        report["status"] = "partial" if revision in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and dossier.stop_reason in ("planning_failed", "execution_failed") else "no_results"
    if revision == "question_loop_v20":
        initial_searches = [s for s in searches if s.get("recall_phase") == "initial"]
        report["method"]["initial_recall"] = initial_searches[-1].get("initial_recall", {}) if initial_searches else {}
        report["method"]["retrieval_not_executed"] = not attempts and not frozen_documents
        failures = [row for row in attempts if row.get("status") not in ("ok", "ok_empty")]
        unreviewed = sum(s.get("unreviewed_count", 0) for s in searches)
        report["method"]["retrieval_incomplete"] = bool(failures or unreviewed)
        report["method"]["unreviewed_candidate_observations"] = unreviewed
        if not frozen_documents and (failures or unreviewed):
            report["status"] = "partial"
            report["summary"] = "检索或候选筛选尚未完成，当前没有准入材料可形成研究结论；不能据此认定没有相关论文。"
        if report["method"]["retrieval_not_executed"] and dossier.stop_reason == "model_error":
            report["status"] = "partial"
    if revision in ("question_loop_v4", "question_loop_v5", "question_loop_v6") and chapters:
        # Surface a complete checked paragraph, without another rewrite or truncating a claim.
        paragraphs = re.split(r"\n\s*\n", chapters[0]["text"])
        lead = next((p for p in paragraphs if not p.startswith("**") and re.search(r"\[\d+\]", p)), "")
        if lead and len(lead) <= 700:
            report["summary"] = lead
    if revision in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and chapters:
        ledger = chapters[0]["requirement_ledger"]
        report["summary"] = f"本问题已核查 {sum(bool(u.get('resolved')) for u in ledger)} / {len(ledger)} 项必答内容。" + (
            "各项回答、文献关系和原文依据见下文。" if complete else "未解决项目已保留为缺口，已有依据的部分见下文。")
    if revision in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and dossier.stop_reason == "planning_failed":
        report["summary"] = "研究规划未完成，尚未执行文献检索与分析；当前没有研究结论。"
    if revision in ("question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and report["method"].get("stop_details") and dossier.stop_reason == "resource_limit":
        report["summary"] += "剩余额度无法同时承担补读与最终综合，保留当前有依据的答案及缺口。"
    if revision in ("question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20"):
        report["delivery"]["answer_status"] = "complete" if complete and used_keys else "partial" if used_keys else "none"
        if not used_keys and dossier.stop_reason != "planning_failed":
            report["summary"] = "尚未形成通过核查的研究结论；已保留材料、核查记录与未解决问题。"
    if revision in ("question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and dossier.stop_reason == "material_selection_failed":
        report["status"] = "partial"
        report["summary"] = "选材工具未完成，已保留召回材料与失败记录；当前没有完整研究答案。"
    if revision in ("question_loop_v18", "question_loop_v19", "question_loop_v20"):
        report["method"]["material_selection"] = manifest.get("material_selection", {})
        if dossier.stop_reason == "material_selection_empty" and frozen_documents:
            report["status"] = "partial"
            report["summary"] = "已召回可读候选，但尚未选出可执行的阅读组合；具体选材缺口见下文，当前没有受支持的研究答案。"
        elif dossier.stop_reason == "material_selection_empty":
            report["summary"] = "未取得可读的准入材料；检索记录与具体缺口已保留，当前没有研究结论。"
    if revision in ("question_loop_v19", "question_loop_v20") and chapters and used_keys:
        report["method"]["writer"] = "supported_narrative_projection_v2"
        ledger = chapters[0]["requirement_ledger"]
        report["method"]["requirements_checked"] = {
            "resolved": sum(bool(u.get("resolved")) for u in ledger), "total": len(ledger), "human_reviewed": False,
        }
        lead = next((p for p in chapters[0]["text"].split("\n\n")
                     if not p.startswith(("#", "**", "待补充")) and re.search(r"\[\d+\]", p)), "")
        if lead:
            report["summary"] = lead
    markdown = ["# " + report["title"], "", report["summary"], "", "停止原因：" + dossier.stop_reason, ""]
    quoted_windows = set()
    appendix = []
    for chapter in chapters:
        markdown += ["## " + chapter["title"], "", chapter["text"], ""]
        for evidence in chapter["evidence"]:
            if revision in ("question_loop_v19", "question_loop_v20"):
                for span in evidence["spans"]:
                    key = digest({"asset_id": evidence["asset_id"], "doc_content_hash": evidence.get("doc_content_hash"), "span": span})
                    if key not in quoted_windows:
                        appendix += ["### " + papers[asset_keys[evidence["asset_id"]]]["title"], "",
                                     "> " + span["quote"].replace("\n", "\n> "), ""]
                        quoted_windows.add(key)
            else:
                markdown += ["> " + evidence["spans"][0]["quote"].replace("\n", "\n> "), ""]
    markdown += ["## 参考材料", "", *[f"[{idx}] {p.get('title')} ({p.get('year') or '年份未知'})" for idx, p in enumerate(references, 1)]]
    if appendix:
        markdown += ["", "## 原文核查材料", "", *appendix]
    report["markdown"] = "\n".join(markdown)
    return report


def save_report(tools, dossier, report):
    repo = tools.repo
    with repo.transaction() as session:
        prior = session.exec(select(ResearchReportRevision).where(
            ResearchReportRevision.run_id == repo.run_id, ResearchReportRevision.is_selected == True)).first()  # noqa: E712
        if prior:
            prior.is_selected = False
            session.add(prior)
        asset = ResearchAsset(run_id=repo.run_id, content_kind="report", inline_payload=report,
                              content_hash=digest(report), parse_status="parsed")
        session.add(asset)
        session.flush()
        revision = ResearchReportRevision(run_id=repo.run_id, report_asset_id=asset.asset_id,
            parent_revision=prior.revision_id if prior else None, dossier_revision_id=dossier.revision_id,
            protocol_hash=dossier.manifest["protocol_hash"], asset_manifest_hash=dossier.content_hash,
            quality_status=report["quality_status"], stop_reason=dossier.stop_reason, is_selected=True)
        session.add(revision)
        session.flush()
        run = session.get(ExplorationRun, repo.run_id)
        papers = report.get("materials", report["references"])
        run.result = {"papers": papers, "survey_report": report, "loop": {"revision": report["workflow_revision"],
            "stop_reason": dossier.stop_reason, "dossier_revision_id": dossier.revision_id}}
        run.status = "completed" if report["status"] == "completed" else "partial"
        run.current_stage = "report"
        run.state_version += 1
        session.add(run)
        append_event(session, run_id=repo.run_id, type="report_revision_created",
                     payload={"revision_id": revision.revision_id, "dossier_revision_id": dossier.revision_id})
        append_event(session, run_id=repo.run_id, type="run_state_changed", payload={"status": run.status, "state_version": run.state_version})
        return revision.revision_id

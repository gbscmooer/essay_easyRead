"""Immutable manifests and retrieval restricted to a frozen revision."""
from ....models.research_core import ResearchAsset, ResearchQuestion, ResearchAnswerRevision, ResearchDossierRevision, ResearchTask, ResearchRelation
from .contracts import digest


def freeze(tools, stop_reason: str):
    repo = tools.repo
    questions = repo.rows(ResearchQuestion)
    answers = {a.revision_id: a for a in repo.rows(ResearchAnswerRevision)}
    evidence = {e["evidence_id"]: e for q in questions for e in tools.evidence(q.question_id)}
    assets = {a.asset_id: a for a in repo.rows(ResearchAsset) if a.status == "active"}
    selection_failed = stop_reason == "material_selection_failed" and (repo.protocol() or {}).get("execution_revision") in ("question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20")
    selection_diagnostics = {}
    if (repo.protocol() or {}).get("execution_revision") in ("question_loop_v18", "question_loop_v19", "question_loop_v20"):
        from .portfolio_v18 import latest
        from ...research_exclusions import title_key

        for q in questions:
            portfolio = latest(tools, q.question_id)
            if portfolio and portfolio.get("protocol_hash") == repo.protocol()["hash"]:
                selection_diagnostics[q.question_id] = {k: portfolio.get(k) for k in ("stage", "selection_status", "gaps", "candidate_count", "read_count", "source_window_count")}
                read_titles = {title_key((repo.asset(aid, readable=True).inline_payload or {}).get("paper", {}).get("title", "")) for t in repo.rows(ResearchTask) if t.subquestion_id == q.question_id and t.kind in ("read_document", "read_documents") and t.state == "succeeded" for aid in t.payload.get("asset_ids") or [t.payload.get("asset_id")] if aid in assets}
                selection_diagnostics[q.question_id].update(read_count=len(read_titles), selected_count=len(portfolio.get("choices", [])), evidence_count=len(tools.evidence(q.question_id)))
    selected_answers = []
    for q in questions:
        answer = answers.get(q.answer_revision_id)
        if answer and all(eid in evidence for eid in answer.evidence_ids):
            selected_answers.append(answer.model_dump(exclude={"id", "run_id", "created_at"}))
        else:
            diagnosis = selection_diagnostics.get(q.question_id, {})
            default_gap = "选材工具未完成，已保留召回材料；尚未形成可核查的研究答案" if selection_failed else "证据仍不足，或引用材料当前不可用"
            if (repo.protocol() or {}).get("execution_revision") == "question_loop_v20" and stop_reason == "model_error":
                default_gap = "答案生成或核查因模型调用/输出错误未完成；未核验候选不能作为研究结论"
            actual_gaps = list(dict.fromkeys([*([default_gap] if selection_failed else []), *(diagnosis.get("gaps") or [])])) or [default_gap]
            if (repo.protocol() or {}).get("execution_revision") == "question_loop_v20":
                # Navigation diagnostics are not checked research facts and
                # cannot add requirements or claim coverage in a failed report.
                units = (tools.plan() or {}).get("requirement_ledger", {}).get(q.question_id, [])
                actual_gaps = [default_gap, *["尚未完成核查：" + u["requirement"] for u in units]]
            if diagnosis.get("read_count"):
                actual_gaps.insert(0, f"实际已阅读{diagnosis['read_count']}篇、保留{diagnosis['evidence_count']}条通过提取检查的材料要点，但尚未形成通过核查的综合答案")
            selected_answers.append({"question_id": q.question_id, "revision_id": None, "status": "insufficient",
                "conclusion": "", "evidence_ids": [], "conditions": [], "conflicts": [],
                "gaps": actual_gaps[:8], "kind": "fact"})
        selected = selected_answers[-1]
        q.status, q.gaps, q.answer_revision_id = selected["status"], selected["gaps"], selected["revision_id"]
    used_evidence = evidence
    document_assets = {a.asset_id: a.content_hash for a in tools.documents()}
    document_assets.update({e["asset_id"]: assets[e["asset_id"]].content_hash for e in evidence.values()})
    selected_ids = {a["revision_id"] for a in selected_answers if a["revision_id"]}
    relations = [r.model_dump(exclude={"id", "run_id", "created_at", "updated_at"}) for r in repo.rows(ResearchRelation)
        if r.reviewer_version in ("question_loop_v7", "question_loop_v8", "question_loop_v9", "question_loop_v10", "question_loop_v11", "question_loop_v12", "question_loop_v13", "question_loop_v14", "question_loop_v15", "question_loop_v16", "question_loop_v17", "question_loop_v18", "question_loop_v19", "question_loop_v20") and r.conditions.get("answer_revision_id") in selected_ids
        and r.verification_state in ("abstract_supported", "fulltext_supported")
        and all(eid in evidence for eid in r.evidence_ids)]
    manifest = {"schema_version": 2, "protocol_hash": repo.protocol()["hash"],
                "questions": [q.model_dump(exclude={"id", "run_id"}) for q in questions], "answers": selected_answers,
                "evidence": used_evidence, "relations": relations,
                "assets": document_assets,
                "examined_assets": list(dict.fromkeys(aid for t in repo.rows(ResearchTask)
                    if t.kind in ("read_document", "read_documents") and t.state == "succeeded"
                    for aid in t.payload.get("asset_ids") or [t.payload.get("asset_id")] if aid in document_assets)),
                "search_records": {a.asset_id: a.content_hash for a in assets.values() if a.content_kind == "search_record"},
                "material_tasks": [{"kind": t.kind, "state": t.state, "asset_id": t.payload.get("asset_id"),
                                    "error_code": t.error_code} for t in repo.rows(ResearchTask)
                                   if t.kind in ("read_document", "read_documents", "fetch_document")],
                "stop_reason": stop_reason}
    if selection_diagnostics:
        manifest["material_selection"] = selection_diagnostics
    with repo.transaction() as session:
        for question in questions:
            session.add(question)
        revision = ResearchDossierRevision(run_id=repo.run_id, protocol_asset_id=repo.run().protocol_asset_id,
                                          content_hash=digest(manifest), manifest=manifest, stop_reason=stop_reason)
        session.add(revision)
        session.flush()
        return revision


def retrieve(manifest: dict, question_id: str) -> list[dict]:
    answer = next(a for a in manifest["answers"] if a["question_id"] == question_id)
    return [manifest["evidence"][eid] for eid in answer["evidence_ids"]]

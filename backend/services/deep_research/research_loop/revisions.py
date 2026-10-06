"""User-requested research forks. Copy source material, never prior conclusions."""
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import update
from sqlmodel import select

from ....models.exploration import ExplorationRun, ExplorationMessage
from ....models.research_core import ResearchAsset, ResearchQuestion
from ...research_event_log import RunStateError, append_event, budget_snapshot
from ...research_sources import seed_options, load_seeds, SourceError
from ...research_exclusions import excluded
from .contracts import LOOP_VERSION, SUPPORTED_LOOP_VERSIONS, digest
from .questions import initial_questions
from .resources import limits_for

RAW_FIELDS = {
    "key", "paper_id", "title", "abstract", "year", "authors", "doi", "arxiv_id", "url", "pdf_url",
    "venue", "publication_date", "public_available_at", "arxiv_version", "version", "version_identity",
    "citation_count", "source_query", "identity", "providers", "field_sources",
}


def _run(session, run_id):
    row = session.get(ExplorationRun, run_id)
    if not row:
        raise RunStateError(404, "NOT_FOUND", "研究记录不存在")
    workflow = (row.options or {}).get("workflow") or {}
    if row.workflow_version != "academic_survey_v2" or workflow.get("loop_revision") not in SUPPORTED_LOOP_VERSIONS:
        raise RunStateError(400, "UNSUPPORTED_WORKFLOW", "研究修订仅支持已接入的问题研究流程")
    return row


def _documents(session, run_id):
    rows = session.exec(select(ResearchAsset).where(
        ResearchAsset.run_id == run_id, ResearchAsset.content_kind == "document",
        ResearchAsset.status == "active").order_by(ResearchAsset.id)).all()
    families = {}
    for row in rows:
        payload = row.inline_payload or {}
        if not payload.get("text", "").strip() or digest(payload) != row.content_hash:
            continue
        old = families.get(row.family_key)
        if old is None or payload.get("source_kind") != "abstract" or (old.inline_payload or {}).get("source_kind") == "abstract":
            families[row.family_key] = row
    return list(families.values())


def create_revision(session, parent_id, request, *, worker_active=False):
    parent = _run(session, parent_id)
    if parent.state_version != request.expected_state_version:
        raise RunStateError(409, "STALE_STATE_VERSION", "研究版本已变化，请刷新后重试")
    if parent.status in ("pending", "running"):
        raise RunStateError(409, "NEEDS_PAUSE", "请先暂停研究，再生成修订版")
    if parent.status not in ("paused", "completed", "partial", "failed", "cancelled"):
        raise RunStateError(409, "INVALID_STATE", "当前状态不能生成修订版")
    stamp = parent.worker_lease_expires_at
    leased = bool(parent.worker_owner and stamp and stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) > datetime.now(timezone.utc))
    if worker_active or leased:
        raise RunStateError(409, "WORKER_ACTIVE", "执行者尚未退出，请等待暂停完成")
    # Take a database write lock before checking other runs and committing the fork.
    applied = session.exec(update(ExplorationRun).where(
        ExplorationRun.id == parent_id, ExplorationRun.state_version == request.expected_state_version,
        ExplorationRun.status == parent.status).values(
            state_version=parent.state_version + 1,
            status="cancelled" if parent.status == "paused" else parent.status,
            updated_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
    if applied.rowcount != 1:
        raise RunStateError(409, "STATE_CONFLICT", "并发修改冲突，请刷新后重试")
    if session.exec(select(ExplorationRun.id).where(ExplorationRun.id != parent_id,
            ExplorationRun.status.in_(("pending", "running", "paused")))).first():
        raise RunStateError(409, "EXPLORATION_RUNNING", "已有其他活动研究，请先处理该研究")
    if session.exec(select(ExplorationMessage.id).where(ExplorationMessage.status.in_(("pending", "running")))).first():
        raise RunStateError(409, "CHAT_RUNNING", "追问正在执行，请等待结束后修订")
    options = deepcopy(parent.options or {})
    workflow = options.get("workflow") or {}
    online = options.get("search_online", True) if request.search_online is None else request.search_online
    documents = _documents(session, parent_id)
    # Every exclusion must belong to this parent, including an abstract superseded by full text.
    all_documents = session.exec(select(ResearchAsset).where(ResearchAsset.run_id == parent_id,
        ResearchAsset.content_kind == "document", ResearchAsset.status.in_(("active", "superseded")))).all()
    excluded_rows = [a for a in all_documents if a.asset_id in request.exclude_asset_ids]
    if {a.asset_id for a in excluded_rows} != set(request.exclude_asset_ids):
        raise RunStateError(422, "INVALID_MATERIAL", "排除材料必须属于当前研究且未被隔离")
    exclusions = options.setdefault("exclusions", {})
    excluded_families = {a.family_key for a in excluded_rows}
    for row in excluded_rows:
        paper = (row.inline_payload or {}).get("paper", {})
        for source, target in (("doi", "dois"), ("arxiv_id", "arxiv_ids"), ("title", "titles")):
            if paper.get(source):
                exclusions[target] = list(dict.fromkeys([*exclusions.get(target, []), paper[source]]))
    materials = []
    if request.reuse_materials:
        for row in documents:
            if row.family_key in excluded_families or excluded((row.inline_payload or {}).get("paper", {}), options):
                continue
            materials.append(({k: deepcopy(v) for k, v in row.inline_payload["paper"].items() if k in RAW_FIELDS},
                row.inline_payload["text"], row.inline_payload.get("source_kind", "abstract"),
                {"parent_run_id": parent_id, "parent_run_instance_id": parent.run_instance_id,
                 "parent_asset_id": row.asset_id, "parent_content_hash": row.content_hash}))
    if request.seed_paper_ids:
        try:
            seed_snapshot = seed_options(session, request.workspace_id, request.seed_paper_ids, online)
            seeds, _ = load_seeds(session, {**seed_snapshot, "exclusions": exclusions})
        except SourceError as exc:
            raise RunStateError(exc.status, "INVALID_SOURCES", str(exc)) from exc
        for paper in seeds:
            materials.append(({k: v for k, v in paper.items() if k in RAW_FIELDS},
                paper.get("source_text") or paper.get("abstract") or "", paper["source_kind"], {"added_paper_id": paper["paper_id"]}))
    elif request.workspace_id is not None:
        raise RunStateError(422, "INVALID_SOURCES", "选择工作区后请明确选择补充论文")
    unique = {}
    for entry in materials:
        previous = unique.get(entry[0]["key"])
        if previous is None or entry[2] != "abstract":
            unique[entry[0]["key"]] = entry
    materials = list(unique.values())
    if len(materials) > 240:
        raise RunStateError(422, "TOO_MANY_MATERIALS", "修订版最多复用或增加240篇材料，请缩小选择")
    if not online and not any(text.strip() for _, text, _, _ in materials):
        raise RunStateError(422, "NO_MATERIALS", "不联网的修订需要可读材料")
    protocol_options = {"questions": [{"text": q.strip(), "answer_criteria": ["回答该补充问题；每项事实提供材料依据；证据不足明确说明"]}
                                       for q in request.additional_questions]}
    initial_questions(request.question.strip(), protocol_options)
    depth = request.depth or parent.depth
    limits_for(depth, workflow.get("budget"), revision=LOOP_VERSION)
    lineage = (workflow.get("revision") or {})
    options.update(search_online=online, workspace_id=None, seed_paper_ids=[])
    options["workflow"] = {"version": "academic_survey_v2", "loop_revision": LOOP_VERSION,
        "protocol_options": protocol_options, "budget": deepcopy(workflow.get("budget") or {}),
        "content_cutoff": workflow.get("content_cutoff"), "policy_id": None,
        "revision": {"parent_run_id": parent_id, "parent_run_instance_id": parent.run_instance_id,
            "root_run_id": lineage.get("root_run_id", parent_id),
            "root_run_instance_id": lineage.get("root_run_instance_id", parent.run_instance_id),
            "reuse_materials": request.reuse_materials, "excluded_asset_ids": request.exclude_asset_ids,
            "added_paper_ids": request.seed_paper_ids, "workspace_id": request.workspace_id,
            "requested_at": datetime.now(timezone.utc).isoformat(), "candidate_count": len(materials),
            "cost_policy": "new_run_separate_budget"}}
    child = ExplorationRun(question=request.question.strip(), depth=depth, status="pending", state_version=1,
        workflow_version="academic_survey_v2", options=options)
    session.add(child)
    session.flush()
    for paper, text, kind, provenance in materials:
        payload = {"paper": paper, "text": text[:200000], "source_kind": kind}
        session.add(ResearchAsset(run_id=child.id, content_kind="reuse_candidate", family_key=paper["key"],
            inline_payload=payload, content_hash=digest(payload), parse_status="parsed" if text else "degraded",
            format="text", source_record=provenance))
    append_event(session, run_id=parent_id, type="research_revision_created", payload={"child_run_id": child.id,
        "state_version": request.expected_state_version + 1, "status": "cancelled" if parent.status == "paused" else parent.status})
    append_event(session, run_id=child.id, type="run_created", payload={"parent_run_id": parent_id, "loop_revision": LOOP_VERSION})
    session.commit()
    session.refresh(child)
    return child


def version_view(session, run_id, *, worker_active=False):
    run = _run(session, run_id)
    def item(row):
        budget = budget_snapshot(session, row.id)
        return {"run_id": row.id, "question": row.question, "status": row.status, "depth": row.depth,
            "created_at": row.created_at, "usage": [{k: v for k, v in b.items() if k in ("resource", "used", "usage_known")}
                for b in budget if b["resource"] in ("llm_calls", "llm_tokens")],
            "parent_run_id": (((row.options or {}).get("workflow") or {}).get("revision") or {}).get("parent_run_id")}
    ancestors, cursor, seen = [], run, {run_id}
    for _ in range(32):
        revision = ((cursor.options or {}).get("workflow") or {}).get("revision") or {}
        parent_id = revision.get("parent_run_id")
        if not parent_id or parent_id in seen:
            break
        parent = session.get(ExplorationRun, parent_id)
        if not parent or parent.run_instance_id != revision.get("parent_run_instance_id"):
            break
        ancestors.append(item(parent))
        seen.add(parent_id)
        cursor = parent
    children = []
    # Do not load every report to discover version links.
    for row_id, options in session.exec(select(ExplorationRun.id, ExplorationRun.options)).all():
        revision = ((options or {}).get("workflow") or {}).get("revision") or {}
        if revision.get("parent_run_id") == run_id and revision.get("parent_run_instance_id") == run.run_instance_id:
            children.append(item(session.get(ExplorationRun, row_id)))
    return {"run_id": run_id, "state_version": run.state_version, "status": run.status,
        "worker_active": worker_active, "ancestors": ancestors, "children": children[-100:],
        "materials": [{"asset_id": a.asset_id, "title": (a.inline_payload or {}).get("paper", {}).get("title", "未命名论文"),
            "source_kind": (a.inline_payload or {}).get("source_kind", "abstract")} for a in _documents(session, run_id)[:240]]}


async def admit_reused_materials(tools):
    """New protocol admission, durable batch checkpoints, ordinary billed LLM Core."""
    candidates = [a for a in tools.repo.rows(ResearchAsset) if a.content_kind == "reuse_candidate" and a.status == "active"]
    for question in tools.repo.rows(ResearchQuestion):
        for offset in range(0, len(candidates), 36):
            batch = candidates[offset:offset + 36]
            key = digest({"question_id": question.question_id, "candidates": [(a.asset_id, a.content_hash) for a in batch]})
            if any(a.content_kind == "reuse_admission" and a.version_key == key for a in tools.repo.rows(ResearchAsset)):
                continue
            valid = [a for a in batch if digest(a.inline_payload) == a.content_hash and tools.allowed(a.inline_payload["paper"])]
            accepted, decisions, enrichment = await tools.admit([a.inline_payload["paper"] for a in valid], question.question_id) if valid else ([], [], [])
            with tools.repo.transaction() as session:
                for paper in accepted:
                    source = next(a for a in valid if a.inline_payload["paper"]["key"] == paper["key"])
                    spec = tools.document_spec(paper, text=source.inline_payload["text"], source_kind=source.inline_payload["source_kind"])
                    spec["source_record"] = {**spec["source_record"], "reuse_candidate_id": source.asset_id, **source.source_record}
                    session.add(ResearchAsset(run_id=tools.repo.run_id, **spec))
                payload = {"question_id": question.question_id, "decisions": decisions, "enrichment": enrichment,
                    "accepted": len(accepted), "candidates": len(batch)}
                session.add(ResearchAsset(run_id=tools.repo.run_id, content_kind="reuse_admission", version_key=key,
                    inline_payload=payload, content_hash=digest(payload), parse_status="parsed"))
                append_event(session, run_id=tools.repo.run_id, type="materials_readmitted", payload={"question_id": question.question_id,
                    "candidates": len(batch), "accepted": len(accepted)})

"""Deeper bounded recall and complete, auditable relevance batches for new runs."""

import asyncio
import re
import time

from ...http_diagnostics import capture_http_attempts, http_failure
from ...paper_identity import normalize_arxiv_id, normalize_doi
from ...research_exclusions import title_key
from ...llm import LLMError
from .diagnostics_v20 import model_error
from .contracts import digest
from .resources import ResourceExhausted

SOURCE_LIMIT = 25
TARGET_SOURCE_LIMIT = 6
SEARCH_SOURCES = ("openalex", "arxiv", "scopus")
SOURCE_TIMEOUT_SECONDS = 25
RELEVANCE_BATCH_SIZE = 24
MINIMAX_RELEVANCE_BATCH_SIZE = 12
RELEVANCE_OUTPUT_TOKENS = 6000
MINIMAX_RELEVANCE_OUTPUT_TOKENS = 9000
INITIAL_TARGET = 50


def relevance_batch_size(tools):
    from .timeout_v20 import model_timeout, MINIMAX_ATTEMPT_SECONDS
    return MINIMAX_RELEVANCE_BATCH_SIZE if model_timeout(tools) == MINIMAX_ATTEMPT_SECONDS else RELEVANCE_BATCH_SIZE


def relevance_output_tokens(tools):
    return MINIMAX_RELEVANCE_OUTPUT_TOKENS if relevance_batch_size(tools) == MINIMAX_RELEVANCE_BATCH_SIZE else RELEVANCE_OUTPUT_TOKENS


def identity_aliases(paper):
    """Only explicit identifiers, or exact title+author metadata, join work records.

    A metadata join is an operational duplicate group, not proof that two full-text
    versions are identical. A shared title alone never joins different authors.
    """
    aliases = set()
    key = str(paper.get("key") or "")
    doi = normalize_doi(paper.get("doi"))
    arxiv = normalize_arxiv_id(paper.get("arxiv_id"))
    if key.startswith("doi:"):
        doi = doi or normalize_doi(key[4:])
    if key.startswith("arxiv:"):
        arxiv = arxiv or normalize_arxiv_id(key[6:])
    if doi:
        aliases.add("doi:" + doi)
    if arxiv:
        aliases.add("arxiv:" + arxiv)
    if key and not key.startswith(("titlefp:", "t:", "weak:")):
        aliases.add("key:" + key)
    elif key:
        aliases.add("weak_record:" + digest({"key": key, "title": paper.get("title"),
                                             "authors": paper.get("authors"), "year": paper.get("year")}))
    authors = paper.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    names = []
    for author in authors:
        if isinstance(author, dict):
            author = author.get("name") or author.get("display_name") or ""
        name = re.sub(r"\W+", " ", str(author).casefold()).strip()
        if name:
            names.append(name)
    title = title_key(paper.get("title", ""))
    if title and names:
        aliases.add("metadata:" + digest({"title": title, "authors": sorted(set(names))}))
    return aliases


def merge_work_records(papers):
    """Union explicit identity bridges, retaining every provider observation."""
    rows = [dict(p) for p in papers]
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    aliases, first = {}, {}
    for i, row in enumerate(rows):
        aliases[i] = identity_aliases(row)
        for alias in aliases[i]:
            if alias in first:
                parent[root(i)] = root(first[alias])
            else:
                first[alias] = i
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append(row)
    merged = []
    for members in groups.values():
        # Existing document identity comes first when the caller supplies it; this
        # preserves its family key across a later DOI/arXiv bridge observation.
        current = dict(members[0])
        observations = []
        for row in members:
            for key, value in row.items():
                if value and not current.get(key):
                    current[key] = value
            observations.extend(row.get("retrieval_observations", []))
        current["retrieval_observations"] = list(
            {digest(o): o for o in observations}.values()
        )
        current["identity_aliases"] = sorted(set().union(*(identity_aliases(p) for p in members)))
        current["identity_grouping"] = (
            "explicit_identifier_or_metadata" if len(members) > 1 else "single_record"
        )
        merged.append(current)
    return merged


def recall_summary(tools, question_id, current_audit):
    """Cumulative initial-candidate target; reading and evidence are separate."""
    from ....models.research_core import ResearchAsset

    prior = [
        a.inline_payload
        for a in tools.repo.rows(ResearchAsset)
        if a.content_kind == "search_record"
        and a.status == "active"
        and (a.inline_payload or {}).get("question_id") == question_id
        and (a.inline_payload or {}).get("method") == "bounded_multi_direction_v20"
        and (a.inline_payload or {}).get("recall_phase") == "initial"
    ]
    audits = [*prior, *([current_audit] if current_audit["recall_phase"] == "initial" else [])]
    documents = audits[0].get("initial_existing_candidates", []) if audits else []
    families = merge_work_records([*documents, *(p for a in audits for p in a.get("admitted_candidates", []))])
    titles = {title_key(p.get("title", "")) for p in families} - {""}
    # Equal titles with unproven identity cannot inflate a breadth target either.
    count = min(len(families), len(titles))
    source_counts = {}
    for audit in audits:
        for record in audit.get("attempts", []):
            source = record["provider"]
            stats = source_counts.setdefault(source, {"raw_hits": 0, "operations": 0, "failed_operations": 0})
            stats["raw_hits"] += record.get("raw_hits", record.get("hits", 0))
            stats["operations"] += 1
            stats["failed_operations"] += record.get("status") not in ("ok", "ok_empty", "partial")
    return {
        "target_initial_candidates": INITIAL_TARGET,
        "admitted_family_count": len(families),
        "admitted_title_count": len(titles),
        "breadth_candidate_count": count,
        "target_reached": count >= INITIAL_TARGET,
        "searches_completed": len(audits),
        "raw_hits": sum(a.get("raw_hits", 0) for a in audits),
        "sources": source_counts,
        "unreviewed_candidate_count": sum(a.get("unreviewed_count", 0) for a in audits),
        "limitation": (
            "初始准入候选达到数量目标；数量不证明覆盖、阅读或结论质量"
            if count >= INITIAL_TARGET
            else "当前初始准入候选不足50；保留实际材料，不能用无关或未核查论文补足"
        ),
    }


async def search(tools, action):
    from ..recall_engine import _default_search_one
    from ..explorer import _dedupe_candidates
    from .retrieval import search_arxiv

    records, papers = [], []
    plan = tools.plan() or {}
    recall_phase = "initial" if any(q["question_id"] == action.question_id and q["query"].casefold() == action.query.casefold()
                                    for q in plan.get("queries", [])) else "followup"
    target = next(
        (t for t in plan.get("targets", [])
         if t["question_id"] == action.question_id and t["name"].casefold() == action.query.casefold()),
        None,
    )
    limit = TARGET_SOURCE_LIMIT if target else SOURCE_LIMIT
    if tools.repo.run().options.get("search_online", True):
        sources = ("arxiv", *(source for source in SEARCH_SOURCES if source != "arxiv")) if target else SEARCH_SOURCES
        for source in sources:
            started = time.monotonic()
            record = {"provider": source, "query": action.query, "requested_limit": limit,
                      "retrieval_mode": "single_operation_deeper_results", "raw_hits": 0, "hits": 0}
            with capture_http_attempts() as http_attempts:
                try:
                    if source == "arxiv" and plan.get("strategy") == "material_first_v3":
                        found = await asyncio.wait_for(search_arxiv(
                            action.query, cutoff=((tools.repo.run().options or {}).get("workflow") or {}).get("content_cutoff"),
                            limit=limit), timeout=SOURCE_TIMEOUT_SECONDS)
                        record["raw_hits"] = len(found)
                    else:
                        candidates = await asyncio.wait_for(_default_search_one(
                            source, action.query, limit, "relevance", _state=tools.provider_state), timeout=SOURCE_TIMEOUT_SECONDS)
                        record["raw_hits"] = len(candidates)
                        found = _dedupe_candidates(candidates)
                    for paper in found:
                        papers.append({**paper, "retrieval_observations": [
                            {"provider": source, "query": action.query, "paper_key": paper.get("key")}
                        ]})
                    status = getattr(tools.provider_state.get(source), "last_request_status", "ok")
                    record.update(hits=len(found), status=("ok" if found else "ok_empty") if status == "ok" else status)
                    if tools.resources.source_exhausted:
                        record["status"] = "resource_exhausted"
                except ResourceExhausted:
                    tools.resources.source_exhausted = True
                    record["status"] = "resource_exhausted"
                except Exception as exc:
                    record.update(status="error", **http_failure(exc, http_attempts))
                finally:
                    last_status = next((a.get("http_status") for a in reversed(http_attempts) if a.get("http_status")), None)
                    record.update(http_status=last_status, last_http_status=last_status,
                                  elapsed_ms=round((time.monotonic() - started) * 1000),
                                  http_attempts=list(http_attempts))
            records.append(record)
            if tools.resources.source_exhausted or (target and record["hits"]):
                break
    existing = [(a.inline_payload or {}).get("paper", {}) for a in tools.documents()]
    existing_keys = {p.get("key") for p in existing}
    current_groups = merge_work_records(papers)
    # Include old records in identity union to preserve already accepted family IDs.
    current_aliases = set().union(*(identity_aliases(p) for p in papers)) if papers else set()
    combined = merge_work_records([*existing, *papers])
    merged = [p for p in combined if identity_aliases(p) & current_aliases]
    eligible = [p for p in merged if tools.allowed(p)]
    already = [p for p in eligible if p.get("key") in existing_keys and action.question_id in p.get("relevant_questions", [])]
    pending = [p for p in eligible if p not in already]
    accepted, decisions, enrichment, batches = [], [], [], []
    reviewed_keys = set()
    batch_size = relevance_batch_size(tools)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        keys = [p["key"] for p in batch]
        row = {"batch_index": offset // batch_size + 1, "candidate_keys": keys, "candidate_count": len(batch)}
        try:
            admitted, judged, filled = await tools.admit(batch, action.question_id)
            accepted.extend(admitted)
            decisions.extend(judged)
            enrichment.extend(filled)
            # A missing/duplicate model verdict remains uncertain, but was still
            # presented to relevance screening by Tools.admit's strict key logic.
            reviewed_keys.update(keys)
            row.update(status="screened", admitted=len(admitted), decisions=len(judged),
                       screening_method="literal_primary_identity_v1" if judged and all(d.get("method") == "literal_primary_identity_v1" for d in judged) else "model_relevance")
        except ResourceExhausted:
            row.update(status="resource_exhausted", error_code="ResourceExhausted")
            batches.append(row)
            break
        except LLMError as exc:
            row.update(status="model_failed", **model_error(exc))
            batches.append(row)
            # Keep fetched metadata and earlier verdicts; do not spend another
            # batch on an unavailable model or mistake missing verdicts for admit.
            break
        except ValueError as exc:
            row.update(status="structured_output_failed", error_code=type(exc).__name__)
        batches.append(row)
    audit = {
        "method": "bounded_multi_direction_v20", "question_id": action.question_id,
        "recall_phase": recall_phase,
        "query": action.query, "attempts": records, "admitted": len(accepted),
        "raw_hits": sum(r["raw_hits"] for r in records),
        "identity_deduplicated_count": len(current_groups),
        "eligible_count": len(eligible), "already_admitted": len(already),
        "policy_rejected": len(merged) - len(eligible),
        "screened_count": len(reviewed_keys), "unreviewed_count": len(pending) - len(reviewed_keys),
        "unreviewed_keys": [p["key"] for p in pending if p["key"] not in reviewed_keys],
        "screening_batches": batches, "decisions": decisions, "enrichment": enrichment,
        "relevance_batch_size": batch_size,
        "candidate_identities": [
            {"key": p.get("key"), "title": p.get("title"), "aliases": p.get("identity_aliases", []),
             "observations": p.get("retrieval_observations", [])} for p in current_groups
        ],
        "unreviewed_candidates": [p for p in pending if p["key"] not in reviewed_keys],
        "admitted_candidates": [
            {k: p.get(k) for k in ("key", "title", "doi", "arxiv_id", "authors")}
            for p in accepted
        ],
        "initial_existing_candidates": [
            {k: p.get(k) for k in ("key", "title", "doi", "arxiv_id", "authors")}
            for p in existing if not p.get("relevant_questions") or action.question_id in p["relevant_questions"]
        ] if recall_phase == "initial" else [],
        "source_counts": {
            record["provider"]: {
                "raw_hits": record["raw_hits"],
                "identity_deduplicated_count": sum(any(o["provider"] == record["provider"] for o in p.get("retrieval_observations", [])) for p in current_groups),
                "eligible_count": sum(any(o["provider"] == record["provider"] and o["query"] == action.query for o in p.get("retrieval_observations", [])) for p in eligible),
                "admitted_count": sum(any(o["provider"] == record["provider"] and o["query"] == action.query for o in p.get("retrieval_observations", [])) for p in accepted),
            } for record in records
        },
    }
    audit["initial_recall"] = recall_summary(tools, action.question_id, audit)
    return {"assets": [tools.document_spec(p) for p in accepted] + [
        {"content_kind": "search_record", "inline_payload": audit, "content_hash": digest(audit), "parse_status": "parsed"}
    ], "payload": {"admitted": len(accepted), "initial_recall": audit["initial_recall"]}}

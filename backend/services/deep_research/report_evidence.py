"""Shared, versioned evidence store embedded in legacy survey report artifacts."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Iterable

from .document_evidence import DocumentEvidenceReader, normalize_text


STORE_VERSION = 1
EXPERIMENT_FIELDS = (
    "method", "baseline_method", "task", "dataset", "split", "metric", "value",
    "baseline_value", "unit", "metric_direction", "hardware", "batch_size",
    "input_size", "precision", "comparison_limit",
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identity_payload(record: dict) -> dict:
    """Fields that identify evidence independently of mutable review metadata."""
    return {
        "evidence_type": record.get("evidence_type"),
        "fact": record.get("fact") or {},
        "source": record.get("source") or {},
        "spans": record.get("spans") or [],
    }


def _content_payload(record: dict) -> dict:
    return {key: value for key, value in record.items()
            if key not in {"evidence_id", "identity_hash", "content_hash"}}


# 子问题的语义信号词（小写子串匹配 fact+quote 文本）：表格类型只是主归类，
# 不能决定研究覆盖——"下一代视觉基础模型候选"在 taxonomy 表里就永远进不了
# 趋势/查新（v7.4.1 研究 5 实测效率/局限/趋势三个子问题 0 条证据）。
# 信号词保持保守：宁可少关联，也不把无关证据灌进子问题污染覆盖统计。
_SUBQUESTION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("runtime_efficiency", ("throughput", "latency", "speed", "memory", "flop", "img/s",
                            "fps", "efficien", "faster", "slower", "compute", "params",
                            "gpu hour", "inference time", "training time", "cost")),
    ("limitations", ("limitation", "challenge", "drawback", "bottleneck", "fail", "struggl",
                     "difficulty", "restrict", "open problem", "cannot", "bias", "overfit",
                     "issue")),
    ("novelty_scope", ("future", "next-generation", "next generation", "direction", "trend",
                       "emerging", "outlook", "roadmap", "open question", "candidate",
                       "paradigm", "frontier", "toward", "unexplored")),
    ("method_mechanism", ("architecture", "mechanism", "attention", "module", "encoder",
                          "hierarch", "token", "variant", "component", "self-supervised",
                          "pretrain", "fine-tun")),
    ("effect_comparison", ("benchmark", "accuracy", "performance", "comparison", "baseline",
                           "state-of-the-art", "sota", "evaluation", "outperform", "score")),
)


def _subquestions(table: str, fact: dict, quote: str = "") -> list[str]:
    """表格类型给主归类，语义信号补充次归类（一条证据可关联多个子问题）。"""
    joined = " ".join(normalize_text(value) for value in (*fact.values(), quote)).casefold()
    if table == "experimental_comparisons":
        result = ["effect_comparison"]
        if any(word in joined for word in ("throughput", "latency", "speed", "memory",
                                           "flop", "img/s", "fps")):
            result.append("runtime_efficiency")
        if (fact.get("hardware") or fact.get("batch_size") or fact.get("input_size") or fact.get("precision")) and "runtime_efficiency" not in result:
            result.append("runtime_efficiency")
    elif table == "taxonomy":
        result = ["method_mechanism"]
    elif table in {"frameworks", "metrics", "tasks"}:
        result = ["effect_comparison"]
    else:
        result = ["novelty_scope"]
    for subquestion_id, signals in _SUBQUESTION_SIGNALS:
        if subquestion_id not in result and any(signal in joined for signal in signals):
            result.append(subquestion_id)
    return result


class ReportEvidenceStore:
    """Complete evidence remains here even when an LLM reads only paged indexes."""

    def __init__(self, records: Iterable[dict] | None = None):
        self._records: dict[str, dict] = {}
        self._content_to_id: dict[str, str] = {}
        for record in records or []:
            if isinstance(record, dict) and record.get("evidence_id"):
                item = dict(record)
                identity_hash = item.get("identity_hash") or _hash(_identity_payload(item))
                item["identity_hash"] = identity_hash
                item["content_hash"] = _hash(_content_payload(item))
                self._records[item["evidence_id"]] = item
                self._content_to_id[identity_hash] = item["evidence_id"]

    def add(
        self,
        *,
        evidence_type: str,
        fact: dict,
        paper: dict,
        quote: str,
        source_kind: str,
        reader: DocumentEvidenceReader | None,
        subquestion_ids: list[str] | None = None,
        verification_status: str = "relation_verified",
        verification_note: str = "",
    ) -> tuple[dict, bool]:
        location = reader.locate_quote(quote) if reader else {
            "location_status": "unavailable", "page": None, "section": None,
            "table": None, "char_start": None, "char_end": None,
        }
        cleaned_fact = {key: value for key, value in fact.items() if key not in {"id", "evidence"}}
        expected = EXPERIMENT_FIELDS if evidence_type == "experimental_comparisons" else tuple(cleaned_fact)
        missing_fields = [key for key in expected if not cleaned_fact.get(key)]
        if evidence_type == "experimental_comparisons":
            has_baseline = bool(cleaned_fact.get("baseline_method") or cleaned_fact.get("baseline_value"))
            comparability = {
                "status": "limited" if has_baseline else "not_a_comparison",
                "cross_paper_fairness_assumed": False,
                "limitations": list(dict.fromkeys(filter(None, [
                    normalize_text(cleaned_fact.get("comparison_limit")),
                    "跨论文结果不默认视为公平对比。",
                ]))),
            }
        else:
            comparability = {"status": "not_applicable", "cross_paper_fairness_assumed": False,
                             "limitations": []}
        content = {
            "evidence_type": evidence_type,
            "fact": cleaned_fact,
            "source": {
                "paper_key": paper.get("key"), "title": paper.get("title"),
                "document_hash": reader.document_hash if reader else None,
                "source_kind": source_kind,
            },
            "spans": [{"quote": normalize_text(quote), **location}],
            "missing_fields": missing_fields,
            "comparability": comparability,
            "subquestion_ids": subquestion_ids or _subquestions(evidence_type, cleaned_fact, quote),
            "verification": {"status": verification_status, "note": verification_note},
        }
        identity_hash = _hash(_identity_payload(content))
        existing = self._content_to_id.get(identity_hash)
        if existing:
            record = self._records[existing]
            record["subquestion_ids"] = list(dict.fromkeys([
                *(record.get("subquestion_ids") or []), *(content.get("subquestion_ids") or []),
            ]))
            # A later semantic review may strengthen a located-only record without changing
            # its externally stable evidence ID.
            if verification_status == "relation_verified":
                record["verification"] = content["verification"]
            record["content_hash"] = _hash(_content_payload(record))
            return record, False
        evidence_id = f"ev_{identity_hash[:24]}"
        record = {"evidence_id": evidence_id, "identity_hash": identity_hash, **content}
        record["content_hash"] = _hash(_content_payload(record))
        self._records[evidence_id] = record
        self._content_to_id[identity_hash] = evidence_id
        return record, True

    def add_context(
        self,
        *,
        paper: dict,
        context: dict,
        subquestion_id: str,
        query: str,
        reader: DocumentEvidenceReader,
    ) -> tuple[dict, bool]:
        return self.add(
            evidence_type="targeted_context",
            fact={"focus": normalize_text(query)[:400]},
            paper=paper,
            quote=context.get("text") or "",
            source_kind=reader.source_kind,
            reader=reader,
            subquestion_ids=[subquestion_id],
            verification_status="located_unverified",
            verification_note="已定位原文上下文；语义断言仍须终稿核验。",
        )

    def attach_table_rows(
        self,
        tables: dict[str, list[dict]],
        papers: dict[str, dict],
        readers: dict[str, DocumentEvidenceReader],
    ) -> None:
        for table, rows in tables.items():
            for row in rows:
                fact = {key: value for key, value in row.items() if key not in {"id", "evidence"}}
                for evidence in row.get("evidence") or []:
                    paper_key = evidence.get("paper_key")
                    paper = papers.get(paper_key, {"key": paper_key, "title": ""})
                    record, _ = self.add(
                        evidence_type=table, fact=fact, paper=paper,
                        quote=evidence.get("quote") or "",
                        source_kind=evidence.get("source_kind") or "unknown",
                        reader=readers.get(paper_key),
                    )
                    evidence["evidence_id"] = record["evidence_id"]
                    evidence["page"] = record["spans"][0].get("page")
                    evidence["section"] = record["spans"][0].get("section")
                    evidence["table"] = record["spans"][0].get("table")
                    evidence["location_status"] = record["spans"][0].get("location_status")

    @property
    def evidence_version(self) -> str:
        pairs = [(key, self._records[key].get("content_hash")) for key in sorted(self._records)]
        return f"es_{_hash(pairs)[:24]}"

    def index(self, *, subquestion_id: str | None = None, cursor: int = 0, limit: int = 12) -> dict:
        cursor = max(0, int(cursor))
        limit = max(1, min(int(limit), 50))
        records = list(self._records.values())
        if subquestion_id:
            records = [row for row in records if subquestion_id in row.get("subquestion_ids", [])]
        records.sort(key=lambda row: (row.get("evidence_type", ""), row["evidence_id"]))
        page = records[cursor:cursor + limit]
        return {
            "evidence_version": self.evidence_version,
            "subquestion_id": subquestion_id,
            "cursor": cursor,
            "limit": limit,
            "total": len(records),
            "next_cursor": cursor + limit if cursor + limit < len(records) else None,
            "items": [{
                "evidence_id": row["evidence_id"],
                "evidence_type": row.get("evidence_type"),
                "subquestion_ids": row.get("subquestion_ids", []),
                "paper_key": (row.get("source") or {}).get("paper_key"),
                "fact_summary": normalize_text(" | ".join(str(value) for value in (row.get("fact") or {}).values() if value))[:360],
                "missing_fields": row.get("missing_fields", []),
                "comparability_status": (row.get("comparability") or {}).get("status"),
                "verification_status": (row.get("verification") or {}).get("status"),
                "location_status": ((row.get("spans") or [{}])[0]).get("location_status"),
            } for row in page],
        }

    def details(self, evidence_ids: list[str]) -> list[dict]:
        return [self._records[evidence_id] for evidence_id in dict.fromkeys(evidence_ids)
                if evidence_id in self._records]

    def coverage(self, subquestion_ids: list[str]) -> dict:
        counts = defaultdict(int)
        verified = defaultdict(int)
        for row in self._records.values():
            for subquestion_id in row.get("subquestion_ids", []):
                counts[subquestion_id] += 1
                if (row.get("verification") or {}).get("status") == "relation_verified":
                    verified[subquestion_id] += 1
        return {subquestion_id: {"evidence_count": counts[subquestion_id],
                                 "verified_count": verified[subquestion_id]}
                for subquestion_id in subquestion_ids}

    def audit_sources(self, paper_order: list[dict]) -> list[dict]:
        by_paper: dict[str, list[dict]] = defaultdict(list)
        priority = {"experimental_comparisons": 0, "targeted_context": 1,
                    "metrics": 2, "tasks": 3, "frameworks": 4, "taxonomy": 5}
        records = sorted(self._records.values(), key=lambda row: (
            priority.get(row.get("evidence_type"), 9), row["evidence_id"],
        ))
        seen_quotes: dict[str, set[str]] = defaultdict(set)
        for record in records:
            paper_key = (record.get("source") or {}).get("paper_key")
            for span in record.get("spans") or []:
                quote = span.get("quote")
                quote_key = normalize_text(quote).casefold()
                if paper_key and quote and quote_key not in seen_quotes[paper_key]:
                    seen_quotes[paper_key].add(quote_key)
                    by_paper[paper_key].append({"evidence_id": record["evidence_id"],
                                                "quote": quote})
        sources = []
        for number, paper in enumerate(paper_order, 1):
            spans = by_paper.get(paper.get("key"), [])
            if not spans:
                continue
            sources.append({
                "id": f"P{number}", "title": paper.get("title") or "", "abstract": "",
                "text": "\n\n".join(span["quote"] for span in spans),
                "evidence_spans": spans,
            })
        return sources

    def audit_sources_for(
        self,
        evidence_ids: list[str],
        paper_order: list[dict],
        *,
        fallback_paper_keys: list[str] | None = None,
    ) -> list[dict]:
        """段级证据裁剪：只携带指定证据（及其论文身份）的核验 sources。

        P 编号规则与 audit_sources 完全一致（论文在 paper_order 中的序号），
        只是过滤到被引用的证据条目——终稿核验每批发送全量 sources 是 run6
        21 段全部 AUDIT_TIMEOUT 的载荷根因（≤48 源 × 8k 字符 ≈ 60k 字符/批）。
        evidence_ids 为空时退用 fallback_paper_keys 的全证据；两者皆空返回 []
        （纯 process_note 段无可核验来源，audit_answer 会按 NO_SOURCE_TEXT 跳过）。
        """
        wanted = {str(e) for e in evidence_ids if str(e)}
        by_paper: dict[str, list[dict]] = defaultdict(list)
        if wanted:
            records = [self._records[e] for e in sorted(wanted) if e in self._records]
        elif fallback_paper_keys:
            keys = set(fallback_paper_keys)
            records = [row for row in self._records.values()
                       if (row.get("source") or {}).get("paper_key") in keys]
        else:
            return []
        seen: set[tuple[str, str]] = set()
        for record in records:
            paper_key = (record.get("source") or {}).get("paper_key")
            for span in record.get("spans") or []:
                quote = span.get("quote")
                dedupe = (str(paper_key), normalize_text(quote).casefold())
                if paper_key and quote and dedupe not in seen:
                    seen.add(dedupe)
                    by_paper[paper_key].append({"evidence_id": record["evidence_id"],
                                                "quote": quote})
        sources = []
        for number, paper in enumerate(paper_order, 1):
            spans = by_paper.get(paper.get("key"), [])
            if not spans:
                continue
            sources.append({
                "id": f"P{number}", "title": paper.get("title") or "", "abstract": "",
                "text": "\n\n".join(span["quote"] for span in spans),
                "evidence_spans": spans,
            })
        return sources

    def to_dict(self) -> dict:
        records = sorted(self._records.values(), key=lambda row: row["evidence_id"])
        return {"version": STORE_VERSION, "evidence_version": self.evidence_version,
                "count": len(records), "records": records}

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ReportEvidenceStore":
        return cls((payload or {}).get("records") or [])

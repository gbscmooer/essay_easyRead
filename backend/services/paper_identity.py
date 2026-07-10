"""
论文身份规范化 — 纯函数模块

将 DOI / arXiv / OpenAlex / S2 / 标题 等异构标识统一为 canonical_id，
并兼容历史 external_id 格式。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlmodel import Session
    from ..models.paper import Paper

# ── 识别模式 ──────────────────────────────────────────────

_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:\s*)",
    re.IGNORECASE,
)
_ARXIV_PREFIX_RE = re.compile(r"^arxiv:\s*", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
_OPENALEX_PREFIX_RE = re.compile(r"^(?:openalex:\s*)?", re.IGNORECASE)
_S2_PREFIX_RE = re.compile(r"^s2:\s*", re.IGNORECASE)

_BARE_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_BARE_ARXIV_NEW_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_BARE_ARXIV_OLD_RE = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?/\d{7}$", re.IGNORECASE)
_OPENALEX_ID_RE = re.compile(r"^W\d+$", re.IGNORECASE)
_S2_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_S2_HEX_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)

_TITLE_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_TITLE_SPACE_RE = re.compile(r"\s+")


# ── 规范化 ──────────────────────────────────────────────


def normalize_doi(value: str | None) -> str | None:
    """DOI 小写，去除 https://doi.org/ 等前缀。"""
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    s = _DOI_PREFIX_RE.sub("", s)
    s = s.strip().rstrip(".")
    if not s:
        return None
    return s.lower()


def normalize_arxiv_id(value: str | None) -> str | None:
    """arXiv ID 去 arxiv: 前缀与 vN 版本后缀。"""
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    s = _ARXIV_PREFIX_RE.sub("", s)
    s = _ARXIV_VERSION_RE.sub("", s)
    s = s.strip()
    if not s:
        return None
    if _BARE_ARXIV_NEW_RE.match(s) or _BARE_ARXIV_OLD_RE.match(s):
        return s.lower() if "/" not in s else s
    return s


def normalize_openalex_id(value: str | None) -> str | None:
    """OpenAlex ID 规范为 W123 形式（大写 W + 数字）。"""
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    # Provider 响应与 legacy external_id 可能保存完整 OpenAlex URL。
    s = re.sub(r"^https?://(?:www\.)?openalex\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^openalex:\s*", "", s, flags=re.IGNORECASE)
    s = s.strip().upper()
    if not s.startswith("W"):
        s = f"W{s}"
    if not _OPENALEX_ID_RE.match(s):
        return None
    return s


def normalize_s2_id(value: str | None) -> str | None:
    """Semantic Scholar paperId 小写化，去 s2: 前缀。"""
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    s = _S2_PREFIX_RE.sub("", s)
    s = s.strip().lower()
    if _S2_UUID_RE.match(s) or _S2_HEX_RE.match(s):
        return s
    # 兼容 32 位 hex（部分旧数据）
    if re.match(r"^[0-9a-f]{32}$", s):
        return s
    return None


def normalize_title(title: str | None) -> str | None:
    """标题规范化：小写、去标点、折叠空白。"""
    if not title or not str(title).strip():
        return None
    s = str(title).strip().lower()
    s = _TITLE_PUNCT_RE.sub(" ", s)
    s = _TITLE_SPACE_RE.sub(" ", s).strip()
    return s or None


def _title_fingerprint(title: str | None) -> str | None:
    normalized = normalize_title(title)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return digest


def build_canonical_id(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    title: str | None = None,
) -> str | None:
    """
    按优先级构建 canonical_id：doi > arxiv > openalex > s2 > titlefp 兜底。
    """
    ndoi = normalize_doi(doi)
    if ndoi:
        return f"doi:{ndoi}"

    narxiv = normalize_arxiv_id(arxiv_id)
    if narxiv:
        return f"arxiv:{narxiv}"

    noa = normalize_openalex_id(openalex_id)
    if noa:
        return f"openalex:{noa}"

    ns2 = normalize_s2_id(s2_id)
    if ns2:
        return f"s2:{ns2}"

    fp = _title_fingerprint(title)
    if fp:
        return f"titlefp:{fp}"

    return None


def is_strong_canonical_id(canonical_id: str | None) -> bool:
    """强 ID 指非 titlefp 的 canonical_id。"""
    if not canonical_id:
        return False
    return not canonical_id.startswith("titlefp:")


def parse_legacy_external_id(external_id: str | None) -> dict[str, str | None]:
    """
    解析历史 external_id，兼容：
    DOI:/doi:/arxiv:/W123/裸 DOI/裸 arXiv/S2 UUID。
    """
    result: dict[str, str | None] = {
        "doi": None,
        "arxiv_id": None,
        "openalex_id": None,
        "s2_id": None,
    }
    if not external_id or not str(external_id).strip():
        return result

    raw = str(external_id).strip()

    # 显式前缀
    if raw.upper().startswith("DOI:") or raw.lower().startswith("doi:"):
        result["doi"] = normalize_doi(raw)
        return result

    lower = raw.lower()
    if lower.startswith("arxiv:"):
        result["arxiv_id"] = normalize_arxiv_id(raw)
        return result

    if lower.startswith("openalex:"):
        result["openalex_id"] = normalize_openalex_id(raw)
        return result

    if lower.startswith("s2:"):
        result["s2_id"] = normalize_s2_id(raw)
        return result

    # OpenAlex W-id / 完整 URL
    normalized_openalex = normalize_openalex_id(raw)
    if normalized_openalex and (
        _OPENALEX_ID_RE.match(raw)
        or re.match(r"^https?://(?:www\.)?openalex\.org/", raw, re.IGNORECASE)
    ):
        result["openalex_id"] = normalized_openalex
        return result

    # 裸 DOI
    candidate_doi = normalize_doi(raw)
    if candidate_doi and _BARE_DOI_RE.match(candidate_doi):
        result["doi"] = candidate_doi
        return result

    # 裸 arXiv
    narxiv = normalize_arxiv_id(raw)
    if narxiv and (
        _BARE_ARXIV_NEW_RE.match(raw) or _BARE_ARXIV_OLD_RE.match(raw)
    ):
        result["arxiv_id"] = narxiv
        return result

    # S2 paperId
    ns2 = normalize_s2_id(raw)
    if ns2:
        result["s2_id"] = ns2
        return result

    # 兜底：若像 DOI 也尝试
    if candidate_doi and "/" in candidate_doi and candidate_doi.startswith("10."):
        result["doi"] = candidate_doi

    return result


def title_similarity(title_a: str | None, title_b: str | None) -> float:
    """基于规范化标题词集的 Jaccard 相似度，范围 [0, 1]。"""
    na = normalize_title(title_a)
    nb = normalize_title(title_b)
    if not na or not nb:
        return 0.0
    tokens_a = set(na.split())
    tokens_b = set(nb.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def build_source_ids(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    legacy_external_id: str | None = None,
) -> dict[str, Any] | None:
    """聚合各来源 ID 为 source_ids JSON 结构。"""
    payload: dict[str, Any] = {}
    if doi:
        payload["doi"] = doi
    if arxiv_id:
        payload["arxiv_id"] = arxiv_id
    if openalex_id:
        payload["openalex_id"] = openalex_id
    if s2_id:
        payload["s2_id"] = s2_id
    if legacy_external_id:
        payload["legacy_external_id"] = legacy_external_id
    return payload or None


# ── 匹配 / 登记（需 DB session）────────────────────────────


@dataclass
class PaperWork:
    """待匹配或登记的论文工作单元（元数据，可无 PDF）。"""

    title: str | None = None
    authors: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    external_id: str | None = None
    url: str | None = None
    source: str = "uploaded"
    level: int = 1
    parent_paper_id: int | None = None
    filename: str | None = None


@dataclass
class MatchResult:
    paper: Paper | None
    matched: bool
    method: str | None = None
    confidence: float = 0.0


_FUZZY_TITLE_THRESHOLD = 0.85
_YEAR_TOLERANCE = 1


def normalize_work(work: PaperWork) -> PaperWork:
    """规范化 work 中的各标识，并合并 legacy external_id。"""
    parsed = parse_legacy_external_id(work.external_id)
    doi = normalize_doi(work.doi or parsed.get("doi"))
    arxiv_id = normalize_arxiv_id(work.arxiv_id or parsed.get("arxiv_id"))
    openalex_id = normalize_openalex_id(work.openalex_id or parsed.get("openalex_id"))
    s2_id = normalize_s2_id(work.s2_id or parsed.get("s2_id"))
    return PaperWork(
        title=(work.title or "").strip() or None,
        authors=(work.authors or "").strip() or None,
        year=work.year,
        venue=(work.venue or "").strip() or None,
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        s2_id=s2_id,
        external_id=(work.external_id or "").strip() or None,
        url=(work.url or "").strip() or None,
        source=work.source,
        level=work.level,
        parent_paper_id=work.parent_paper_id,
        filename=work.filename,
    )


def extract_first_author(authors: str | None) -> str | None:
    """取第一作者并规范化（小写、去标点）。"""
    if not authors or not str(authors).strip():
        return None
    raw = str(authors).strip()
    for sep in (";", " and ", " & "):
        raw = raw.replace(sep, ",")
    first = raw.split(",")[0].strip()
    if not first:
        return None
    first = _TITLE_PUNCT_RE.sub(" ", first.lower())
    first = _TITLE_SPACE_RE.sub(" ", first).strip()
    return first or None


def first_author_match(authors_a: str | None, authors_b: str | None) -> bool:
    """第一作者一致（规范化后完全相等）。"""
    fa = extract_first_author(authors_a)
    fb = extract_first_author(authors_b)
    if not fa or not fb:
        return False
    return fa == fb


def year_within_tolerance(
    year_a: int | None,
    year_b: int | None,
    *,
    delta: int = _YEAR_TOLERANCE,
) -> bool:
    if year_a is None or year_b is None:
        return False
    return abs(int(year_a) - int(year_b)) <= delta


def weak_fuzzy_match(work: PaperWork, candidate: Paper) -> tuple[bool, float]:
    """
    弱匹配：title Jaccard≥0.85 AND 第一作者一致 AND 年份±1。
    禁止仅标题匹配。
    """
    sim = title_similarity(work.title, candidate.title)
    if sim < _FUZZY_TITLE_THRESHOLD:
        return False, sim
    if not first_author_match(work.authors, candidate.authors):
        return False, sim
    if not year_within_tolerance(work.year, candidate.year):
        return False, sim
    return True, sim


def _legacy_external_variants(work: PaperWork) -> list[str]:
    """生成可用于 legacy external_id 字段比对的变体。"""
    variants: list[str] = []
    if work.external_id:
        variants.append(work.external_id)
    if work.doi:
        variants.extend([work.doi, f"DOI:{work.doi}", f"doi:{work.doi}"])
    if work.arxiv_id:
        variants.extend([work.arxiv_id, f"arxiv:{work.arxiv_id}"])
    if work.openalex_id:
        variants.extend([work.openalex_id, f"openalex:{work.openalex_id}"])
    if work.s2_id:
        variants.extend([work.s2_id, f"s2:{work.s2_id}"])
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def apply_identity_to_paper(paper: Paper, work: PaperWork) -> None:
    """将 work 中的身份字段写入 paper（仅填空，不覆盖已有强 ID）。"""
    work = normalize_work(work)
    if work.title and not paper.title:
        paper.title = work.title
    if work.authors and not paper.authors:
        paper.authors = work.authors
    if work.venue and not paper.venue:
        paper.venue = work.venue
    if work.year is not None and paper.year is None:
        paper.year = work.year

    if work.doi and not paper.doi:
        paper.doi = work.doi
    if work.arxiv_id and not paper.arxiv_id:
        paper.arxiv_id = work.arxiv_id
    if work.openalex_id and not paper.openalex_id:
        paper.openalex_id = work.openalex_id
    if work.s2_id and not paper.s2_id:
        paper.s2_id = work.s2_id
    if work.external_id and not paper.external_id:
        paper.external_id = work.external_id

    if work.title:
        tn = normalize_title(work.title)
        if tn and not paper.title_normalized:
            paper.title_normalized = tn

    if not paper.canonical_id:
        cid = build_canonical_id(
            doi=paper.doi or work.doi,
            arxiv_id=paper.arxiv_id or work.arxiv_id,
            openalex_id=paper.openalex_id or work.openalex_id,
            s2_id=paper.s2_id or work.s2_id,
            title=paper.title or work.title,
        )
        if cid:
            paper.canonical_id = cid

    merged = build_source_ids(
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        openalex_id=paper.openalex_id,
        s2_id=paper.s2_id,
        legacy_external_id=paper.external_id,
    )
    if merged:
        paper.source_ids = merged


def paper_to_work(paper: Paper) -> PaperWork:
    """从 Paper 记录构造 PaperWork。"""
    return PaperWork(
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        venue=paper.venue,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        openalex_id=paper.openalex_id,
        s2_id=paper.s2_id,
        external_id=paper.external_id,
        source=paper.source or "uploaded",
        level=paper.level or 1,
        parent_paper_id=paper.parent_paper_id,
        filename=paper.filename,
    )


def match_local(session: Session, work: PaperWork) -> MatchResult:
    """
    本地匹配，优先级：
    canonical_id → doi → arxiv → openalex → s2 → legacy external_id → fuzzy。
    """
    from sqlmodel import select
    from ..models.paper import Paper

    work = normalize_work(work)

    cid = build_canonical_id(
        doi=work.doi,
        arxiv_id=work.arxiv_id,
        openalex_id=work.openalex_id,
        s2_id=work.s2_id,
        title=work.title,
    )
    if cid and is_strong_canonical_id(cid):
        hit = session.exec(select(Paper).where(Paper.canonical_id == cid)).first()
        if hit:
            return MatchResult(hit, True, "canonical", 1.0)

    if work.doi:
        hit = session.exec(select(Paper).where(Paper.doi == work.doi)).first()
        if hit:
            return MatchResult(hit, True, "doi", 1.0)
        for ext in _legacy_external_variants(PaperWork(doi=work.doi)):
            hit = session.exec(select(Paper).where(Paper.external_id == ext)).first()
            if hit:
                return MatchResult(hit, True, "legacy", 1.0)

    if work.arxiv_id:
        hit = session.exec(select(Paper).where(Paper.arxiv_id == work.arxiv_id)).first()
        if hit:
            return MatchResult(hit, True, "arxiv", 1.0)
        for ext in _legacy_external_variants(PaperWork(arxiv_id=work.arxiv_id)):
            hit = session.exec(select(Paper).where(Paper.external_id == ext)).first()
            if hit:
                return MatchResult(hit, True, "legacy", 1.0)

    if work.openalex_id:
        hit = session.exec(select(Paper).where(Paper.openalex_id == work.openalex_id)).first()
        if hit:
            return MatchResult(hit, True, "openalex", 1.0)

    if work.s2_id:
        hit = session.exec(select(Paper).where(Paper.s2_id == work.s2_id)).first()
        if hit:
            return MatchResult(hit, True, "s2", 1.0)

    for ext in _legacy_external_variants(work):
        hit = session.exec(select(Paper).where(Paper.external_id == ext)).first()
        if hit:
            return MatchResult(hit, True, "legacy", 1.0)

    if work.title:
        candidates = session.exec(
            select(Paper).where(Paper.title.is_not(None))  # type: ignore[union-attr]
        ).all()
        best: Paper | None = None
        best_conf = 0.0
        for cand in candidates:
            ok, conf = weak_fuzzy_match(work, cand)
            if ok and conf > best_conf:
                best = cand
                best_conf = conf
        if best:
            return MatchResult(best, True, "fuzzy", best_conf)

    return MatchResult(None, False, None, 0.0)


def link_workspace(session: Session, workspace_id: int, paper_id: int) -> bool:
    """将 paper 关联到工作区；已关联则跳过。返回是否新建关联。"""
    from sqlmodel import select
    from ..models.workspace import WorkspacePaper

    existing = session.exec(
        select(WorkspacePaper)
        .where(WorkspacePaper.workspace_id == workspace_id)
        .where(WorkspacePaper.paper_id == paper_id)
    ).first()
    if existing:
        return False
    session.add(WorkspacePaper(workspace_id=workspace_id, paper_id=paper_id))
    session.commit()
    return True


def register_stub(session: Session, work: PaperWork) -> Paper:
    """登记无 PDF 的论文 stub（download_status 非 available）。"""
    from ..models.paper import Paper

    work = normalize_work(work)
    title = work.title or "untitled"
    filename = work.filename or f"{title[:60]}.pdf"

    legacy_ext = work.external_id
    if not legacy_ext and work.doi:
        legacy_ext = f"DOI:{work.doi}"
    elif not legacy_ext and work.arxiv_id:
        legacy_ext = f"arxiv:{work.arxiv_id}"

    paper = Paper(
        filename=filename,
        pdf_path="",
        file_size=0,
        title=title,
        authors=work.authors,
        venue=work.venue,
        year=work.year,
        status="pending",
        source=work.source,
        level=work.level,
        parent_paper_id=work.parent_paper_id,
        external_id=legacy_ext,
        download_status="unavailable",
    )
    apply_identity_to_paper(paper, work)
    paper.match_confidence = 1.0
    paper.updated_at = datetime.now(timezone.utc)
    session.add(paper)
    session.commit()
    session.refresh(paper)
    return paper


def register_or_link(
    session: Session,
    work: PaperWork,
    workspace_id: int | None = None,
) -> tuple[Paper, bool, str]:
    """
    命中则 enrich + 可选 link workspace；未命中则 register stub。

    Returns:
        (paper, is_new, action)  action ∈ matched|registered
    """
    result = match_local(session, work)
    if result.matched and result.paper:
        paper = result.paper
        apply_identity_to_paper(paper, work)
        paper.match_confidence = result.confidence
        paper.updated_at = datetime.now(timezone.utc)
        session.add(paper)
        session.commit()
        session.refresh(paper)
        if workspace_id is not None:
            link_workspace(session, workspace_id, paper.id)
        return paper, False, "matched"

    try:
        paper = register_stub(session, work)
    except Exception as exc:
        # 多 seed 并发扩展时可能同时 miss 后登记同一强 canonical_id。
        # 唯一索引负责裁决；失败方 rollback 后重新读取赢家，避免整条 pipeline 中断。
        from sqlalchemy.exc import IntegrityError

        if not isinstance(exc, IntegrityError):
            raise
        session.rollback()
        raced = match_local(session, work)
        if not raced.matched or raced.paper is None:
            raise
        paper = raced.paper
        apply_identity_to_paper(paper, work)
        paper.match_confidence = raced.confidence
        paper.updated_at = datetime.now(timezone.utc)
        session.add(paper)
        session.commit()
        session.refresh(paper)
        if workspace_id is not None:
            link_workspace(session, workspace_id, paper.id)
        return paper, False, "matched"
    if workspace_id is not None:
        link_workspace(session, workspace_id, paper.id)
    return paper, True, "registered"


def find_or_register(
    session: Session,
    work: PaperWork,
    workspace_id: int | None = None,
) -> tuple[Paper, bool]:
    """match_local 命中则 link；否则 register stub。返回 (paper, is_new)。"""
    paper, is_new, _action = register_or_link(session, work, workspace_id)
    return paper, is_new


def enrich_uploaded_paper(session: Session, paper_id: int) -> None:
    """上传后补充身份字段与 download_status=available。"""
    from pathlib import Path
    from ..models.paper import Paper

    paper = session.get(Paper, paper_id)
    if paper is None:
        return
    work = paper_to_work(paper)
    apply_identity_to_paper(paper, work)
    if paper.pdf_path and Path(paper.pdf_path).is_file():
        paper.download_status = "available"
    paper.updated_at = datetime.now(timezone.utc)
    session.add(paper)
    session.commit()

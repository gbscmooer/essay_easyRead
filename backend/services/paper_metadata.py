"""Source-aware bibliographic metadata, independent of PDF and reading status."""

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlmodel import Session, select

from ..models.paper import Paper

ARXIV_SUBJECTS = {
    "cs.CV": "计算机视觉", "cs.LG": "机器学习", "stat.ML": "机器学习",
    "cs.CL": "自然语言处理", "cs.AI": "人工智能", "cs.AR": "计算机体系结构",
    "cs.RO": "机器人", "cs.IR": "信息检索", "cs.NE": "神经与进化计算",
    "eess.IV": "图像与视频处理", "eess.AS": "音频与语音处理",
    "cs.DC": "分布式计算", "cs.CR": "计算机安全", "cs.SE": "软件工程",
    "cs.DM": "离散数学", "cs.NI": "计算机网络", "cs.SC": "符号计算",
    "cs.SI": "社会与信息网络", "math.OC": "优化与控制",
    "physics.data-an": "数据分析与统计", "physics.soc-ph": "社会物理",
    "cond-mat.stat-mech": "统计力学",
}
UNKNOWN = {"", "unknown", "n/a", "none", "null", "未知", "未识别"}
TYPES = {"journal", "conference", "preprint", "book", "other", "unknown"}
FORMAL_TYPES = {"journal", "conference", "book"}
SUBJECT_LABELS = {
    "Artificial Intelligence": "人工智能", "Computer Vision and Pattern Recognition": "计算机视觉",
    "Computer Networks and Communications": "计算机网络与通信", "Information Systems": "信息系统",
    "Information Systems and Management": "信息系统与管理", "Software": "软件工程",
    "Computer Science Applications": "计算机应用", "Signal Processing": "信号处理",
    "Control and Systems Engineering": "控制与系统工程",
}


def openalex_subjects(item):
    topic = item.get("primary_topic") or next(iter(item.get("topics") or []), {})
    subject = (topic.get("subfield") or {}).get("display_name")
    return [SUBJECT_LABELS.get(subject, subject)] if subject else []


def scopus_publication_type(item):
    kind = publication_type(item.get("prism:aggregationType"), item.get("prism:publicationName"))
    return kind if kind != "unknown" else publication_type(item.get("subtypeDescription"), item.get("prism:publicationName"))


def clean(value):
    value = str(value or "").strip()
    return value if value.casefold() not in UNKNOWN else None


def publication_type(value, venue=None):
    raw = str(value or "").lower()
    if raw in TYPES:
        return raw
    mapped = {"journal-article": "journal", "journal article": "journal", "journal": "journal",
              "conference paper": "conference", "conference-paper": "conference", "cp": "conference",
              "proceedings-article": "conference", "conference proceeding": "conference",
              "posted-content": "preprint", "book-chapter": "book", "book-series": "book"}
    if raw in mapped:
        return mapped[raw]
    name = venue or ""
    if re.search(r"\b(CVPR|ICCV|ECCV|WACV|ICLR|ICML|NeurIPS|AAAI|IJCAI|ACL|EMNLP|conference|proceedings|workshop)\b", name, re.I):
        return "conference"
    if re.search(r"\b(journal|transactions)\b", name, re.I):
        return "journal"
    return "unknown"


def view(paper) -> dict:
    get = paper.get if isinstance(paper, dict) else lambda k, default=None: getattr(paper, k, default)
    bib = get("bibliography") or {}
    ids = get("source_ids") or {}
    origins = dict(bib.get("field_sources") or {})
    category = clean(get("category"))
    subjects = list(bib.get("subjects") or [])
    if category in ARXIV_SUBJECTS and (bib.get("field_sources") or {}).get("subjects") != "manual":
        subjects = [ARXIV_SUBJECTS[category]]
        origins["subjects"] = "arxiv"
    if category and not subjects and (bib.get("field_sources") or {}).get("subjects") != "manual":
        subjects = [ARXIV_SUBJECTS.get(category, category)]
    venue = clean(get("venue"))
    providers = list(bib.get("providers") or [])
    for name, field in (("arxiv", "arxiv_id"), ("openalex", "openalex_id"), ("semantic_scholar", "s2_id"), ("scopus", "scopus_id")):
        if get(field) or ids.get(field) or (name == "arxiv" and category and "." in category):
            providers.append(name)
    return {"subjects": sorted(set(subjects)), "publication_type": publication_type(bib.get("publication_type"), venue),
            "publisher": bib.get("publisher"), "providers": sorted(set(providers)),
            "metadata_updated_at": bib.get("updated_at"), "metadata_sources": origins,
            "doi": get("doi"), "arxiv_id": get("arxiv_id"), "venue": venue,
            "year": get("year") if isinstance(get("year"), int) and 1900 <= get("year") <= datetime.now(timezone.utc).year + 1 else None}


def merge(paper: Paper, data: dict, provider: str) -> None:
    """Nonempty source data only; user corrections outrank automatic extraction."""
    bib = dict(paper.bibliography or {})
    origins = dict(bib.get("field_sources") or {})
    current_kind = publication_type(bib.get("publication_type"), paper.venue)
    incoming_kind = publication_type(data.get("publication_type"), data.get("venue"))
    keep_publication = provider != "manual" and current_kind in FORMAL_TYPES and incoming_kind not in FORMAL_TYPES
    for key in ("title", "authors", "venue", "year", "abstract", "doi", "arxiv_id", "openalex_id"):
        value = data.get(key)
        if keep_publication and key in ("venue", "year"):
            continue
        if key not in data or (provider != "manual" and (value is None or value == "")) or (origins.get(key) == "manual" and provider != "manual"):
            continue
        if key == "year" and value is not None and (not isinstance(value, int) or not 1900 <= value <= datetime.now(timezone.utc).year + 1):
            continue
        if key in ("doi", "arxiv_id", "openalex_id") and getattr(paper, key) and getattr(paper, key) != value:
            continue
        if key == "venue" and not clean(value) and provider != "manual":
            continue
        if isinstance(value, str):
            value = value.strip()
        setattr(paper, key, value)
        if key == "title":
            from .paper_identity import normalize_title
            paper.title_normalized = normalize_title(value)
        origins[key] = provider if provider == "manual" else (data.get("field_sources") or {}).get(key, provider)
    for key in ("subjects", "publication_type", "publisher"):
        value = data.get(key)
        if keep_publication and key in ("publication_type", "publisher"):
            continue
        if key == "subjects" and provider == "crossref" and origins.get(key) == "openalex" and bib.get(key):
            continue
        if value is None or (origins.get(key) == "manual" and provider != "manual"):
            continue
        if key == "subjects":
            value = list(dict.fromkeys(str(v).strip() for v in value if clean(v)))[:30]
        if key == "publication_type":
            value = publication_type(value, data.get("venue"))
        if value == "unknown" and bib.get(key) not in (None, "unknown") and provider != "manual":
            continue
        if value or provider == "manual":
            bib[key] = value
            origins[key] = provider if provider == "manual" else (data.get("field_sources") or {}).get(key, provider)
    bib["providers"] = sorted(set((bib.get("providers") or []) + ([] if provider == "manual" else [provider])))
    bib["field_sources"] = origins
    bib["updated_at"] = datetime.now(timezone.utc).isoformat()
    paper.bibliography = bib
    paper.updated_at = datetime.now(timezone.utc)


def filter_query(statement, session, *, subject=None, venue=None, publication_type=None, provider=None):
    if not any((subject, venue, publication_type, provider)):
        return statement
    # Read only compact metadata, never the potentially 300k-character PDF text.
    rows = session.exec(select(Paper.id, Paper.category, Paper.venue, Paper.year, Paper.bibliography,
                               Paper.source_ids, Paper.doi, Paper.arxiv_id, Paper.openalex_id, Paper.s2_id)).all()
    ids = []
    for row in rows:
        meta = view(dict(row._mapping))
        if subject and (bool(meta["subjects"]) if subject == "__missing__" else subject not in meta["subjects"]):
            continue
        if venue and (bool(meta["venue"]) if venue == "__missing__" else venue != meta["venue"]):
            continue
        if publication_type and publication_type != meta["publication_type"]:
            continue
        if provider and (bool(meta["providers"]) if provider == "__missing__" else provider not in meta["providers"]):
            continue
        ids.append(row.id)
    return statement.where(Paper.id.in_(ids))


def crossref_metadata(item: dict) -> dict:
    from .deep_research.crossref_client import _extract_year
    return {"title": next(iter(item.get("title") or []), None),
            "authors": ", ".join(" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])) or None,
            "venue": next(iter(item.get("container-title") or []), None),
            "year": _extract_year(item), "doi": str(item.get("DOI") or "").lower() or None,
            "publication_type": publication_type(item.get("type")), "publisher": item.get("publisher"),
            "subjects": item.get("subject") or []}


def openalex_metadata(item: dict) -> dict:
    from .deep_research.providers.openalex import _reconstruct_abstract, _extract_authors
    source = (item.get("primary_location") or {}).get("source") or {}
    kind = "preprint" if item.get("type") == "preprint" or source.get("type") == "repository" else publication_type(source.get("type"), source.get("display_name"))
    if kind == "unknown":
        kind = publication_type(item.get("type"), source.get("display_name"))
    return {"title": item.get("title"), "authors": _extract_authors(item), "year": item.get("publication_year"),
            "venue": source.get("display_name") if source.get("type") != "repository" else None,
            "doi": str(item.get("doi") or "").replace("https://doi.org/", "").lower() or None,
            "openalex_id": str(item.get("id") or "").rsplit("/", 1)[-1] or None,
            "abstract": _reconstruct_abstract(item), "subjects": openalex_subjects(item),
            "publication_type": kind, "publisher": source.get("host_organization_name")}


async def enrich(paper_id: int, *, engine=None) -> bool:
    from .database import get_engine
    from .paper_identity import normalize_title
    from .deep_research.crossref_client import _crossref_get, CROSSREF_API
    from .deep_research.providers.openalex import OpenAlexProvider, WORK_FIELDS
    engine = engine or get_engine()
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            return False
        doi, aid, oid, title, path = paper.doi, paper.arxiv_id, paper.openalex_id, paper.title, paper.pdf_path
    # Header identifiers must come from the first page, not a reference-list DOI.
    if path and Path(path).is_file() and (not doi or not aid):
        from .auto_indexer import _extract, _heuristic_meta
        _, metadata, first = await asyncio.to_thread(_extract, path)
        header = _heuristic_meta(metadata, first)
        aid = aid or header.get("arxiv_id")
        matches = re.findall(r"(?:https?://(?:dx\.)?doi\.org/|\bdoi\s*:\s*)(10\.\d{4,9}/[^\s<>]+)", first, re.I)
        if not doi and len(set(matches)) == 1:
            doi = matches[0].rstrip(".,;)").lower()
    updates = []
    if aid:
        try:
            from .deep_research.arxiv_client import get_metadata
            am = await get_metadata(aid)
            if am:
                title = am.get("title") or title
                doi = doi or am.get("doi")
                am["publication_type"] = "preprint"
                am["subjects"] = [ARXIV_SUBJECTS.get(am["category"], am["category"])] if am.get("category") else []
                updates.append(("arxiv", am))
        except Exception:
            pass
    from .ai_config import get_research_config
    from ..config import settings
    from .deep_research.providers.scopus import ScopusProvider
    api_key = get_research_config().get("elsevier_api_key") or settings.elsevier_api_key
    if api_key:
        scopus = ScopusProvider(api_key)
        try:
            query = f"DOI({doi})" if doi else f'TITLE("{title.replace(chr(34), " ")}")' if title else None
            response = await scopus._get("/content/search/scopus", {"query": query, "count": 3}) if query else None
            entries = scopus._search_entries(response)
            entry = next((e for e in entries if (doi and scopus._entry_doi(e) and scopus._entry_doi(e).lower() == doi.lower())
                          or (not doi and normalize_title(e.get("dc:title")) == normalize_title(title))), None)
            if not entry and doi and title:
                response = await scopus._get("/content/search/scopus", {"query": f'TITLE("{title.replace(chr(34), " ")}")', "count": 3})
                entry = next((e for e in scopus._search_entries(response) if normalize_title(e.get("dc:title")) == normalize_title(title)), None)
            if entry:
                sm = {"title": entry.get("dc:title"), "authors": entry.get("dc:creator"),
                      "venue": entry.get("prism:publicationName"), "doi": scopus._entry_doi(entry),
                      "year": scopus._entry_year(entry), "publication_type": scopus_publication_type(entry),
                      "scopus_id": scopus._entry_eid(entry)}
                updates.append(("scopus", sm))
                doi = doi or sm.get("doi")
        except Exception:
            pass
        finally:
            await scopus.aclose()
    oa = OpenAlexProvider()
    try:
        lookup = f"/works/doi:{quote(doi, safe='')}" if doi else f"/works/{oid}" if oid else None
        item = await oa._safe_get(lookup, {"select": WORK_FIELDS}) if lookup else None
        if not item and title:
            data = await oa._safe_get("/works", {"search": title, "per-page": 3, "select": WORK_FIELDS})
            item = next((w for w in (data or {}).get("results", []) if normalize_title(w.get("title")) == normalize_title(title)), None)
        if item:
            om = openalex_metadata(item)
            if not doi or not om.get("doi") or doi.lower() == om["doi"]:
                updates.append(("openalex", om))
                doi = doi or om.get("doi")
    except Exception:
        pass
    finally:
        await oa.aclose()
    if doi:
        try:
            data = await _crossref_get(f"{CROSSREF_API}/{quote(doi, safe='')}")
            item = data.get("message") or {}
            if str(item.get("DOI", "")).lower() == doi.lower():
                updates.append(("crossref", crossref_metadata(item)))
        except Exception:
            pass
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            return False
        for provider, data in updates:
            merge(paper, data, provider)
            if data.get("scopus_id"):
                paper.source_ids = {**(paper.source_ids or {}), "scopus_id": data["scopus_id"]}
        session.add(paper)
        session.commit()
    return bool(updates)


_task: asyncio.Task | None = None
_progress = {"running": False, "total": 0, "completed": 0, "unmatched": 0}


def refresh_status():
    return dict(_progress)


def start_refresh(source: str) -> dict:
    from .database import get_engine
    global _task
    if _task and not _task.done():
        return refresh_status()
    with Session(get_engine()) as session:
        query = select(Paper.id)
        if source != "all":
            query = query.where(Paper.source == source)
        ids = list(session.exec(query).all())
    _progress.update(running=bool(ids), total=len(ids), completed=0, unmatched=0)

    async def run():
        semaphore = asyncio.Semaphore(3)

        async def one(pid):
            async with semaphore:
                try:
                    if not await asyncio.wait_for(enrich(pid), timeout=90):
                        _progress["unmatched"] += 1
                except Exception:
                    _progress["unmatched"] += 1
                finally:
                    _progress["completed"] += 1
        try:
            await asyncio.gather(*(one(pid) for pid in ids))
        finally:
            _progress["running"] = False
    _task = asyncio.create_task(run())
    return refresh_status()

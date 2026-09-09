"""Run-scoped holdouts, shared by corpus selection and HTTP download redirects."""

from contextvars import ContextVar
import re
import unicodedata
from urllib.parse import unquote, urlparse

from .paper_identity import normalize_doi as _doi

active_exclusions: ContextVar[dict] = ContextVar("research_exclusions", default={})


def normalize_doi(value: str) -> str:
    value = unquote(str(value or "")).strip().lower().removeprefix("doi.org/")
    return _doi(value) or ""


def normalize_arxiv_id(value: str) -> str:
    value = unquote(str(value or "")).strip().lower()
    value = value.removeprefix("arxiv:").removeprefix("doi:")
    if "arxiv.org/" in value:
        path = urlparse(value if "://" in value else "https://" + value).path
        value = re.sub(r"^/(abs|pdf|html)/", "", path)
    value = normalize_doi(value).removeprefix("10.48550/arxiv.")
    value = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", value).strip()
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})", value) else ""


def title_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", value)
    return re.sub(r"[\W_]+", "", value)


def excluded(paper: dict, options: dict | None = None) -> bool:
    options = active_exclusions.get() if options is None else options
    policy = options.get("exclusions") or {}
    dois = {normalize_doi(x) for x in policy.get("dois", [])} - {""}
    arxivs = {normalize_arxiv_id(x) for x in [*policy.get("arxiv_ids", []), *dois]} - {""}
    titles = {title_key(x) for x in policy.get("titles", [])} - {""}
    if title_key(paper.get("title")) in titles:
        return True
    for field in ("doi", "arxiv_id", "key", "canonical_id", "url", "pdf_url"):
        value = str(paper.get(field) or "")
        if normalize_doi(value) in dois or normalize_arxiv_id(value) in arxivs:
            return True
        path = unquote(urlparse(value).path)
        if any(path.lower().endswith("/" + doi) for doi in dois):
            return True
    return False


def apply_exclusions(papers: list[dict], options: dict) -> tuple[list[dict], dict]:
    if not options.get("exclusions"):
        return papers, {"count": 0}
    kept, removed = [], []
    for paper in papers:
        (removed if excluded(paper, options) else kept).append(paper)
    return kept, {"count": len(removed), **(options.get("exclusions") or {}),
                  "removed_titles": [p.get("title") or p.get("key") for p in removed][:10]}


def check_download_url(url: str) -> None:
    if excluded({"url": url}):
        raise ValueError("EXCLUDED_RESEARCH_PAPER")

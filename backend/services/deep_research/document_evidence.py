"""Read-only indexing and targeted reading for already acquired paper text.

The download layer owns file acquisition.  This module only indexes text that is
already present on a paper record, exposes bounded search/context reads, and
resolves literal quotes back to page/section/table locations when possible.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


_PAGE_MARKER = re.compile(r"(?m)^\s*---\s*Page\s+(\d+)\s*---\s*$", re.IGNORECASE)
_HEADING = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)\s+)?(?:abstract|introduction|related work|method(?:ology)?|"
    r"experiments?|experimental setup|implementation details|results?|discussion|"
    r"limitations?|conclusion|appendix|supplementary materials?)\b",
    re.IGNORECASE,
)
_TABLE = re.compile(r"\b(?:table|tab\.)\s*([A-Z0-9IVX.-]+)", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.%×+-]*|[\u4e00-\u9fff]")
_QUESTION_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{1,}|[\u4e00-\u9fff]{2,}")
_QUESTION_STOPWORDS = {
    "about", "and", "are", "does", "for", "from", "have", "how", "into",
    "research", "the", "this", "using", "what", "which", "with",
}


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(normalize_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class TextBlock:
    location_id: str
    text: str
    char_start: int
    char_end: int
    page: int | None
    section: str | None
    table: str | None
    ordinal: int

    def summary(self, *, max_chars: int = 900) -> dict:
        return {
            "location_id": self.location_id,
            "page": self.page,
            "section": self.section,
            "table": self.table,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": normalize_text(self.text)[:max_chars],
            "location_status": "resolved" if self.page is not None else "partial",
        }


class DocumentEvidenceReader:
    """A bounded, deterministic reader over one already acquired document."""

    def __init__(self, paper_key: str, text: str, *, source_kind: str = "full_text"):
        self.paper_key = str(paper_key)
        self.text = str(text or "")
        self.source_kind = source_kind
        self.document_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        self.blocks = self._build_blocks()
        self._by_id = {block.location_id: block for block in self.blocks}

    def _page_at(self, offset: int, markers: list[tuple[int, int]]) -> int | None:
        page = None
        for position, number in markers:
            if position > offset:
                break
            page = number
        return page

    def _build_blocks(self) -> list[TextBlock]:
        if not self.text.strip():
            return []
        markers = [(match.start(), int(match.group(1))) for match in _PAGE_MARKER.finditer(self.text)]
        paragraphs = list(re.finditer(r"\S(?:.|\n)*?(?=\n\s*\n|\Z)", self.text))
        if len(paragraphs) < 3:
            paragraphs = list(re.finditer(r"\S(?:.|\n){0,1799}(?=\s|\Z)", self.text))
        blocks: list[TextBlock] = []
        current_section: str | None = None
        for ordinal, match in enumerate(paragraphs):
            raw = match.group(0).strip()
            cleaned = normalize_text(raw)
            if not cleaned or _PAGE_MARKER.fullmatch(raw):
                continue
            first_line = normalize_text(raw.splitlines()[0])[:180]
            if len(first_line) <= 180 and _HEADING.match(first_line):
                current_section = first_line
            table_match = _TABLE.search(cleaned[:500])
            table = f"Table {table_match.group(1)}" if table_match else None
            location_id = _stable_id("loc", self.paper_key, match.start(), match.end(), cleaned[:160])
            blocks.append(TextBlock(
                location_id=location_id,
                text=raw,
                char_start=match.start(),
                char_end=match.end(),
                page=self._page_at(match.start(), markers),
                section=current_section,
                table=table,
                ordinal=len(blocks),
            ))
        return blocks

    @staticmethod
    def _score(text: str, query: str) -> float:
        tokens = [token.casefold() for token in _TOKEN.findall(normalize_text(query))]
        if not tokens:
            return 0.0
        haystack = normalize_text(text).casefold()
        unique = set(tokens)
        score = sum(1.0 + min(3, haystack.count(token)) * 0.2 for token in unique if token in haystack)
        phrase = normalize_text(query).casefold()
        return score + (3.0 if phrase and phrase in haystack else 0.0)

    def search(self, query: str, *, limit: int = 4, offset: int = 0) -> dict:
        limit = max(1, min(int(limit), 12))
        offset = max(0, int(offset))
        ranked = sorted(
            ((self._score(block.text, query), block) for block in self.blocks),
            key=lambda item: (-item[0], item[1].ordinal),
        )
        matches = [block for score, block in ranked if score > 0]
        page = matches[offset:offset + limit]
        return {
            "query": normalize_text(query)[:400],
            "offset": offset,
            "limit": limit,
            "total": len(matches),
            "next_offset": offset + limit if offset + limit < len(matches) else None,
            "items": [block.summary() for block in page],
        }

    def read_context(self, location_id: str, *, before: int = 1, after: int = 1) -> dict:
        block = self._by_id.get(str(location_id))
        if block is None:
            raise KeyError("unknown location_id")
        before = max(0, min(int(before), 3))
        after = max(0, min(int(after), 3))
        start = max(0, block.ordinal - before)
        end = min(len(self.blocks), block.ordinal + after + 1)
        selected = self.blocks[start:end]
        return {
            "location_id": block.location_id,
            "paper_key": self.paper_key,
            "page": block.page,
            "section": block.section,
            "table": block.table,
            "char_start": selected[0].char_start,
            "char_end": selected[-1].char_end,
            "text": "\n\n".join(item.text for item in selected),
            "location_status": "resolved" if block.page is not None else "partial",
        }

    def initial_material(self, queries: list[str], *, limit: int = 6) -> str:
        chosen: dict[str, TextBlock] = {}
        for query in queries:
            for item in self.search(query, limit=1)["items"]:
                block = self._by_id[item["location_id"]]
                chosen.setdefault(block.location_id, block)
        for block in self.blocks:
            if len(chosen) >= limit:
                break
            chosen.setdefault(block.location_id, block)
        return "\n\n".join(block.text for block in sorted(chosen.values(), key=lambda row: row.ordinal)[:limit])

    def locate_quote(self, quote: str) -> dict:
        needle = normalize_text(quote)
        if not needle:
            return {"location_status": "unavailable", "page": None, "section": None,
                    "table": None, "char_start": None, "char_end": None}
        candidates = [block for block in self.blocks if needle in normalize_text(block.text)]
        if not candidates:
            # A quote may span a table header, row and footnote. Search bounded adjacent context.
            for block in self.blocks:
                context = self.read_context(block.location_id, before=1, after=1)
                if needle in normalize_text(context["text"]):
                    candidates.append(block)
                    break
        if not candidates:
            return {"location_status": "unavailable", "page": None, "section": None,
                    "table": None, "char_start": None, "char_end": None}
        block = candidates[0]
        return {
            "location_id": block.location_id,
            "location_status": "resolved" if block.page is not None else "partial",
            "page": block.page,
            "section": block.section,
            "table": block.table,
            "char_start": block.char_start,
            "char_end": block.char_end,
        }

    def context_for_quotes(self, quotes: list[str], *, max_chars: int = 18000) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for quote in quotes:
            location = self.locate_quote(quote)
            location_id = location.get("location_id")
            if not location_id or location_id in seen:
                continue
            seen.add(location_id)
            part = self.read_context(location_id, before=1, after=1)["text"]
            if sum(len(value) for value in parts) + len(part) > max_chars:
                break
            parts.append(part)
        return "\n\n".join(parts)


def _question_scope(question: str, *, max_terms: int = 12) -> str:
    """Return a bounded, domain-neutral lexical scope for evidence lookup."""
    terms: list[str] = []
    for token in _QUESTION_TOKEN.findall(normalize_text(question)):
        folded = token.casefold()
        if folded in _QUESTION_STOPWORDS or folded in {item.casefold() for item in terms}:
            continue
        terms.append(token)
        if len(terms) >= max_terms:
            break
    return " ".join(terms) or "research topic"


def report_subquestions(question: str, scope_terms: list[str] | None = None) -> list[dict]:
    """Build stable, domain-neutral report-reading tasks scoped to the actual question."""
    # Keep table/condition signals compact, but always include the user's topic.  This avoids
    # silently applying the former image-classification vocabulary to unrelated domains.
    scoped_input = " ".join(str(item) for item in (scope_terms or []) if str(item).strip()) or question
    scope = _question_scope(scoped_input)
    return [
        {"id": "effect_comparison", "label": "效果比较",
         "query": f"{scope} outcome result effectiveness comparison baseline metric accuracy score value table"},
        {"id": "runtime_efficiency", "label": "运行效率与实验条件",
         "query": f"{scope} efficiency runtime latency throughput cost hardware resource batch size input precision experimental setting"},
        {"id": "method_mechanism", "label": "方法机制与适用范围",
         "query": f"{scope} method approach architecture mechanism assumption scope"},
        {"id": "limitations", "label": "局限与不可比条件",
         "query": f"{scope} limitation drawback comparable fair comparison protocol dataset split"},
        {"id": "novelty_scope", "label": "趋势与查新边界",
         "query": f"{scope} novelty prior work trend conclusion discussion future work evidence"},
    ]

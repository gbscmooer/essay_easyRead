"""arXiv HTML（LaTeXML 生成，arxiv.org/html 与 ar5iv 同构）→ 章节名与每节引用的参考文献。

PaSa 的 [Expand] 动作：Crawler 看到论文的章节名，选出值得追的章节，只把这些章节里引用的论文加入队列。
这里用标准库 HTMLParser 解析：
    <section class="ltx_section"> <h2 class="ltx_title">2 Related Work</h2> … <cite><a href="#bib.bib12">
    <li id="bib.bib12" class="ltx_bibitem"> <span class="ltx_bibblock">作者</span><span class="ltx_bibblock">题名</span>…
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

HTML_URLS = ("https://arxiv.org/html/{id}", "https://ar5iv.labs.arxiv.org/html/{id}")
USER_AGENT = "essay_read-section-expand/1.0 (academic thesis project)"
TIMEOUT = 25.0
MAX_BYTES = 6_000_000
_GATES: dict[int, asyncio.Semaphore] = {}   # arXiv 要求放慢抓取；按事件循环各一个
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_ARXIV_ID = re.compile(r"(?:arXiv[:\s]*|abs/)(\d{4}\.\d{4,5})", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;\]\)]+")
_SPACE = re.compile(r"\s+")
_VENUE = re.compile(r"^(in\b|proceedings|proc\.|arxiv|advances in|journal|transactions|conference|workshop)", re.I)


@dataclass
class Reference:
    bib_id: str
    text: str
    title: str = ""
    arxiv_id: str = ""
    doi: str = ""


@dataclass
class PaperSections:
    sections: dict[str, list[str]] = field(default_factory=dict)   # 章节名 → bib id（按首次引用顺序）
    references: dict[str, Reference] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [name for name, ids in self.sections.items() if ids]

    def cited(self, names: list[str], limit: int) -> list[Reference]:
        out, seen = [], set()
        for name in names:
            for bib in self.sections.get(name, []):
                ref = self.references.get(bib)
                if ref is None or bib in seen:
                    continue
                seen.add(bib)
                out.append(ref)
                if len(out) >= limit:
                    return out
        return out


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = PaperSections()
        self._heading_depth = 0          # >0 表示正在读标题文字
        self._heading_level = ""
        self._heading_text: list[str] = []
        self._section = "(front)"
        self._top = ""                   # 当前一级章节名（子章节名前缀用）
        self._bib: str | None = None     # 正在读的参考文献条目 id
        self._bib_depth = 0
        self._block_depth = 0
        self._blocks: list[list[str]] = []
        self._raw: list[str] = []
        self._in_biblio = False

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in ("h2", "h3", "h4") and "ltx_title" in cls and not self._in_biblio:
            self._heading_depth, self._heading_level, self._heading_text = 1, tag, []
            return
        if self._heading_depth:
            self._heading_depth += 1
        if tag == "section" and "ltx_bibliography" in cls:
            self._in_biblio = True
        if tag == "li" and "ltx_bibitem" in cls and a.get("id"):
            self._bib, self._bib_depth, self._blocks, self._raw = a["id"], 1, [], []
            return
        if self._bib is not None:
            self._bib_depth += 1
            if tag == "span" and "ltx_bibblock" in cls:
                self._block_depth = 1
                self._blocks.append([])
            elif self._block_depth:
                self._block_depth += 1
            return
        if tag == "a" and not self._in_biblio:
            href = a.get("href") or ""
            if "#bib." in href:
                bib = href.split("#", 1)[1]
                ids = self.result.sections.setdefault(self._section, [])
                if bib not in ids:
                    ids.append(bib)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                name = _clean_heading(_SPACE.sub(" ", "".join(self._heading_text)).strip())
                if name and not self._in_biblio:
                    if self._heading_level == "h2":
                        self._top = name
                        self._section = name
                    else:
                        self._section = f"{self._top} / {name}" if self._top else name
                    self.result.sections.setdefault(self._section, [])
            return
        if self._bib is not None:
            if self._block_depth:
                self._block_depth -= 1
            self._bib_depth -= 1
            if self._bib_depth == 0:
                blocks = [_SPACE.sub(" ", "".join(b)).strip() for b in self._blocks]
                raw = _SPACE.sub(" ", "".join(self._raw)).strip()
                self.result.references[self._bib] = _reference(self._bib, raw, [b for b in blocks if b])
                self._bib = None
        if tag == "section" and self._in_biblio:
            self._in_biblio = False

    def handle_data(self, data):
        if self._heading_depth:
            self._heading_text.append(data)
        if self._bib is not None:
            self._raw.append(data)
            if self._block_depth and self._blocks:
                self._blocks[-1].append(data)


def _clean_heading(name: str) -> str:
    return re.sub(r"^\s*(?:[A-Z]|\d+(?:\.\d+)*)\s*[.:]?\s+", "", name).strip()


def _author_like(part: str) -> bool:
    words = part.replace(",", " ").split()
    if not words:
        return True
    initials = sum(1 for w in words if re.fullmatch(r"[A-Z]\.?(?:-[A-Z]\.?)?", w))
    caps = sum(1 for w in words if w[:1].isupper()) / len(words)
    return initials >= 1 and caps > 0.6 or ("," in part and caps > 0.75) or " et al" in part


def _title_from(text: str) -> str:
    """从一段参考文献文字里取题名：按句号切开，取第一个 ≥4 词、不像作者名单也不像出处的句段。"""
    parts = [p.strip() for p in re.split(r"\.\s+", text) if p.strip()]
    return next((p for p in parts if len(p.split()) >= 4 and not _VENUE.match(p) and not _author_like(p)), "")


def _reference(bib_id: str, raw: str, blocks: list[str]) -> Reference:
    title = ""
    if len(blocks) >= 2 and len(blocks[1].split()) >= 3 and not _VENUE.match(blocks[1]) and not _author_like(blocks[1]):
        title = blocks[1]
    if not title:
        title = _title_from(" ".join(blocks) or raw)
    title = title.strip(" .,\"“”")
    arxiv = _ARXIV_ID.search(raw)
    doi = _DOI.search(raw)
    return Reference(bib_id=bib_id, text=raw[:600], title=title[:300],
                     arxiv_id=arxiv.group(1) if arxiv else "", doi=doi.group(0).rstrip(".") if doi else "")


def parse_html(html: str) -> PaperSections:
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 —— 坏 HTML 只丢这一篇的展开
        logger.warning("arXiv HTML parse failed: %s", exc)
    result = parser.result
    # 去掉参考文献、附录等不该追的“章节”，并把编号前缀去掉方便模型阅读
    cleaned: dict[str, list[str]] = {}
    for name, ids in result.sections.items():
        if re.search(r"\b(references|bibliography|acknowledg)", name, re.I):
            continue
        cleaned[name] = ids
    result.sections = cleaned
    return result


async def fetch_sections(arxiv_id: str) -> PaperSections | None:
    """取 arXiv HTML 并解析；两处都取不到或没有参考文献时返回 None（由调用方改走参考文献列表）。"""
    import httpx

    aid = re.sub(r"v\d+$", "", str(arxiv_id or "").strip())
    if not aid:
        return None
    loop_id = id(asyncio.get_running_loop())
    gate = _GATES.setdefault(loop_id, asyncio.Semaphore(2))
    async with gate:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            for template in HTML_URLS:
                url = template.format(id=aid)
                try:
                    r = await client.get(url)
                except Exception as exc:  # noqa: BLE001
                    logger.info("arXiv HTML fetch failed %s: %s", url, exc)
                    continue
                if r.status_code != 200 or "ltx_bibitem" not in r.text[:MAX_BYTES]:
                    continue
                parsed = parse_html(r.text[:MAX_BYTES])
                if parsed.references and parsed.names():
                    return parsed
    return None

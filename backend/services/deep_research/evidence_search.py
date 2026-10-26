"""证据检索工具：把论文原文切成句子，每句一个指针（S1、S2…），供模型检索与引用。

模型不复述原文，只引用指针；程序按指针取回逐字原文，因此摘录不会被改写或编造。
检索按两层组织：
- 文献层（找论文）：本地 arXiv 镜像按题名精确查找、按检索式检索（字面 BM25 + 向量）；
- 句子层（找证据）：在已收入的论文句子上做 BM25 预筛、名称与数字的精确匹配（grep），
  再由交叉编码器按查询重排；read 按指针取前后文。
只有数量预算，没有分数阈值；题名查找的“同一篇”判定是身份核对，不是相关性筛选。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from .quote_finder import _PAGE, _REFS_HEAD, FULL_TEXT_MIN_CHARS, split_sentences
from .reranker import rerank_scores

logger = logging.getLogger(__name__)

BM25_K1, BM25_B = 1.2, 0.75
PREFILTER = 40                 # 每次检索进入重排的候选句数
GREP_PER_TERM = 8              # 每个名称/数字精确匹配补充的候选句数
RERANK_CONCURRENCY = 8
TITLE_MATCH_RATIO = 0.85       # 模型给出的题名与镜像题名视为同一篇的词序相似度下限（身份核对）
TABLE_MIN_CELLS = 6            # 表题旁至少这么多行短行才当作表格
TABLE_BLOCK_CHARS = 600        # 每个表格块的字符上限（大表切成几块，每块都带表题）
TABLE_MAX_BLOCKS = 3           # 每张表最多几块

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have in into is it its of on or that the their "
                  "this to was were which with we our can not these those such also more most than then "
                  "how what why when where who does do using use used based between".split())
# 专名（驼峰、全大写缩写、带数字的模型名）与具体数字：最需要逐字核对、也最容易被改写的信息
_ENTITY = re.compile(r"\b(?:[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9-]*|[A-Za-z]+-?\d+[A-Za-z0-9.-]*"
                     r"|\d+(?:\.\d+)?\s?(?:%|[KMB]\b)|\d+\.\d+)")
_YEARLIKE = re.compile(r"^(?:19|20)\d{2}$")


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def entities(text: str) -> list[str]:
    """陈述里值得精确匹配的专名与数字（去掉年份这类过于常见的数字）。"""
    out = []
    for m in _ENTITY.findall(text or ""):
        m = m.strip()
        if len(m) >= 3 and not _YEARLIKE.match(m) and m not in out:
            out.append(m)
    return out


def title_key(title: str) -> str:
    return " ".join(_TOKEN.findall((title or "").lower()))


_CAPTION = re.compile(r"^(?:table|tab\.)\s*([0-9]+|[ivxl]+)\s*[.:|]\s*\S", re.IGNORECASE)
_TABLE_MENTION = re.compile(r"\b(?:table|tab\.)\s*([0-9]+|[IVXL]+)\b", re.IGNORECASE)
_URL_LINE = re.compile(r"^(?:[a-z\d*†‡§]\s*)?https?://\S+$", re.IGNORECASE)


def table_blocks(text: str) -> list[tuple[str, str]]:
    """全文里的表格 → [(表号, 表格块文本)]。PDF 抽出的表格是一串短行（每格一行），切句时会被当作
    碎片丢掉，而论文自建数据集的组成、来源项目、规模与结果常常只写在表里。按表题（Table N:）定位，
    取表题一侧连续的短行（含表注里的网址）拼成“表题 — 格 | 格 | …”；表题在表上方还是下方按整篇
    论文的多数排版统一取一侧，避免把相邻表格的内容配到错的表题下。"""
    text = str(text or "")
    heads = list(_REFS_HEAD.finditer(text))
    if heads and heads[-1].start() > len(text) * 0.3:
        text = text[:heads[-1].start()]
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not _PAGE.fullmatch(ln)]
    widths = sorted(len(ln) for ln in lines if len(ln) > 20)
    full = widths[len(widths) // 2] if widths else 80

    def cell(ln: str) -> bool:
        return (len(ln) < 0.75 * full or bool(_URL_LINE.match(ln))) and not _CAPTION.match(ln)

    found = []
    for i, ln in enumerate(lines):
        m = _CAPTION.match(ln)
        if not m:
            continue
        cap, j = ln, i + 1
        while j < len(lines) and not re.search(r"[.:]$", cap) and len(cap) < 300 and not cell(lines[j]):
            cap += " " + lines[j]
            j += 1
        k, before = i - 1, []
        while k >= 0 and cell(lines[k]):
            before.append(lines[k])
            k -= 1
        k, after = j, []
        while k < len(lines) and cell(lines[k]):
            after.append(lines[k])
            k += 1
        found.append((m.group(1).upper(), cap, before[::-1], after))
    below = sum(len(b) > len(a) for _, _, b, a in found) > len(found) / 2   # 表题在表下方的论文
    out = []
    for no, cap, before, after in found:
        cells = before if below else after
        if len(cells) < TABLE_MIN_CELLS:
            continue
        head = f"{cap.rstrip()} —"
        blocks, cur = [], head
        for c in cells:
            if len(cur) + len(c) + 3 > TABLE_BLOCK_CHARS and cur != head:
                blocks.append(cur)
                cur = head
                if len(blocks) == TABLE_MAX_BLOCKS:
                    break
            cur += (" " if cur == head else " | ") + c
        if cur != head and len(blocks) < TABLE_MAX_BLOCKS:
            blocks.append(cur)
        out += [(no, b) for b in blocks]
    return out


def _full_text_units(text: str, tables: bool = True) -> list[tuple[str, str]]:
    """全文 → [(文本, 表号)]：正文句在前（表号为空），tables=True 时表格块接在后面。"""
    units = [(s, "") for s in split_sentences(text)]
    return units + [(b, no) for no, b in table_blocks(text)] if tables else units


@dataclass(frozen=True)
class Passage:
    pid: str
    key: str
    title: str
    year: int | None
    text: str
    source: str        # full_text | abstract
    ordinal: int       # 在该论文句子序列中的位置（read 取前后文用）
    table: str = ""    # 表格块的表号（正文句为空）

    def brief(self, limit: int = 420) -> dict:
        return {"pid": self.pid, "title": self.title, "year": self.year, "source": self.source,
                "text": self.text if len(self.text) <= limit else self.text[: limit - 1] + "…"}


class EvidenceIndex:
    """一次作答的证据库：收入论文 → 句子指针；BM25 倒排索引支持增量收入。
    tables=True 时全文里的表格也按块收入（精读用；默认关，现行作答的证据库与之前一致）。"""

    def __init__(self, *, tables: bool = False) -> None:
        self.tables = tables
        self.passages: list[Passage] = []
        self.papers: dict[str, dict] = {}
        self.by_key: dict[str, list[int]] = defaultdict(list)
        self._titles: dict[str, str] = {}
        self._ids: set[str] = set()     # arXiv 编号与 DOI：同一篇论文从不同来源（召回用 DOI 主键、镜像用 arXiv 主键）只收一次
        self._post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._len: list[int] = []
        self._total_len = 0

    def __len__(self) -> int:
        return len(self.passages)

    def has_title(self, title: str) -> str | None:
        return self._titles.get(title_key(title))

    def add_paper(self, paper: dict, *, origin: str = "recall") -> int:
        """收入一篇论文：有全文用全文句子，否则用摘要句子；同一主键或同题名只收一次。"""
        key = str(paper.get("key") or "")
        tkey = title_key(paper.get("title") or "")
        ids = set()
        if paper.get("arxiv_id"):
            ids.add(f"arxiv:{str(paper['arxiv_id']).lower()}")
        if paper.get("doi"):
            ids.add(f"doi:{str(paper['doi']).lower()}")
        if not key or key in self.papers or (tkey and tkey in self._titles) or ids & self._ids:
            return 0
        full = str(paper.get("source_text") or "")
        if len(full) >= FULL_TEXT_MIN_CHARS:
            units, source = _full_text_units(full, self.tables), "full_text"
        else:
            units, source = [(s, "") for s in split_sentences(paper.get("abstract") or "")], "abstract"
        if not units:
            return 0
        self.papers[key] = {k: paper.get(k) for k in ("key", "title", "year", "doi", "arxiv_id", "authors")}
        self.papers[key]["origin"] = origin
        self.papers[key]["source"] = source
        if tkey:
            self._titles[tkey] = key
        self._ids |= ids
        self._append(key, str(paper.get("title") or ""), paper.get("year"), units, source)
        return len(units)

    def add_full_text(self, key: str, text: str) -> int:
        """为已收入（只有摘要）的论文补全文句子与表格块；已有指针不变，新句接在后面编号。"""
        if key not in self.papers or self.papers[key].get("source") == "full_text":
            return 0
        meta = self.papers[key]
        have = {self.passages[i].text.casefold() for i in self.by_key[key]}
        units = [(s, t) for s, t in _full_text_units(text, self.tables) if s.casefold() not in have]
        if not units:
            return 0
        meta["source"] = "full_text"
        self._append(key, meta.get("title") or "", meta.get("year"), units, "full_text")
        return len(units)

    def _append(self, key: str, title: str, year, units: list[tuple[str, str]], source: str) -> None:
        title_tokens = tokens(title)
        start = len(self.by_key[key])
        for offset, (text, table) in enumerate(units):
            idx = len(self.passages)
            self.passages.append(Passage(f"S{idx + 1}", key, title, year, text, source, start + offset, table))
            self.by_key[key].append(idx)
            doc = tokens(text) + title_tokens   # 题名词并入每句：点名某篇论文的陈述能命中该文的句子
            for term, tf in Counter(doc).items():
                self._post[term].append((idx, tf))
            self._len.append(len(doc))
            self._total_len += len(doc)

    def tables_mentioned(self, passage: Passage) -> list[Passage]:
        """句中提到的本文表格（Table 3 / Table III）对应的表格块。"""
        nos = {m.upper() for m in _TABLE_MENTION.findall(passage.text)}
        if not nos or passage.table:
            return []
        return [self.passages[i] for i in self.by_key[passage.key] if self.passages[i].table in nos]

    def get(self, pid: str) -> Passage | None:
        m = re.fullmatch(r"S(\d+)", str(pid).strip())
        if not m:
            return None
        i = int(m.group(1)) - 1
        return self.passages[i] if 0 <= i < len(self.passages) else None

    def bm25(self, query: str, k: int = PREFILTER, keys: set[str] | None = None) -> list[Passage]:
        q = set(tokens(query))
        n = len(self.passages)
        if not q or not n:
            return []
        avg = self._total_len / n
        scores: dict[int, float] = defaultdict(float)
        for term in q:
            post = self._post.get(term)
            if not post:
                continue
            idf = math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for idx, tf in post:
                if keys is not None and self.passages[idx].key not in keys:
                    continue
                dl = self._len[idx]
                scores[idx] += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avg))
        top = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:k]
        return [self.passages[i] for i, _ in top]

    def grep(self, pattern: str, *, regex: bool = False, limit: int = GREP_PER_TERM,
             keys: set[str] | None = None) -> list[Passage]:
        """字面（默认，忽略大小写）或正则匹配；按收入顺序返回前 limit 条。"""
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        except re.error:
            return []
        out = []
        for p in self.passages:
            if (keys is None or p.key in keys) and rx.search(p.text):
                out.append(p)
                if len(out) == limit:
                    break
        return out

    def read(self, pid: str, *, before: int = 1, after: int = 1) -> list[Passage]:
        p = self.get(pid)
        if p is None:
            return []
        rows = self.by_key[p.key]
        lo, hi = max(0, p.ordinal - before), min(len(rows), p.ordinal + after + 1)
        return [self.passages[i] for i in rows[lo:hi]]

    async def find(self, query: str, *, k: int = 3, keys: set[str] | None = None,
                   sem: asyncio.Semaphore | None = None, use_entities: bool = True,
                   prefilter: int = PREFILTER) -> list[Passage]:
        """BM25 预筛 + 专名/数字精确匹配补充 → 交叉编码器重排，取前 k 句（重排不可用时保留预筛顺序）。"""
        found, _ = await self.find_scored(query, k=k, keys=keys, sem=sem, use_entities=use_entities,
                                          prefilter=prefilter)
        return found

    async def find_scored(self, query: str, *, k: int = 3, keys: set[str] | None = None,
                          sem: asyncio.Semaphore | None = None, use_entities: bool = True,
                          prefilter: int = PREFILTER) -> tuple[list[Passage], list[float]]:
        """同 find，另返回对应的重排分（重排不可用时为空列表）；分数只用于排序与诊断。"""
        cands = self.bm25(query, prefilter, keys)
        if use_entities:
            seen = {p.pid for p in cands}
            for term in entities(query):
                for p in self.grep(term, keys=keys):
                    if p.pid not in seen:
                        seen.add(p.pid)
                        cands.append(p)
        if len(cands) <= 1:
            return cands[:k], []
        docs = [f"{p.title}: {p.text}" for p in cands]
        if sem is None:
            scores = await rerank_scores(query, docs)
        else:
            async with sem:
                scores = await rerank_scores(query, docs)
        if scores is None:
            return cands[:k], []
        order = sorted(range(len(cands)), key=lambda i: -scores[i])[:k]
        return [cands[i] for i in order], [scores[i] for i in order]


# ── 文献层：本地 arXiv 镜像 ─────────────────────────────────────────────────────

def _mirror_paper(row: dict, provenance: str) -> dict | None:
    from ...models.recall import RecallSource, build_recall_candidate
    from .explorer import _dedupe_candidates

    cand = build_recall_candidate(
        title=row["title"], arxiv_id=row["arxiv_id"], doi=row.get("doi"), year=row.get("year"),
        authors=row.get("authors"), abstract=row.get("abstract"),
        pdf_url=f"https://arxiv.org/pdf/{row['arxiv_id']}", citation_count=0,
        source=RecallSource.SEARCH, provenance=["provider:arxiv", "index:local_mirror", provenance])
    rows = _dedupe_candidates([cand]) if cand else []
    return rows[0] if rows else None


_NAME = re.compile(r"^(?=.*[A-Z].*[A-Z]|.*\d)[A-Za-z][A-Za-z0-9-]{3,}$")   # 系统名：TypeEvalPy、Type4Py、BERT


def _same_title(a: str, b: str) -> bool:
    """模型给出的题名与镜像题名是否指同一篇（身份核对）：
    完全一致；或冒号前是同一个有辨识度的系统名；或较短题名是较长题名的开头（≥4 词，常见于省略副标题）；
    或按词序高度相似。"""
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    head_a, head_b = (str(t).split(":", 1)[0].strip() for t in (a, b))
    if title_key(head_a) == title_key(head_b) and (_NAME.match(head_a) or len(head_a.split()) >= 3):
        return True
    sa, sb = ka.split(), kb.split()
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) >= 4 and long_[:len(short)] == short:
        return True
    return SequenceMatcher(None, sa, sb).ratio() >= TITLE_MATCH_RATIO   # 按词序比较：词相同但次序不同是别的论文


async def mirror_lookup_titles(titles: list[str], mirror) -> list[dict]:
    """按模型给出的题名在镜像中找同一篇论文（找不到就不收，不用相近论文顶替）。"""
    def lookup() -> list[dict]:
        out = []
        exact = mirror.match([(None, t) for t in titles])     # match 自带锁
        for title, row in zip(titles, exact):
            if row is None:
                with mirror._lock:
                    near = mirror.lexical(title, 5)
                row = next((c for c in near if _same_title(title, c.get("title") or "")
                            and (c.get("abstract") or "").strip()), None)
            paper = _mirror_paper(row, "method:title_lookup") if row else None
            if paper:
                paper["requested_title"] = title
                out.append(paper)
        return out

    return await asyncio.to_thread(lookup)


async def mirror_search(queries: list[str], mirror, *, per_query: int = 4) -> list[dict]:
    """检索式 → 镜像字面 + 向量两路提名，每条检索式取前 per_query 篇（两路交替）。"""
    from .explorer import _dedupe_candidates

    search_one = mirror.as_search_one()
    out, seen = [], set()
    for q in queries:
        try:
            cands = await search_one("arxiv", q, per_query, "relevance")
        except Exception as exc:  # noqa: BLE001 —— 单条检索式失败跳过
            logger.warning("mirror search failed for %r: %s", q, exc)
            continue
        channels = defaultdict(list)
        for c in cands:
            channel = next((x for x in (getattr(c, "provenance", None) or []) if x.startswith("channel:")), "")
            channels[channel].append(c)
        ranked = [c for group in zip(*channels.values()) for c in group] if len(channels) > 1 else list(cands)
        ranked += [c for c in cands if c not in ranked]
        taken = 0
        for p in _dedupe_candidates(ranked):
            if p["key"] in seen or not (p.get("abstract") or "").strip():
                continue
            seen.add(p["key"])
            out.append({**p, "source_query": f"answer_search:{q}"})
            taken += 1
            if taken == per_query:
                break
    return out


async def fetch_arxiv_full_text(arxiv_id: str, *, timeout: float = 25.0) -> str:
    """按 arXiv 编号下载官方 PDF 并抽取正文（只用 arXiv 开放全文；失败返回空串）。"""
    import os

    from ..pdf_parser import extract_pdf_content
    from .pdf_downloader import download_pdf_detailed

    path = None
    try:
        result = await asyncio.wait_for(download_pdf_detailed(f"https://arxiv.org/pdf/{arxiv_id}", timeout=15),
                                        timeout=timeout)
        if result.status != "available" or not result.path:
            return ""
        path = result.path
        content = await asyncio.to_thread(extract_pdf_content, path)
        return (content.full_text or "")[:100000]
    except Exception as exc:  # noqa: BLE001 —— 全文是增益，失败只记日志
        logger.warning("arXiv full text fetch failed for %s: %s", arxiv_id, exc)
        return ""
    finally:
        if path and os.path.isfile(path):
            os.remove(path)

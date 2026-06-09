"""GROBID 引用解析后端

通过 GROBID 服务把 PDF 解析为结构化 TEI，再抽取每条参考文献的
标题 / arXiv ID / DOI，以及正文内被引用次数（重要性代理）。

相比正则解析器（ref_parser.py），GROBID 的优势：
  - 兼容任意引用格式（数字 [N]、author-year 等），不依赖论文是否印 ID
  - 直接给出结构化标题，无需 LLM 提取，下载更准确可控
  - 正文内 <ref type="bibr"> 链接可精确统计引用频次

GROBID 是独立 Java 服务，标准跑法（Docker）：
    docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf
健康检查： GET /api/isalive

输出 dict 与 ref_parser.extract_references() 同构：
    {index, raw_text, title, arxiv_id, doi, freq}
另外多带一个 title 字段（正则解析器没有），expand_arxiv 会优先使用它。
"""
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_MAX_CONTEXTS_PER_REF = 20

# TEI 命名空间
_TEI = {"tei": "http://www.tei-c.org/ns/1.0"}
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# 从 "arXiv:2303.08774v2" / "abs/2303.08774" 等抽取纯 ID
_ARXIV_NUM_RE = re.compile(r"(\d{4}\.\d{4,5})")


async def is_grobid_alive(grobid_url: str, timeout: float = 5.0) -> bool:
    """探测 GROBID 服务是否可用。"""
    url = grobid_url.rstrip("/") + "/api/isalive"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return resp.status_code == 200 and "true" in resp.text.lower()
    except Exception as e:
        logger.debug(f"GROBID isalive check failed ({grobid_url}): {e}")
        return False


async def parse_references(
    pdf_path: str, grobid_url: str, timeout: float = 120.0
) -> tuple[list[dict], list[dict]]:
    """调 GROBID processFulltextDocument，返回 (结构化引用列表, 引用上下文列表)。

    引用上下文：每条记录包含 ref_id（如 "b12"）、section（章节名）、context（上下文文本）。
    GROBID 不可达或解析失败时抛异常（由调用方决定是否回退正则）。
    回退正则时上下文为空列表。
    """
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    url = grobid_url.rstrip("/") + "/api/processFulltextDocument"
    files = {"input": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")}
    data = {
        "includeRawCitations": "1",
        "consolidateCitations": "0",
        "consolidateHeader": "0",
        "segmentSentences": "1",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data)
    resp.raise_for_status()
    # 同一份 TEI 只解析一遍，refs 与 contexts 共用 root
    root = ET.fromstring(resp.text)
    refs = _parse_tei(root)
    contexts = _parse_citation_contexts(root)
    return refs, contexts


async def parse_header(
    pdf_path: str, grobid_url: str, timeout: float = 60.0
) -> Optional[dict]:
    """调 GROBID processHeaderDocument，抽取论文头部元数据。

    返回 {title, authors, year, venue}（缺失字段为 None / ""）；出错抛异常，
    由调用方决定是否回退启发式提取。
    """
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    url = grobid_url.rstrip("/") + "/api/processHeaderDocument"
    files = {"input": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")}
    data = {"consolidateHeader": "0"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data)
    resp.raise_for_status()
    return _parse_header_tei(resp.text)


def _parse_header_tei(xml_text: str) -> dict:
    """解析 processHeaderDocument 的 TEI，取 title / authors / year / venue。"""
    root = ET.fromstring(xml_text)

    # 标题：fileDesc/titleStmt/title 优先，回退 analytic/title
    title_el = root.find(".//tei:fileDesc/tei:titleStmt/tei:title", _TEI)
    if title_el is None or not (title_el.text or "").strip():
        title_el = root.find(".//tei:sourceDesc//tei:analytic/tei:title", _TEI)
    title = (title_el.text or "").strip() if title_el is not None else ""

    # 作者：analytic/author/persName → "Forename Surname"
    authors: list[str] = []
    for pers in root.iterfind(".//tei:sourceDesc//tei:author/tei:persName", _TEI):
        fore = " ".join((e.text or "").strip() for e in pers.iterfind("tei:forename", _TEI) if (e.text or "").strip())
        sur_el = pers.find("tei:surname", _TEI)
        sur = (sur_el.text or "").strip() if sur_el is not None else ""
        name = (fore + " " + sur).strip()
        if name:
            authors.append(name)

    # 年份：任意 date[@when] 里抽 4 位年
    year = None
    for date_el in root.iterfind(".//tei:sourceDesc//tei:date", _TEI):
        when = date_el.get("when") or (date_el.text or "")
        m = re.search(r"(19|20)\d{2}", when)
        if m:
            year = int(m.group(0))
            break

    # 刊物：monogr/title（会议/期刊名）
    venue_el = root.find(".//tei:sourceDesc//tei:monogr/tei:title", _TEI)
    venue = (venue_el.text or "").strip() if venue_el is not None else ""

    # arXiv ID：header 里偶尔带 <idno type="arXiv">，用于后续查学科分类
    arxiv_id = None
    for idno in root.iterfind(".//tei:sourceDesc//tei:idno", _TEI):
        if (idno.get("type") or "").lower() == "arxiv":
            m = _ARXIV_NUM_RE.search(idno.text or "")
            if m:
                arxiv_id = m.group(1)
                break

    return {
        "title": title or None,
        "authors": ", ".join(authors) or None,
        "year": year,
        "venue": venue or None,
        "arxiv_id": arxiv_id,
    }


def _parse_tei(root) -> list[dict]:
    """解析 GROBID TEI（已解析的 root），抽取引用 + 正文引用频次。"""

    # ① 正文内引用频次：<ref type="bibr" target="#b12"> → 计数 b12
    freq: dict[str, int] = {}
    for ref in root.iterfind(".//tei:ref[@type='bibr']", _TEI):
        target = ref.get("target") or ""
        if target.startswith("#"):
            key = target[1:]
            freq[key] = freq.get(key, 0) + 1

    # ② 参考文献列表：<listBibl><biblStruct xml:id="b0">...
    results: list[dict] = []
    for bibl in root.iterfind(".//tei:listBibl/tei:biblStruct", _TEI):
        xid = bibl.get(_XML_ID) or ""

        # 标题：优先 analytic（论文标题），回退 monogr（书/会议名）
        title_el = bibl.find(".//tei:analytic/tei:title", _TEI)
        if title_el is None or not (title_el.text or "").strip():
            title_el = bibl.find(".//tei:monogr/tei:title", _TEI)
        title = (title_el.text or "").strip() if title_el is not None else ""

        # ID：遍历所有 <idno>，按 type 取 DOI / arXiv
        doi: Optional[str] = None
        arxiv_id: Optional[str] = None
        for idno in bibl.iterfind(".//tei:idno", _TEI):
            itype = (idno.get("type") or "").lower()
            val = (idno.text or "").strip()
            if not val:
                continue
            if itype == "doi" and not doi:
                doi = val
            elif itype == "arxiv" and not arxiv_id:
                m = _ARXIV_NUM_RE.search(val)
                if m:
                    arxiv_id = m.group(1)

        # 原始引用文本（includeRawCitations）
        raw_el = bibl.find(".//tei:note[@type='raw_reference']", _TEI)
        raw = (raw_el.text or "").strip() if raw_el is not None else ""

        # 兜底：raw 里再正则捞一次 arXiv（GROBID 有时不填 idno）
        if not arxiv_id and raw:
            m = re.search(
                r"(?:arxiv[:\s]*|abs/)(\d{4}\.\d{4,5})", raw, re.IGNORECASE
            )
            if m:
                arxiv_id = m.group(1)

        # index：从 xml:id "b12" 取数字，仅用于排序时的稳定 tiebreak
        idx = 0
        m = re.search(r"\d+", xid)
        if m:
            idx = int(m.group(0))

        results.append({
            "index": idx,
            "raw_text": (raw or title)[:500],
            "title": title or None,
            "arxiv_id": arxiv_id,
            "doi": doi,
            "freq": freq.get(xid, 0),
        })

    return results


def _get_all_text(el) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_get_all_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


# 引用意图启发式：按优先级匹配上下文文本里的关键词，零额外 LLM 开销。
# 返回稳定 code，中文标签与配色在前端 EdgeDetail 映射。
_INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("baseline", (
        "baseline", "compared to", "compared with", "comparison", "compare with",
        "outperform", "outperforms", "outperformed", " vs ", "versus",
        "state-of-the-art", "state of the art", "sota", "better than", "worse than",
        "we compare", "against",
    )),
    ("reuse", (
        "based on", "build upon", "built upon", "building upon", "backbone",
        "we adopt", "we use", "we follow", "following", "we employ", "we utilize",
        "leverage", "pretrained", "pre-trained", "initialized from", "fine-tune",
        "fine-tuned", "built on top of", "we extend", "extends",
    )),
    ("inspiration", (
        "inspired by", "motivated by", "similar to", "analogous to", "draws on",
        "in the spirit of", "akin to",
    )),
]


def _classify_intent(text: str) -> str:
    """根据上下文文本判定引用意图（对比基线 / 方法复用 / 启发借鉴 / 背景引用）。"""
    low = (text or "").lower()
    for intent, kws in _INTENT_PATTERNS:
        if any(k in low for k in kws):
            return intent
    return "background"


def _parse_citation_contexts(root) -> list[dict]:
    """从 TEI body（已解析的 root）中提取每条引用标记所在的章节名、上下文文本与意图。

    返回 [{ref_id, section, context, intent}]，ref_id 如 "b12"。
    优先取 <s>（句子）级上下文；无 <s> 时回退到 <p>（段落）。
    """
    body = root.find(".//tei:body", _TEI)
    if body is None:
        return []

    contexts: list[dict] = []
    divs = body.findall(".//tei:div", _TEI)
    if not divs:
        divs = [body]

    for div in divs:
        head_el = div.find("tei:head", _TEI)
        section = (head_el.text or "").strip() if head_el is not None else ""

        sentences = div.findall(".//tei:s", _TEI)
        if sentences:
            for s in sentences:
                refs_in_s = s.findall(".//tei:ref[@type='bibr']", _TEI)
                if not refs_in_s:
                    continue
                sent_text = _get_all_text(s).strip()
                if not sent_text:
                    continue
                for ref in refs_in_s:
                    target = ref.get("target") or ""
                    if not target.startswith("#"):
                        continue
                    contexts.append({
                        "ref_id": target[1:],
                        "section": section,
                        "context": sent_text[:500],
                        "intent": _classify_intent(sent_text),
                    })
        else:
            for p in div.findall(".//tei:p", _TEI):
                refs_in_p = p.findall(".//tei:ref[@type='bibr']", _TEI)
                if not refs_in_p:
                    continue
                p_text = _get_all_text(p).strip()
                if not p_text:
                    continue
                for ref in refs_in_p:
                    target = ref.get("target") or ""
                    if not target.startswith("#"):
                        continue
                    contexts.append({
                        "ref_id": target[1:],
                        "section": section,
                        "context": p_text[:500],
                        "intent": _classify_intent(p_text),
                    })

    per_ref_count: dict[str, int] = {}
    capped: list[dict] = []
    for ctx in contexts:
        n = per_ref_count.get(ctx["ref_id"], 0)
        if n >= _MAX_CONTEXTS_PER_REF:
            continue
        per_ref_count[ctx["ref_id"]] = n + 1
        capped.append(ctx)

    return capped

"""GROBID 引用解析后端

通过 GROBID 服务把 PDF 解析为结构化 TEI，再抽取每条参考文献的
标题 / arXiv ID / DOI，以及正文内被引用次数（重要性代理）。

相比正则解析器（ref_parser.py），GROBID 的优势：
  - 兼容任意引用格式（数字 [N]、author-year 等），不依赖论文是否印 ID
  - 直接给出结构化标题，无需 LLM 提取，下载更准确可控
  - 正文内 <ref type="bibr"> 链接可精确统计引用频次

GROBID 是独立 Java 服务，标准跑法（Docker）：
    docker run --rm -p 8070:8070 grobid/grobid:0.8.1
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
) -> list[dict]:
    """调 GROBID processFulltextDocument，返回结构化引用列表。

    出错抛异常（由调用方决定是否回退正则）。
    """
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    url = grobid_url.rstrip("/") + "/api/processFulltextDocument"
    files = {"input": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")}
    data = {
        # 保留原始引用文本，便于日志与回退
        "includeRawCitations": "1",
        # 不做外部 consolidation（避免额外网络依赖），ID 抽取靠 TEI 自身字段
        "consolidateCitations": "0",
        "consolidateHeader": "0",
        "segmentSentences": "0",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data)
    resp.raise_for_status()
    return _parse_tei(resp.text)


def _parse_tei(xml_text: str) -> list[dict]:
    """解析 GROBID TEI XML，抽取引用 + 正文引用频次。"""
    root = ET.fromstring(xml_text)

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

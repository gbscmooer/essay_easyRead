"""
学术 PDF 引用块解析器

解析 [N] 格式的引用列表（CV/ML 论文常见格式）。
每个块提取：引用编号、原始文本、arXiv ID（有则提取）、DOI（有则提取）。
"""
import re
from typing import Optional

# 匹配 [1]~[999]，支持行首有空白，内容到下一个 [N] 或文档末尾
_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*\[(\d{1,3})\]\s+(.+?)(?=\n\s*\[\d{1,3}\]|\Z)",
    re.DOTALL,
)

# arXiv ID 多种格式：
#   arXiv:2303.08774    arXiv: 2303.08774v2
#   arxiv.org/abs/2303.08774
#   CoRR, abs/2303.08774
_ARXIV_RE = re.compile(
    r"(?:\barXiv[:\s]+|\barxiv\.org/abs/|\babs/)(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)

# DOI
_DOI_RE = re.compile(
    r"(?:doi\.org/|DOI[:\s]+)([^\s,\]\n]+)",
    re.IGNORECASE,
)

# References/Bibliography 段落标题
_REF_HEADING_RE = re.compile(
    r"\n\s*(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY)\s*\n",
)

# 正文内引用标记：[12] / [12, 15] / [12-15] / [3–7]（含 en/em-dash）
# 内容只允许数字、逗号、空白、连字符，避免误匹配 [Fig 1] 等
_CITE_GROUP_RE = re.compile(r"\[([\d,\s–—\-]+)\]")
_CITE_RANGE_RE = re.compile(r"^(\d+)\s*[–—\-]\s*(\d+)$")


def _count_intext_citations(body: str) -> dict[int, int]:
    """统计正文中每个引用编号 [N] 被引用的次数。

    重要性代理：被正文反复引用的论文比只出现一次的更核心。
    支持合并引用 [1, 2, 5] 与区间引用 [3-7]（区间内每个编号各计一次）。
    """
    freq: dict[int, int] = {}
    for m in _CITE_GROUP_RE.finditer(body):
        for token in m.group(1).split(","):
            token = token.strip()
            if not token:
                continue
            rng = _CITE_RANGE_RE.match(token)
            if rng:
                a, b = int(rng.group(1)), int(rng.group(2))
                # 防御异常区间（倒序或过大）
                if a <= b and b - a < 100:
                    for n in range(a, b + 1):
                        freq[n] = freq.get(n, 0) + 1
            elif token.isdigit():
                n = int(token)
                freq[n] = freq.get(n, 0) + 1
    return freq


def _clean_doi(value: str) -> str:
    value = value.rstrip(".,;")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1].rstrip(".,;")
    return value


def extract_references(text: str) -> list[dict]:
    """从论文全文解析 [N] 引用块列表。

    找不到 References 标题时返回 []（避免把正文行内引用误当引用块）。

    返回字段：
      index    — 引用编号（int）
      raw_text — 清洗后的原始引用文本（最多 500 字符）
      arxiv_id — 若正则匹配到 arXiv ID 则填写，否则为 None
      doi      — 若匹配到 DOI 则填写，否则为 None
      freq     — 该编号在正文中被引用的次数（重要性代理）
    """
    m = _REF_HEADING_RE.search(text)
    if m is None:
        return []

    body = text[:m.start()]        # References 之前的正文，用于统计引用频次
    section = text[m.start():]     # References 段落本身，用于解析引用块
    freq_map = _count_intext_citations(body)

    results: list[dict] = []
    for bm in _BLOCK_RE.finditer(section):
        raw = re.sub(r"\s+", " ", bm.group(2).strip())  # 折叠空白
        idx = int(bm.group(1))
        ax_m = _ARXIV_RE.search(raw)
        doi_m = _DOI_RE.search(raw)
        results.append({
            "index": idx,
            "raw_text": raw[:500],
            "arxiv_id": ax_m.group(1).split("v")[0] if ax_m else None,
            "doi": _clean_doi(doi_m.group(1)) if doi_m else None,
            "freq": freq_map.get(idx, 0),
        })
    return results

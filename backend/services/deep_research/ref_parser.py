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


def _find_ref_section(text: str) -> str:
    """尝试定位 References 段落，找到则返回该段落之后的文本。"""
    m = _REF_HEADING_RE.search(text)
    return text[m.start():] if m else text


def extract_references(text: str) -> list[dict]:
    """从论文全文解析 [N] 引用块列表。

    返回字段：
      index    — 引用编号（int）
      raw_text — 清洗后的原始引用文本（最多 500 字符）
      arxiv_id — 若正则匹配到 arXiv ID 则填写，否则为 None
      doi      — 若匹配到 DOI 则填写，否则为 None
    """
    section = _find_ref_section(text)
    results: list[dict] = []
    for m in _BLOCK_RE.finditer(section):
        raw = re.sub(r"\s+", " ", m.group(2).strip())  # 折叠空白
        ax_m = _ARXIV_RE.search(raw)
        doi_m = _DOI_RE.search(raw)
        results.append({
            "index": int(m.group(1)),
            "raw_text": raw[:500],
            "arxiv_id": ax_m.group(1).split("v")[0] if ax_m else None,
            "doi": doi_m.group(1) if doi_m else None,
        })
    return results

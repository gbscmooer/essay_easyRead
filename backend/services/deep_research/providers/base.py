"""
Research Provider 接口

每个 provider 实现两个方法：
- find_paper_id: 通过 DOI/arXivId/标题 解析提供方内部的论文 ID
- get_paper_references: 返回引用论文列表（含开放获取 PDF 链接）

引用列表的标准化字段：
{
  "ext_id": str,          # 提供方内部 ID（用于去重）
  "title": str,
  "year": int | None,
  "citation_count": int,
  "doi": str | None,
  "arxiv_id": str | None,
  "pdf_url": str | None,  # 可下载的 PDF 链接（None 则跳过）
}
"""

from typing import Optional, Protocol


class ResearchProvider(Protocol):
    name: str

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        ...

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        ...

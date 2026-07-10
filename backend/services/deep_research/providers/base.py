"""
Research Provider 接口

每个 provider 实现：
- find_paper_id: 通过 DOI/arXivId/标题 解析提供方内部的论文 ID
- get_paper_references: 返回引用论文列表（含开放获取 PDF 链接，legacy dict 格式）
- get_paper_citations: 正向引文 → RecallCandidate
- get_recommendations: 推荐论文 → RecallCandidate
- search_works: 关键词检索 → RecallCandidate

引用列表 legacy 标准化字段：
{
  "ext_id": str,
  "title": str,
  "year": int | None,
  "citation_count": int,
  "doi": str | None,
  "arxiv_id": str | None,
  "pdf_url": str | None,
}
"""

from typing import Optional, Protocol

from ....models.recall import RecallCandidate


class ResearchProvider(Protocol):
    name: str

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        ...

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        ...

    async def get_paper_citations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        ...

    async def get_recommendations(self, paper_id: str, limit: int = 50) -> list[RecallCandidate]:
        ...

    async def search_works(self, query: str, limit: int = 20) -> list[RecallCandidate]:
        ...

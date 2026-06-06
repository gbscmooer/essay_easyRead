"""
Local Provider — 关闭联网扩展

返回空结果，使 expand 阶段直接跳过下载和浅分析，只用工作区已有论文构建图谱。
"""

from typing import Optional


class LocalProvider:
    name = "local"

    async def find_paper_id(self, title: str, doi: str = "", arxiv_id: str = "") -> Optional[str]:
        return None

    async def get_paper_references(self, paper_id: str, limit: int = 50) -> list[dict]:
        return []

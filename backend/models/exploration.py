"""
领域探索数据模型（问句驱动的免读领域认知，见 GOAL.md）

ExplorationRun: 一次「领域探索」任务的执行记录与完整产物。
与 ResearchRun（工作区种子扩展）互补：探索从自然语言问句冷启动，不需要任何种子论文。

result 列的完整结构（explorer.py 产出）：
{
  "decompose":  {"intent", "en_queries", "focus_terms", "fallback": bool},
  "papers":     [{key, title, year, citation_count, citation_velocity, surge,
                  cluster, cluster_label, doi, arxiv_id, authors, abstract, source_query}],
  "metrics":    {"total", "by_year", "recent_share", "year_span", "top_cited", "rising",
                  "clusters", "modularity"},
  "graph":      {"nodes", "edges", "stats"}   # ResearchGraph 形状，前端总览视图直接复用
  "synthesis":  ExplorationSynthesis | None   # LLM 跨文献综合（无 Key 为 None）
}
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON

from .research import ParadigmInfo


# 查新结论的枚举口径：结论只允许落在这些档位上，防止 LLM 自由发挥
NOVELTY_STATUSES = (
    "done_extensively",  # 已被充分实现：多个成熟系统/方法，SOTA 明确
    "done_partial",      # 部分实现：有相关工作但未完全覆盖问题的全部条件
    "emerging",          # 新兴：近 1~2 年开始出现，尚无公认范式
    "gap",               # 空白：检索未发现直接实现（注意与「检索失败」区分）
    "unclear",           # 证据不足以判定
)


class ExplorationVerdict(SQLModel):
    """查新结论：这个 idea 有没有人做。evidence_keys 指向 result.papers 的 key。"""

    status: str = "unclear"          # NOVELTY_STATUSES 之一
    confidence: float = 0.0          # 0~1，证据充分度
    explanation: str = ""            # 2~4 句判定依据（必须引用材料）
    evidence_keys: list[str] = []


class ExplorationSynthesis(SQLModel):
    """LLM 跨文献综合产物（只基于召回材料，铁律见 GOAL.md）。"""

    verdict: Optional[ExplorationVerdict] = None
    maturity: str = ""               # 实现到什么程度：代表系统/SOTA 水平/基准一句话
    landscape_summary: str = ""      # 领域现状一段话
    hotspots: list[str] = []         # 近期热点与突破
    gaps: list[str] = []             # 尚未解决的空白
    follow_ups: list[str] = []       # 建议的后续检索式/相邻问题
    # 子方向流派（cluster_id 与 graph 节点 meta.cluster 对齐，前端范式卡片复用）
    paradigms: list[ParadigmInfo] = []
    markdown_report: str = ""        # 完整 Markdown 报告
    partial: bool = False
    stats: dict = {}


class ExplorationRun(SQLModel, table=True):
    """一次领域探索任务的执行记录（问句维度，不挂工作区）"""

    __tablename__ = "exploration_runs"

    id: Optional[int] = Field(default=None, primary_key=True)

    question: str                    # 学者的自然语言问句
    depth: str = "standard"          # quick | standard | deep
    status: str = "pending"          # pending | running | done | failed
    current_stage: Optional[str] = None  # decompose | recall | enrich | metrics | synthesize | persist
    error_message: Optional[str] = None

    # 实际使用的检索源（scopus/openalex/…，透明度用）
    provider: str = ""

    decompose: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

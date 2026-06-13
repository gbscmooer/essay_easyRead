"""
PaperFeatures — 算法与系统之间的纯数据边界

这是 graphkit 唯一的输入类型。它是一个普通 dataclass，**不依赖 SQLModel / DB**，
可由 JSON 直接构造（见 from_dict / to_dict），便于在 test_g/ 用 fixtures 复现。

字段对应论文解读结果（AnalysisResult）里 facet 计算真正需要的部分，外加引用关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class PaperFeatures:
    id: int
    title: str = ""
    year: int | None = None
    is_analyzed: bool = False  # 是否有 LLM 解读结果（决定能否参与语义 facet）

    # —— facet 数据（来自 AnalysisResult）——
    purpose_text: str = ""              # problem + one_sentence + key_contributions
    method_text: str = ""               # method.overview + pipeline
    datasets: list[str] = field(default_factory=list)
    baselines: list[str] = field(default_factory=list)

    # —— 题录信息（所有论文都有，叶子论文也有；驱动 topic 向量的兜底文本）——
    abstract: str = ""                  # 摘要/题录（联网扩展的叶子常只有 title+abstract）

    # —— 引用结构 ——
    cites: set[int] = field(default_factory=set)  # 本文引用到的 paper id 集合（出边）

    # —— 可选：预计算的论文级向量（如 bge_centroid 的 ChromaDB chunk 质心）——
    doc_vector: list[float] | None = None

    def topic_text(self) -> str:
        """论文的"主题代表文本"——用于在没有 doc_vector 时回退做 embedding。

        已解读：purpose + method（最能区分研究方向）；未解读叶子：title + abstract。
        始终带上 title，标题在 CV/ML 里信息量很高（"AnomalyDINO" 等已自带方向）。
        """
        parts = [self.title]
        if self.is_analyzed and (self.purpose_text or self.method_text):
            parts += [self.purpose_text, self.method_text]
        else:
            parts.append(self.abstract)
        return " ".join(p for p in parts if p and p.strip()).strip()

    # ---- 序列化（JSON 友好；set 转 list）----
    def to_dict(self) -> dict:
        d = asdict(self)
        d["cites"] = sorted(self.cites)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PaperFeatures":
        return cls(
            id=int(d["id"]),
            title=d.get("title", ""),
            year=d.get("year"),
            is_analyzed=bool(d.get("is_analyzed", False)),
            purpose_text=d.get("purpose_text", ""),
            method_text=d.get("method_text", ""),
            datasets=list(d.get("datasets", []) or []),
            baselines=list(d.get("baselines", []) or []),
            abstract=d.get("abstract", ""),
            cites=set(int(x) for x in (d.get("cites", []) or [])),
            doc_vector=d.get("doc_vector"),
        )

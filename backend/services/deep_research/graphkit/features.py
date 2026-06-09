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

    # —— 引用结构 ——
    cites: set[int] = field(default_factory=set)  # 本文引用到的 paper id 集合（出边）

    # —— 可选：预计算的论文级向量（如 bge_centroid 的 doc 向量）——
    doc_vector: list[float] | None = None

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
            cites=set(int(x) for x in (d.get("cites", []) or [])),
            doc_vector=d.get("doc_vector"),
        )

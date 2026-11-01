"""循环参数：按研究深度给出固定的小预算（PaSa 推理时用 5 条检索式 × 10 篇、展开 2 层、每层 20 篇）。

所有数字都在这里，评测时用 options["loop"] 覆盖任意一项做对照，不在业务代码里散落常量。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace


@dataclass(frozen=True)
class LoopConfig:
    # Crawler：检索
    queries: int = 6               # 初始检索式条数（一次模型调用生成）
    per_query: int = 12            # 每条检索式每个源取几篇
    sources: tuple[str, ...] = ("arxiv", "scopus", "openalex")
    # Crawler：展开
    expand_layers: int = 2         # 展开层数（0 = 只检索不展开）
    expand_papers: int = 10        # 每层展开几篇（按 Selector 分数取最高的）
    sections_per_paper: int = 3    # 每篇最多追几个章节的引用
    refs_per_paper: int = 30       # 每篇最多解析多少条引用（章节内按出现顺序）
    reference_fallback: int = 20   # 拿不到 arXiv HTML 时，按题名与问题的词重合从参考文献列表取几条
    refine_rounds: int = 1         # 第一层展开后按已选论文补检的轮数（每轮一次模型调用）
    refine_queries: int = 4
    # Selector
    judge_batch: int = 12          # 每次模型调用判定几篇
    rerank_floor: float = 0.02     # 交叉编码器分低于此值直接判 0（不花模型调用）；None/0 关闭
    rerank_keep: int = 400         # 每批候选最多进入模型判定的篇数（按交叉编码器分取前）
    min_label: int = 1             # 入选门槛：1 = 背景相关即入选，2 = 只收直接相关
    # 作答
    answer_papers: int = 40        # 交给作答的已选论文上限（按分数）
    # 时间
    time_cap_seconds: float = 1500.0   # 研究侧（检索 + 展开）总时长上限，到点停止扩展，直接作答

    def merged(self, overrides: dict | None) -> "LoopConfig":
        if not overrides:
            return self
        allowed = {f.name for f in fields(self)}
        clean = {k: (tuple(v) if k == "sources" else v) for k, v in overrides.items() if k in allowed}
        return replace(self, **clean)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sources"] = list(self.sources)
        return data


PRESETS: dict[str, LoopConfig] = {
    "lite": LoopConfig(queries=4, per_query=10, expand_layers=1, expand_papers=5, refine_rounds=0,
                       answer_papers=24, time_cap_seconds=600.0),
    "quick": LoopConfig(queries=5, per_query=10, expand_layers=1, expand_papers=8,
                        answer_papers=30, time_cap_seconds=900.0),
    "standard": LoopConfig(),
    "deep": LoopConfig(queries=8, per_query=15, expand_layers=2, expand_papers=20, refine_queries=6,
                       answer_papers=48, time_cap_seconds=1800.0),
}


def config_for(depth: str, overrides: dict | None = None) -> LoopConfig:
    return PRESETS.get(depth, PRESETS["standard"]).merged(overrides)


# 指针式作答的默认参数（来自旧版 RECOMMENDED，只保留经评测确认有效的那条路径）：
# drafts=2（E22：ResearchQA 50 题 +0.025，SE 0.008）、question_search=3（E17）、claim_search=2、
# fetch_full_text=6、以草稿为准逐句挂证据（attach）。其余实验开关已删除。
ANSWER_DEFAULTS = {"drafts": 2, "question_search": 3, "claim_search": 2, "fetch_full_text": 6, "max_attempts": 2}

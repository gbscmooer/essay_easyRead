"""单循环文献研究（PaSa 式 Crawler + Selector，加指针式作答）。

一次研究只有一个循环，模型只做三类窄决策，其余全部是确定性代码：

    Crawler.queries     问题 → 若干条互不重叠的英文检索式（一次调用）
    Selector.judge      每篇论文（题名 + 摘要）一个判定：2 直接相关 / 1 背景相关 / 0 无关
                        （先交叉编码器粗排，再按批让模型只输出判定串，不写理由）
    Crawler.sections    论文章节名 → 值得追引用的章节（一次调用，输出几个章节名）

循环（见 runner.run_research）：

    search(queries) → judge → 队列按分数排序
    for layer in range(expand_layers):
        取队列里分数最高、尚未展开的若干篇 → 读章节 → 追所选章节的引用 → judge → 入队
        （可选）按已选论文补一轮检索式 refine → search → judge
    已选论文 → 指针式作答（answer.write_answer）→ 计量/聚类/图谱（landscape）

旧实现（explorer 八阶段、research_loop v1–v20、综述报告栈）已整体删除，不保留兼容层。
"""

from .runtime import (  # noqa: F401
    cancel_pause_request, cancel_worker, drop_bus, get_bus, has_bus, has_live_worker,
    pause_pending, request_pause, start_worker,
)
from .runner import run_research  # noqa: F401

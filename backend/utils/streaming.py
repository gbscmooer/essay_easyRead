"""
流式响应工具

SSE (Server-Sent Events) 格式化和 Markdown 导出。
"""

from ..models.schemas import StreamEvent


def format_sse(event: StreamEvent) -> str:
    """
    将 StreamEvent 格式化为 SSE 文本。

    SSE 格式：
        event: {event.event}
        data: {json.dumps(event.dict())}

    Args:
        event: 流式事件

    Returns:
        str: 格式化后的 SSE 文本（包含换行符）

    实现要点：
        - event 行指定 SSE 事件类型
        - data 行是 JSON 序列化的完整事件
        - 事件之间用空行分隔
    """
    ...


def format_analysis_as_markdown(analysis: dict) -> str:
    """
    将解析结果格式化为 Markdown 文本。

    用于导出功能，生成可读性强的 Markdown 文件。

    Args:
        analysis: 完整的解析结果 dict（对应 AnalysisResponse 结构）

    Returns:
        str: 格式化后的 Markdown 文本

    输出格式示例：
        # {title}
        > {one_sentence}

        **会议**: {venue} | **年份**: {year}

        ## 研究问题
        {problem}

        ## 核心贡献
        - {contribution1}
        - {contribution2}

        ## 方法
        ### 总览
        {method.overview}

        ### Pipeline
        {method.pipeline}

        ### 关键模块
        | 模块 | 作用 | 创新点 |
        |------|------|--------|
        | ... | ... | ... |

        ## 实验
        ### 数据集
        - {dataset1}
        ...

        ### 主要结果
        {experiments.main_results}

        ### 消融实验
        {experiments.ablation}

        ## 代码映射
        | 概念 | 实现思路 |
        |------|----------|
        | ... | ... |

        ## 面试题
        **Q: {q}**
        A: {a}
        ...

        ## 局限性
        {limitations}

        ## 相关论文
        - {paper1}
        ...
    """
    ...

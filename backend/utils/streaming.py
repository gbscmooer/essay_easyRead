"""
流式响应工具

SSE (Server-Sent Events) 格式化和 Markdown 导出。
"""

import json
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
    return f"event: {event.event}\ndata: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"


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
    lines = []

    # 标题和一句话总结
    lines.append(f"# {analysis.get('title', 'Unknown')}")
    lines.append(f"> {analysis.get('one_sentence', '')}")
    lines.append("")
    lines.append(f"**会议**: {analysis.get('venue', 'Unknown')} | **年份**: {analysis.get('year', 'Unknown')}")
    lines.append("")

    # 研究问题
    lines.append("## 研究问题")
    lines.append(analysis.get('problem', ''))
    lines.append("")

    # 核心贡献
    lines.append("## 核心贡献")
    for contribution in analysis.get('key_contributions', []):
        lines.append(f"- {contribution}")
    lines.append("")

    # 论文大纲
    outline = analysis.get('outline', [])
    if outline:
        lines.append("## 论文大纲")
        for item in outline:
            page = f" (p.{item['page']})" if item.get('page') else ""
            lines.append(f"### {item.get('section', '')}{page}")
            lines.append(item.get('summary', ''))
            lines.append("")

    # 方法
    lines.append("## 方法")
    method = analysis.get('method', {})
    lines.append("### 总览")
    lines.append(method.get('overview', ''))
    lines.append("")
    lines.append("### Pipeline")
    lines.append(method.get('pipeline', ''))
    lines.append("")

    # 关键模块
    key_modules = method.get('key_modules', [])
    if key_modules:
        lines.append("### 关键模块")
        lines.append("| 模块 | 作用 | 创新点 |")
        lines.append("|------|------|--------|")
        for module in key_modules:
            lines.append(f"| {module.get('name', '')} | {module.get('role', '')} | {module.get('innovation', '')} |")
        lines.append("")

    # 实验
    lines.append("## 实验")
    experiments = analysis.get('experiments', {})

    # 数据集
    datasets = experiments.get('datasets', [])
    if datasets:
        lines.append("### 数据集")
        for dataset in datasets:
            lines.append(f"- {dataset}")
        lines.append("")

    # 基线方法
    baselines = experiments.get('baselines', [])
    if baselines:
        lines.append("### 基线方法")
        for baseline in baselines:
            lines.append(f"- {baseline}")
        lines.append("")

    # 实验结果表格
    result_table = experiments.get('result_table', [])
    if result_table:
        lines.append("### 实验结果")
        lines.append("| 方法 | 数据集 | 指标 | 数值 |")
        lines.append("|------|--------|------|------|")
        for row in result_table:
            lines.append(f"| {row.get('method', '')} | {row.get('dataset', '')} | {row.get('metric', '')} | {row.get('value', '')} |")
        lines.append("")

    # 主要结果
    lines.append("### 主要结果")
    lines.append(experiments.get('main_results', ''))
    lines.append("")

    # 消融实验
    lines.append("### 消融实验")
    lines.append(experiments.get('ablation', ''))
    lines.append("")

    # 代码映射
    code_mapping = analysis.get('code_mapping', [])
    if code_mapping:
        lines.append("## 代码映射")
        lines.append("| 概念 | 实现思路 |")
        lines.append("|------|----------|")
        for mapping in code_mapping:
            lines.append(f"| {mapping.get('concept', '')} | {mapping.get('likely_implementation', '')} |")
        lines.append("")

    # 面试题
    interview_qa = analysis.get('interview_qa', [])
    if interview_qa:
        lines.append("## 面试题")
        for qa in interview_qa:
            lines.append(f"**Q: {qa.get('q', '')}**")
            lines.append(f"A: {qa.get('a', '')}")
            lines.append("")

    # 局限性
    lines.append("## 局限性")
    lines.append(analysis.get('limitations', ''))
    lines.append("")

    # 相关论文
    related_papers = analysis.get('related_papers', [])
    if related_papers:
        lines.append("## 相关论文")
        for paper in related_papers:
            if isinstance(paper, dict):
                title = paper.get('title', '')
                authors = paper.get('authors', '')
                venue = paper.get('venue', '')
                year = paper.get('year', '')
                doi = paper.get('doi', '')
                url = paper.get('url', '')
                relevance = paper.get('relevance', '')
                lines.append(f"### {title}")
                if authors:
                    lines.append(f"- **作者**: {authors}")
                if venue or year:
                    lines.append(f"- **发表**: {venue} {year}")
                if doi:
                    lines.append(f"- **DOI**: {doi}")
                if url:
                    lines.append(f"- **链接**: {url}")
                if relevance:
                    lines.append(f"- **相关性**: {relevance}")
                lines.append("")
            else:
                lines.append(f"- {paper}")
        lines.append("")

    return "\n".join(lines)

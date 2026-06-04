"""
AI 分析服务

调用 MiMo API 对论文文本进行结构化解析。
使用 OpenAI 兼容格式，支持 streaming 输出。
"""

import json
import re
from typing import AsyncGenerator
from ..models.schemas import StreamEvent


# AI 系统提示词
# 同时输出分析结果和 Mermaid 流程图，避免两次 API 调用
SYSTEM_PROMPT = """你是一个专业的学术论文分析助手，擅长 CV/ML 领域的论文精读。
用户会提供一篇论文的全文内容，你需要进行深度分析并输出结构化的 JSON 结果。

请严格按照以下 JSON 格式输出，不要添加任何额外文字：

{
  "title": "论文标题",
  "venue": "发表会议/期刊（CVPR/ICCV/NeurIPS/ECCV/AAAI/ICML/ICLR 等，如无法判断则填 'Unknown'）",
  "year": 2024,
  "one_sentence": "一句话核心贡献，适合在面试中清晰表达",
  "problem": "这篇论文要解决什么问题？现有方法有什么局限性？",
  "key_contributions": ["贡献1", "贡献2", "贡献3"],
  "method": {
    "overview": "方法的总体描述",
    "pipeline": "Step 1: ...\nStep 2: ...\nStep 3: ...",
    "key_modules": [
      {"name": "模块名", "role": "作用", "innovation": "创新点"}
    ],
    "mermaid_chart": "graph TD\\n    A[输入] --> B[处理]\\n    B --> C[输出]"
  },
  "experiments": {
    "datasets": ["数据集1", "数据集2"],
    "baselines": ["方法1", "方法2"],
    "main_results": "主要实验结论",
    "ablation": "消融实验说明了什么",
    "result_table": [
      {"method": "本文方法", "dataset": "数据集名", "metric": "指标名", "value": "数值"},
      {"method": "Baseline A", "dataset": "数据集名", "metric": "指标名", "value": "数值"}
    ]
  },
  "code_mapping": [
    {"concept": "论文中的概念/公式", "likely_implementation": "PyTorch 风格的代码实现思路"}
  ],
  "interview_qa": [
    {"q": "面试问题", "a": "简洁清晰的回答"}
  ],
  "limitations": "局限性和未来工作方向",
  "related_papers": ["相关论文1", "相关论文2"]
}

要求：
1. one_sentence 要简洁有力，能让人在30秒内理解论文核心
2. method.pipeline 要用清晰的步骤描述完整流程
3. method.mermaid_chart 用 Mermaid graph TD 语法画出 pipeline 流程图，节点用英文简写
4. key_modules 列出 2-4 个最关键的模块
5. experiments.result_table 提取论文中 SOTA 对比表格的数值数据，每行一个方法在某数据集上的指标值
6. code_mapping 给出 3-5 个核心概念的代码映射
7. interview_qa 生成 5-8 个高频面试问题
8. 所有内容基于论文实际内容，不要编造"""


# 顶层字段名列表，用于检测 JSON 中哪些字段已完整
_TOP_LEVEL_FIELDS = [
    "title", "venue", "year", "one_sentence", "problem",
    "key_contributions", "method", "experiments",
    "code_mapping", "interview_qa", "limitations",
    "related_papers",
]


def _try_parse_partial_json(text: str) -> dict | None:
    """
    尝试从不完整的 JSON 字符串中提取已完成的顶层字段。

    策略：找到每个顶层字段的键，提取其值（处理嵌套括号），
    尝试构建一个只有已完成字段的完整 JSON 并 parse。

    Returns:
        已解析的字段 dict，或 None（如果无法解析任何字段）
    """
    result = {}
    for field in _TOP_LEVEL_FIELDS:
        # 匹配 "field_name": 后面的内容
        pattern = f'"{field}"\\s*:\\s*'
        match = re.search(pattern, text)
        if not match:
            continue

        start = match.end()
        remaining = text[start:]

        # 提取该字段的值（处理嵌套 {} 和 []）
        value_str = _extract_json_value(remaining)
        if value_str is None:
            continue

        try:
            result[field] = json.loads(value_str)
        except json.JSONDecodeError:
            continue

    return result if result else None


def _extract_json_value(text: str) -> str | None:
    """
    从 JSON 片段中提取第一个完整的值（字符串/数字/对象/数组）。

    处理嵌套括号和字符串中的转义引号。
    """
    if not text:
        return None

    # 跳过前导空白
    text = text.lstrip()

    if text[0] == '"':
        # 字符串值：找到未转义的结束引号
        i = 1
        while i < len(text):
            if text[i] == '\\':
                i += 2  # 跳过转义字符
                continue
            if text[i] == '"':
                return text[:i + 1]
            i += 1
        return None  # 字符串未闭合

    if text[0] in ('{', '['):
        # 对象或数组：匹配括号
        open_char = text[0]
        close_char = '}' if open_char == '{' else ']'
        depth = 0
        in_string = False
        i = 0
        while i < len(text):
            c = text[i]
            if c == '\\':
                i += 2
                continue
            if c == '"':
                in_string = not in_string
            elif not in_string:
                if c == open_char:
                    depth += 1
                elif c == close_char:
                    depth -= 1
                    if depth == 0:
                        return text[:i + 1]
            i += 1
        return None  # 括号未闭合

    # 数字/布尔/null：取到下一个逗号或 } 或 ]
    for i, c in enumerate(text):
        if c in (',', '}', ']', '\n'):
            return text[:i].rstrip()
    return text


async def analyze_paper_stream(
    pdf_text: str, api_key: str, base_url: str, model: str
) -> AsyncGenerator[StreamEvent, None]:
    """
    真流式调用 AI 分析论文。

    通过 OpenAI 兼容 API 以 streaming 模式接收响应，
    每收到一个 chunk 就尝试增量解析 JSON，已完成的字段立即 yield。

    Args:
        pdf_text: 论文全文文本
        api_key: MiMo API Key
        base_url: API 基础 URL
        model: 模型名称

    Yields:
        StreamEvent: 流式事件，包含进度状态和解析字段

    真流式实现：
        1. 使用 stream=True 调用 API
        2. 逐 chunk 拼接字符串
        3. 每次拼接后用 _try_parse_partial_json 增量解析
        4. 跟踪已 yield 的字段，新字段完成时立即 yield field 事件
        5. 全部字段完成后 yield done 事件
    """
    from openai import AsyncOpenAI

    yield StreamEvent(event="status", data={"message": "正在分析论文内容..."})

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": pdf_text},
            ],
            stream=True,
            temperature=0.1,
        )

        accumulated = ""
        yielded_fields: set[str] = set()

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                accumulated += chunk.choices[0].delta.content

                # 尝试增量解析
                parsed = _try_parse_partial_json(accumulated)
                if parsed:
                    for field_name in _TOP_LEVEL_FIELDS:
                        if field_name in parsed and field_name not in yielded_fields:
                            yielded_fields.add(field_name)
                            yield StreamEvent(
                                event="field",
                                field_name=field_name,
                                data={"value": parsed[field_name]},
                            )

        # 流结束，确保所有字段都被 yield
        final = json.loads(accumulated)  # 最终应该是完整 JSON
        for field_name in _TOP_LEVEL_FIELDS:
            if field_name in final and field_name not in yielded_fields:
                yielded_fields.add(field_name)
                yield StreamEvent(
                    event="field",
                    field_name=field_name,
                    data={"value": final[field_name]},
                )

        # mermaid_chart 嵌套在 method 中，单独提取
        mermaid = final.get("method", {}).get("mermaid_chart")
        if mermaid:
            yield StreamEvent(
                event="field",
                field_name="mermaid_chart",
                data={"value": mermaid},
            )

        yield StreamEvent(event="done", data={"message": "解析完成"})

    except Exception as e:
        yield StreamEvent(event="error", data={"message": f"解析失败: {str(e)}"})


async def generate_mermaid_chart(pipeline_text: str, api_key: str, base_url: str, model: str) -> str:
    """
    基于 pipeline 描述生成 Mermaid 流程图。

    调用 AI 将文字描述的 pipeline 转换为 Mermaid 语法的流程图。

    Args:
        pipeline_text: pipeline 的文字描述
        api_key: MiMo API Key
        base_url: API 基础 URL
        model: 模型名称

    Returns:
        str: Mermaid 语法的流程图字符串

    实现要点：
        1. 构造专门的 prompt，要求输出 Mermaid graph TD 语法
        2. 调用 API（非 streaming）
        3. 提取返回内容中的 Mermaid 代码块
        4. 验证语法基本正确性
    """
    ...

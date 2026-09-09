"""
AI 分析服务

调用统一 LLM 配置（OpenAI 兼容或 Anthropic 兼容）对论文文本进行结构化解析。
支持真流式输出和增量 JSON 解析。

借鉴 DeepTutor 的重试机制和错误处理模式。
"""

import asyncio
import json
import re
import logging
from typing import AsyncGenerator
from ..models.schemas import StreamEvent
from .llm import (
    LLMResponseError,
    complete_json,
    stream_text,
)
from .llm.client import MAX_RETRIES, is_retryable, retry_delays

logger = logging.getLogger(__name__)

def _is_transient_error(exc: Exception) -> bool:
    """判断是否为瞬态错误（可重试）"""
    if isinstance(exc, (json.JSONDecodeError, LLMResponseError)):
        return True
    return is_retryable(exc)


def _build_retry_delays() -> list[float]:
    """构建指数退避延迟序列"""
    return retry_delays(MAX_RETRIES)


# AI 系统提示词
SYSTEM_PROMPT = """你是一个专业的学术论文分析助手，擅长 CV/ML 领域的论文精读。
用户会提供一篇论文的全文内容，你需要进行深度分析并输出结构化的 JSON 结果。

请严格按照以下 JSON 格式输出，不要添加任何额外文字：

{
  "title": "论文标题",
  "authors": "作者1, 作者2, 作者3（从论文中提取完整作者列表，去除上标数字和特殊符号）",
  "venue": "发表会议/期刊（CVPR/ICCV/NeurIPS/ECCV/AAAI/ICML/ICLR 等，如无法判断则填 'Unknown'）",
  "year": 2024,
  "one_sentence": "一句话核心贡献，适合在面试中清晰表达",
  "problem": "这篇论文要解决什么问题？现有方法有什么局限性？",
  "key_contributions": ["贡献1", "贡献2", "贡献3"],
  "outline": [
    {"section": "Introduction", "page": "1-2", "summary": "本节介绍了..."},
    {"section": "Related Work", "page": "2-3", "summary": "回顾了..."},
    {"section": "Method", "page": "3-5", "summary": "提出了..."},
    {"section": "Experiments", "page": "5-7", "summary": "实验结果表明..."},
    {"section": "Conclusion", "page": "8", "summary": "总结了..."}
  ],
  "method": {
    "overview": "方法的总体描述",
    "pipeline": "Step 1: ...\nStep 2: ...\nStep 3: ...",
    "key_modules": [
      {"name": "模块名", "role": "作用", "innovation": "创新点"}
    ],
    "mermaid_chart": "graph TD\\n    A[Input] --> B[Process]\\n    B --> C[Output]"
  },
  "experiments": {
    "datasets": ["数据集1", "数据集2"],
    "baselines": ["方法1", "方法2"],
    "main_results": "主要实验结论",
    "ablation": "消融实验说明了什么",
    "result_table": [
      {"method": "本文方法", "dataset": "数据集名", "metric": "指标名", "value": "数值"},
      {"method": "Baseline A", "dataset": "数据集名", "metric": "指标名", "value": "数值"}
    ],
    "figures": [
      {
        "name": "Table 1",
        "caption": "在 XX 数据集上与 SOTA 方法的对比",
        "page": "6",
        "chart_type": "bar",
        "columns": ["Method", "mAP", "F1"],
        "rows": [
          ["Ours", "52.3", "48.1"],
          ["Baseline A", "49.1", "45.6"]
        ],
        "interpretation": "本文方法在 mAP 和 F1 指标上均优于所有基线方法..."
      }
    ]
  },
  "code_mapping": [
    {"concept": "论文中的概念/公式", "likely_implementation": "PyTorch 风格的代码实现思路"}
  ],
  "interview_qa": [
    {"q": "面试问题", "a": "简洁清晰的回答", "source": "Section X.Y, Page Z"}
  ],
  "limitations": "局限性和未来工作方向"
}

要求：
1. one_sentence 要简洁有力，能让人在30秒内理解论文核心
2. authors 从论文中提取完整的作者列表，去除上标数字（如 ∗1,2）、特殊符号（如 ¨o → o），用逗号分隔
3. outline 按论文实际章节结构列出每节的标题、页码和内容概述，page 字段填写该章节在论文中的起始页码（纯数字），用于跳转
3. method.pipeline 要用清晰的步骤描述完整流程
4. method.mermaid_chart 用 Mermaid graph TD 语法画出 pipeline 流程图。严格遵守以下规则：
   - 节点文本只能用英文、数字、空格，不能用括号 () [] {} 等特殊字符
   - 箭头用 --> 连接
   - 每行一个连接，不要把多行写在一行里
   - 节点ID用简短英文如 A, B, C 或 Step1, Step2
   - 示例：graph TD\\n    A[Input Image] --> B[Feature Extraction]\\n    B --> C[Anomaly Detection]
5. key_modules 列出 2-4 个最关键的模块
6. experiments.figures 是重点：逐一列出论文中每一个数据表格(Table)和图表(Figure)，每个都要：
   - name: 图表编号，如 "Table 1", "Figure 3"
   - caption: 图表的原始标题或说明文字
   - page: 该图表所在的页码（纯数字，如 "6"），用于点击跳转到论文对应页
   - chart_type: 推荐可视化类型（bar=柱状图, line=折线图, scatter=散点图, table=纯表格）
   - columns: 表格的列名数组
   - rows: 表格数据，每行是字符串数组，数值保持原始精度
   - interpretation: AI 对该图表的解读（2-3句话），说明图表展示了什么结论
   注意：不要遗漏任何一个 Table 或 Figure！每个图表都必须有独立的分析。
7. experiments.result_table 保留作为汇总（可从 figures 中提取主要对比数据）
8. code_mapping 给出 3-5 个核心概念的代码映射
9. interview_qa 生成 5-8 个高频面试问题，每个问题的 source 字段标注答案来源于论文的哪个章节和页码（如 "Section 3.2, Page 5"）
10. 所有内容基于论文实际内容，不要编造"""


# 顶层字段名列表，用于检测 JSON 中哪些字段已完整
_TOP_LEVEL_FIELDS = [
    "title", "authors", "venue", "year", "one_sentence", "problem",
    "key_contributions", "outline", "method", "experiments",
    "code_mapping", "interview_qa", "limitations",
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


def _sanitize_content(text: str) -> str:
    """
    清理内容，防止空内容导致 API 400 错误。
    借鉴 DeepTutor 的 _sanitize_empty_content 模式。
    """
    if not text or not text.strip():
        return "（空内容）"
    return text


def _repair_json(text: str) -> str | None:
    """尝试修复 LLM 输出的不完整 / 格式错误的 JSON。

    常见问题：
    1. 字符串值内含未转义的换行符 → 替换为 \\n
    2. 字符串值内含未转义的双引号 → 尝试转义
    3. 末尾截断导致括号不闭合 → 尝试截断到最后一个完整字段
    """
    if not text:
        return None
    # 1) 替换字符串值内的裸换行符为 \n 转义
    #    思路：在双引号内的 literal \n/\r 替换为转义序列
    fixed = []
    in_str = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_str and i + 1 < len(text):
            fixed.append(ch)
            fixed.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        if in_str and ch in ('\n', '\r'):
            fixed.append('\\n')
            i += 1
            continue
        fixed.append(ch)
        i += 1
    repaired = ''.join(fixed)
    # 2) 尝试直接 parse
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass
    # 3) 尝试截断到最后一个 } 并补全
    last_brace = repaired.rfind('}')
    if last_brace > 0:
        candidate = repaired[:last_brace + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return None


# ============================================================
# 图谱速读（轻量解读）—— 只抽知识图谱所需的 facet + 简短总结
# ============================================================

DIGEST_SYSTEM_PROMPT = """你是论文速读助手。请只为"知识图谱"抽取最关键的结构化信息，不要做完整精读。
严格输出 JSON（不要任何额外文字、不要 markdown 代码块），字段如下：
{
  "summary": "2-3 句中文总结，说清这篇论文做了什么",
  "one_sentence": "一句话核心贡献",
  "problem": "它解决的核心问题 / 针对现有方法的什么局限",
  "key_contributions": ["贡献点1", "贡献点2"],
  "method": "方法/思路概述（1-3 句，说清技术路线）",
  "datasets": ["论文里明确出现的数据集名称"],
  "baselines": ["论文里明确对比的基线方法名称"]
}
要求：datasets/baselines 只填论文中明确出现的专有名称（如 ImageNet、ResNet-50），没有就给空数组；
所有描述用中文，专有名词保留英文原名。"""


def _normalize_digest(data: dict, model: str) -> dict:
    from datetime import datetime, timezone

    def _s(x) -> str:
        return str(x).strip() if x is not None else ""

    def _lst(x) -> list[str]:
        if isinstance(x, list):
            return [_s(i) for i in x if _s(i)]
        return [_s(x)] if _s(x) else []

    return {
        "summary": _s(data.get("summary")),
        "one_sentence": _s(data.get("one_sentence")),
        "problem": _s(data.get("problem")),
        "key_contributions": _lst(data.get("key_contributions")),
        "method": _s(data.get("method")),
        "datasets": _lst(data.get("datasets")),
        "baselines": _lst(data.get("baselines")),
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def generate_graph_digest(pdf_text: str, api_key: str, base_url: str, model: str,
                                *, title: str = "", temperature: float = 0.1,
                                max_tokens: int = 1500,
                                thinking_effort: str | None = None,
                                provider: str = "custom",
                                api_format: str | None = None) -> dict:
    """图谱速读：一次非流式 LLM 调用，只抽知识图谱需要的 facet + 一段简短总结。

    与全量精读（analyze_paper_stream）的区别：输入截断（头 8000 + 尾 1500 字符，摘要/引言/
    方法多在前部、结论/实验在尾部），输出只有 7 个小字段，不生成大纲/逐图分析/代码映射/
    面试问答——token 成本约为全量精读的 1/5~1/10。返回 _normalize_digest 后的 dict。
    思考参数由 LLM Core 按协议和 token 上限统一处理。
    """
    text = _sanitize_content(pdf_text or "")
    head, tail = 8000, 1500
    if len(text) > head + tail:
        text = text[:head] + "\n...(中间略)...\n" + text[-tail:]
    user = (f"论文标题: {title}\n\n" if title else "") + f"论文文本（可能已截断）:\n{text}"

    cfg = {
        "provider": provider,
        "api_format": api_format,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking_effort": thinking_effort or "off",
    }
    data = await complete_json(
        cfg,
        system=DIGEST_SYSTEM_PROMPT,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_effort=thinking_effort,
        max_retries=MAX_RETRIES,
    )
    return _normalize_digest(data, model)


async def analyze_paper_stream(
    pdf_text: str, api_key: str, base_url: str, model: str,
    temperature: float = 0.1, max_tokens: int = 16384,
    system_prompt: str | None = None,
    thinking_effort: str | None = None,
    provider: str = "custom",
    api_format: str | None = None,
    timeout_seconds: float = 150,
) -> AsyncGenerator[StreamEvent, None]:
    """
    通过统一 LLM Core 流式分析论文；provider/api_format 由全局配置明确指定。

    仅在读取没有 api_format 的历史配置时才允许在配置边界按 URL 推断协议。
    每收到一个 chunk 就尝试增量解析 JSON，已完成的字段立即 yield。

    借鉴 DeepTutor 的重试机制：对瞬态错误进行指数退避重试。

    Yields:
        StreamEvent: 流式事件，包含进度状态和解析字段
    """
    yield StreamEvent(event="status", data={"message": "正在分析论文内容..."})

    system = system_prompt or SYSTEM_PROMPT
    pdf_text = _sanitize_content(pdf_text)
    delays = _build_retry_delays()
    cfg = {
        "provider": provider,
        "api_format": api_format,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking_effort": thinking_effort or "off",
        "timeout_seconds": timeout_seconds,
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            accumulated = ""
            yielded_fields: set[str] = set()

            async for text_chunk in stream_text(
                cfg,
                system=system,
                user=pdf_text,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_effort=thinking_effort,
                timeout_seconds=timeout_seconds,
            ):
                accumulated += text_chunk

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
            try:
                final = json.loads(accumulated)
            except json.JSONDecodeError as e:
                logger.warning(f"Final JSON parse failed: {e}, accumulated length: {len(accumulated)}")
                # 尝试从不完整 JSON 中提取已有字段
                partial = _try_parse_partial_json(accumulated)
                if partial:
                    final = partial
                else:
                    raise ValueError(f"无法解析 AI 返回的 JSON: {e}")

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
            return  # 成功，退出重试循环

        except Exception as e:
            if _is_transient_error(e) and attempt < MAX_RETRIES:
                delay = delays[attempt]
                logger.warning(f"Transient error (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}. Retrying in {delay:.1f}s...")
                yield StreamEvent(
                    event="status",
                    data={"message": f"遇到临时错误，正在重试（{attempt + 1}/{MAX_RETRIES + 1}）..."},
                )
                await asyncio.sleep(delay)
                continue

            logger.error(f"Analysis failed: {e}")
            yield StreamEvent(event="error", data={"message": f"解析失败: {str(e)}"})
            return


def extract_code_url(pdf_text: str) -> str:
    """
    用正则从论文全文中提取代码仓库 URL。

    搜索 github.com、gitlab.com、bitbucket.org、gitee.com 等代码托管平台的链接。
    优先返回 github.com 的链接（最常见）。

    Args:
        pdf_text: 论文全文

    Returns:
        代码仓库 URL，未找到则返回空字符串
    """
    import re

    # 匹配代码仓库 URL（支持有/无 https:// 前缀）
    # 有前缀: https://github.com/user/repo
    # 无前缀: github.com/user/repo（常见于论文脚注）
    pattern = r'(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|gitee\.com)/[\w\-./]+'
    matches = re.findall(pattern, pdf_text)

    if not matches:
        return ""

    # 清理 URL
    cleaned = []
    for url in matches:
        # 去掉末尾标点
        url = url.rstrip(".,;:!?")
        # 去掉 URL fragment (#...)
        if "#" in url:
            url = url[:url.index("#")]
        # 去掉尾部斜杠
        url = url.rstrip("/")
        # 只保留 owner/repo（去掉更深的路径如 /issues/123）
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part in ("github.com", "gitlab.com", "bitbucket.org", "gitee.com"):
                if i + 2 < len(parts):
                    clean_url = "/".join(parts[:i + 3])
                    cleaned.append(clean_url)
                elif i + 1 < len(parts):
                    clean_url = "/".join(parts[:i + 2])
                    cleaned.append(clean_url)
                break
        else:
            cleaned.append(url)

    # 去重，保持顺序
    seen = set()
    unique = []
    for url in cleaned:
        normalized = url.rstrip("/").lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(url)

    # 优先返回 github.com 链接
    for url in unique:
        if "github.com" in url:
            return url

    return unique[0] if unique else ""

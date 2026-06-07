import json
import logging

logger = logging.getLogger(__name__)

TAG_PROMPT = """你是一个学术论文分类助手。根据以下论文的标题、核心贡献和方法论，给出 2-5 个最相关的分类标签。

标签应该从以下维度选取：
1. 研究领域：如"目标检测"、"图像分割"、"自然语言处理"、"强化学习"、"生成模型"、"图神经网络"等
2. 方法类型：如"Transformer"、"CNN"、"扩散模型"、"对比学习"、"自监督学习"等
3. 应用场景：如"自动驾驶"、"医学影像"、"机器人"等
4. 阅读状态：如"必读"、"推荐"等（根据论文影响力判断）

请只输出 JSON 数组，不要添加任何额外文字。示例：
["目标检测", "Transformer", "自动驾驶", "必读"]

论文信息：
标题：{title}
核心贡献：{one_sentence}
方法论：{method_overview}
会议/期刊：{venue}
"""


async def auto_tag_paper(
    title: str,
    one_sentence: str,
    method_overview: str,
    venue: str,
    api_key: str,
    base_url: str,
    model: str,
) -> list[str]:
    from .ai_analyzer import _create_client, _extract_text

    if not api_key or not base_url or not model:
        return []

    prompt = TAG_PROMPT.format(
        title=title or "Unknown",
        one_sentence=one_sentence or "",
        method_overview=method_overview or "",
        venue=venue or "Unknown",
    )

    client = None
    try:
        client = _create_client(api_key, base_url)
        message = await client.messages.create(
            model=model,
            max_tokens=256,
            system="你是一个学术论文分类助手。只输出 JSON 数组，不要添加任何额外文字。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        text = _extract_text(message).strip()
        tags = json.loads(text)
        if isinstance(tags, list):
            return [str(t).strip() for t in tags if isinstance(t, str) and t.strip()][:5]
    except Exception as e:
        logger.warning(f"Auto-tagging failed: {e}")
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass

    return []

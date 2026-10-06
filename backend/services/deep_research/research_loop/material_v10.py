"""Explicit task/label boundaries in addition to v9 modality boundaries."""

import re

from .material_v9 import outside_scope as modality_outside_scope


def outside_scope(question, fact):
    if modality_outside_scope(question, fact):
        return True
    image_only = re.search(
        r"只(?:研究|讨论|考虑)[^。；]{0,12}图像|only[^.;]{0,24}images?", question, re.I
    )
    if image_only and re.search(
        r"\bNLP\b|维基百科|语言模型|自然语言处理|文本异常|Wikipedia|language processing",
        fact,
        re.I,
    ):
        return True
    if "无监督" in question or re.search(r"\bunsupervised\b", question, re.I):
        for sentence in re.split(r"[。；;]", fact):
            labels = re.search(
                r"已(?:标注|标记|知)异常|真实(?:缺陷|异常).{0,8}标注|带标记的异常|known anomalies|labeled anomal",
                sentence,
                re.I,
            )
            training = re.search(r"训练|优化|train", sentence, re.I)
            negation = re.search(
                r"无需|不使用|不需要|不依赖|不能|不得|无需|without|does not|no labeled",
                sentence,
                re.I,
            )
            if labels and training and not negation:
                return True
    return False


def passages(
    asset, question, plan, focus_queries=(), exclude_quotes=(), targeted=False
):
    from .material import body_material
    from .material_v9 import overview_material

    payload = asset.inline_payload or {}
    paper = payload.get("paper", {})
    text = payload.get("text", "")
    if payload.get("source_kind") == "abstract":
        return [{"passage_id": "abstract", "text": text[:7000]}]
    queries = [q["query"] for q in plan.get("queries", [])]
    if paper.get("research_role") == "survey":
        chunks = overview_material(
            asset.asset_id,
            text,
            queries,
            focus_queries=focus_queries,
            exclude_quotes=exclude_quotes,
            targeted=targeted,
            max_chars=7000,
            question=question,
        )
    else:
        chunks = body_material(
            asset.asset_id,
            text,
            queries,
            focus_queries=focus_queries or plan.get("reading_queries", []),
            primary_targets=paper.get("primary_targets", []),
            exclude_quotes=exclude_quotes,
            targeted=targeted,
            max_chars=7000,
            return_passages=True,
        )
    bounded, used = [], 0
    for chunk in chunks:
        short = {**chunk, "text": chunk["text"][:1400]}
        if used + len(short["text"]) <= 7000:
            bounded.append(short)
            used += len(short["text"])
    return bounded


def description_only(fact):
    from .material_v9 import description_only as previous

    # An attributed, concrete operation is substantive content. A prefix such as
    # 'the paper' does not by itself make actual mechanism prose a chapter agenda.
    if re.search(
        r"提取|保存|建库|拟合|重建|匹配|计算|训练|存储|淘汰|拒绝", fact
    ) and re.search(r"图像|样本|特征|分布|距离|残差|差异|判据|记忆|队列|数据", fact):
        return False
    return previous(fact)

"""RAG answer generation kept outside the HTTP routing layer."""

from __future__ import annotations

from .llm import complete_text, set_llm_scope, set_llm_stage


async def generate_research_answer(
    question: str,
    hits: list[dict],
    graph_context: str,
    config: dict,
) -> str:
    set_llm_scope("workspace_research")
    set_llm_stage("rag_qa")
    context = "\n\n".join(
        f"[P{index + 1}] {hit['paper_title']} / {hit['section_name']}:\n{hit['snippet']}"
        for index, hit in enumerate(hits)
    )
    prompt = (
        "你是一名学术研究助手。基于下列检索到的论文片段与学术文献关系回答用户问题。"
        f"必须使用中文，并用 [P编号] 标注引用。不得用记忆补充文献事实。\n\n问题：{question}\n\n"
        f"原文资料：\n{context}"
    )
    return await complete_text(
        config,
        system="你是学术研究助手，每条文献事实必须基于提供的原文并引用 [P编号]。材料不足时说明缺口。",
        user=prompt,
        max_tokens=2048,
        temperature=0.2,
        thinking_effort=config.get("thinking_effort", "off"),
    )


async def generate_audited_answer(question, hits, config):
    from .deep_research.evidence_audit import audit_answer
    from .answer_delivery import deliver_audited_answer
    candidate = await generate_research_answer(question, hits, "", config)
    sources = [{"id": f"P{i + 1}", "title": hit["paper_title"], "text": hit["snippet"],
                "source_kind": "full_text_excerpt"} for i, hit in enumerate(hits)]
    set_llm_stage("rag_evidence_audit")
    audit = await audit_answer(candidate, sources, config)
    answer, status = deliver_audited_answer(candidate, audit)
    return answer, status, audit

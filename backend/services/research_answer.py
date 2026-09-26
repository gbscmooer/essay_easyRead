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
        f"[{index + 1}] {hit['paper_title']} / {hit['section_name']}:\n{hit['snippet']}"
        for index, hit in enumerate(hits)
    )
    prompt = (
        "你是一名学术研究助手。基于下列检索到的论文片段与学术文献关系回答用户问题。"
        f"必须使用中文，并用 [编号] 标注引用。\n\n问题：{question}\n\n"
        f"资料：\n{context}\n{graph_context}"
    )
    return await complete_text(
        config,
        system="你是学术研究助手，必须基于提供的资料与文献网络关系回答，引用编号写在文末。",
        user=prompt,
        max_tokens=2048,
        temperature=0.2,
        thinking_effort=config.get("thinking_effort", "off"),
    )

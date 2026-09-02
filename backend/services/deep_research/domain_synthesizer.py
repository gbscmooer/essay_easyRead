"""
Domain Synthesizer — 领域认知综合（stage_synthesize）

在算法骨架（8 通道关联度 + Disparity Filter + Louvain）之上，用 LLM 注入学术语义，
让学者不读单篇论文也能看懂领域全局：

- Task 1  节点学术角色：landmark / rising_star / benchmark / tool_sota / survey，
          外加一句话核心创新与方法标签（≤25 字，图谱上直接可读）。
- Task 2  连边因果：**只研判 cites 边**（有向引用 + 引用上下文），输出
          improves / replaces / evaluates_on / inspires。related 边是无向主题相似，
          没有引用证据，强行判因果只会逼模型幻觉 —— 不做。
- Task 3  技术流派：把 Louvain 簇具象化（名称 / 哲学 / 优劣势 / 代表作）。
- Task 4  领域全景简报：结构化 + Markdown 综述。

设计约束（与 auto_digest 同一套纪律）：
- 未配置解读 API Key → 整段跳过，不阻塞流水线；
- 单任务失败 → 保留其余产物并标记 partial；
- 全部批量化调用（每批节点 / 边合并一次 LLM 请求），上限可配，避免成本失控。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from ..ai_analyzer import (
    _build_retry_delays, _complete_once, _create_client, _create_openai_client,
    _extract_json_value, _is_openai_format, _is_transient_error, _repair_json, MAX_RETRIES,
)
from ...models.research import (
    EDGE_RELATIONS, NODE_ROLES,
    BriefingMilestone, EdgeSemantic, ExecutiveBriefing, GraphSynthesis, NodeSemantic, ParadigmInfo,
)

logger = logging.getLogger(__name__)

# 每批送入 LLM 的节点 / 边数量，以及单次运行的总上限
DEFAULT_NODE_BATCH = 15
DEFAULT_EDGE_BATCH = 20
DEFAULT_MAX_NODES = 60
DEFAULT_MAX_EDGES = 60
# 摘要 / 引用上下文送入 LLM 前的截断长度（字符）
_ABSTRACT_CLIP = 600
_CONTEXT_CLIP = 220

NODE_ROLE_PROMPTS = {
    "landmark": "奠基基石：高历史被引的领域开山/转折之作",
    "rising_star": "前沿黑马：近 1~2 年发表、上升势头猛的当前焦点",
    "benchmark": "标准基准：提出公认 Dataset/Benchmark 的代表文献",
    "tool_sota": "工程标杆：高影响力开源实现/工具库/落地系统",
    "survey": "权威综述：跨分支引用的全局总结",
    "regular": "常规论文：以上皆非",
}

EDGE_RELATION_PROMPTS = {
    "improves": "改进继承：B 在 A 基础上改进了复杂度/精度/稳定性",
    "replaces": "范式替代：B 用截然不同的流派方案解决 A 针对的同一痛点",
    "evaluates_on": "基准评测：B 将 A 作为实验 Baseline 或在其数据集上评测",
    "inspires": "跨界借鉴：B 引入 A 的跨学科理论并迁移到本领域",
    "cites": "普通引用：无明确因果关系，仅背景引用",
}


def _clip(text: str | None, limit: int) -> str:
    t = (text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


async def _llm_json(cfg: dict, system: str, user: str, *, max_tokens: int,
                    temperature: float = 0.15) -> dict:
    """一次非流式 LLM 调用 → JSON dict。复用 ai_analyzer 的客户端/重试/修复逻辑。"""
    api_key, base_url, model = cfg["api_key"], cfg.get("base_url", ""), cfg["model"]
    use_openai = _is_openai_format(base_url)
    delays = _build_retry_delays()
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        client = _create_openai_client(api_key, base_url) if use_openai else _create_client(api_key, base_url)
        try:
            raw = await _complete_once(client, use_openai, model, system, user,
                                       max_tokens=max_tokens, temperature=temperature)
            payload = _extract_json_value(raw) or raw
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                repaired = _repair_json(payload)
                if repaired is None:
                    raise
                data = json.loads(repaired)
            if not isinstance(data, dict):
                raise ValueError("synthesize 输出不是 JSON 对象")
            return data
        except Exception as e:
            last_err = e
            # JSON 解析失败（空 content / 模型输出夹带散文）也视为瞬时错误参与重试：
            # 推理型模型偶发空回复，一次重试通常就能拿到正常 JSON
            retryable = _is_transient_error(e) or isinstance(e, json.JSONDecodeError) or isinstance(e, ValueError)
            if retryable and attempt < MAX_RETRIES:
                logger.warning(f"synthesize transient error (attempt {attempt + 1}): {e}, retrying...")
                await asyncio.sleep(delays[attempt])
                continue
            raise
        finally:
            await client.close()
    raise last_err  # pragma: no cover


# ── 输入装配 ────────────────────────────────────────────────────────────────

def _node_inputs(nodes: list[dict], papers_by_id: dict[int, object], *, max_nodes: int) -> list[dict]:
    """节点 → LLM 输入条目（图谱指标 + 论文元数据/摘要）。按 importance 优先打标。"""
    items: list[dict] = []
    for n in nodes:
        meta = n.get("meta") or {}
        pid = meta.get("paper_id")
        p = papers_by_id.get(pid) if pid is not None else None
        digest = getattr(p, "graph_digest", None) or {}
        abstract = getattr(p, "abstract", None)
        if not abstract and digest:
            # 无召回摘要时退而求其次：速读总结比标题信息量大
            abstract = digest.get("summary") or digest.get("one_sentence")
        items.append({
            "id": n["id"],
            "title": n.get("label") or "",
            "year": meta.get("year"),
            "venue": meta.get("venue"),
            "citations": getattr(p, "citation_count", None) if p else meta.get("citation_count"),
            "pagerank": meta.get("importance"),
            "foundational": meta.get("foundational"),
            "cluster": meta.get("cluster"),
            "abstract": _clip(abstract, _ABSTRACT_CLIP),
        })
    items.sort(key=lambda x: (x["pagerank"] or 0, x["citations"] or 0), reverse=True)
    return items[:max_nodes]


def _cites_edges(nodes: list[dict], edges: list[dict], *, max_edges: int) -> list[dict]:
    """cites 边 → LLM 输入条目。有引用上下文的排前面（证据充分者优先）。"""
    label_by_id = {n["id"]: (n.get("label") or "") for n in nodes}
    items: list[dict] = []
    for e in edges:
        if e.get("kind") != "cites":
            continue
        src, tgt = e.get("source", ""), e.get("target", "")
        if src not in label_by_id or tgt not in label_by_id:
            continue
        contexts = e.get("contexts") or []
        items.append({
            "key": f"{src}>{tgt}",
            "citing": label_by_id[src],      # 引用方（后来的工作）
            "cited": label_by_id[tgt],       # 被引方（更早/被依托的工作）
            "intent_hint": (e.get("intents") or [""])[0],
            "contexts": [_clip(c.get("context"), _CONTEXT_CLIP) for c in contexts[:2]],
        })
    items.sort(key=lambda x: len(x["contexts"]), reverse=True)
    return items[:max_edges]


def _clusters(nodes: list[dict], node_semantics: dict[str, NodeSemantic], *,
              per_cluster: int = 10) -> list[dict]:
    """Louvain 簇 → LLM 输入条目（带 Task 1 的角色/一句话，辅助流派命名）。"""
    by_cluster: dict[int, list[dict]] = {}
    for n in nodes:
        meta = n.get("meta") or {}
        c = meta.get("cluster")
        if not isinstance(c, int) or c < 0:
            continue
        sem = node_semantics.get(n["id"])
        by_cluster.setdefault(c, []).append({
            "id": n["id"],
            "title": n.get("label") or "",
            "year": meta.get("year"),
            "role": sem.role if sem else "",
            "takeaway": sem.short_takeaway if sem else "",
            "pagerank": meta.get("importance") or 0,
        })
    out: list[dict] = []
    for c, members in by_cluster.items():
        if len(members) < 2:  # 孤立/微型簇不成流派
            continue
        members.sort(key=lambda m: m["pagerank"], reverse=True)
        out.append({"cluster_id": c, "members": members[:per_cluster]})
    out.sort(key=lambda x: len(x["members"]), reverse=True)
    return out[:8]


# ── 四个任务 ────────────────────────────────────────────────────────────────

_NODE_SYSTEM = (
    "你是文献计量与学术情报专家。根据论文标题、年份、被引数、PageRank 与摘要，"
    "判定每篇论文在本领域中的学术角色，并提炼一句话核心创新。"
    "只输出 JSON，不要输出任何其他文字。"
)


def _node_user(batch: list[dict]) -> str:
    lines = ["## 待研判论文", ""]
    for i, it in enumerate(batch):
        bits = [f'标题: {it["title"]}']
        if it.get("year"):
            bits.append(f'年份: {it["year"]}')
        if it.get("citations") is not None:
            bits.append(f'被引: {it["citations"]}')
        if it.get("pagerank") is not None:
            bits.append(f'PageRank: {round(float(it["pagerank"]), 3)}')
        if it.get("venue"):
            bits.append(f'发表处: {it["venue"]}')
        if it.get("abstract"):
            bits.append(f'摘要: {it["abstract"]}')
        lines.append(f"[{i}] " + "；".join(bits))
    roles = "\n".join(f'- {k}: {v}' for k, v in NODE_ROLE_PROMPTS.items())
    lines += [
        "",
        "## 角色定义",
        roles,
        "",
        "## 输出格式",
        '```json',
        '{"papers": [{"i": 0, "role": "landmark|rising_star|benchmark|tool_sota|survey|regular", '
        '"takeaway": "不超过25字的一句话核心创新", "techniques": ["标签1", "标签2"]}]}',
        '```',
        "要求：takeaway 必须具体（说清做了什么、解决了什么），禁止空话；techniques 2~3 个。",
    ]
    return "\n".join(lines)


def _normalize_node_semantic(item: dict, batch: list[dict]) -> tuple[str, NodeSemantic] | None:
    try:
        i = int(item.get("i", -1))
    except (TypeError, ValueError):
        return None
    if i < 0 or i >= len(batch):
        return None
    role = str(item.get("role") or "regular").strip()
    if role not in NODE_ROLES:
        role = "regular"
    techniques = item.get("techniques") or []
    if isinstance(techniques, str):
        techniques = [techniques]
    return batch[i]["id"], NodeSemantic(
        role=role,
        short_takeaway=_clip(str(item.get("takeaway") or ""), 60),
        key_techniques=[_clip(str(t), 30) for t in techniques[:3]],
    )


async def _task_nodes(cfg: dict, node_items: list[dict], *, batch_size: int) -> dict[str, NodeSemantic]:
    out: dict[str, NodeSemantic] = {}
    for start in range(0, len(node_items), batch_size):
        batch = node_items[start:start + batch_size]
        data = await _llm_json(cfg, _NODE_SYSTEM, _node_user(batch), max_tokens=4000)
        for item in data.get("papers") or []:
            parsed = _normalize_node_semantic(item, batch)
            if parsed:
                out[parsed[0]] = parsed[1]
    return out


_EDGE_SYSTEM = (
    "你是学术引文分析专家。每条输入是一条真实引用（citing 引用了 cited，可能附引用原文句），"
    "判定这条引用的学术因果类型并给一句话说明。宁可归为普通引用，也不要编造关系。"
    "只输出 JSON，不要输出任何其他文字。"
)


def _edge_user(batch: list[dict]) -> str:
    lines = ["## 待研判引用（方向：citing → cited）", ""]
    for i, it in enumerate(batch):
        bits = [f'citing(引用方): {it["citing"]}', f'cited(被引方): {it["cited"]}']
        if it.get("intent_hint"):
            bits.append(f'上下文启发式意图: {it["intent_hint"]}')
        for j, ctx in enumerate(it.get("contexts") or []):
            if ctx:
                bits.append(f'引用原文{j + 1}: 「{ctx}」')
        lines.append(f"[{i}] " + "；".join(bits))
    relations = "\n".join(f'- {k}: {v}' for k, v in EDGE_RELATION_PROMPTS.items())
    lines += [
        "",
        "## 关系定义",
        relations,
        "",
        "## 输出格式",
        '```json',
        '{"edges": [{"i": 0, "relation": "improves|replaces|evaluates_on|inspires|cites", '
        '"summary": "一句话因果说明（不超过40字）"}]}',
        '```',
    ]
    return "\n".join(lines)


def _normalize_edge_semantic(item: dict, batch: list[dict]) -> tuple[str, EdgeSemantic] | None:
    try:
        i = int(item.get("i", -1))
    except (TypeError, ValueError):
        return None
    if i < 0 or i >= len(batch):
        return None
    rel = str(item.get("relation") or "cites").strip()
    if rel not in EDGE_RELATIONS:
        rel = "cites"
    return batch[i]["key"], EdgeSemantic(
        relation_type=rel,
        relation_summary=_clip(str(item.get("summary") or ""), 80),
    )


async def _task_edges(cfg: dict, edge_items: list[dict], *, batch_size: int) -> dict[str, EdgeSemantic]:
    out: dict[str, EdgeSemantic] = {}
    for start in range(0, len(edge_items), batch_size):
        batch = edge_items[start:start + batch_size]
        data = await _llm_json(cfg, _EDGE_SYSTEM, _edge_user(batch), max_tokens=4000)
        for item in data.get("edges") or []:
            parsed = _normalize_edge_semantic(item, batch)
            if parsed:
                out[parsed[0]] = parsed[1]
    return out


_PARADIGM_SYSTEM = (
    "你是领域综述专家。下面每个簇是一批相关论文（已按影响力排序，附角色与一句话创新），"
    "请把每个簇归纳为一个具体的技术流派/研究路线。"
    "只输出 JSON，不要输出任何其他文字。"
)


def _paradigm_user(clusters: list[dict]) -> str:
    lines = ["## 论文簇（同一簇内论文属于同一研究路线）", ""]
    for i, cl in enumerate(clusters):
        lines.append(f"### 簇 {i}（cluster_id={cl['cluster_id']}）")
        for m in cl["members"]:
            bits = [f'- [{m["id"]}] {m["title"]}']
            if m.get("year"):
                bits.append(f'({m["year"]})')
            if m.get("role") and m["role"] != "regular":
                bits.append(f'角色={m["role"]}')
            if m.get("takeaway"):
                bits.append(f'创新: {m["takeaway"]}')
            lines.append(" ".join(bits))
        lines.append("")
    lines += [
        "## 输出格式",
        '```json',
        '{"paradigms": [{"cluster": 0, "name": "流派具象名称（如：状态空间模型 (SSM) 路线）", '
        '"philosophy": "一句话核心哲学", "pros": ["优势1"], "cons": ["软肋1"], '
        '"representatives": ["paper:12", "paper:34"]}]}',
        '```',
        "要求：representatives 从该簇成员的 [id] 里选 2~3 个；name 必须具体到技术路线，"
        "禁止「方向一」这类空名。",
    ]
    return "\n".join(lines)


def _normalize_paradigm(item: dict, clusters: list[dict]) -> ParadigmInfo | None:
    try:
        ci = int(item.get("cluster", -1))
    except (TypeError, ValueError):
        return None
    if ci < 0 or ci >= len(clusters):
        return None
    valid_ids = {m["id"] for m in clusters[ci]["members"]}

    def _lst(x) -> list[str]:
        if isinstance(x, list):
            return [_clip(str(s), 60) for s in x if str(s).strip()][:4]
        return [_clip(str(x), 60)] if x and str(x).strip() else []

    reps = [str(r) for r in (item.get("representatives") or []) if str(r) in valid_ids][:3]
    return ParadigmInfo(
        cluster_id=clusters[ci]["cluster_id"],
        name=_clip(str(item.get("name") or ""), 40),
        core_philosophy=_clip(str(item.get("philosophy") or ""), 80),
        pros=_lst(item.get("pros")),
        cons=_lst(item.get("cons")),
        representative_node_ids=reps,
    )


async def _task_paradigms(cfg: dict, clusters: list[dict]) -> list[ParadigmInfo]:
    data = await _llm_json(cfg, _PARADIGM_SYSTEM, _paradigm_user(clusters), max_tokens=4000)
    out = []
    for item in data.get("paradigms") or []:
        parsed = _normalize_paradigm(item, clusters)
        if parsed and parsed.name:
            out.append(parsed)
    return out


_BRIEFING_SYSTEM = (
    "你是资深领域综述作者。基于给定的领域材料（论文清单含角色与创新点、引用因果关系、"
    "技术流派），写一份供学者冷启动的《领域全景透视简报》：不读任何单篇论文，"
    "3 分钟建立对该领域的系统认知。所有结论必须来自给定材料，不得编造论文。"
    "只输出 JSON，不要输出任何其他文字。"
)


def _briefing_user(
    node_items: list[dict], node_semantics: dict[str, NodeSemantic],
    edge_semantics: dict[str, EdgeSemantic], paradigms: list[ParadigmInfo],
) -> str:
    lines = ["## 论文清单（按影响力排序）", ""]
    for it in node_items[:30]:
        sem = node_semantics.get(it["id"])
        bits = [f'- {it["title"]}']
        if it.get("year"):
            bits.append(f'({it["year"]})')
        if it.get("citations") is not None:
            bits.append(f'被引{it["citations"]}')
        if sem:
            bits.append(f'[{sem.role}] {sem.short_takeaway}')
        lines.append(" ".join(bits))
    causal = [
        f'- {k.split(">")[0]} → {k.split(">")[1]}: {v.relation_type}'
        + (f'（{v.relation_summary}）' if v.relation_summary else '')
        for k, v in list(edge_semantics.items())[:40]
    ]
    if causal:
        lines += ["", "## 引用因果关系（部分）", ""] + causal
    if paradigms:
        lines += ["", "## 技术流派", ""]
        for p in paradigms:
            lines.append(
                f'- {p.name}：{p.core_philosophy}；优势：{"、".join(p.pros)}；软肋：{"、".join(p.cons)}'
            )
    lines += [
        "",
        "## 输出格式",
        '```json',
        '{"domain_problem": "该领域在解决什么核心矛盾（2~4 句）",',
        ' "timeline": [{"year": "2017", "event": "里程碑事件一句话"}],',
        ' "benchmarks": ["公认必测 Benchmark/Baseline 与指标"],',
        ' "hotspots": ["近 1~2 年最密集的攻坚方向"],',
        ' "challenges": ["未解决的公开挑战"],',
        ' "markdown": "完整 Markdown 综述，含演进时间轴、流派横向对比表、给新手的必读路线建议"}',
        '```',
    ]
    return "\n".join(lines)


def _normalize_briefing(data: dict) -> ExecutiveBriefing:
    timeline = []
    for m in data.get("timeline") or []:
        if isinstance(m, dict) and (m.get("event") or m.get("year")):
            timeline.append(BriefingMilestone(
                year=_clip(str(m.get("year") or ""), 12),
                event=_clip(str(m.get("event") or ""), 120),
            ))

    def _lst(x) -> list[str]:
        if isinstance(x, list):
            return [_clip(str(s), 80) for s in x if str(s).strip()][:6]
        return [_clip(str(x), 80)] if x and str(x).strip() else []

    md = str(data.get("markdown") or data.get("markdown_report") or "").strip()
    return ExecutiveBriefing(
        domain_problem=_clip(str(data.get("domain_problem") or ""), 400),
        milestone_timeline=timeline[:12],
        benchmark_standards=_lst(data.get("benchmarks") or data.get("benchmark_standards")),
        recent_hotspots=_lst(data.get("hotspots") or data.get("recent_hotspots")),
        open_challenges=_lst(data.get("challenges") or data.get("open_challenges")),
        markdown_report=md[:12000],
    )


async def _task_briefing(
    cfg: dict, node_items: list[dict], node_semantics: dict[str, NodeSemantic],
    edge_semantics: dict[str, EdgeSemantic], paradigms: list[ParadigmInfo],
) -> ExecutiveBriefing:
    data = await _llm_json(
        cfg, _BRIEFING_SYSTEM,
        _briefing_user(node_items, node_semantics, edge_semantics, paradigms),
        max_tokens=4000,
    )
    return _normalize_briefing(data)


# ── 入口 ────────────────────────────────────────────────────────────────────

def synthesize_config() -> dict:
    """research.synthesize 段配置（缺省补全）。"""
    from ..ai_config import get_research_config
    cfg = (get_research_config().get("synthesize") or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "max_nodes": int(cfg.get("max_nodes", DEFAULT_MAX_NODES)),
        "max_edges": int(cfg.get("max_edges", DEFAULT_MAX_EDGES)),
        "node_batch": int(cfg.get("node_batch", DEFAULT_NODE_BATCH)),
        "edge_batch": int(cfg.get("edge_batch", DEFAULT_EDGE_BATCH)),
    }


def apply_semantics_to_graph(graph: dict, synthesis: GraphSynthesis) -> None:
    """把语义标注就地写进 graph 的 nodes / edges（持久化与前端共用一份结构）。"""
    for n in graph.get("nodes") or []:
        sem = synthesis.enriched_nodes.get(n.get("id", ""))
        if sem:
            n["semantic"] = sem.model_dump()
    for e in graph.get("edges") or []:
        key = f'{e.get("source", "")}>{e.get("target", "")}'
        sem = synthesis.enriched_edges.get(key)
        if sem:
            e["semantic"] = sem.model_dump()


async def synthesize_domain(
    graph: dict,
    papers_by_id: dict[int, object],
    *,
    progress=None,
) -> GraphSynthesis | None:
    """领域认知综合主入口。返回 GraphSynthesis；未配置 Key 返回 None（调用方跳过阶段）。

    progress: 可选回调 progress(task, done, total)，供 SSE 推送细分进度。
    """
    from ..ai_config import get_analysis_config

    cfg = get_analysis_config()
    if not cfg.get("api_key"):
        logger.info("synthesize: 未配置解读 API Key，跳过领域认知综合")
        return None

    scfg = synthesize_config()
    if not scfg["enabled"]:
        return None

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return None

    synthesis = GraphSynthesis()
    node_items = _node_inputs(nodes, papers_by_id, max_nodes=scfg["max_nodes"])
    edge_items = _cites_edges(nodes, edges, max_edges=scfg["max_edges"])
    total_tasks = (1 if node_items else 0) + (1 if edge_items else 0) + 1  # 简报必做（只要有材料）
    done_tasks = 0

    def _tick(task: str):
        nonlocal done_tasks
        done_tasks += 1
        if progress:
            try:
                progress(task, done_tasks, total_tasks)
            except Exception:
                pass

    # Task 1：节点角色
    if node_items:
        try:
            synthesis.enriched_nodes = await _task_nodes(cfg, node_items, batch_size=scfg["node_batch"])
            logger.info("synthesize: %d/%d 个节点获得学术角色", len(synthesis.enriched_nodes), len(node_items))
        except Exception as e:
            synthesis.partial = True
            logger.warning(f"synthesize Task1 (nodes) failed (non-fatal): {e}")
        _tick("nodes")

    # Task 2：cites 边因果
    if edge_items:
        try:
            synthesis.enriched_edges = await _task_edges(cfg, edge_items, batch_size=scfg["edge_batch"])
            logger.info("synthesize: %d/%d 条引用边获得因果标注", len(synthesis.enriched_edges), len(edge_items))
        except Exception as e:
            synthesis.partial = True
            logger.warning(f"synthesize Task2 (edges) failed (non-fatal): {e}")
        _tick("edges")

    # Task 3：流派（依赖 Task 1 的角色；Task 1 失败也能跑，只是输入少一档信息）
    cluster_items = _clusters(nodes, synthesis.enriched_nodes)
    if cluster_items:
        try:
            synthesis.paradigms = await _task_paradigms(cfg, cluster_items)
            logger.info("synthesize: %d 个流派", len(synthesis.paradigms))
        except Exception as e:
            synthesis.partial = True
            logger.warning(f"synthesize Task3 (paradigms) failed (non-fatal): {e}")

    # Task 4：全景简报
    try:
        synthesis.briefing = await _task_briefing(
            cfg, node_items, synthesis.enriched_nodes, synthesis.enriched_edges, synthesis.paradigms,
        )
    except Exception as e:
        synthesis.partial = True
        logger.warning(f"synthesize Task4 (briefing) failed (non-fatal): {e}")

    if synthesis.partial and not (synthesis.enriched_nodes or synthesis.enriched_edges or synthesis.paradigms or synthesis.briefing):
        logger.warning("synthesize: 全部任务失败，不产出 synthesis")
        return None

    synthesis.stats = {
        "nodes_labeled": len(synthesis.enriched_nodes),
        "nodes_total": len(node_items),
        "edges_labeled": len(synthesis.enriched_edges),
        "edges_total": len(edge_items),
        "paradigms": len(synthesis.paradigms),
        "model": cfg.get("model", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partial": synthesis.partial,
    }
    apply_semantics_to_graph(graph, synthesis)
    return synthesis

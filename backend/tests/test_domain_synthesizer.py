"""领域认知综合（domain_synthesizer / stage_synthesize）单测。

守住四条行为契约：
1. 未配置解读 Key → 整段跳过（不抛异常、不产出）；
2. Task 2 只研判 cites 边：related / bibcoupling / cocitation 无向边不进因果研判；
3. LLM 输出的角色 / 关系枚举越界时归一到安全值，索引越界条目被丢弃；
4. 单任务失败不阻塞：其余产物保留并标记 partial。
"""

import asyncio

import pytest

from backend.services.deep_research.domain_synthesizer import (
    _cites_edges, _clusters, _node_inputs, _normalize_briefing, _normalize_edge_semantic,
    _normalize_node_semantic, apply_semantics_to_graph, synthesize_domain, synthesize_config,
)
from backend.models.research import EdgeSemantic, GraphSynthesis, NodeSemantic


# ── 测试用图谱与论文 ────────────────────────────────────────────────────────

def _make_graph():
    nodes = [
        {"id": "paper:1", "kind": "paper", "label": "Mamba", "meta": {
            "paper_id": 1, "year": 2023, "importance": 0.9, "cluster": 0, "citation_count": 800}},
        {"id": "paper:2", "kind": "paper", "label": "Attention Is All You Need", "meta": {
            "paper_id": 2, "year": 2017, "importance": 0.85, "cluster": 0, "citation_count": 180000}},
        {"id": "paper:3", "kind": "paper", "label": "S4", "meta": {
            "paper_id": 3, "year": 2021, "importance": 0.6, "cluster": 0}},
        {"id": "paper:4", "kind": "paper", "label": "ViT", "meta": {
            "paper_id": 4, "year": 2020, "importance": 0.4, "cluster": 1}},
        {"id": "paper:5", "kind": "paper", "label": "孤立点", "meta": {
            "paper_id": 5, "year": 2024, "importance": 0.05, "cluster": -1}},
    ]
    edges = [
        {"source": "paper:1", "target": "paper:2", "kind": "cites", "weight": 1.2,
         "contexts": [{"section": "intro", "context": "We replace attention with selective SSM", "intent": "background"}]},
        {"source": "paper:3", "target": "paper:2", "kind": "cites", "weight": 1.0},
        {"source": "paper:1", "target": "paper:3", "kind": "related", "weight": 0.7},
        {"source": "paper:2", "target": "paper:4", "kind": "bibcoupling", "weight": 0.3},
        {"source": "paper:2", "target": "paper:5", "kind": "cocitation", "weight": 0.2},
    ]
    return {"nodes": nodes, "edges": edges, "stats": {}}


class _FakePaper:
    """最小 Paper 替身：synthesize 只读这些属性。"""

    def __init__(self, pid, abstract=None, citation_count=None, digest=None):
        self.id = pid
        self.abstract = abstract
        self.citation_count = citation_count
        self.graph_digest = digest


def _fake_papers():
    return {
        1: _FakePaper(1, abstract="Selective state space models with linear scaling.", citation_count=800),
        2: _FakePaper(2, abstract="Transformer replaces recurrence with attention.", citation_count=180000,
                      digest={"summary": "提出 Transformer，全面替代 RNN"}),
        # 3 无摘要但有速读 digest：验证回退路径
        3: _FakePaper(3, digest={"summary": "结构化状态空间建模长程依赖"}),
        4: _FakePaper(4),
        5: _FakePaper(5),
    }


# ── 输入装配 ────────────────────────────────────────────────────────────────

def test_cites_edges_only_undirected_kinds_excluded():
    graph = _make_graph()
    items = _cites_edges(graph["nodes"], graph["edges"], max_edges=10)
    keys = [it["key"] for it in items]
    assert keys == ["paper:1>paper:2", "paper:3>paper:2"]
    # 有引用上下文的边排前面（证据充分者优先）
    assert items[0]["contexts"]


def test_node_inputs_rank_by_importance_and_clip():
    graph = _make_graph()
    items = _node_inputs(graph["nodes"], _fake_papers(), max_nodes=3)
    assert [it["id"] for it in items] == ["paper:1", "paper:2", "paper:3"]
    assert items[0]["abstract"].startswith("Selective")
    # 有召回摘要的论文优先用摘要
    assert items[1]["abstract"].startswith("Transformer replaces")
    # 无召回摘要的论文回退到速读 summary
    assert items[2]["abstract"].startswith("结构化状态空间")
    # max_nodes 截断：重要性最低的孤立点被裁掉
    assert "paper:5" not in {it["id"] for it in _node_inputs(graph['nodes'], _fake_papers(), max_nodes=3)}


def test_clusters_drop_tiny_and_isolated():
    graph = _make_graph()
    clusters = _clusters(graph["nodes"], {})
    ids = [c["cluster_id"] for c in clusters]
    assert 0 in ids          # 3 篇的簇保留
    assert 1 not in ids       # 1 篇的簇不成流派
    assert -1 not in ids      # 孤立点不进流派


# ── LLM 输出归一化 ──────────────────────────────────────────────────────────

def test_normalize_node_semantic_invalid_enum_and_index():
    batch = _node_inputs(_make_graph()["nodes"], _fake_papers(), max_nodes=10)
    ok = _normalize_node_semantic(
        {"i": 1, "role": "rising_star", "takeaway": "x" * 99, "techniques": ["SSM", "线性复杂度", "硬件感知", "多余"]}, batch)
    assert ok[0] == "paper:2"
    assert ok[1].role == "rising_star"
    assert len(ok[1].short_takeaway) <= 61      # 截断到 60 + 省略号
    assert len(ok[1].key_techniques) == 3       # 最多 3 个标签
    bad_role = _normalize_node_semantic({"i": 0, "role": "genius", "takeaway": "t"}, batch)
    assert bad_role[1].role == "regular"        # 越界枚举回落 regular
    oob = _normalize_node_semantic({"i": 99, "role": "survey"}, batch)
    assert oob is None                          # 索引越界丢弃


def test_normalize_edge_semantic_invalid_relation():
    graph = _make_graph()
    batch = _cites_edges(graph["nodes"], graph["edges"], max_edges=10)
    ok = _normalize_edge_semantic({"i": 0, "relation": "replaces", "summary": "s"}, batch)
    assert ok[0] == "paper:1>paper:2" and ok[1].relation_type == "replaces"
    bad = _normalize_edge_semantic({"i": 0, "relation": "mystery"}, batch)
    assert bad[1].relation_type == "cites"      # 越界关系回落普通引用


def test_normalize_briefing_flexible_keys():
    b = _normalize_briefing({
        "domain_problem": "长序列建模的效率与表达力矛盾",
        "timeline": [{"year": 2017, "event": "Transformer"}, {"year": "", "event": ""}],
        "benchmark_standards": ["Long Range Arena"],
        "recent_hotspots": ["SSM 稀疏化"],
        "open_challenges": ["联想记忆弱"],
        "markdown": "# 综述\n正文",
    })
    assert b.domain_problem.startswith("长序列")
    assert [m.year for m in b.milestone_timeline] == ["2017"]   # 空条目剔除
    assert b.benchmark_standards == ["Long Range Arena"]
    assert b.markdown_report.startswith("# 综述")


# ── 主流程（monkeypatch LLM）────────────────────────────────────────────────

class _FakeLLM:
    """按调用次数返回预设 JSON 的假 _llm_json。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, cfg, system, user, *, max_tokens, temperature=0.15):
        self.calls.append(system[:24])
        if not self.responses:
            raise RuntimeError("no more canned responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def patch_llm(monkeypatch):
    def _patch(fake):
        import backend.services.deep_research.domain_synthesizer as mod
        monkeypatch.setattr(mod, "_llm_json", fake)
    return _patch


@pytest.fixture
def analysis_cfg(monkeypatch):
    """提供可用解读配置（有 key）。"""
    import backend.services.deep_research.domain_synthesizer as mod
    monkeypatch.setattr(mod, "synthesize_config", lambda: {
        "enabled": True, "max_nodes": 60, "max_edges": 60, "node_batch": 15, "edge_batch": 20,
    })
    import backend.services.ai_config as ai_config
    monkeypatch.setattr(
        "backend.services.ai_config.get_analysis_config",
        lambda: {"api_key": "k", "base_url": "https://api.openai.com/v1", "model": "gpt-test"},
    )
    # synthesizer 内部是 from ..ai_config import get_analysis_config（局部导入），patch 模块属性即可


def test_synthesize_skips_without_api_key(monkeypatch):
    import backend.services.ai_config as ai_config
    monkeypatch.setattr(ai_config, "get_analysis_config", lambda: {"api_key": "", "base_url": "", "model": "m"})
    graph = _make_graph()
    result = asyncio.run(synthesize_domain(graph, _fake_papers()))
    assert result is None
    assert all("semantic" not in n for n in graph["nodes"])


def test_synthesize_full_run_and_graph_enrichment(patch_llm, analysis_cfg):
    fake = _FakeLLM([
        {"papers": [
            {"i": 0, "role": "rising_star", "takeaway": "选择性状态空间实现线性长序列", "techniques": ["SSM"]},
            {"i": 1, "role": "landmark", "takeaway": "注意力全面替代循环网络", "techniques": ["Attention"]},
        ]},
        {"edges": [
            {"i": 0, "relation": "replaces", "summary": "以 SSM 替换注意力解决二次方开销"},
        ]},
        {"paradigms": [
            {"cluster": 0, "name": "状态空间模型 (SSM) 路线", "philosophy": "线性复杂度建模长程依赖",
             "pros": ["推理快"], "cons": ["联想记忆弱"], "representatives": ["paper:1", "paper:3"]},
        ]},
        {"domain_problem": "效率与表达力矛盾", "timeline": [{"year": "2017", "event": "Transformer"}],
         "benchmarks": ["LRA"], "hotspots": ["SSM"], "challenges": ["记忆"],
         "markdown": "# 领域综述"},
    ])
    patch_llm(fake)
    graph = _make_graph()
    synthesis = asyncio.run(synthesize_domain(graph, _fake_papers()))
    assert synthesis is not None
    assert synthesis.enriched_nodes["paper:1"].role == "rising_star"
    assert synthesis.enriched_nodes["paper:2"].role == "landmark"
    assert synthesis.enriched_edges["paper:1>paper:2"].relation_type == "replaces"
    assert synthesis.paradigms[0].representative_node_ids == ["paper:1", "paper:3"]
    assert synthesis.briefing.domain_problem == "效率与表达力矛盾"
    assert synthesis.partial is False
    # 图谱就地 enrichment：节点/边带上 semantic
    assert graph["nodes"][0]["semantic"]["role"] == "rising_star"
    assert graph["edges"][0]["semantic"]["relation_type"] == "replaces"
    assert "semantic" not in graph["edges"][2]      # related 边不 enrichment
    assert synthesis.stats["nodes_labeled"] == 2


def test_synthesize_partial_when_one_task_fails(patch_llm, analysis_cfg):
    fake = _FakeLLM([
        {"papers": [{"i": 0, "role": "benchmark", "takeaway": "提出基准", "techniques": []}]},
        RuntimeError("edge task exploded"),           # Task 2 失败
        {"paradigms": []},                            # Task 3 正常（空）
        RuntimeError("briefing exploded"),            # Task 4 失败
    ])
    patch_llm(fake)
    graph = _make_graph()
    import asyncio
    synthesis = asyncio.run(synthesize_domain(graph, _fake_papers()))
    assert synthesis is not None
    assert synthesis.partial is True
    assert synthesis.enriched_nodes["paper:1"].role == "benchmark"
    assert synthesis.enriched_edges == {}
    assert synthesis.briefing is None


def test_synthesize_all_fail_returns_none(patch_llm, analysis_cfg):
    fake = _FakeLLM([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    patch_llm(fake)
    graph = _make_graph()
    import asyncio
    assert asyncio.run(synthesize_domain(graph, _fake_papers())) is None


def test_apply_semantics_idempotent_for_missing_entries():
    graph = _make_graph()
    syn = GraphSynthesis(
        enriched_nodes={"paper:9": NodeSemantic(role="survey", short_takeaway="x")},
        enriched_edges={"paper:9>paper:8": EdgeSemantic(relation_type="inspires")},
    )
    apply_semantics_to_graph(graph, syn)
    assert all("semantic" not in n for n in graph["nodes"])   # 不存在的 id 不产生标注


def test_synthesize_config_defaults(monkeypatch):
    cfg = synthesize_config()
    assert cfg["enabled"] is True
    assert cfg["max_nodes"] > 0 and cfg["node_batch"] > 0

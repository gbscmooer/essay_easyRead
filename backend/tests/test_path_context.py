from backend.services.deep_research.path_context import build_path_context, parse_paper_node_id


def test_parse_paper_node_id_accepts_prefixed_and_legacy_ids():
    assert parse_paper_node_id("paper:123") == 123
    assert parse_paper_node_id("123") == 123
    assert parse_paper_node_id(123) == 123
    assert parse_paper_node_id("paper:not-an-id") is None
    assert parse_paper_node_id(True) is None


def test_build_path_context_finds_paths_between_retrieved_papers():
    graph = {
        "nodes": [
            {"id": "paper:1", "kind": "paper", "label": "Seed Transformer", "meta": {"cluster": 0, "cluster_label": "检测", "importance": 0.9}},
            {"id": "paper:2", "kind": "paper", "label": "Citation Bridge", "meta": {"cluster": 0, "cluster_label": "检测", "importance": 0.7}},
            {"id": "paper:3", "kind": "paper", "label": "Method Bridge", "meta": {"cluster": 1, "cluster_label": "表示学习", "importance": 0.6}},
            {"id": "paper:4", "kind": "paper", "label": "Target GraphRAG", "meta": {"cluster": 1, "cluster_label": "表示学习", "importance": 0.8}},
        ],
        "edges": [
            {"source": "paper:1", "target": "paper:2", "kind": "cites", "weight": 1.2},
            {"source": "paper:2", "target": "paper:3", "kind": "similar", "weight": 0.8},
            {"source": "paper:3", "target": "paper:4", "kind": "shared-method", "weight": 1.1},
        ],
    }
    hits = [
        {"paper_id": 1, "paper_title": "Seed Transformer", "snippet": "seed"},
        {"paper_id": 4, "paper_title": "Target GraphRAG", "snippet": "target"},
    ]

    result = build_path_context(graph, hits, max_paths=2, max_path_nodes=4)

    assert result.evidence
    evidence = result.evidence[0]
    assert evidence["path"] == ["paper:1", "paper:2", "paper:3", "paper:4"]
    assert evidence["paper_ids"] == [1, 2, 3, 4]
    assert [edge["kind"] for edge in evidence["edges"]] == ["cites", "similar", "shared-method"]
    assert "PathRAG 图谱路径证据" in result.context
    assert "Seed Transformer" in result.context
    assert "Target GraphRAG" in result.context


def test_build_path_context_handles_empty_graph_and_missing_paths():
    hits = [
        {"paper_id": 1, "paper_title": "A"},
        {"paper_id": 2, "paper_title": "B"},
    ]

    empty = build_path_context(None, hits)
    assert empty.context == ""
    assert empty.evidence == []

    disconnected = build_path_context(
        {
            "nodes": [
                {"id": "paper:1", "kind": "paper", "label": "A", "meta": {}},
                {"id": "paper:2", "kind": "paper", "label": "B", "meta": {}},
            ],
            "edges": [],
        },
        hits,
    )
    assert disconnected.evidence == []
    assert "命中论文图谱位置" in disconnected.context

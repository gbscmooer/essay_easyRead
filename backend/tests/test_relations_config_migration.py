import asyncio
import copy
import json
from unittest.mock import Mock

import pytest

from backend.services import ai_config


LEGACY_WEIGHTS = {
    "topic": 0.18, "purpose": 0.24, "method": 0.16, "term": 0.14,
    "dataset": 0.06, "baseline": 0.04, "bibcoupling": 0.12, "cocitation": 0.06,
}


@pytest.mark.parametrize("method,weights,migrates", [
    (None, LEGACY_WEIGHTS, True),
    (None, {**LEGACY_WEIGHTS, "topic": 0.19}, False),
    (None, {"topic": 0.18}, False),
    (None, {name: 0 for name in LEGACY_WEIGHTS}, False),
    ("weighted_rrf_v1", LEGACY_WEIGHTS, False),
])
def test_load_migrates_only_unversioned_shipped_weights(tmp_path, monkeypatch, method, weights, migrates):
    raw = {"relations": {"calibrate": True, "weights": weights},
           "research": {"backbone_alpha": 0.12},
           "analysis": {"api_key": "test-placeholder"}}
    if method:
        raw["relations"]["method"] = method
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    writer = Mock(side_effect=AssertionError("Loading must never save config"))
    monkeypatch.setattr(ai_config, "dump_file_config", writer)

    loaded = ai_config.load_config(str(path))
    expected = copy.deepcopy(ai_config.DEFAULT_CONFIG["relations"]["weights"])
    if not migrates:
        expected.update(weights)
    assert loaded["relations"] == {
        "method": "weighted_rrf_v1", "calibrate": False, "weights": expected,
    }
    assert loaded["analysis"]["api_key"] == "test-placeholder"
    assert loaded["research"]["backbone_alpha"] == 0.12
    assert path.read_bytes() == before
    writer.assert_not_called()
    assert ai_config.load_config(str(path)) == loaded


def test_new_defaults_are_independent_and_equal(tmp_path):
    config = ai_config.load_config(str(tmp_path / "missing.json"))
    assert set(config["relations"]["weights"].values()) == {0.125}
    assert config["research"]["backbone_alpha"] == 0.05
    assert config["relations"]["calibrate"] is False
    config["relations"]["weights"]["topic"] = 1
    assert ai_config.DEFAULT_CONFIG["relations"]["weights"]["topic"] == 0.125


def test_config_api_retains_custom_weights_and_method_on_partial_update(monkeypatch):
    from backend.routers.analysis import AppConfig, get_config, update_config

    prior = copy.deepcopy(ai_config.DEFAULT_CONFIG)
    prior["relations"]["weights"] = dict(LEGACY_WEIGHTS)
    prior["research"]["recall"]["weights"]["forward"] = 0.6
    prior["research"]["hybrid_retrieval"]["weights"]["bm25"] = 0.4
    prior["research"]["hybrid_retrieval"]["graph"]["top_k"] = 21
    monkeypatch.setattr(ai_config, "load_config", lambda: copy.deepcopy(prior))
    writer = Mock()
    monkeypatch.setattr(ai_config, "save_config", writer)
    response = asyncio.run(get_config())
    assert response.relations.method == "weighted_rrf_v1"
    assert response.relations.calibrate is False

    request = AppConfig.model_validate({
        "relations": {"calibrate": True, "weights": {"topic": 0.7}},
        "research": {"recall": {"weights": {"pdf_ref": 0.3}},
                     "hybrid_retrieval": {"weights": {"dense": 0.2}}},
    })
    asyncio.run(update_config(request))
    saved = writer.call_args.args[0]
    assert saved["relations"]["weights"] == {**LEGACY_WEIGHTS, "topic": 0.7}
    assert saved["relations"]["method"] == "weighted_rrf_v1"
    assert saved["relations"]["calibrate"] is False
    assert saved["research"]["recall"]["weights"]["forward"] == 0.6
    assert saved["research"]["recall"]["weights"]["pdf_ref"] == 0.3
    assert saved["research"]["hybrid_retrieval"]["weights"]["bm25"] == 0.4
    assert saved["research"]["hybrid_retrieval"]["weights"]["dense"] == 0.2
    assert saved["research"]["hybrid_retrieval"]["graph"]["top_k"] == 21
    assert saved["research"]["recall"]["method"] == "weighted_rrf_v1"
    assert saved["research"]["hybrid_retrieval"]["method"] == "weighted_rrf_v1"


@pytest.mark.parametrize("name,legacy", [
    ("recall", {"pdf_ref": 1.0, "backward": 1.0, "forward": 0.9, "recommend": 0.85, "graph_ppr": 0.8}),
    ("hybrid_retrieval", {"dense": 1.0, "bm25": 0.95, "graph_ppr": 0.8}),
])
@pytest.mark.parametrize("variant", ["shipped", "custom", "partial", "versioned"])
def test_retrieval_default_migration(tmp_path, monkeypatch, name, legacy, variant):
    weights = dict(legacy)
    if variant == "custom":
        weights["graph_ppr"] = 0
    elif variant == "partial":
        weights = {"graph_ppr": 0.8}
    section = {"weights": weights, "rrf_k": 41}
    if variant == "versioned":
        section["method"] = "weighted_rrf_v1"
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"research": {name: section}}))
    writer = Mock(side_effect=AssertionError("No writes during migration"))
    monkeypatch.setattr(ai_config, "dump_file_config", writer)
    before = path.read_bytes()
    loaded = ai_config.load_config(str(path))["research"][name]
    expected = {channel: 1.0 for channel in legacy}
    if variant != "shipped":
        expected.update(weights)
    assert loaded["weights"] == expected
    assert loaded["rrf_k"] == 41
    assert loaded["method"] == "weighted_rrf_v1"
    assert path.read_bytes() == before
    writer.assert_not_called()


@pytest.mark.parametrize("alpha,method,expected", [
    (0.25, None, 0.05), (0.12, None, 0.12), (0.25, "disparity_v1", 0.25),
])
def test_backbone_threshold_migration(tmp_path, monkeypatch, alpha, method, expected):
    research = {"backbone_alpha": alpha}
    if method:
        research["backbone_method"] = method
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"research": research}))
    before = path.read_bytes()
    writer = Mock(side_effect=AssertionError("No writes during migration"))
    monkeypatch.setattr(ai_config, "dump_file_config", writer)
    loaded = ai_config.load_config(str(path))["research"]
    assert loaded["backbone_alpha"] == expected
    assert loaded["backbone_method"] == "mutual_knn_with_disparity_annotation_v1"
    assert path.read_bytes() == before
    writer.assert_not_called()

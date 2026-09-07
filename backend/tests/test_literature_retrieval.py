import asyncio

import numpy as np
import pytest

from backend.services.deep_research import literature_retrieval as r


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    r._vectors.clear()
    monkeypatch.setattr(r, "get_embedding_config", lambda: {"provider": "stub", "model": "test", "batch_size": 2})
    monkeypatch.setattr(r, "get_reranker_config", lambda: {"enabled": False})
    async def missing(*args):
        raise RuntimeError("no model")
    monkeypatch.setattr(r, "embed_texts", missing)
    monkeypatch.setattr(r, "embed_query", missing)


def papers():
    return [{"key": "a", "title": "Cancer diagnosis", "abstract": "tumor detection", "citation_count": 0},
            {"key": "b", "title": "Database transactions", "abstract": "SQL databases", "citation_count": 99999},
            {"key": "c", "title": "Cancer diagnosis", "abstract": "tumor detection", "citation_count": 4}]


def test_bm25_is_not_popularity_and_empty_corpus_is_valid():
    assert r.lexical_order(papers(), "tumor detection")[0]["key"] == "a"
    assert r.lexical_scores(["", ""], "test").tolist() == [0, 0]
    assert r.lexical_scores(["SQL", "tumor"], "unmatched").tolist() == [0, 0]


def test_fallback_and_manual_selection():
    selected, meta = asyncio.run(r.retrieve_materials(papers(), "tumor", ["b", "b", "unknown"], limit=2))
    assert [p["key"] for p in selected] == ["b", "a"]
    assert meta["channels"] == ["bm25plus"] and meta["warnings"]


def test_semantic_recall_across_languages_and_ties(monkeypatch):
    async def docs(texts):
        return [[1, 0] if "Cancer" in text else [0, 1] for text in texts]
    async def query(text):
        return [1, 0]
    monkeypatch.setattr(r, "embed_texts", docs)
    monkeypatch.setattr(r, "embed_query", query)
    selected, meta = asyncio.run(r.retrieve_materials(papers(), "肿瘤识别", limit=2))
    assert [p["key"] for p in selected] == ["a", "c"]
    assert "dense" in meta["channels"]
    reverse, _ = asyncio.run(r.retrieve_materials(list(reversed(papers())), "肿瘤识别", limit=2))
    assert [p["key"] for p in reverse] == ["a", "c"]


def test_rerank_can_recover_cross_language_document_and_preserves_pins(monkeypatch):
    monkeypatch.setattr(r, "get_reranker_config", lambda: {"enabled": True, "model": "stub"})
    async def ranked(query, texts, top_n):
        return [{"index": 1, "score": .9}, {"index": 1, "score": .8}, {"index": 999, "score": 1}]
    monkeypatch.setattr(r, "rerank", ranked)
    selected, meta = asyncio.run(r.retrieve_materials(papers(), "数据库", ["c"], limit=2))
    assert [p["key"] for p in selected] == ["c", "b"]
    assert "reranker" in meta["channels"]


def test_rerank_fallback_not_reported_as_model_success(monkeypatch):
    monkeypatch.setattr(r, "get_reranker_config", lambda: {"enabled": True})
    async def ranked(query, texts, top_n):
        return [{"index": i, "score": 0, "fallback": True} for i in range(len(texts))]
    monkeypatch.setattr(r, "rerank", ranked)
    selected, meta = asyncio.run(r.retrieve_materials(papers(), "tumor", limit=1))
    assert selected[0]["key"] == "a" and "reranker" not in meta["channels"]


def test_cached_document_vectors_are_bounded_by_model_identity(monkeypatch):
    calls = []
    async def docs(texts):
        calls.append(texts)
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr(r, "embed_texts", docs)
    asyncio.run(r.document_vectors(["a", "b"]))
    asyncio.run(r.document_vectors(["b", "a"]))
    assert len(calls) == 1
    monkeypatch.setattr(r, "get_embedding_config", lambda: {"provider": "stub", "model": "new"})
    asyncio.run(r.document_vectors(["a"]))
    assert len(calls) == 2


def test_invalid_embeddings_fall_back_to_standard_tfidf(monkeypatch):
    async def bad(texts):
        return [[float("nan"), 0] for _ in texts]
    monkeypatch.setattr(r, "embed_texts", bad)
    sims, meta = asyncio.run(r.paper_similarity(papers()))
    assert np.isfinite(sims).all() and np.allclose(sims.diagonal(), 0)
    assert sims[0, 2] > sims[0, 1]
    assert meta["representation"] == "sklearn_tfidf"


def test_bge_v15_uses_tokenizer_window_not_character_estimate(monkeypatch):
    from backend.services.deep_research import embedder
    calls = []
    class Tokenizer:
        def __call__(self, texts, **kwargs):
            calls.append(kwargs)
            return {"input_ids": [[1] * kwargs["max_length"] for _ in texts]}
        def batch_decode(self, tokens, **kwargs):
            return ["trimmed" for _ in tokens]
    monkeypatch.setattr(embedder, "_tokenizers", {"BAAI/bge-large-en-v1.5": Tokenizer()})
    result = asyncio.run(embedder._model_truncate(["long paper " * 1000], {
        "model": "BAAI/bge-large-en-v1.5", "max_input_tokens": 7000,
    }))
    assert result == ["trimmed"] and calls[0]["max_length"] == 510

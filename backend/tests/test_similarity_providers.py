import asyncio
from types import SimpleNamespace


def test_similarity_provider_registry_includes_cross_encoder():
    from backend.services.deep_research.similarity import get_similarity_provider, list_providers

    assert "cross_encoder" in list_providers()
    provider = get_similarity_provider({"provider": "cross_encoder"})
    assert provider.name == "cross_encoder"


def test_cross_encoder_falls_back_to_base_pairs(monkeypatch):
    from backend.services.deep_research.similarity.cross_encoder import CrossEncoderProvider

    class DummyPaper(SimpleNamespace):
        pass

    async def fake_base_score_pairs(self, papers, *, ws_id):
        return [(1, 2, 0.72)]

    monkeypatch.setattr(
        "backend.services.deep_research.similarity.bge_centroid.BgeCentroidProvider.score_pairs",
        fake_base_score_pairs,
    )
    monkeypatch.setattr(
        "backend.services.deep_research.similarity.cross_encoder.get_reranker_config",
        lambda: {"enabled": False},
    )

    provider = CrossEncoderProvider()
    result = asyncio.run(provider.score_pairs([DummyPaper(id=1), DummyPaper(id=2)], ws_id=1))

    assert result == [(1, 2, 0.72)]

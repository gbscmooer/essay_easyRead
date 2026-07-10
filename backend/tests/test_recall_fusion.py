"""RRF 融合单元测试 — 纯逻辑，零网络。"""

from __future__ import annotations

from backend.models.recall import RecallSource, build_recall_candidate
from backend.services.deep_research.fusion_ranker import (
    CHANNEL_BACKWARD,
    CHANNEL_FORWARD,
    CHANNEL_PDF_REF,
    CHANNEL_RECOMMEND,
    fuse_candidates,
    tag_channel,
)
from backend.services.paper_identity import build_canonical_id


def _cand(title: str, doi: str | None = None, *, source: RecallSource, rank: int, seed: str = "1"):
    c = build_recall_candidate(
        title=title,
        doi=doi,
        source=source,
        source_rank=rank,
        seed_paper_id=seed,
        citation_count=100 - rank,
    )
    assert c is not None
    return c


def test_cross_source_same_doi_merges_provenance():
    doi = "10.1234/merged.paper"
    backward = tag_channel(
        _cand("Merged Paper", doi, source=RecallSource.BACKWARD_REF, rank=0),
        CHANNEL_BACKWARD,
    )
    forward = tag_channel(
        _cand("Merged Paper", doi, source=RecallSource.FORWARD_CIT, rank=0),
        CHANNEL_FORWARD,
    )
    forward = forward.model_copy(
        update={"provenance": forward.provenance + ["provider:openalex", "method:forward_cit"]}
    )

    fused = fuse_candidates({
        CHANNEL_BACKWARD: [backward],
        CHANNEL_FORWARD: [forward],
    })

    assert len(fused) == 1
    assert fused[0].canonical_key == build_canonical_id(doi=doi)
    assert CHANNEL_BACKWARD in fused[0].channels
    assert CHANNEL_FORWARD in fused[0].channels
    assert "provider:openalex" in fused[0].provenance
    assert "method:backward_ref" in fused[0].provenance or any(
        "backward" in p for p in fused[0].provenance
    )


def test_rrf_order_stable_under_pool_key_iteration():
    """池子 dict 构造顺序不同，融合排名应一致。"""
    a = tag_channel(
        _cand("Paper A", "10.1234/a", source=RecallSource.BACKWARD_REF, rank=0),
        CHANNEL_BACKWARD,
    )
    b = tag_channel(
        _cand("Paper B", "10.1234/b", source=RecallSource.BACKWARD_REF, rank=1),
        CHANNEL_BACKWARD,
    )
    c = tag_channel(
        _cand("Paper C", "10.1234/c", source=RecallSource.RECOMMEND, rank=0),
        CHANNEL_RECOMMEND,
    )

    pools_ab = {CHANNEL_BACKWARD: [a, b], CHANNEL_RECOMMEND: [c]}
    pools_ba = {CHANNEL_RECOMMEND: [c], CHANNEL_BACKWARD: [a, b]}

    keys_ab = [f.canonical_key for f in fuse_candidates(pools_ab)]
    keys_ba = [f.canonical_key for f in fuse_candidates(pools_ba)]
    assert keys_ab == keys_ba


def test_rrf_not_pure_citation_count_order():
    """高 citation 但仅单源靠后的论文，应低于多源靠前的论文。"""
    high_cit = tag_channel(
        build_recall_candidate(
            title="High Citations Only",
            doi="10.1234/highcit",
            source=RecallSource.BACKWARD_REF,
            source_rank=9,
            citation_count=99999,
            seed_paper_id="1",
        ),
        CHANNEL_BACKWARD,
    )
    assert high_cit is not None
    multi_source = tag_channel(
        build_recall_candidate(
            title="Multi Source Winner",
            doi="10.1234/winner",
            source=RecallSource.BACKWARD_REF,
            source_rank=0,
            citation_count=10,
            seed_paper_id="1",
        ),
        CHANNEL_BACKWARD,
    )
    assert multi_source is not None
    multi_forward = tag_channel(
        build_recall_candidate(
            title="Multi Source Winner",
            doi="10.1234/winner",
            source=RecallSource.FORWARD_CIT,
            source_rank=0,
            citation_count=10,
            seed_paper_id="1",
        ),
        CHANNEL_FORWARD,
    )
    assert multi_forward is not None
    pdf_boost = tag_channel(
        build_recall_candidate(
            title="Multi Source Winner",
            doi="10.1234/winner",
            source=RecallSource.BACKWARD_REF,
            source_rank=0,
            seed_paper_id="1",
        ),
        CHANNEL_PDF_REF,
    )
    assert pdf_boost is not None

    fused = fuse_candidates({
        CHANNEL_BACKWARD: [multi_source, high_cit],
        CHANNEL_FORWARD: [multi_forward],
        CHANNEL_PDF_REF: [pdf_boost],
    })

    winner_key = build_canonical_id(doi="10.1234/winner")
    high_key = build_canonical_id(doi="10.1234/highcit")
    rank_map = {f.canonical_key: i for i, f in enumerate(fused)}
    assert rank_map[winner_key] < rank_map[high_key]

    # 单源 citation_count 排序会把 high_cit 放第一
    backward_only = sorted(
        [multi_source, high_cit],
        key=lambda c: c.citation_count or 0,
        reverse=True,
    )
    assert backward_only[0].canonical_key == high_key

"""paper_identity 规范化与 legacy 解析单元测试。"""

import hashlib

import pytest

from backend.services.paper_identity import (
    build_canonical_id,
    is_strong_canonical_id,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_s2_id,
    normalize_title,
    parse_legacy_external_id,
    title_similarity,
)


# ── normalize_doi ─────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1234/ABC.XYZ", "10.1234/abc.xyz"),
        ("https://doi.org/10.1234/abc", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("DOI:10.1234/abc", "10.1234/abc"),
        ("doi:10.1234/abc", "10.1234/abc"),
        ("  10.1234/abc  ", "10.1234/abc"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


# ── normalize_arxiv_id ────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("arxiv:1706.03762", "1706.03762"),
        ("arXiv:1706.03762v5", "1706.03762"),
        ("1706.03762v2", "1706.03762"),
        ("cs/0101001", "cs/0101001"),
        ("arxiv:cs/0101001v3", "cs/0101001"),
        ("", None),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert normalize_arxiv_id(raw) == expected


# ── normalize_openalex_id ─────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("W2741809807", "W2741809807"),
        ("w2741809807", "W2741809807"),
        ("openalex:W123", "W123"),
        ("https://openalex.org/W2741809807", "W2741809807"),
        ("http://www.openalex.org/w123", "W123"),
        ("2741809807", "W2741809807"),
        ("not-valid", None),
    ],
)
def test_normalize_openalex_id(raw, expected):
    assert normalize_openalex_id(raw) == expected


# ── normalize_s2_id ───────────────────────────────────────


def test_normalize_s2_id():
    uuid_val = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert normalize_s2_id(uuid_val) == uuid_val.lower()
    assert normalize_s2_id(f"s2:{uuid_val}") == uuid_val.lower()
    hex40 = "a" * 40
    assert normalize_s2_id(hex40) == hex40
    assert normalize_s2_id("not-a-s2-id") is None


# ── normalize_title ───────────────────────────────────────


def test_normalize_title():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("  Foo   Bar  ") == "foo bar"
    assert normalize_title("") is None


# ── build_canonical_id 优先级 ─────────────────────────────


def test_build_canonical_id_priority():
    assert build_canonical_id(
        doi="10.1234/abc",
        arxiv_id="1706.03762",
        openalex_id="W123",
        s2_id="a" * 40,
        title="Some Title",
    ) == "doi:10.1234/abc"

    assert build_canonical_id(
        arxiv_id="1706.03762",
        openalex_id="W123",
        title="Some Title",
    ) == "arxiv:1706.03762"

    assert build_canonical_id(
        openalex_id="W123",
        title="Some Title",
    ) == "openalex:W123"

    assert build_canonical_id(
        s2_id="a" * 40,
        title="Some Title",
    ) == f"s2:{'a' * 40}"

    cid = build_canonical_id(title="Attention Is All You Need")
    norm = normalize_title("Attention Is All You Need")
    expected_fp = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    assert cid == f"titlefp:{expected_fp}"
    assert not is_strong_canonical_id(cid)


def test_build_canonical_id_returns_none_without_inputs():
    assert build_canonical_id() is None


# ── parse_legacy_external_id ──────────────────────────────


@pytest.mark.parametrize(
    "external_id,expected_key,expected_val",
    [
        ("DOI:10.1234/abc", "doi", "10.1234/abc"),
        ("doi:10.1234/abc", "doi", "10.1234/abc"),
        ("10.1234/abc", "doi", "10.1234/abc"),
        ("arxiv:1706.03762v3", "arxiv_id", "1706.03762"),
        ("1706.03762", "arxiv_id", "1706.03762"),
        ("cs/0101001", "arxiv_id", "cs/0101001"),
        ("W2741809807", "openalex_id", "W2741809807"),
        ("openalex:W99", "openalex_id", "W99"),
        ("https://openalex.org/W99", "openalex_id", "W99"),
        (
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "s2_id",
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        ),
    ],
)
def test_parse_legacy_external_id(external_id, expected_key, expected_val):
    parsed = parse_legacy_external_id(external_id)
    assert parsed[expected_key] == expected_val


def test_parse_legacy_external_id_empty():
    assert parse_legacy_external_id(None) == {
        "doi": None, "arxiv_id": None, "openalex_id": None, "s2_id": None,
    }
    assert parse_legacy_external_id("") == {
        "doi": None, "arxiv_id": None, "openalex_id": None, "s2_id": None,
    }


# ── title_similarity ──────────────────────────────────────


def test_title_similarity_jaccard():
    assert title_similarity(
        "Attention Is All You Need",
        "attention is all you need!",
    ) == 1.0
    sim = title_similarity("graph neural networks", "graph attention networks")
    assert 0.0 < sim < 1.0
    assert title_similarity(None, "foo") == 0.0
    assert title_similarity("", "") == 0.0


# ── is_strong_canonical_id ────────────────────────────────


def test_is_strong_canonical_id():
    assert is_strong_canonical_id("doi:10.1234/abc") is True
    assert is_strong_canonical_id("titlefp:abc123") is False
    assert is_strong_canonical_id(None) is False

import asyncio

from sqlmodel import Session

from backend.models.paper import Paper
from backend.services import paper_metadata as metadata
from backend.services.paper_identity import PaperWork, register_stub, apply_identity_to_paper
from backend.services.database import list_papers, list_paper_facets


def test_provider_metadata_survives_registration_and_identity_merge(session):
    paper = register_stub(session, PaperWork(title="An Elsevier paper", doi="10.1234/test", venue="Information Fusion",
        bibliography={"publication_type": "Journal", "subjects": ["Computer Vision"], "providers": ["scopus"], "scopus_id": "123"}))
    apply_identity_to_paper(paper, PaperWork(doi="10.1234/test", openalex_id="W123"))
    session.add(paper)
    session.commit()
    result = metadata.view(paper)
    assert result["venue"] == "Information Fusion"
    assert result["publication_type"] == "journal"
    assert result["subjects"] == ["Computer Vision"]
    assert "scopus" in result["providers"]
    assert paper.source_ids["scopus_id"] == "123"


def test_manual_corrections_and_clears_survive_automatic_refresh():
    paper = Paper(filename="paper.pdf", pdf_path="", file_size=0, category="cs.CV")
    metadata.merge(paper, {"title": "Correct title", "venue": "My conference", "subjects": ["Vision"], "year": 2024}, "manual")
    metadata.merge(paper, {"title": "Wrong title", "venue": "Other", "subjects": ["Wrong"], "year": 2020}, "openalex")
    assert paper.title == "Correct title" and paper.year == 2024
    assert metadata.view(paper)["subjects"] == ["Vision"]
    metadata.merge(paper, {"venue": "", "year": None, "subjects": [], "publication_type": "unknown"}, "manual")
    assert paper.venue == "" and paper.year is None
    assert metadata.view(paper)["subjects"] == []
    apply_identity_to_paper(paper, PaperWork(venue="Old venue", year=2020))
    assert paper.venue == "" and paper.year is None
    assert paper.title_normalized == "correct title"


def test_formal_publication_survives_repository_refresh():
    paper = Paper(filename="paper.pdf", pdf_path="", file_size=0)
    metadata.merge(paper, {"venue": "ICLR 2023", "year": 2023, "publication_type": "conference"}, "scopus")
    metadata.merge(paper, {"year": 2022, "publication_type": "preprint", "subjects": ["人工智能"]}, "openalex")
    metadata.merge(paper, {"venue": "ICLR 2023", "year": 2022, "publication_type": "preprint"}, "arxiv")
    assert paper.year == 2023 and paper.venue == "ICLR 2023"
    assert metadata.view(paper)["publication_type"] == "conference"
    assert metadata.view(paper)["subjects"] == ["人工智能"]


def test_openalex_uses_primary_subject_only():
    result = metadata.openalex_metadata({"primary_topic": {"subfield": {"display_name": "Artificial Intelligence"}},
        "topics": [{"subfield": {"display_name": "Materials Chemistry"}}]})
    assert result["subjects"] == ["人工智能"]


def test_fusion_preserves_formal_publication_when_richer_candidate_is_preprint():
    from backend.models.recall import build_recall_candidate, RecallSource
    from backend.services.deep_research.fusion_ranker import fuse_candidates
    from backend.services.deep_research.stages.expand import _fused_to_work
    formal = build_recall_candidate(title="A paper", doi="10.1234/fusion", year=2023, source=RecallSource.BACKWARD_REF,
        source_rank=1, extra={"venue_name": "ICLR 2023", "publication_type": "conference"}, provenance=["provider:scopus"])
    preprint = build_recall_candidate(title="A paper", doi="10.1234/fusion", year=2022, abstract="Long abstract", authors="An author",
        source=RecallSource.FORWARD_CIT, source_rank=1, extra={"publication_type": "preprint", "subjects": ["人工智能"]}, provenance=["provider:openalex"])
    fused = fuse_candidates({"backward": [formal], "forward": [preprint]})[0]
    work = _fused_to_work(fused, Paper(id=1, filename="seed.pdf", pdf_path="", file_size=0), 1)
    assert work.venue == "ICLR 2023" and work.year == 2023
    assert work.bibliography["publication_type"] == "conference"
    assert set(work.bibliography["providers"]) == {"scopus", "openalex"}
    assert work.bibliography["field_sources"]["venue"] == "scopus"
    assert work.bibliography["field_sources"]["subjects"] == "openalex"


def test_facets_filters_count_before_pagination(session):
    for index in range(24):
        paper = Paper(filename=f"{index}.pdf", title=f"Paper {index}", pdf_path="", file_size=0, source="expanded")
        metadata.merge(paper, {"venue": "Information Fusion", "subjects": ["Computer Vision"], "publication_type": "journal", "year": 2025}, "scopus")
        session.add(paper)
    session.add(Paper(filename="manual.pdf", pdf_path="", file_size=0, category="cs.CV", venue="Unknown", year=202))
    session.commit()
    facets = list_paper_facets(session, "all")
    assert "Computer Vision" in facets["subjects"] and "计算机视觉" in facets["subjects"]
    assert "Unknown" not in facets["venues"] and 202 not in facets["years"]
    rows, total = list_papers(session, source="all", subject="Computer Vision", venue="Information Fusion", publication_type="journal", provider="scopus", page=2)
    assert total == 24 and len(rows) == 4
    rows, total = list_papers(session, source="all", venue="__missing__")
    assert total == 1 and rows[0]["venue"] is None


def test_update_endpoint_and_search_use_same_metadata(client, session):
    paper = Paper(filename="source.pdf", title="A paper", pdf_path="", file_size=0, source="expanded")
    session.add(paper)
    session.commit()
    session.refresh(paper)
    payload = {"title": "A paper", "venue": "Information Fusion", "publication_type": "journal", "subjects": ["Vision"], "year": 2024}
    url = f"/api/papers/{paper.id}/metadata"
    response = client.post(url, json=payload)
    assert response.status_code == 200 and response.json()["publication_type"] == "journal"
    assert client.get(f"/api/papers/{paper.id}").json()["subjects"] == ["Vision"]
    params = {"subject": "Vision", "venue": "Information Fusion", "publication_type": "journal"}
    assert client.get("/api/papers", params={**params, "source": "all"}).json()["total"] == 1
    assert client.get("/api/papers/smart-search", params={**params, "q": "paper", "include_expanded": True}).json()["total"] == 1
    assert client.get("/api/papers/smart-search", params={**params, "q": "paper"}).json()["total"] == 0
    assert client.post(url, json={**payload, "title": " "}).status_code == 422
    from backend.config import settings
    cors = client.options(url, headers={"Origin": settings.frontend_origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert cors.status_code == 200 and cors.headers["access-control-allow-origin"] == settings.frontend_origin


def test_metadata_parsers_keep_publication_separate_from_provider():
    assert metadata.scopus_publication_type({"subtypeDescription": "Article", "prism:aggregationType": "Journal"}) == "journal"
    item = {"DOI": "10.1/abc", "title": ["A paper"], "type": "proceedings-article", "publisher": "Elsevier",
            "container-title": ["A conference"], "published-print": {"date-parts": [[2025]]}}
    parsed = metadata.crossref_metadata(item)
    assert parsed["publication_type"] == "conference" and parsed["publisher"] == "Elsevier"
    oa = metadata.openalex_metadata({"title": "A paper", "type": "preprint", "primary_location": {"source": {"type": "repository", "display_name": "arXiv"}},
        "topics": [{"subfield": {"display_name": "Computer Vision"}}]})
    assert oa["venue"] is None and oa["publication_type"] == "preprint"


def test_existing_doi_enriches_without_pdf(engine, session, monkeypatch):
    from backend.config import settings
    from backend.services import ai_config
    from backend.services.deep_research import crossref_client
    from backend.services.deep_research.providers.openalex import OpenAlexProvider
    monkeypatch.setattr(settings, "elsevier_api_key", "")
    monkeypatch.setattr(ai_config, "get_research_config", lambda: {})

    async def no_oa(*args, **kwargs):
        return None

    async def cr(*args, **kwargs):
        return {"message": {"DOI": "10.1234/a", "title": ["Publisher title"], "type": "journal-article", "container-title": ["Information Fusion"], "publisher": "Elsevier", "subject": ["Artificial Intelligence"]}}

    monkeypatch.setattr(OpenAlexProvider, "_safe_get", no_oa)
    monkeypatch.setattr(crossref_client, "_crossref_get", cr)
    paper = Paper(filename="source.pdf", pdf_path="", file_size=0, title="Old title", doi="10.1234/a")
    session.add(paper)
    session.commit()
    session.refresh(paper)
    assert asyncio.run(metadata.enrich(paper.id, engine=engine))
    session.refresh(paper)
    assert paper.venue == "Information Fusion" and metadata.view(paper)["publisher"] == "Elsevier"

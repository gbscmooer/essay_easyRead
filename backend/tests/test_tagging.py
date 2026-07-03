import pytest
from sqlmodel import select


@pytest.mark.asyncio
async def test_apply_auto_tags_to_paper(session, monkeypatch):
    from backend.models.paper import Paper
    from backend.models.tag import PaperTag, Tag
    from backend.services.tagging import apply_auto_tags_to_paper

    paper = Paper(
        filename="tagged.pdf",
        pdf_path="/tmp/tagged.pdf",
        file_size=1,
        title="Tagged Paper",
        status="done",
        source="uploaded",
    )
    session.add(paper)
    session.commit()
    session.refresh(paper)

    async def fake_auto_tag_paper(**_kwargs):
        return ["Transformer", "Vision", "Transformer"]

    monkeypatch.setattr("backend.services.tagging.auto_tag_paper", fake_auto_tag_paper)

    applied = await apply_auto_tags_to_paper(
        paper.id,
        {
            "title": "Tagged Paper",
            "one_sentence": "A paper about transformers for vision.",
            "method": {"overview": "Use a transformer backbone."},
            "venue": "CVPR",
        },
        {"api_key": "test", "base_url": "https://example.com/v1", "model": "demo"},
    )

    assert applied == ["Transformer", "Vision"]

    tags = session.exec(select(Tag)).all()
    assert {tag.name for tag in tags} == {"Transformer", "Vision"}

    links = session.exec(select(PaperTag).where(PaperTag.paper_id == paper.id)).all()
    assert len(links) == 2

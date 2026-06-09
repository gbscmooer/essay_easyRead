import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, create_engine, select
<<<<<<< HEAD
from sqlalchemy import func
=======
>>>>>>> worktree-agent-a598e72a4d81f8239
from datetime import datetime, timezone

from ..models.tag import Tag, PaperTag
from ..models.paper import Paper
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags", tags=["tags"])

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = Field("#3b82f6", pattern=r"^#[0-9a-fA-F]{6}$")


class TagResponse(BaseModel):
    id: int
    name: str
    color: str
    paper_count: int = 0


class PaperTagRequest(BaseModel):
    tag_id: int


@router.get("", response_model=list[TagResponse])
async def list_tags() -> list[TagResponse]:
    engine = _get_engine()
    with Session(engine) as session:
        tags = session.exec(select(Tag).order_by(Tag.name)).all()
<<<<<<< HEAD
        # Batch query: get all counts in one GROUP BY instead of N queries
        count_stmt = select(PaperTag.tag_id, func.count().label('cnt')).group_by(PaperTag.tag_id)
        counts = {row[0]: row[1] for row in session.exec(count_stmt).all()}
        result = []
        for tag in tags:
            result.append(TagResponse(
                id=tag.id, name=tag.name, color=tag.color, paper_count=counts.get(tag.id, 0),
=======
        result = []
        for tag in tags:
            count = len(session.exec(
                select(PaperTag).where(PaperTag.tag_id == tag.id)
            ).all())
            result.append(TagResponse(
                id=tag.id, name=tag.name, color=tag.color, paper_count=count,
>>>>>>> worktree-agent-a598e72a4d81f8239
            ))
        return result


@router.post("", response_model=TagResponse)
async def create_tag(req: TagCreate) -> TagResponse:
    engine = _get_engine()
    with Session(engine) as session:
        existing = session.exec(select(Tag).where(Tag.name == req.name)).first()
        if existing:
            raise HTTPException(status_code=400, detail={"detail": "标签已存在", "code": "DUPLICATE"})
        tag = Tag(name=req.name, color=req.color)
        session.add(tag)
        session.commit()
        session.refresh(tag)
        return TagResponse(id=tag.id, name=tag.name, color=tag.color, paper_count=0)


@router.delete("/{tag_id}")
async def delete_tag(tag_id: int) -> dict:
    engine = _get_engine()
    with Session(engine) as session:
        tag = session.get(Tag, tag_id)
        if not tag:
            raise HTTPException(status_code=404, detail={"detail": "标签不存在", "code": "NOT_FOUND"})
        pts = session.exec(select(PaperTag).where(PaperTag.tag_id == tag_id)).all()
        for pt in pts:
            session.delete(pt)
        session.delete(tag)
        session.commit()
    return {"message": "标签已删除"}


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(tag_id: int, req: TagCreate) -> TagResponse:
    engine = _get_engine()
    with Session(engine) as session:
        tag = session.get(Tag, tag_id)
        if not tag:
            raise HTTPException(status_code=404, detail={"detail": "标签不存在", "code": "NOT_FOUND"})
        dup = session.exec(select(Tag).where(Tag.name == req.name, Tag.id != tag_id)).first()
        if dup:
            raise HTTPException(status_code=400, detail={"detail": "标签名已存在", "code": "DUPLICATE"})
        tag.name = req.name
        tag.color = req.color
        session.add(tag)
        session.commit()
        session.refresh(tag)
<<<<<<< HEAD
        # Single count query instead of fetching all rows
        count_stmt = select(func.count()).where(PaperTag.tag_id == tag_id)
        count = session.exec(count_stmt).one()
=======
        count = len(session.exec(select(PaperTag).where(PaperTag.tag_id == tag_id)).all())
>>>>>>> worktree-agent-a598e72a4d81f8239
        return TagResponse(id=tag.id, name=tag.name, color=tag.color, paper_count=count)


@router.get("/paper/{paper_id}", response_model=list[TagResponse])
async def get_paper_tags(paper_id: int) -> list[TagResponse]:
    engine = _get_engine()
    with Session(engine) as session:
<<<<<<< HEAD
        # Single JOIN query instead of N+1 (fetch PaperTags + Tags in one shot)
        stmt = (
            select(Tag)
            .join(PaperTag, PaperTag.tag_id == Tag.id)
            .where(PaperTag.paper_id == paper_id)
            .order_by(Tag.name)
        )
        tags = session.exec(stmt).all()
        return [TagResponse(id=t.id, name=t.name, color=t.color) for t in tags]
=======
        pts = session.exec(select(PaperTag).where(PaperTag.paper_id == paper_id)).all()
        result = []
        for pt in pts:
            tag = session.get(Tag, pt.tag_id)
            if tag:
                result.append(TagResponse(id=tag.id, name=tag.name, color=tag.color))
        return result
>>>>>>> worktree-agent-a598e72a4d81f8239


@router.post("/paper/{paper_id}")
async def add_paper_tag(paper_id: int, req: PaperTagRequest) -> dict:
    engine = _get_engine()
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        tag = session.get(Tag, req.tag_id)
        if not tag:
            raise HTTPException(status_code=404, detail={"detail": "标签不存在", "code": "NOT_FOUND"})
        existing = session.exec(
            select(PaperTag).where(PaperTag.paper_id == paper_id, PaperTag.tag_id == req.tag_id)
        ).first()
        if existing:
            return {"message": "标签已存在"}
        pt = PaperTag(paper_id=paper_id, tag_id=req.tag_id)
        session.add(pt)
        session.commit()
    return {"message": "标签已添加"}


@router.delete("/paper/{paper_id}/{tag_id}")
async def remove_paper_tag(paper_id: int, tag_id: int) -> dict:
    engine = _get_engine()
    with Session(engine) as session:
        pt = session.exec(
            select(PaperTag).where(PaperTag.paper_id == paper_id, PaperTag.tag_id == tag_id)
        ).first()
        if pt:
            session.delete(pt)
            session.commit()
    return {"message": "标签已移除"}


@router.get("/papers/{tag_id}", response_model=list[dict])
async def get_papers_by_tag(tag_id: int) -> list[dict]:
    engine = _get_engine()
    with Session(engine) as session:
        pts = session.exec(select(PaperTag).where(PaperTag.tag_id == tag_id)).all()
        result = []
        for pt in pts:
            paper = session.get(Paper, pt.paper_id)
            if paper:
                result.append({
                    "id": paper.id,
                    "filename": paper.filename,
                    "title": paper.title,
                    "venue": paper.venue,
                    "year": paper.year,
                    "status": paper.status,
                    "source": paper.source,
                    "created_at": paper.created_at,
                    "updated_at": paper.updated_at,
                })
        return result

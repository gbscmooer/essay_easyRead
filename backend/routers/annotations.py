import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, create_engine, select
from datetime import datetime, timezone
from typing import Optional

from ..models.annotation import Annotation
from ..models.paper import Paper
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/annotations", tags=["annotations"])

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


class AnnotationCreate(BaseModel):
    page_number: int = Field(..., ge=1)
    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    width: float = Field(..., ge=0, le=1)
    height: float = Field(..., ge=0, le=1)
    annotation_type: str = Field("highlight", max_length=20)
    color: str = Field("#fde047", pattern=r"^#[0-9a-fA-F]{6}$")
    text: str = Field("", max_length=5000)
    group_id: Optional[str] = Field(None, max_length=64)


class AnnotationResponse(BaseModel):
    id: int
    paper_id: int
    page_number: int
    x: float
    y: float
    width: float
    height: float
    annotation_type: str
    color: str
    text: str
    group_id: Optional[str] = None
    created_at: datetime


@router.get("/paper/{paper_id}", response_model=list[AnnotationResponse])
<<<<<<< HEAD
async def list_annotations(paper_id: int):
=======
async def list_annotations(paper_id: int) -> list[AnnotationResponse]:
>>>>>>> worktree-agent-af1e8d0021794f851
    engine = _get_engine()
    with Session(engine) as session:
        annotations = session.exec(
            select(Annotation)
            .where(Annotation.paper_id == paper_id)
            .order_by(Annotation.page_number, Annotation.y)
        ).all()
        return [
            AnnotationResponse(
                id=a.id, paper_id=a.paper_id, page_number=a.page_number,
                x=a.x, y=a.y, width=a.width, height=a.height,
                annotation_type=a.annotation_type, color=a.color,
                text=a.text, group_id=a.group_id, created_at=a.created_at,
            )
            for a in annotations
        ]


@router.post("/paper/{paper_id}", response_model=AnnotationResponse)
<<<<<<< HEAD
async def create_annotation(paper_id: int, req: AnnotationCreate):
=======
async def create_annotation(paper_id: int, req: AnnotationCreate) -> AnnotationResponse:
>>>>>>> worktree-agent-af1e8d0021794f851
    engine = _get_engine()
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        annotation = Annotation(
            paper_id=paper_id,
            page_number=req.page_number,
            x=req.x, y=req.y,
            width=req.width, height=req.height,
            annotation_type=req.annotation_type,
            color=req.color,
            text=req.text,
            group_id=req.group_id,
        )
        session.add(annotation)
        session.commit()
        session.refresh(annotation)
        return AnnotationResponse(
            id=annotation.id, paper_id=annotation.paper_id,
            page_number=annotation.page_number,
            x=annotation.x, y=annotation.y,
            width=annotation.width, height=annotation.height,
            annotation_type=annotation.annotation_type,
            color=annotation.color, text=annotation.text,
            group_id=annotation.group_id, created_at=annotation.created_at,
        )


@router.delete("/group/{group_id}")
<<<<<<< HEAD
async def delete_annotation_group(group_id: str):
=======
async def delete_annotation_group(group_id: str) -> dict:
>>>>>>> worktree-agent-af1e8d0021794f851
    """Delete all annotations sharing the same group_id (created in one action)."""
    engine = _get_engine()
    with Session(engine) as session:
        annotations = session.exec(
            select(Annotation).where(Annotation.group_id == group_id)
        ).all()
        if not annotations:
            raise HTTPException(status_code=404, detail={"detail": "标注组不存在", "code": "NOT_FOUND"})
        for ann in annotations:
            session.delete(ann)
        session.commit()
    return {"message": f"已删除 {len(annotations)} 条标注"}


@router.delete("/{annotation_id}")
<<<<<<< HEAD
async def delete_annotation(annotation_id: int):
=======
async def delete_annotation(annotation_id: int) -> dict:
>>>>>>> worktree-agent-af1e8d0021794f851
    engine = _get_engine()
    with Session(engine) as session:
        annotation = session.get(Annotation, annotation_id)
        if not annotation:
            raise HTTPException(status_code=404, detail={"detail": "标注不存在", "code": "NOT_FOUND"})
        session.delete(annotation)
        session.commit()
    return {"message": "标注已删除"}

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from datetime import datetime, timezone
from typing import Optional

from ..models.note import Note
from ..models.paper import Paper
from ..services.database import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteCreate(BaseModel):
    content: str = Field(..., max_length=10000)
    note_type: str = Field("general", max_length=20)
    section_name: Optional[str] = Field(None, max_length=200)
    page_number: Optional[int] = Field(None, ge=1)


class NoteUpdate(BaseModel):
    content: str = Field(..., max_length=10000)


class NoteResponse(BaseModel):
    id: int
    paper_id: int
    content: str
    note_type: str
    section_name: Optional[str] = None
    page_number: Optional[int] = None
    created_at: datetime
    updated_at: datetime


@router.get("/paper/{paper_id}", response_model=list[NoteResponse])
async def list_notes(paper_id: int, note_type: Optional[str] = None) -> list[NoteResponse]:
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Note).where(Note.paper_id == paper_id)
        if note_type:
            stmt = stmt.where(Note.note_type == note_type)
        notes = session.exec(stmt.order_by(Note.created_at.desc())).all()
        return [
            NoteResponse(
                id=n.id, paper_id=n.paper_id, content=n.content,
                note_type=n.note_type, section_name=n.section_name,
                page_number=n.page_number, created_at=n.created_at, updated_at=n.updated_at,
            )
            for n in notes
        ]


@router.post("/paper/{paper_id}", response_model=NoteResponse)
async def create_note(paper_id: int, req: NoteCreate) -> NoteResponse:
    engine = get_engine()
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        note = Note(
            paper_id=paper_id,
            content=req.content,
            note_type=req.note_type,
            section_name=req.section_name,
            page_number=req.page_number,
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return NoteResponse(
            id=note.id, paper_id=note.paper_id, content=note.content,
            note_type=note.note_type, section_name=note.section_name,
            page_number=note.page_number, created_at=note.created_at, updated_at=note.updated_at,
        )


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note(note_id: int, req: NoteUpdate) -> NoteResponse:
    engine = get_engine()
    with Session(engine) as session:
        note = session.get(Note, note_id)
        if not note:
            raise HTTPException(status_code=404, detail={"detail": "笔记不存在", "code": "NOT_FOUND"})
        note.content = req.content
        note.updated_at = datetime.now(timezone.utc)
        session.add(note)
        session.commit()
        session.refresh(note)
        return NoteResponse(
            id=note.id, paper_id=note.paper_id, content=note.content,
            note_type=note.note_type, section_name=note.section_name,
            page_number=note.page_number, created_at=note.created_at, updated_at=note.updated_at,
        )


@router.delete("/{note_id}")
async def delete_note(note_id: int) -> dict:
    engine = get_engine()
    with Session(engine) as session:
        note = session.get(Note, note_id)
        if not note:
            raise HTTPException(status_code=404, detail={"detail": "笔记不存在", "code": "NOT_FOUND"})
        session.delete(note)
        session.commit()
    return {"message": "笔记已删除"}

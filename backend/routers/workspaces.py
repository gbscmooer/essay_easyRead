"""
工作区路由

管理工作区的 CRUD 和论文关联。
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from sqlalchemy import desc, func
from datetime import datetime, timezone

from ..models.workspace import Workspace, WorkspacePaper
from ..models.paper import Paper
from ..services.database import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field("未命名工作区", max_length=200)


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    paper_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceDetailResponse(BaseModel):
    id: int
    name: str
    papers: list[dict] = []
    created_at: datetime
    updated_at: datetime


class AddPaperRequest(BaseModel):
    paper_id: int


# ============================================================
# GET /api/workspaces
# ============================================================
@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces() -> list[WorkspaceResponse]:
    """获取所有工作区"""
    engine = get_engine()
    with Session(engine) as session:
        workspaces = session.exec(select(Workspace).order_by(desc(Workspace.updated_at))).all()
        result = []
        for ws in workspaces:
            count_statement = select(func.count()).select_from(WorkspacePaper).where(WorkspacePaper.workspace_id == ws.id)
            paper_count = session.exec(count_statement).one()
            result.append(WorkspaceResponse(
                id=ws.id, name=ws.name, paper_count=paper_count,
                created_at=ws.created_at, updated_at=ws.updated_at,
            ))
        return result


# ============================================================
# POST /api/workspaces
# ============================================================
@router.post("", response_model=WorkspaceResponse)
async def create_workspace(req: WorkspaceCreate) -> WorkspaceResponse:
    """创建工作区"""
    engine = get_engine()
    with Session(engine) as session:
        ws = Workspace(name=req.name)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        return WorkspaceResponse(
            id=ws.id, name=ws.name, paper_count=0,
            created_at=ws.created_at, updated_at=ws.updated_at,
        )


# ============================================================
# GET /api/workspaces/{workspace_id}
# ============================================================
@router.get("/{workspace_id}", response_model=WorkspaceDetailResponse)
async def get_workspace(workspace_id: int) -> WorkspaceDetailResponse:
    """获取工作区详情（含论文列表）"""
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})

        # 获取关联的论文
        stmt = (
            select(Paper, WorkspacePaper.added_at)
            .join(WorkspacePaper, WorkspacePaper.paper_id == Paper.id)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .order_by(desc(WorkspacePaper.added_at))
        )
        rows = session.exec(stmt).all()
        papers = []
        for paper, added_at in rows:
            papers.append({
                "id": paper.id,
                "filename": paper.filename,
                "title": paper.title,
                "venue": paper.venue,
                "year": paper.year,
                "status": paper.status,
                "code_url": paper.code_url,
                "code_status": paper.code_status,
                "source": getattr(paper, "source", "uploaded"),
                "level": getattr(paper, "level", 1),
                "parent_paper_id": getattr(paper, "parent_paper_id", None),
                "created_at": paper.created_at.isoformat() if paper.created_at else None,
                "added_at": added_at.isoformat() if added_at else None,
            })

        return WorkspaceDetailResponse(
            id=ws.id, name=ws.name, papers=papers,
            created_at=ws.created_at, updated_at=ws.updated_at,
        )


# ============================================================
# PUT /api/workspaces/{workspace_id}
# ============================================================
@router.put("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(workspace_id: int, req: WorkspaceCreate) -> WorkspaceResponse:
    """更新工作区名称"""
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})
        ws.name = req.name
        ws.updated_at = datetime.now(timezone.utc)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        count_statement = select(func.count()).select_from(WorkspacePaper).where(WorkspacePaper.workspace_id == ws.id)
        paper_count = session.exec(count_statement).one()
        return WorkspaceResponse(
            id=ws.id, name=ws.name, paper_count=paper_count,
            created_at=ws.created_at, updated_at=ws.updated_at,
        )


# ============================================================
# DELETE /api/workspaces/{workspace_id}
# ============================================================
@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: int) -> dict:
    """删除工作区"""
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})
        # 删除关联记录
        stmt = select(WorkspacePaper).where(WorkspacePaper.workspace_id == workspace_id)
        for wp in session.exec(stmt).all():
            session.delete(wp)
        session.delete(ws)
        session.commit()
        return {"message": "工作区已删除"}


# ============================================================
# POST /api/workspaces/{workspace_id}/papers
# ============================================================
@router.post("/{workspace_id}/papers")
async def add_paper_to_workspace(workspace_id: int, req: AddPaperRequest) -> dict:
    """添加论文到工作区"""
    engine = get_engine()
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail={"detail": "工作区不存在", "code": "NOT_FOUND"})
        paper = session.get(Paper, req.paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        # 检查是否已存在
        existing = session.exec(
            select(WorkspacePaper)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .where(WorkspacePaper.paper_id == req.paper_id)
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail={"detail": "论文已在工作区中", "code": "CONFLICT"})
        wp = WorkspacePaper(workspace_id=workspace_id, paper_id=req.paper_id)
        session.add(wp)
        ws.updated_at = datetime.now(timezone.utc)
        session.add(ws)
        session.commit()
        return {"message": "已添加"}


# ============================================================
# POST /api/workspaces/{workspace_id}/papers/{paper_id}/promote
# ============================================================
@router.post("/{workspace_id}/papers/{paper_id}/promote")
async def promote_paper(workspace_id: int, paper_id: int) -> dict:
    """把"深度研究发现"中的论文（L2+）提升为工作区正式论文（L1）。

    - level 设为 1
    - source 改为 'promoted'（保留 parent_paper_id 作为发现来源溯源）
    - 仍保留 status（pending / indexed / done）不变
    """
    engine = get_engine()
    with Session(engine) as session:
        wp = session.exec(
            select(WorkspacePaper)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .where(WorkspacePaper.paper_id == paper_id)
        ).first()
        if not wp:
            raise HTTPException(status_code=404, detail={"detail": "论文不在该工作区中", "code": "NOT_FOUND"})
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail={"detail": "论文不存在", "code": "NOT_FOUND"})
        paper.level = 1
        paper.source = "promoted"
        session.add(paper)
        ws = session.get(Workspace, workspace_id)
        if ws:
            ws.updated_at = datetime.now(timezone.utc)
            session.add(ws)
        session.commit()
        return {"message": "已移入工作区", "paper_id": paper_id}


# ============================================================
# DELETE /api/workspaces/{workspace_id}/papers/{paper_id}
# ============================================================
@router.delete("/{workspace_id}/papers/{paper_id}")
async def remove_paper_from_workspace(workspace_id: int, paper_id: int) -> dict:
    """从工作区移除论文"""
    engine = get_engine()
    with Session(engine) as session:
        stmt = (
            select(WorkspacePaper)
            .where(WorkspacePaper.workspace_id == workspace_id)
            .where(WorkspacePaper.paper_id == paper_id)
        )
        wp = session.exec(stmt).first()
        if not wp:
            raise HTTPException(status_code=404, detail={"detail": "论文不在该工作区中", "code": "NOT_FOUND"})
        session.delete(wp)
        ws = session.get(Workspace, workspace_id)
        if ws:
            ws.updated_at = datetime.now(timezone.utc)
            session.add(ws)
        session.commit()
        return {"message": "已移除"}

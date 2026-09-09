import os
import sys
import tempfile
import pytest
from pathlib import Path

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

os.environ.setdefault("LLM_API_KEY", "test-key-for-ci")


@pytest.fixture(scope="session")
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture(scope="session")
def test_db_url(tmp_data_dir):
    db_path = tmp_data_dir / "test.db"
    return f"sqlite:///{db_path}"


@pytest.fixture(scope="session")
def test_upload_dir(tmp_data_dir):
    upload_dir = tmp_data_dir / "uploads"
    upload_dir.mkdir()
    return str(upload_dir)


@pytest.fixture(scope="session", autouse=True)
def patch_settings(test_db_url, test_upload_dir):
    from backend.config import settings
    settings.database_url = test_db_url
    settings.upload_dir = test_upload_dir
    settings.llm_api_key = "test-key"


@pytest.fixture(scope="session")
def engine(test_db_url):
    from sqlmodel import create_engine, SQLModel
    from backend.models.paper import Paper
    from backend.models.analysis import AnalysisResult
    from backend.models.code_analysis import CodeAnalysis
    from backend.models.research import ResearchRun, ResearchGraph, PaperReference
    from backend.models.exploration import ExplorationRun
    from backend.models.research_core import (  # noqa: F401 — 注册 academic_survey_v2 表
        ResearchAsset, ResearchBudgetEvent, ResearchClaim, ResearchEvent, ResearchEvidence,
        ResearchIdentityDecision, ResearchRelation, ResearchReportRevision, ResearchTask,
        ResearchTaskAttempt,
    )
    from backend.models.workspace import Workspace, WorkspacePaper
    from backend.models.tag import Tag, PaperTag
    from backend.models.note import Note
    from backend.models.annotation import Annotation

    eng = create_engine(test_db_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    # 生产启动会执行轻量迁移（含 partial unique indexes）；测试环境保持同一 schema 行为。
    from backend.services.database import _migrate_schema
    _migrate_schema(eng)
    return eng


@pytest.fixture(autouse=True)
def clean_database(engine):
    """每个测试前后清空共享测试库，避免提交型 API 测试相互污染。"""
    from sqlmodel import SQLModel

    def clear_tables():
        with engine.begin() as connection:
            for table in reversed(SQLModel.metadata.sorted_tables):
                connection.execute(table.delete())

    clear_tables()
    yield
    clear_tables()


@pytest.fixture
def session(engine):
    from sqlmodel import Session
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine, test_upload_dir):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.routers import papers, analysis, code, workspaces, research

    papers._engine = engine
    analysis._engine = engine
    code._engine = engine
    workspaces._engine = engine
    research._engine = engine

    os.makedirs(test_upload_dir, exist_ok=True)

    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_pdf(test_upload_dir):
    pdf_path = os.path.join(test_upload_dir, "sample.pdf")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4 test content")
    return pdf_path


@pytest.fixture
def create_paper(session, sample_pdf):
    from backend.services.database import create_paper as db_create

    def _create(filename="test.pdf", status="pending"):
        pid = db_create(session, filename, sample_pdf, 100)
        if status != "pending":
            from backend.services.database import update_paper_status
            update_paper_status(session, pid, status, None)
        session.commit()
        return pid

    return _create

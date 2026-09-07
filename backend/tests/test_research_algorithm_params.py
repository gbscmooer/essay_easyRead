import asyncio

import pytest
from sqlmodel import Session

from backend.models.schemas import ResearchRunCreate
from backend.models.workspace import Workspace
from backend.routers import research


@pytest.mark.parametrize("explicit, expected", [(None, .12), (.08, .08)])
def test_new_run_snapshots_configured_alpha_without_overwriting_explicit_request(engine, monkeypatch, explicit, expected):
    monkeypatch.setattr(research, "get_engine", lambda: engine)
    monkeypatch.setattr(research, "_has_collectable_papers", lambda *args: True)
    monkeypatch.setattr(research, "get_event_bus", lambda *args: None)
    monkeypatch.setattr("backend.services.ai_config.get_research_config", lambda: {"backbone_alpha": .12})
    async def pipeline(run_id):
        pass
    monkeypatch.setattr(research, "run_pipeline", pipeline)
    with Session(engine) as session:
        workspace = Workspace(name="algorithm parameter test")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        ws_id = workspace.id
    req = ResearchRunCreate(**({"backbone_alpha": explicit} if explicit is not None else {}))
    result = asyncio.run(research.create_run(ws_id, req))
    assert result.params["backbone_alpha"] == expected
    assert ResearchRunCreate().backbone_alpha == .05

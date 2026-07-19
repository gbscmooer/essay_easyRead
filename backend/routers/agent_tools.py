"""Agent Tool routes.

These routes expose paper-grounded tools to external coding agents.  They are
kept separate from the existing research routes so the original UI/API behavior
does not depend on agent integration details.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..models.schemas import (
    AgentToolManifestResponse,
    DevBriefRequest,
    EvidenceRequest,
    GraphNeighborsRequest,
    PaperSearchRequest,
    ResearchAgentRunRequest,
    ResearchAgentRunResponse,
)
from ..services.agent_tools import (
    AgentToolError,
    build_reading_plan,
    build_survey,
    create_development_brief,
    get_paper_context,
    get_workspace_graph,
    graph_neighbors,
    list_agent_tools,
    list_workspace_papers,
    list_workspaces,
    retrieve_evidence,
    search_papers,
)
from ..services.agent_workflow import get_langgraph_workflow_spec
from ..services.research_harness import (
    get_research_workflow_spec,
    list_research_workflows,
    run_research_workflow,
)


router = APIRouter(prefix="/api/agent-tools", tags=["agent-tools"])


def _agent_error_response(exc: AgentToolError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@router.get("/manifest", response_model=AgentToolManifestResponse)
async def manifest() -> AgentToolManifestResponse:
    """获取 Agent/MCP 工具清单。"""

    return AgentToolManifestResponse(tools=list_agent_tools())


@router.get("/workspaces")
async def get_agent_workspaces() -> dict:
    """列出可供 Agent 使用的工作区。"""

    return {"workspaces": list_workspaces()}


@router.get("/workspaces/{workspace_id}/papers")
async def get_agent_workspace_papers(workspace_id: int) -> dict:
    """列出工作区内论文。"""

    try:
        return {"workspace_id": workspace_id, "papers": list_workspace_papers(workspace_id)}
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/papers/search")
async def search_agent_papers(req: PaperSearchRequest) -> dict:
    """检索论文，返回候选论文和命中字段。"""

    try:
        return search_papers(
            query=req.query,
            workspace_id=req.workspace_id,
            limit=req.limit,
            include_expanded=req.include_expanded,
        )
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.get("/papers/{paper_id}")
async def get_agent_paper(paper_id: int) -> dict:
    """获取单篇论文结构化上下文。"""

    try:
        return get_paper_context(paper_id)
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.get("/workspaces/{workspace_id}/graph")
async def get_agent_graph(workspace_id: int) -> dict:
    """获取工作区最新图谱快照。"""

    try:
        return get_workspace_graph(workspace_id)
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/workspaces/{workspace_id}/graph/neighbors")
async def get_agent_graph_neighbors(workspace_id: int, req: GraphNeighborsRequest) -> dict:
    """从已持久化图谱中查询论文邻域。"""

    try:
        return graph_neighbors(
            workspace_id=workspace_id,
            paper_id=req.paper_id,
            depth=req.depth,
            limit=req.limit,
            edge_kinds=req.edge_kinds,
        )
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/workspaces/{workspace_id}/evidence")
async def retrieve_agent_evidence(workspace_id: int, req: EvidenceRequest) -> dict:
    """检索工作区向量索引中的论文证据片段。"""

    try:
        return await retrieve_evidence(
            workspace_id=workspace_id,
            query=req.query,
            top_k=req.top_k,
            paper_ids=req.paper_ids,
        )
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/workspaces/{workspace_id}/dev-brief")
async def create_agent_dev_brief(workspace_id: int, req: DevBriefRequest) -> dict:
    """生成论文证据约束的开发 brief。"""

    try:
        return await create_development_brief(
            workspace_id=workspace_id,
            goal=req.goal,
            top_k=req.top_k,
            paper_ids=req.paper_ids,
        )
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.get("/workflow/langgraph-spec")
async def langgraph_workflow_spec() -> dict:
    """返回论文驱动开发的 LangGraph 工作流规范。"""

    return get_langgraph_workflow_spec()


@router.get("/workflows")
async def list_agent_workflows() -> dict:
    """列出研究 Agent Harness 可运行的 workflow。"""

    return {"workflows": list_research_workflows()}


@router.get("/workflows/{workflow_name}")
async def get_agent_workflow(workflow_name: str) -> dict:
    """获取单个研究 workflow 规范。"""

    try:
        return get_research_workflow_spec(workflow_name)
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/workspaces/{workspace_id}/reading-plan")
async def agent_reading_plan(workspace_id: int, target_paper_id: int | None = None) -> dict:
    """Agent 工具：构建工作区阅读路径。"""

    try:
        return build_reading_plan(workspace_id, target_paper_id=target_paper_id)
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/workspaces/{workspace_id}/survey")
async def agent_survey(workspace_id: int) -> dict:
    """Agent 工具：构建工作区综述矩阵。"""

    try:
        return build_survey(workspace_id)
    except AgentToolError as exc:
        return _agent_error_response(exc)


@router.post("/runs", response_model=ResearchAgentRunResponse)
async def run_agent_workflow(req: ResearchAgentRunRequest) -> ResearchAgentRunResponse | JSONResponse:
    """运行研究 Agent Harness：只接收 goal + workflow + workspace_id。"""

    try:
        result = await run_research_workflow(
            workflow=req.workflow,
            goal=req.goal,
            workspace_id=req.workspace_id,
        )
        return ResearchAgentRunResponse(**result)
    except AgentToolError as exc:
        return _agent_error_response(exc)

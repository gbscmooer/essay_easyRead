import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.services.deep_research import arxiv_client, survey_report as sr
from backend.services.rate_limiter import AdaptiveThrottle, DomainRateLimiter
from backend.services.research_exclusions import active_exclusions


@pytest.mark.asyncio
async def test_arxiv_cache_and_atom_material(monkeypatch):
    arxiv_client._req_lock = asyncio.Lock()
    arxiv_client._query_cache.clear()
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>https://arxiv.org/abs/2305.06983v2</id><title>Active retrieval</title>
    <published>2023-05-01</published><summary>Evaluation with a benchmark</summary></entry></feed>'''
    request = AsyncMock(return_value=SimpleNamespace(text=xml))
    monkeypatch.setattr("backend.services.rate_limiter.resilient_get", request)
    a, b = await asyncio.gather(arxiv_client.search_topic("retrieval generation"),
                                arxiv_client.search_topic("retrieval generation"))
    assert a == b and a[0]["abstract"] == "Evaluation with a benchmark"
    assert a[0]["arxiv_id"] == "2305.06983"
    request.assert_awaited_once()
    assert request.call_args.kwargs["params"]["search_query"] == "all:retrieval AND all:generation"


@pytest.mark.asyncio
async def test_scopus_throttle_is_shared_and_cancel_does_not_leak():
    from backend.services.deep_research.providers.scopus import _shared_throttle
    assert _shared_throttle() is _shared_throttle()
    throttle = AdaptiveThrottle(start=0.01, floor=0.01, concurrency=1)
    throttle.on_rate_limited(1)
    task = asyncio.create_task(throttle.acquire())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert throttle._sem._value == 1


@pytest.mark.asyncio
async def test_retry_after_delays_other_waiting_requests():
    throttle = AdaptiveThrottle(start=0.02, floor=0.02, concurrency=2)
    await throttle.acquire()
    throttle.release()
    waiter = asyncio.create_task(throttle.acquire())
    await asyncio.sleep(0.005)
    start = asyncio.get_running_loop().time()
    throttle.on_rate_limited(0.04)
    await waiter
    throttle.release()
    assert asyncio.get_running_loop().time() - start >= 0.035


def test_arxiv_api_and_pdf_share_a_single_budget():
    limiter = DomainRateLimiter()
    assert limiter._get_domain("https://export.arxiv.org/api/query") == "arxiv.org"
    assert limiter._get_rule("arxiv.org") == (3.0, 1)


@pytest.mark.asyncio
async def test_pdf_candidates_fall_through_and_clean_up(monkeypatch, tmp_path):
    async def select(papers, *args, **kwargs):
        return papers, {"warnings": []}
    monkeypatch.setattr(sr, "retrieve_materials", select)
    monkeypatch.setattr(sr, "_llm_json", AsyncMock(return_value={"metrics": []}))
    monkeypatch.setattr("backend.services.paper_download.resolve_candidate_pdf_urls",
                        AsyncMock(return_value=["https://example.org/failed", "https://example.org/good"]))
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.4")
    from backend.services.deep_research.pdf_downloader import DownloadResult
    download = AsyncMock(side_effect=[
        DownloadResult(status="unavailable",
                       attempts=[{"host": "example.org", "stage": "response",
                                  "error_code": "NON_PDF_CONTENT", "http_status": 200,
                                  "retryable": False, "elapsed_ms": 5}]),
        DownloadResult(status="available", path=str(path)),
    ])
    monkeypatch.setattr("backend.services.deep_research.pdf_downloader.download_pdf_detailed", download)
    monkeypatch.setattr("backend.services.pdf_parser.extract_pdf_content",
                        lambda path: SimpleNamespace(full_text="Evaluation of question answering on ExampleQA."))
    paper = {"key": "doi:10.1/example", "title": "Example", "doi": "10.1/example"}
    report = await sr.build_survey_report("q", [paper], {}, None, {"api_key": "test"}, fetch_full_text=True)
    assert download.await_count == 2
    assert report["coverage"]["full_text"] == 1
    assert paper["full_text_status"] == "available"
    assert not path.exists()


@pytest.mark.asyncio
async def test_supplement_is_bounded_and_filters_holdout(monkeypatch):
    from backend.services.deep_research.explorer import recall_papers
    provider = SimpleNamespace(name="scopus", search_works=AsyncMock(return_value=[]), aclose=AsyncMock())
    monkeypatch.setattr("backend.services.deep_research.providers.get_provider", lambda cfg: provider)
    search = AsyncMock(return_value=[{"title": "Hidden survey", "arxiv_id": "2312.10997"},
                                    {"title": "Independent study", "arxiv_id": "2305.06983", "abstract": "material"}])
    monkeypatch.setattr(arxiv_client, "search_topic", search)
    token = active_exclusions.set({"exclusions": {"arxiv_ids": ["2312.10997"]}})
    try:
        audit = {}
        papers, _ = await recall_papers(["query one", "query two", "query two"], per_query=25, cap=100,
                                        now_year=2026, supplement_arxiv=True, search_audit=audit)
        assert search.await_count == 3
        assert provider.search_works.await_count == 3
        assert [p["arxiv_id"] for p in papers] == ["2305.06983"]
        assert audit["excluded"]["count"] == 1
    finally:
        active_exclusions.reset(token)


def test_table_facets_reserve_material_beyond_top_relevance():
    papers = [{"key": str(i), "title": "retrieval generation study", "abstract": "language models"} for i in range(20)]
    papers += [{"key": "eval", "title": "evaluation framework benchmark tool", "abstract": ""},
               {"key": "tasks", "title": "experiment dataset downstream task", "abstract": ""}]
    chosen = sr.select_report_materials(papers, 12)
    assert len(chosen) == 12
    assert {"eval", "tasks"}.issubset({p["key"] for p in chosen})


def test_depth_contract_lite_accepted_and_knobs_covered():
    """lite 档（2026-09-18）：schema 接受 lite/拒绝未知档；所有按档位分支的
    旋钮表必须覆盖全部合法档位，防止后续加档漏配静默落默认值。"""
    import pytest
    from pydantic import ValidationError

    from backend.models.schemas import ExplorationRunCreate
    from backend.services.deep_research.explorer import (
        AGENT_LOOP_PRESETS, DEPTH_PRESETS,
    )
    from backend.services.deep_research.recall_engine import (
        FILTER_BATCH, PER_SOURCE_LIMIT, RECALL_TARGETS,
    )
    from backend.services.deep_research.survey_report import LIMITS

    assert ExplorationRunCreate(question="lite depth question", depth="lite").depth == "lite"
    with pytest.raises(ValidationError):
        ExplorationRunCreate(question="bad depth question", depth="turbo")

    accepted = {"lite", "quick", "standard", "deep"}
    for name, table in (("RECALL_TARGETS", RECALL_TARGETS),
                        ("FILTER_BATCH", FILTER_BATCH),
                        ("PER_SOURCE_LIMIT", PER_SOURCE_LIMIT),
                        ("DEPTH_PRESETS", DEPTH_PRESETS),
                        ("AGENT_LOOP_PRESETS", AGENT_LOOP_PRESETS),
                        ("LIMITS", LIMITS)):
        missing = accepted - set(table)
        assert not missing, f"{name} 缺档位配置: {sorted(missing)}"
    assert RECALL_TARGETS["lite"] == 50 and DEPTH_PRESETS["lite"]["max_queries"] == 2


# ── v8 分层选材：结构覆盖 + unknown 隔离 + 候补池 ─────────────────────────────

def test_select_stratified_materials_layers_and_unknown_exclusion():
    from backend.services.deep_research.survey_report import select_stratified_materials

    papers = []
    papers.append({"key": "k:dataset-paper", "title": "MVTec AD: A Comprehensive Dataset",
                   "year": 2019, "citation_count": 2000})
    papers.append({"key": "k:survey-paper", "title": "A Survey of Surface Defect Detection",
                   "year": 2021, "citation_count": 800})
    papers.append({"key": "k:recent-a", "title": "New method A", "year": 2026, "citation_count": 3})
    papers.append({"key": "k:recent-b", "title": "New method B", "year": 2025, "citation_count": 5})
    papers.append({"key": "k:classic", "title": "PatchCore foundation", "year": 2020,
                   "citation_count": 900})
    for i in range(6):
        papers.append({"key": f"k:m{i}", "title": f"Method {i}", "year": 2023,
                       "citation_count": 20 + i})
    # unknown 论文（fail-open 遗留）不得进入正选或候补
    papers.append({"key": "k:unknown-survey", "title": "PINN survey review",
                   "year": 2020, "citation_count": 5000,
                   "relevance_status": "unknown"})
    papers.append({"key": "k:unknown-dataset", "title": "UAV benchmark dataset",
                   "year": 2022, "citation_count": 3000,
                   "relevance_status": "unknown"})

    selected, backfill, audit = select_stratified_materials(
        papers, limit=8, now_year=2026, depth="lite")
    keys = {p["key"] for p in selected}
    assert "k:unknown-survey" not in keys and "k:unknown-dataset" not in keys
    assert all(p["key"] not in {b["key"] for b in backfill} for p in backfill)
    assert {b["key"] for b in backfill} & {"k:unknown-survey", "k:unknown-dataset"} == set()
    # dataset/survey 层入选（结构性覆盖，不再是纯相关性聚簇）
    assert "k:dataset-paper" in keys and "k:survey-paper" in keys
    assert audit["policy"] == "stratified_layers_v1"
    assert audit["excluded_unknown"] == 2
    assert len(selected) <= 8


def test_evolution_question_keeps_new_benchmark_and_recent_method():
    from backend.services.deep_research.survey_report import select_stratified_materials

    papers = [
        {"key": "old-benchmark", "title": "MVTec AD benchmark", "year": 2019,
         "citation_count": 3000},
        {"key": "survey", "title": "A survey of visual defect detection", "year": 2023},
        {"key": "new-benchmark", "title": "MVTec AD 2 benchmark", "year": 2026},
        {"key": "new-method", "title": "Segment Any Anomaly", "year": 2026},
        {"key": "foundation", "title": "PatchCore", "year": 2020, "citation_count": 900},
        *({"key": f"method-{i}", "title": f"Method {i}", "year": 2023}
          for i in range(5)),
    ]
    selected, _, audit = select_stratified_materials(
        papers, 8, now_year=2026, depth="lite",
        question="缺陷检测图像任务的发展和最新方向是什么？")
    keys = {paper["key"] for paper in selected}
    assert {"old-benchmark", "new-benchmark", "new-method"} <= keys
    assert audit["evolution_intent"] is True


def test_rescreen_does_not_promote_unresolved_papers():
    from backend.services.deep_research.explorer import _partition_rescreened

    pool = [{"key": "accepted", "relevance_status": "confirmed"},
            {"key": "still-unknown", "relevance_status": "unknown"},
            {"key": "rejected", "relevance_status": "unknown"}]
    recovered, quarantined = _partition_rescreened(pool, pool[:2])
    assert recovered == 1
    assert [paper["key"] for paper in quarantined] == ["still-unknown", "rejected"]


def test_recall_final_trim_escorts_unknown_with_cap():
    """终裁：confirmed 占满 target；unknown 不再补位填满目标量，只随行有界数量。"""
    from backend.services.deep_research import recall_engine as re_mod

    now = 2026
    confirmed = [{"key": f"k:c{i}", "title": f"confirmed {i}", "year": 2024,
                  "citation_count": 100 - i} for i in range(4)]
    unknown = [{"key": f"k:u{i}", "title": f"unknown {i}", "year": 2020,
                "citation_count": 9000 + i, "relevance_status": "unknown"} for i in range(6)]
    final = re_mod.dual_track_trim(confirmed, 4, now)
    escort_cap = max(10, int(4 * 0.2))
    escorted = re_mod.dual_track_trim(unknown, min(len(unknown), escort_cap), now)
    # escort_cap=10 > unknown 数量 → 全随行；小 target 时也至少 10 上限保护
    assert len(final) == 4 and len(escorted) == 6
    # 隔离纪律由 screen 阶段执行：unknown 论文携带 relevance_status 进入下游裁决
    assert all(p.get("relevance_status") == "unknown" for p in escorted)

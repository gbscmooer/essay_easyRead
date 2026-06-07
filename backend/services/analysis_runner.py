"""
论文分析后台任务管理器

将 AI 分析流程从 HTTP SSE 连接中解耦：分析在 asyncio 后台任务里运行，
即使前端断开（切换页面、刷新）也会继续执行。SSE 端点退化为「订阅者」，
连接时回放已产生的事件 + 实时接收新事件；断开只是取消订阅，不会终止分析。

每个 paper_id 对应一个 AnalysisJob，保存：
- events: 事件回放缓冲（新订阅者用它补齐已发生的进度）
- subscribers: 当前所有 SSE 订阅队列
- percent / status: 最新进度，用于无连接时通过 GET 查询

断点续传：AI 解析出的字段会增量写入 data/analysis_cache/{paper_id}.json。
若进程意外退出后重新分析，会读取缓存——AI 字段已全部完成则跳过昂贵的 AI 调用，
相关论文已完成则跳过搜索，直接从断点继续。成功落库后清除缓存。
"""

import asyncio
import json
import logging
import os
from typing import Optional

from sqlmodel import Session, create_engine

from ..config import settings
from ..models.schemas import StreamEvent

logger = logging.getLogger(__name__)


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.resolved_database_url,
            connect_args={"check_same_thread": False},
        )
    return _engine


# 用于进度百分比计算的字段（按典型到达顺序），与前端 LoadingState 对齐
_PROGRESS_FIELDS = [
    "title", "authors", "venue", "year", "one_sentence", "problem",
    "key_contributions", "outline", "method", "experiments",
    "code_mapping", "interview_qa", "limitations", "mermaid_chart",
]
_TOTAL_FIELDS = len(_PROGRESS_FIELDS)

# 相关论文搜索超时（秒）：findpapers 多源 + 网络重试可能卡很久，超时则跳过。
# 主体结果在此步之前已落库并标记完成，故超时只影响「相关论文」模块，不影响其余字段展示。
RELATED_PAPERS_TIMEOUT = 45.0

# 判定「AI 分析阶段已完成」所需的核心字段（用于断点续传时决定是否跳过 AI 调用）
_AI_REQUIRED_FIELDS = [
    "title", "one_sentence", "problem", "key_contributions",
    "outline", "method", "experiments", "code_mapping",
    "interview_qa", "limitations",
]

# 断点续传缓存目录：<project_root>/data/analysis_cache
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "analysis_cache",
)


def _cache_path(paper_id: int) -> str:
    return os.path.join(_CACHE_DIR, f"{paper_id}.json")


def load_cache(paper_id: int) -> dict:
    """读取断点缓存；不存在或损坏时返回空 dict。"""
    try:
        with open(_cache_path(paper_id), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_cache(paper_id: int, result: dict) -> None:
    """原子写入断点缓存（临时文件 + rename）。"""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(paper_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"Failed to write analysis cache for paper {paper_id}: {e}")


def clear_cache(paper_id: int) -> None:
    """删除断点缓存（落库成功或显式重新解析时调用）。"""
    try:
        os.remove(_cache_path(paper_id))
    except OSError:
        pass


class AnalysisJob:
    """单篇论文的后台分析任务状态。"""

    def __init__(self, paper_id: int):
        self.paper_id = paper_id
        self.events: list[StreamEvent] = []          # 回放缓冲
        self.subscribers: set[asyncio.Queue] = set()  # 实时订阅队列
        self.done = False
        self.task: Optional[asyncio.Task] = None
        self.percent = 0
        self.status = "pending"
        self.message = ""

    def emit(self, event: StreamEvent) -> None:
        """记录事件并广播给所有订阅者。"""
        self.events.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        """新建订阅队列，先灌入已发生的全部事件（回放）。"""
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.events:
            q.put_nowait(ev)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)


def _json_safe(obj):
    """递归地将对象转换为 JSON 可序列化的纯结构。

    防止第三方库（如 findpapers 的 Source/Author 对象）混入字段，
    导致 SSE 序列化或数据库写入崩溃。无法识别的对象退化为 str()。
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    # 优先用对象自带的字典化方法
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return _json_safe(fn())
            except Exception:
                break
    return str(obj)


_jobs: dict[int, AnalysisJob] = {}


def get_job(paper_id: int) -> Optional[AnalysisJob]:
    return _jobs.get(paper_id)


def ensure_job(paper_id: int, pdf_path: str) -> AnalysisJob:
    """获取正在运行的任务；若不存在或已结束，则启动一个新的后台任务。"""
    job = _jobs.get(paper_id)
    if job is not None and not job.done:
        return job

    job = AnalysisJob(paper_id)
    _jobs[paper_id] = job
    job.task = asyncio.create_task(_run_analysis(job, pdf_path))
    return job


async def _run_analysis(job: AnalysisJob, pdf_path: str) -> None:
    """后台执行完整分析流程，向 job 发射 SSE 事件。"""
    paper_id = job.paper_id
    engine = _get_engine()

    from .database import (
        update_paper_status, update_paper_metadata,
        save_analysis_result, update_paper_code_url, update_related_papers,
    )

    def emit_progress(percent: int, message: str, status: str) -> None:
        job.percent = percent
        job.status = status
        job.message = message
        job.emit(StreamEvent(
            event="progress",
            data={"percent": percent, "message": message, "status": status},
        ))

    def emit_cached_fields(res: dict) -> None:
        """把缓存里已完成的字段推给前端，实现断点恢复时瞬间显示。"""
        for fname, value in res.items():
            job.emit(StreamEvent(event="field", field_name=fname, data={"value": value}))
        method = res.get("method")
        if isinstance(method, dict) and method.get("mermaid_chart"):
            job.emit(StreamEvent(event="field", field_name="mermaid_chart", data={"value": method["mermaid_chart"]}))

    from .ai_analyzer import analyze_paper_stream, extract_code_url
    from .ai_config import get_analysis_config

    # ---- 0. 读取断点缓存 ----
    result: dict = load_cache(paper_id)
    ai_done = bool(result) and all(f in result for f in _AI_REQUIRED_FIELDS)
    related_done = "related_papers" in result

    try:
        ai_cfg = get_analysis_config()

        # 有缓存：先把已完成字段推给前端（断点恢复）
        if result:
            job.emit(StreamEvent(event="status", data={"status": "analyzing", "message": "检测到断点缓存，正在恢复已完成的分析..."}))
            emit_cached_fields(result)

        # ---- 1. 提取 PDF 文本（用于代码仓库提取，廉价，始终执行）----
        with Session(engine) as session:
            update_paper_status(session, paper_id, "extracting")
        job.emit(StreamEvent(event="status", data={"status": "extracting", "message": "正在提取 PDF 文本..."}))
        emit_progress(3, "正在提取 PDF 文本...", "extracting")

        from .pdf_parser import extract_pdf_content
        content = await asyncio.to_thread(extract_pdf_content, pdf_path)
        emit_progress(10, "PDF 文本提取完成，准备分析", "extracting")

        # ---- 2. AI 流式分析（缓存已完成则跳过昂贵的 AI 调用）----
        with Session(engine) as session:
            update_paper_status(session, paper_id, "analyzing")

        if ai_done:
            emit_progress(85, "已从缓存恢复 AI 分析结果，跳过重新分析", "analyzing")
        else:
            job.emit(StreamEvent(event="status", data={"status": "analyzing", "message": "正在调用 AI 分析..."}))
            received = 0
            async for event in analyze_paper_stream(
                content.full_text,
                ai_cfg["api_key"],
                ai_cfg["base_url"],
                ai_cfg["model"],
                temperature=ai_cfg.get("temperature", 0.1),
                max_tokens=ai_cfg.get("max_tokens", 16384),
            ):
                # analyze_paper_stream 自己会发 error 事件，但我们用异常路径统一处理，
                # 这里仅转发 status / field，done 留给最终阶段统一发。
                if event.event == "error":
                    raise RuntimeError(event.data.get("message", "AI 分析失败"))
                if event.event == "done":
                    continue
                job.emit(event)

                if event.event == "field" and event.field_name:
                    if event.field_name == "mermaid_chart":
                        result.setdefault("method", {})
                        result["method"]["mermaid_chart"] = event.data.get("value")
                    else:
                        result[event.field_name] = event.data.get("value")
                    received += 1
                    pct = min(85, 10 + round(received / _TOTAL_FIELDS * 75))
                    emit_progress(pct, f"已解析 {received}/{_TOTAL_FIELDS} 个字段", "analyzing")
                    # 增量持久化：每完成一个字段就写入缓存，供断点续传
                    save_cache(paper_id, result)

        # ---- 3. 提取代码仓库 URL ----
        code_url = extract_code_url(content.full_text)
        if code_url:
            job.emit(StreamEvent(event="status", data={"status": "analyzing", "message": f"找到代码仓库: {code_url}"}))

        # ---- 4. 先保存「主体结果」并标记完成（相关论文搜索之前）----
        # 关键：把除相关论文以外的全部字段先落库 + 标记 done，前端据此立即展示。
        # 这样即使后续相关论文搜索超时、失败甚至进程退出，主体内容也不受影响。
        emit_progress(90, "正在保存主体分析结果...", "analyzing")
        result.setdefault("related_papers", [])
        for key, default in [
            ("title", ""), ("venue", "Unknown"), ("year", 0),
            ("one_sentence", ""), ("problem", ""),
            ("key_contributions", []), ("outline", []),
            ("method", {"overview": "", "pipeline": "", "key_modules": []}),
            ("experiments", {"datasets": [], "baselines": [], "main_results": "", "ablation": "", "result_table": [], "figures": []}),
            ("code_mapping", []), ("interview_qa", []),
            ("limitations", ""), ("related_papers", []),
        ]:
            result.setdefault(key, default)
        result = _json_safe(result)

        with Session(engine) as session:
            update_paper_metadata(
                session, paper_id,
                result.get("title", ""),
                result.get("venue", "Unknown"),
                result.get("year", 0),
            )
            save_analysis_result(session, paper_id, result)
            if code_url:
                update_paper_code_url(session, paper_id, code_url)
            update_paper_status(session, paper_id, "done")

        # 主体已落库，断点缓存不再需要
        clear_cache(paper_id)
        # main_done：通知前端立即渲染全部主体字段（不等待相关论文）
        job.emit(StreamEvent(event="main_done", data={"message": "主体分析完成"}))

        # ---- 5. 相关论文搜索（最佳努力；超时/失败仅影响此模块）----
        if related_done and result.get("related_papers"):
            emit_progress(96, "已从缓存恢复相关论文", "analyzing")
            job.emit(StreamEvent(event="related_status", data={"status": "ok"}))
            job.emit(StreamEvent(event="field", field_name="related_papers", data={"value": result["related_papers"]}))
        else:
            paper_title = result.get("title", "")
            if paper_title:
                emit_progress(92, "正在后台搜索相关论文...", "analyzing")
                job.emit(StreamEvent(event="related_status", data={"status": "loading"}))

                async def _fetch_related() -> list:
                    from .ai_config import get_findpapers_config
                    from .semantic_scholar import generate_relevance_descriptions
                    fp_cfg = get_findpapers_config()
                    if fp_cfg.get("enabled"):
                        from .findpapers_service import search_related_papers as _fp_search
                        found = await _fp_search(paper_title, fp_cfg)
                    else:
                        from .semantic_scholar import search_related_papers
                        found = await search_related_papers(paper_title)
                    if found:
                        found = await generate_relevance_descriptions(
                            paper_title,
                            result.get("one_sentence", ""),
                            found,
                            ai_cfg["api_key"],
                            ai_cfg["base_url"],
                            ai_cfg["model"],
                        )
                    return found or []

                related_ok = False
                try:
                    related = await asyncio.wait_for(_fetch_related(), timeout=RELATED_PAPERS_TIMEOUT)
                    result["related_papers"] = _json_safe(related)
                    related_ok = True
                except asyncio.TimeoutError:
                    logger.warning(f"Related papers lookup timed out after {RELATED_PAPERS_TIMEOUT}s, skipping")
                except Exception as e:
                    logger.warning(f"Related papers lookup failed: {e}")

                if related_ok:
                    with Session(engine) as session:
                        update_related_papers(session, paper_id, result["related_papers"])
                    job.emit(StreamEvent(event="related_status", data={"status": "ok"}))
                    job.emit(StreamEvent(event="field", field_name="related_papers", data={"value": result["related_papers"]}))
                else:
                    # 仅此模块标记失败，主体结果不受影响
                    job.emit(StreamEvent(event="related_status", data={"status": "failed"}))

        # ---- 6. 完成 ----
        emit_progress(100, "解析完成", "done")
        job.emit(StreamEvent(event="done", data={"message": "解析完成"}))

    except Exception as e:
        logger.error(f"Background analysis failed for paper {paper_id}: {e}")
        with Session(engine) as session:
            update_paper_status(session, paper_id, "failed", str(e))
        job.emit(StreamEvent(event="error", data={"message": f"解析失败: {str(e)}"}))
    finally:
        job.done = True

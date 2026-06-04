"""
Analysis 路由

处理论文解析结果的查询和流式解析。
"""

from fastapi import APIRouter
from ..models.schemas import AnalysisResponse, StreamEvent

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ============================================================
# GET /api/analysis/{paper_id}
# ============================================================
# 获取论文的完整解析结果。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: AnalysisResponse
#   - 包含全部结构化分析数据
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在或尚未解析
#
# Response 400: ErrorResponse
#   - code="NOT_READY": 解析尚未完成
#   - code="PARSING_FAILED": 解析失败


# ============================================================
# GET /api/analysis/{paper_id}/stream
# ============================================================
# SSE 流式解析端点。
#
# 连接此端点后，服务器通过 Server-Sent Events 实时推送解析进度。
# 前端在上传 PDF 后立即连接此端点，逐字段接收解析结果。
#
# Response 200: text/event-stream
#
# SSE 事件格式：
#   event: status
#   data: {"event": "status", "data": {"message": "正在提取 PDF 文本..."}}
#
#   event: field
#   data: {"event": "field", "field_name": "title", "data": {"value": "Attention Is All You Need"}}
#
#   event: done
#   data: {"event": "done", "data": {"message": "解析完成"}}
#
#   event: error
#   data: {"event": "error", "data": {"message": "解析失败: ..."}}
#
# 完整的事件序列：
#   1. status: "正在分析论文内容..."
#   2. field: title
#   3. field: venue
#   4. field: year
#   5. field: one_sentence
#   6. field: problem
#   7. field: key_contributions
#   8. field: method
#   9. field: experiments
#   10. field: code_mapping
#   11. field: interview_qa
#   12. field: limitations
#   13. field: related_papers
#   14. field: mermaid_chart
#   15. done
#
# 重连策略（修正版）：
#   - 重连时检查 paper.status：
#     - 如果 status="done"：直接将已存储的 AnalysisResult 逐字段以 field 事件发送，最后发 done
#     - 如果 status="failed"：直接发 error 事件
#     - 如果 status="analyzing"/"extracting"：重新启动分析流程（因为无法确定 AI 输出到哪了）
#     - 如果 status="pending"：启动新的分析
#   - 不依赖内存中的 partial 结果，而是查 DB 状态决定行为
#
# 实现要点：
#   - 使用 StreamingResponse(media_type="text/event-stream")
#   - 生成器函数中执行分析逻辑，yield SSE 格式文本
#   - 确保设置正确的 CORS 和 cache-control headers
#   - 服务器无需存储 partial 结果，重连时按 status 决定策略


# ============================================================
# POST /api/analysis/{paper_id}/reparse
# ============================================================
# 重新解析一篇论文。
#
# 用于解析失败后重试，或用户想要更新解析结果。
#
# Response 200: PaperResponse
#   - 返回更新后的论文记录，status="pending"
#   - 前端应重新连接 SSE 端点
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在

"""
Papers 路由

处理论文的上传、列表、查询、删除等操作。
"""

from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import FileResponse
from ..models.schemas import PaperResponse, PaperListResponse, ErrorResponse

router = APIRouter(prefix="/api/papers", tags=["papers"])


# ============================================================
# POST /api/papers/upload
# ============================================================
# 上传 PDF 文件并触发解析流程。
#
# Request: multipart/form-data
#   - file: UploadFile (PDF 文件，最大 50MB)
#
# Response 200: PaperResponse
#   - 返回创建的论文记录，status="pending"
#   - 前端收到后应立即连接 SSE 端点获取解析进度
#
# Response 400: ErrorResponse
#   - code="FILE_TOO_LARGE": 文件超过 50MB
#   - code="INVALID_FORMAT": 非 PDF 文件
#
# 业务流程：
#   1. 验证文件格式和大小
#   2. 保存文件到 uploads/ 目录
#   3. 创建 Paper 记录（status="pending"）
#   4. 返回论文记录
#   5. 后台异步启动解析（不在请求中阻塞）


# ============================================================
# GET /api/papers
# ============================================================
# 获取论文列表，支持筛选和搜索。
#
# Query Parameters:
#   - search: str = None      # 按标题/关键词搜索
#   - venue: str = None       # 按会议筛选
#   - year: int = None        # 按年份筛选
#   - page: int = 1           # 页码
#   - per_page: int = 20      # 每页数量
#
# Response 200: PaperListResponse
#   - papers: list[PaperResponse]  # 当前页的论文列表
#   - total: int                   # 总数


# ============================================================
# GET /api/papers/{paper_id}
# ============================================================
# 获取单篇论文详情。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: PaperResponse
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# DELETE /api/papers/{paper_id}
# ============================================================
# 删除一篇论文及其解析结果。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: {"message": "Paper deleted successfully"}
#
# 业务流程：
#   1. 删除 AnalysisResult 记录（如果有）
#   2. 删除 Paper 记录
#   3. 删除上传的 PDF 文件
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在


# ============================================================
# GET /api/papers/{paper_id}/export
# ============================================================
# 导出论文解析结果为 Markdown。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: text/markdown
#   - Content-Disposition: attachment; filename="{title}.md"
#   - 返回格式化的 Markdown 文本
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# Response 400: ErrorResponse
#   - code="NOT_READY": 论文尚未解析完成


# ============================================================
# GET /api/papers/{paper_id}/pdf
# ============================================================
# 获取论文 PDF 文件（用于前端 PdfViewer 预览）。
#
# Path Parameters:
#   - paper_id: int
#
# Response 200: application/pdf
#   - 返回 PDF 文件流
#   - Content-Disposition: inline（浏览器内预览，不下载）
#
# Response 404: ErrorResponse
#   - code="NOT_FOUND": 论文不存在
#
# 实现要点：
#   1. 根据 paper_id 查询 Paper 记录获取 pdf_path
#   2. 使用 FileResponse 返回文件
#   3. media_type="application/pdf"
#   4. 前端通过此 URL 传给 react-pdf 的 Document 组件

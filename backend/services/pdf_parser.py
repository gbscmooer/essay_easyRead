"""
PDF 解析服务

使用 PyMuPDF (fitz) 提取 PDF 文本，pypdf 作为后备方案。
借鉴 DeepTutor 的双路径提取和错误层次化模式。

提取策略：
  - 主路径：PyMuPDF（速度快，处理复杂布局更好）
  - 后备路径：pypdf（当 PyMuPDF 失败时自动切换）
  - 智能截断：保留前3页+正文前部+最后1页
"""

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Token 限制策略
MAX_CHARS = 100_000  # 约 25k-30k tokens


class PDFExtractionError(Exception):
    """PDF 提取错误基类"""
    pass


class PDFNotFoundError(PDFExtractionError):
    """文件不存在"""
    pass


class PDFInvalidError(PDFExtractionError):
    """文件不是有效的 PDF"""
    pass


class PDFEmptyError(PDFExtractionError):
    """PDF 无法提取任何文本"""
    pass


@dataclass
@dataclass
class HeadingPosition:
    """PDF 标题位置"""
    page: int        # 页码（1-based）
    y_percent: float # 标题在页面中的垂直位置（0.0~1.0）
    text: str        # 标题文字


class PDFContent:
    """
    PDF 提取结果

    Attributes:
        full_text: 全文文本，按页拼接，页间用分隔符标记
        page_texts: 每页的文本列表，索引对应页码（0-based）
        total_pages: 总页数
        metadata: PDF 元信息（作者、标题等，可能为空）
        truncated: 是否因超长而被截断
        headings: 标题位置列表（用于大纲精确定位）
    """

    full_text: str
    page_texts: list[str]
    total_pages: int
    metadata: dict
    truncated: bool = False
    headings: list[HeadingPosition] | None = None


def _truncate_text(page_texts: list[str], max_chars: int) -> tuple[str, bool]:
    """
    智能截断策略。

    对于超长论文，保留：
    - 前 3 页（通常包含摘要、引言、方法概述）
    - 之后的页面按顺序填充直到达到限制
    - 最后 1 页（通常包含结论）

    Returns:
        (截断后的全文, 是否被截断)
    """
    # 先尝试不截断
    full = "\n".join(
        f"\n--- Page {i+1} ---\n{text}" for i, text in enumerate(page_texts)
    )
    if len(full) <= max_chars:
        return full, False

    # 需要截断：保留前 3 页 + 最后 1 页，中间按顺序填充
    keep_parts = []
    current_len = 0
    reserved_end = len(page_texts[-1]) + 50  # 最后一页 + 分隔符

    for i, text in enumerate(page_texts):
        part = f"\n--- Page {i+1} ---\n{text}"
        if i < 3 or current_len + len(part) + reserved_end <= max_chars:
            keep_parts.append(part)
            current_len += len(part)
        elif i == len(page_texts) - 1:
            # 最后一页
            keep_parts.append("\n--- [中间页面已省略] ---\n")
            keep_parts.append(part)
            break
        else:
            # 超出限制，跳过中间页面
            continue

    return "".join(keep_parts), True


def _extract_headings(pdf_path: str) -> list[HeadingPosition]:
    """用 PyMuPDF 提取 PDF 标题位置（字号显著大于正文的文字块）"""
    try:
        import fitz
        from collections import Counter

        doc = fitz.open(pdf_path)
        headings: list[HeadingPosition] = []

        try:
            # 第一遍：统计所有字号，找出正文字号（出现最多的）
            font_sizes: Counter[float] = Counter()
            for page in doc:
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                for block in blocks:
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            size = round(span["size"], 1)
                            text = span["text"].strip()
                            if text and len(text) > 1:
                                font_sizes[size] += len(text)

            if not font_sizes:
                return []

            body_size = font_sizes.most_common(1)[0][0]

            # 第二遍：提取字号 > body_size * 1.15 的文字块作为标题
            for page_num, page in enumerate(doc):
                page_height = page.rect.height
                if page_height <= 0:
                    continue
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                for block in blocks:
                    if block.get("type") != 0:
                        continue
                    block_lines = []
                    block_y = None
                    max_size = 0
                    is_bold = False
                    for line in block.get("lines", []):
                        line_text_parts = []
                        for span in line.get("spans", []):
                            text = span["text"].strip()
                            if text:
                                line_text_parts.append(text)
                                if span["size"] > max_size:
                                    max_size = round(span["size"], 1)
                                if "bold" in span.get("font", "").lower():
                                    is_bold = True
                            if block_y is None and span.get("origin"):
                                block_y = span["origin"][1]
                        if line_text_parts:
                            block_lines.append(" ".join(line_text_parts))

                    block_text = " ".join(block_lines).strip()
                    if not block_text or len(block_text) < 2:
                        continue

                    # 标题判定：字号显著大于正文，或者是加粗的大字
                    is_heading = (
                        max_size > body_size * 1.15
                        or (is_bold and max_size >= body_size * 1.05)
                    )
                    # 排除太长的文字块（超过 200 字符一般不是标题）
                    if is_heading and len(block_text) <= 200 and block_y is not None:
                        y_percent = block_y / page_height
                        # 只取页面上半部分的标题（y < 50%）
                        if y_percent < 0.5:
                            headings.append(HeadingPosition(
                                page=page_num + 1,  # 1-based
                                y_percent=round(y_percent, 4),
                                text=block_text[:100],
                            ))

            return headings
        finally:
            doc.close()

    except Exception as e:
        logger.debug(f"Heading extraction failed: {e}")
        return []


def _extract_with_pymupdf(pdf_path: str) -> tuple[list[str], dict] | None:
    """
    使用 PyMuPDF 提取 PDF 文本（主路径）。

    Returns:
        (page_texts, metadata) 或 None（如果失败）
    """
    try:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            page_texts = []
            for page in doc:
                text = page.get_text("text")
                page_texts.append(text)

            metadata = {}
            if doc.metadata:
                for key, value in doc.metadata.items():
                    if value:
                        metadata[key] = value

            return page_texts, metadata
        finally:
            doc.close()

    except Exception as e:
        logger.warning(f"PyMuPDF extraction failed: {e}")
        return None


def _extract_with_pypdf(pdf_path: str) -> tuple[list[str], dict] | None:
    """
    使用 pypdf 提取 PDF 文本（后备路径）。

    借鉴 DeepTutor 的双路径策略：当 PyMuPDF 失败时自动切换到 pypdf。

    Returns:
        (page_texts, metadata) 或 None（如果失败）
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        page_texts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            page_texts.append(text)

        metadata = {}
        if reader.metadata:
            for key, value in reader.metadata.items():
                if value:
                    # pypdf 的 metadata key 可能带 / 前缀
                    clean_key = key.lstrip("/")
                    metadata[clean_key] = str(value)

        return page_texts, metadata

    except Exception as e:
        logger.warning(f"pypdf extraction failed: {e}")
        return None


def extract_pdf_content(pdf_path: str) -> PDFContent:
    """
    从 PDF 文件中提取全部文本内容。

    双路径策略（借鉴 DeepTutor）：
    1. 优先使用 PyMuPDF（速度快，处理复杂布局更好）
    2. 如果 PyMuPDF 失败，自动切换到 pypdf
    3. 两者都失败则抛出异常

    Args:
        pdf_path: PDF 文件的本地路径

    Returns:
        PDFContent 包含全文、逐页文本、页数和元信息

    Raises:
        PDFNotFoundError: 文件不存在
        PDFInvalidError: 文件不是有效的 PDF
        PDFEmptyError: PDF 无法提取任何文本
    """
    if not os.path.exists(pdf_path):
        raise PDFNotFoundError(f"PDF file not found: {pdf_path}")

    # 检查文件魔数（借鉴 DeepTutor 的 magic byte validation）
    try:
        with open(pdf_path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                raise PDFInvalidError("File is not a valid PDF (missing %PDF- header)")
    except (IOError, OSError) as e:
        raise PDFInvalidError(f"Cannot read file: {e}")

    # 主路径：PyMuPDF
    result = _extract_with_pymupdf(pdf_path)

    # 后备路径：pypdf
    if result is None:
        logger.info("Falling back to pypdf for PDF extraction")
        result = _extract_with_pypdf(pdf_path)

    if result is None:
        raise PDFInvalidError("Both PyMuPDF and pypdf failed to extract PDF content")

    page_texts, metadata = result

    # 检查是否提取到了内容
    if not page_texts or all(not text.strip() for text in page_texts):
        raise PDFEmptyError("PDF contains no extractable text (might be scanned/image-based)")

    full_text, truncated = _truncate_text(page_texts, MAX_CHARS)

    # 提取标题坐标（用于大纲精确定位）
    headings = _extract_headings(pdf_path)

    return PDFContent(
        full_text=full_text,
        page_texts=page_texts,
        total_pages=len(page_texts),
        metadata=metadata,
        truncated=truncated,
        headings=headings if headings else None,
    )

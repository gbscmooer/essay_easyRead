"""
PDF 解析服务

使用 PyMuPDF (fitz) 提取 PDF 的文本内容和图片。
为 AI 分析提供原始素材。
"""

from dataclasses import dataclass

# Token 限制策略
# MiMo API 上下文窗口约 128k tokens，但论文文本占大部分。
# 预留 system prompt + JSON 输出的空间，限制输入文本。
MAX_CHARS = 100_000  # 约 25k-30k tokens（英文约 4 chars/token）
# 超过此长度时，保留摘要页（前几页）+ 正文前部 + 结尾，截断中间部分。


@dataclass
class PDFContent:
    """
    PDF 提取结果

    Attributes:
        full_text: 全文文本，按页拼接，页间用分隔符标记
        page_texts: 每页的文本列表，索引对应页码（0-based）
        total_pages: 总页数
        metadata: PDF 元信息（作者、标题等，可能为空）
        truncated: 是否因超长而被截断
    """

    full_text: str
    page_texts: list[str]
    total_pages: int
    metadata: dict
    truncated: bool = False


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
            keep_parts.append(f"\n--- [中间页面已省略] ---\n")
            keep_parts.append(part)
            break
        else:
            # 超出限制，跳过中间页面
            continue

    return "".join(keep_parts), True


def extract_pdf_content(pdf_path: str) -> PDFContent:
    """
    从 PDF 文件中提取全部文本内容。

    Args:
        pdf_path: PDF 文件的本地路径

    Returns:
        PDFContent 包含全文、逐页文本、页数和元信息

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件不是有效的 PDF

    实现要点：
        1. 使用 fitz.open(pdf_path) 打开文件
        2. 遍历每一页，调用 page.get_text() 提取文本
        3. 处理数学公式：保留 LaTeX 表示（如 $E=mc^2$）
        4. 处理表格：尽量保留表格结构
        5. 拼接全文时，使用 _truncate_text 处理超长文本
        6. 提取 PDF 元信息（doc.metadata）
        7. 不提取图片（图片由 AI 直接从 PDF 理解）
        8. 如果文本被截断，PDFContent.truncated=True
    """
    import fitz
    import os

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise ValueError(f"Invalid PDF file: {e}")

    try:
        page_texts = []
        for page in doc:
            text = page.get_text("text")
            page_texts.append(text)

        full_text, truncated = _truncate_text(page_texts, MAX_CHARS)

        metadata = {}
        if doc.metadata:
            for key, value in doc.metadata.items():
                if value:
                    metadata[key] = value

        return PDFContent(
            full_text=full_text,
            page_texts=page_texts,
            total_pages=len(doc),
            metadata=metadata,
            truncated=truncated,
        )
    finally:
        doc.close()

"""
PDF 解析服务的单元测试

测试 _truncate_text 函数的边界条件和截断策略。
"""

import pytest
from backend.services.pdf_parser import _truncate_text, MAX_CHARS


class TestTruncateText:
    """_truncate_text 函数测试"""

    def test_short_text_not_truncated(self):
        """短文本不应被截断"""
        pages = ["Hello World", "Page 2 content"]
        result, truncated = _truncate_text(pages, MAX_CHARS)
        assert not truncated
        assert "Hello World" in result
        assert "Page 2 content" in result

    def test_empty_pages(self):
        """空页面列表"""
        result, truncated = _truncate_text([], MAX_CHARS)
        assert not truncated
        assert result == ""

    def test_single_page(self):
        """单页文档"""
        pages = ["Single page content"]
        result, truncated = _truncate_text(pages, MAX_CHARS)
        assert not truncated
        assert "Single page content" in result

    def test_long_text_truncated(self):
        """超长文本应被截断"""
        # 创建超过限制的文本
        long_page = "x" * 50000
        pages = [long_page] * 5  # 250k 字符
        result, truncated = _truncate_text(pages, MAX_CHARS)
        assert truncated
        # 截断后应比原文本短（原文本 250k+，截断后保留前3页+最后1页）
        assert len(result) < len("\n".join(f"\n--- Page {i+1} ---\n{text}" for i, text in enumerate(pages)))

    def test_truncation_preserves_first_pages(self):
        """截断时应保留前 3 页"""
        pages = [f"Page {i+1} content" for i in range(10)]
        # 使文本足够长以触发截断
        long_pages = [p * 10000 for p in pages]
        result, truncated = _truncate_text(long_pages, MAX_CHARS)
        assert truncated
        assert "Page 1 content" in result
        assert "Page 2 content" in result
        assert "Page 3 content" in result

    def test_truncation_preserves_last_page(self):
        """截断时应保留最后一页"""
        pages = [f"Page {i+1}" for i in range(10)]
        long_pages = [p * 10000 for p in pages]
        result, truncated = _truncate_text(long_pages, MAX_CHARS)
        assert truncated
        assert "Page 10" in result

    def test_truncation_adds_ellipsis(self):
        """截断时应添加省略标记"""
        pages = [f"Page {i+1}" for i in range(10)]
        long_pages = [p * 10000 for p in pages]
        result, truncated = _truncate_text(long_pages, MAX_CHARS)
        assert truncated
        assert "中间页面已省略" in result

    def test_page_separator_format(self):
        """页面分隔符格式正确"""
        pages = ["Page 1", "Page 2"]
        result, _ = _truncate_text(pages, MAX_CHARS)
        assert "--- Page 1 ---" in result
        assert "--- Page 2 ---" in result

    def test_exactly_at_limit(self):
        """刚好在限制内的文本不应被截断"""
        # 创建刚好在限制内的文本
        page_size = MAX_CHARS // 3 - 100  # 减去分隔符开销
        pages = ["a" * page_size, "b" * page_size, "c" * page_size]
        result, truncated = _truncate_text(pages, MAX_CHARS)
        assert not truncated

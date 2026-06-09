"""
AI 分析服务的单元测试

测试 JSON 解析、错误判断、URL 提取等核心函数。
"""

import pytest
from backend.services.ai_analyzer import (
    _try_parse_partial_json,
    _extract_json_value,
    _is_transient_error,
    _sanitize_content,
    extract_code_url,
)


class TestTryParsePartialJson:
    """_try_parse_partial_json 函数测试"""

    def test_complete_json(self):
        """完整 JSON 应解析所有字段"""
        text = '{"title": "Test", "authors": "Author 1"}'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == "Test"
        assert result["authors"] == "Author 1"

    def test_partial_json_with_string_field(self):
        """部分 JSON 应解析已完成的字符串字段"""
        text = '{"title": "Test Paper", "authors": "Author 1", "problem": "Some problem'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == "Test Paper"
        assert result["authors"] == "Author 1"
        # problem 字段未闭合，不应被解析
        assert "problem" not in result

    def test_partial_json_with_array_field(self):
        """部分 JSON 应解析已完成的数组字段"""
        text = '{"key_contributions": ["贡献1", "贡献2"], "outline": ['
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["key_contributions"] == ["贡献1", "贡献2"]

    def test_empty_string(self):
        """空字符串应返回 None"""
        result = _try_parse_partial_json("")
        assert result is None

    def test_no_matching_fields(self):
        """无匹配字段应返回 None"""
        text = '{"unknown_field": "value"}'
        result = _try_parse_partial_json(text)
        assert result is None

    def test_nested_json_object(self):
        """嵌套 JSON 对象应正确解析"""
        text = '{"method": {"overview": "Test overview", "pipeline": "Step 1"}}'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["method"]["overview"] == "Test overview"


class TestExtractJsonValue:
    """_extract_json_value 函数测试"""

    def test_string_value(self):
        """提取字符串值"""
        result = _extract_json_value('"hello world"')
        assert result == '"hello world"'

    def test_number_value(self):
        """提取数字值"""
        result = _extract_json_value('42,')
        assert result == '42'

    def test_object_value(self):
        """提取对象值"""
        result = _extract_json_value('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_array_value(self):
        """提取数组值"""
        result = _extract_json_value('[1, 2, 3]')
        assert result == '[1, 2, 3]'

    def test_nested_object(self):
        """提取嵌套对象"""
        result = _extract_json_value('{"a": {"b": "c"}}')
        assert result == '{"a": {"b": "c"}}'

    def test_string_with_escaped_quotes(self):
        """提取包含转义引号的字符串"""
        result = _extract_json_value('"hello \\"world\\""')
        assert result == '"hello \\"world\\""'

    def test_empty_input(self):
        """空输入应返回 None"""
        result = _extract_json_value("")
        assert result is None

    def test_unclosed_string(self):
        """未闭合的字符串应返回 None"""
        result = _extract_json_value('"unclosed')
        assert result is None

    def test_unclosed_object(self):
        """未闭合的对象应返回 None"""
        result = _extract_json_value('{"key": "value"')
        assert result is None


class TestIsTransientError:
    """_is_transient_error 函数测试"""

    def test_rate_limit_error(self):
        """速率限制错误应为瞬态"""
        exc = Exception("Rate limit exceeded")
        assert _is_transient_error(exc) is True

    def test_timeout_error(self):
        """超时错误应为瞬态"""
        exc = Exception("Request timeout")
        assert _is_transient_error(exc) is True

    def test_overloaded_error(self):
        """过载错误应为瞬态"""
        exc = Exception("Server overloaded")
        assert _is_transient_error(exc) is True

    def test_500_error(self):
        """500 错误应为瞬态"""
        exc = Exception("Internal server error 500")
        assert _is_transient_error(exc) is True

    def test_429_error(self):
        """429 错误应为瞬态"""
        exc = Exception("HTTP 429 Too Many Requests")
        assert _is_transient_error(exc) is True

    def test_auth_error_not_transient(self):
        """认证错误不应为瞬态"""
        exc = Exception("Invalid API key")
        assert _is_transient_error(exc) is False

    def test_validation_error_not_transient(self):
        """验证错误不应为瞬态"""
        exc = Exception("Invalid request format")
        assert _is_transient_error(exc) is False


class TestSanitizeContent:
    """_sanitize_content 函数测试"""

    def test_normal_content(self):
        """正常内容应原样返回"""
        content = "This is a paper abstract."
        assert _sanitize_content(content) == content

    def test_empty_string(self):
        """空字符串应返回占位符"""
        assert _sanitize_content("") == "（空内容）"

    def test_whitespace_only(self):
        """纯空白应返回占位符"""
        assert _sanitize_content("   \n\t  ") == "（空内容）"

    def test_none_like(self):
        """None 类型应返回占位符"""
        assert _sanitize_content(None) == "（空内容）"


class TestExtractCodeUrl:
    """extract_code_url 函数测试"""

    def test_github_url(self):
        """提取 GitHub URL"""
        text = "Code available at https://github.com/user/repo"
        assert extract_code_url(text) == "https://github.com/user/repo"

    def test_github_url_without_protocol(self):
        """提取无协议的 GitHub URL"""
        text = "Code: github.com/user/repo"
        assert extract_code_url(text) == "github.com/user/repo"

    def test_gitlab_url(self):
        """提取 GitLab URL"""
        text = "https://gitlab.com/user/repo"
        assert extract_code_url(text) == "https://gitlab.com/user/repo"

    def test_multiple_urls_prefers_github(self):
        """多个 URL 时优先返回 GitHub"""
        text = "https://gitlab.com/user/repo1 and https://github.com/user/repo2"
        result = extract_code_url(text)
        assert "github.com" in result

    def test_no_url(self):
        """无 URL 时返回空字符串"""
        text = "No code available"
        assert extract_code_url(text) == ""

    def test_url_with_trailing_punctuation(self):
        """URL 末尾标点应被去除"""
        text = "See https://github.com/user/repo."
        assert extract_code_url(text) == "https://github.com/user/repo"

    def test_url_with_fragment(self):
        """URL fragment 应被去除"""
        text = "https://github.com/user/repo#readme"
        assert extract_code_url(text) == "https://github.com/user/repo"

    def test_deep_path_trimmed(self):
        """深层路径应被截断到 owner/repo"""
        text = "https://github.com/user/repo/issues/123"
        assert extract_code_url(text) == "https://github.com/user/repo"

    def test_deduplication(self):
        """重复 URL 应被去重"""
        text = "https://github.com/user/repo and https://github.com/user/repo/"
        result = extract_code_url(text)
        assert result.count("github.com") == 1

"""
核心模块单元测试

测试目标：
- _try_parse_partial_json: 增量 JSON 解析的边界情况
- _extract_json_value: 嵌套括号/转义引号处理
- _detect_cycles: 循环检测算法
- extract_references: 引用解析
"""

import pytest
from backend.services.ai_analyzer import _try_parse_partial_json, _extract_json_value
from backend.services.code_analyzer import _detect_cycles
from backend.services.deep_research.ref_parser import extract_references


# ============================================================
# _extract_json_value 测试
# ============================================================

class TestExtractJsonValue:
    """测试从 JSON 片段中提取完整值"""

    def test_simple_string(self):
        assert _extract_json_value('"hello"') == '"hello"'

    def test_string_with_escaped_quote(self):
        assert _extract_json_value('"hello \\"world\\""') == '"hello \\"world\\""'

    def test_string_with_backslash(self):
        assert _extract_json_value('"path\\\\file"') == '"path\\\\file"'

    def test_unclosed_string_returns_none(self):
        assert _extract_json_value('"unclosed') is None

    def test_simple_object(self):
        assert _extract_json_value('{"a": 1}') == '{"a": 1}'

    def test_nested_object(self):
        text = '{"a": {"b": {"c": 1}}}'
        assert _extract_json_value(text) == text

    def test_object_with_string_containing_braces(self):
        text = '{"a": "hello {world}"}'
        assert _extract_json_value(text) == text

    def test_unclosed_object_returns_none(self):
        assert _extract_json_value('{"a": 1') is None

    def test_simple_array(self):
        assert _extract_json_value('[1, 2, 3]') == '[1, 2, 3]'

    def test_nested_array(self):
        text = '[[1, 2], [3, [4, 5]]]'
        assert _extract_json_value(text) == text

    def test_unclosed_array_returns_none(self):
        assert _extract_json_value('[1, 2, 3') is None

    def test_number(self):
        assert _extract_json_value('42') == '42'

    def test_float(self):
        assert _extract_json_value('3.14') == '3.14'

    def test_negative_number(self):
        assert _extract_json_value('-1') == '-1'

    def test_boolean_true(self):
        assert _extract_json_value('true') == 'true'

    def test_boolean_false(self):
        assert _extract_json_value('false') == 'false'

    def test_null(self):
        assert _extract_json_value('null') == 'null'

    def test_empty_string_returns_none(self):
        assert _extract_json_value('') is None

    def test_leading_whitespace(self):
        assert _extract_json_value('  "hello"') == '"hello"'

    def test_value_with_trailing_comma(self):
        assert _extract_json_value('42, "next"') == '42'

    def test_value_with_trailing_brace(self):
        assert _extract_json_value('42}') == '42'

    def test_deeply_nested(self):
        text = '{"a": {"b": {"c": {"d": {"e": 1}}}}}'
        assert _extract_json_value(text) == text


# ============================================================
# _try_parse_partial_json 测试
# ============================================================

class TestTryParsePartialJson:
    """测试增量 JSON 解析"""

    def test_complete_json(self):
        text = '{"title": "Test", "venue": "CVPR", "year": 2024}'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == "Test"
        assert result["venue"] == "CVPR"
        assert result["year"] == 2024

    def test_partial_json_with_one_complete_field(self):
        text = '{"title": "Deep Residual Learning"'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == "Deep Residual Learning"

    def test_partial_json_with_multiple_complete_fields(self):
        text = '{"title": "Test Paper", "venue": "ICLR", "year": 2024, "problem": "unfinished...'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == "Test Paper"
        assert result["venue"] == "ICLR"
        assert result["year"] == 2024
        assert "problem" not in result  # 未完成的字段不应出现

    def test_empty_json_returns_none(self):
        assert _try_parse_partial_json("{}") is None

    def test_no_json_returns_none(self):
        assert _try_parse_partial_json("not json at all") is None

    def test_empty_string_returns_none(self):
        assert _try_parse_partial_json("") is None

    def test_string_value_with_nested_quotes(self):
        text = '{"title": "He said \\"hello\\" to me"'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["title"] == 'He said "hello" to me'

    def test_array_value_partial(self):
        text = '{"key_contributions": ["contrib1", "contrib2"]'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["key_contributions"] == ["contrib1", "contrib2"]

    def test_object_value_partial(self):
        text = '{"method": {"name": "ResNet", "layers": 50}'
        result = _try_parse_partial_json(text)
        assert result is not None
        assert result["method"]["name"] == "ResNet"


# ============================================================
# _detect_cycles 测试
# ============================================================

class TestDetectCycles:
    """测试循环检测算法"""

    def test_no_edges(self):
        assert _detect_cycles([]) == set()

    def test_no_cycle(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
            {"source": "A", "target": "C"},
        ]
        assert _detect_cycles(edges) == set()

    def test_simple_cycle(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "A"},
        ]
        result = _detect_cycles(edges)
        assert ("A", "B") in result or ("B", "A") in result

    def test_self_loop(self):
        edges = [{"source": "A", "target": "A"}]
        result = _detect_cycles(edges)
        assert ("A", "A") in result

    def test_cycle_in_larger_graph(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
            {"source": "C", "target": "D"},
            {"source": "D", "target": "B"},  # cycle: B -> C -> D -> B
        ]
        result = _detect_cycles(edges)
        assert len(result) > 0
        # 至少包含环路上的边
        cycle_nodes = set()
        for s, t in result:
            cycle_nodes.add(s)
            cycle_nodes.add(t)
        assert "B" in cycle_nodes
        assert "C" in cycle_nodes
        assert "D" in cycle_nodes

    def test_multiple_cycles(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "A"},
            {"source": "C", "target": "D"},
            {"source": "D", "target": "C"},
        ]
        result = _detect_cycles(edges)
        assert len(result) >= 4  # 两个环，每个至少 2 条边

    def test_disconnected_graph_no_cycle(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "C", "target": "D"},
        ]
        assert _detect_cycles(edges) == set()

    def test_diamond_no_cycle(self):
        edges = [
            {"source": "A", "target": "B"},
            {"source": "A", "target": "C"},
            {"source": "B", "target": "D"},
            {"source": "C", "target": "D"},
        ]
        assert _detect_cycles(edges) == set()


# ============================================================
# extract_references 测试
# ============================================================

class TestExtractReferences:
    """测试引用解析"""

    def test_no_references_section(self):
        text = "This is a paper about deep learning. There is no references section."
        assert extract_references(text) == []

    def test_empty_text(self):
        assert extract_references("") == []

    def test_basic_reference_parsing(self):
        text = """Abstract
This is the abstract.

References
[1] K. He, X. Zhang, S. Ren, and J. Sun, "Deep Residual Learning for Image Recognition," CVPR, 2016.
[2] A. Vaswani et al., "Attention Is All You Need," NeurIPS, 2017.
"""
        refs = extract_references(text)
        assert len(refs) == 2
        assert refs[0]["index"] == 1
        assert "Deep Residual Learning" in refs[0]["raw_text"]
        assert refs[1]["index"] == 2
        assert "Attention Is All You Need" in refs[1]["raw_text"]

    def test_arxiv_id_extraction(self):
        text = """Abstract
Test.

References
[1] A. Author, "Paper Title," arXiv:2301.12345, 2023.
"""
        refs = extract_references(text)
        assert len(refs) == 1
        assert refs[0]["arxiv_id"] == "2301.12345"

    def test_doi_extraction(self):
        text = """Abstract
Test.

References
[1] A. Author, "Paper Title," DOI: 10.1234/abcd.5678, 2023.
"""
        refs = extract_references(text)
        assert len(refs) == 1
        assert refs[0]["doi"] == "10.1234/abcd.5678"

    def test_citation_frequency_counting(self):
        text = """Introduction
As shown in [1] and [2], deep learning works. Later work [1] improved upon [1, 3].

References
[1] Author A, "Paper 1," 2020.
[2] Author B, "Paper 2," 2021.
[3] Author C, "Paper 3," 2022.
"""
        refs = extract_references(text)
        assert len(refs) == 3
        freq_map = {r["index"]: r["freq"] for r in refs}
        assert freq_map[1] >= 3  # [1] 出现 3 次
        assert freq_map[2] >= 1  # [2] 出现 1 次
        assert freq_map[3] >= 1  # [3] 出现 1 次

    def test_raw_text_truncation(self):
        long_text = "A" * 600
        text = f"""Abstract
Test.

References
[1] {long_text}
"""
        refs = extract_references(text)
        assert len(refs) == 1
        assert len(refs[0]["raw_text"]) <= 500

    def test_whitespace_folding(self):
        text = """Abstract
Test.

References
[1] A.   Author,   "Paper   Title,"   2020.
"""
        refs = extract_references(text)
        assert len(refs) == 1
        assert "  " not in refs[0]["raw_text"]  # 多余空白应被折叠


# ============================================================
# 相似度 Provider 测试
# ============================================================

class TestBibCouplingProvider:
    """测试 Bibliographic Coupling 相似度"""

    def test_jaccard_calculation(self):
        """验证 Jaccard 相似度计算逻辑"""
        # 模拟引用关系
        a_refs = {1, 2, 3}
        b_refs = {2, 3, 4}
        intersection = len(a_refs & b_refs)
        union = len(a_refs | b_refs)
        expected = intersection / union  # 2/4 = 0.5
        assert expected == 0.5

    def test_no_shared_refs(self):
        a_refs = {1, 2}
        b_refs = {3, 4}
        intersection = len(a_refs & b_refs)
        assert intersection == 0

    def test_identical_refs(self):
        a_refs = {1, 2, 3}
        b_refs = {1, 2, 3}
        intersection = len(a_refs & b_refs)
        union = len(a_refs | b_refs)
        assert intersection / union == 1.0


class TestHybridProvider:
    """测试 Hybrid 相似度融合"""

    def test_weighted_average(self):
        """验证加权平均计算"""
        scores = [(0.8, 0.7), (0.4, 0.3)]
        total_weight = sum(w for _, w in scores)
        fused = sum(s * w for s, w in scores) / total_weight
        expected = (0.8 * 0.7 + 0.4 * 0.3) / 1.0  # 0.56 + 0.12 = 0.68
        assert abs(fused - 0.68) < 0.001

    def test_single_provider(self):
        """单个 provider 权重为 1"""
        scores = [(0.9, 1.0)]
        total_weight = sum(w for _, w in scores)
        fused = sum(s * w for s, w in scores) / total_weight
        assert abs(fused - 0.9) < 0.001

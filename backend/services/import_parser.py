"""
多语言 import 解析器

使用 Python AST 模块精确解析 Python import，
JS/TS/C++ 使用增强正则解析。
"""

import ast
import logging
import os
import re

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field


@dataclass
class ParsedImport:
    """一条 import 语句的解析结果"""
    module: str           # 原始模块路径 ("models.backbone", "./utils")
    names: list[str]      # 导入的具体名称 (["Conv", "BN"])
    is_relative: bool     # 是否相对导入
    line_number: int      # import 所在行号 (1-based)
    is_type_only: bool = False  # 是否仅用于类型检查/类型导入


@dataclass
class FileImports:
    """一个文件的所有 import"""
    file_path: str        # 相对路径
    imports: list[ParsedImport] = field(default_factory=list)


# ============================================================
# Python — AST 解析
# ============================================================

def _parse_python(content: str) -> list[ParsedImport]:
    """使用 AST 精确解析 Python import"""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _parse_python_regex(content)

    visitor = _PythonImportVisitor(tree)
    visitor.visit(tree)
    return visitor.results


def _collect_type_checking_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """收集 TYPE_CHECKING 与 typing 模块别名，便于识别 if 条件。"""
    type_checking_names = {"TYPE_CHECKING"}
    typing_module_names = {"typing"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    typing_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in {"typing", "typing_extensions"}:
                for alias in node.names:
                    if alias.name == "TYPE_CHECKING":
                        type_checking_names.add(alias.asname or alias.name)

    return type_checking_names, typing_module_names


def _is_type_checking_test(
    node: ast.AST,
    type_checking_names: set[str],
    typing_module_names: set[str],
) -> bool:
    """判断 if 条件是否为 TYPE_CHECKING 风格条件。"""
    if isinstance(node, ast.Name):
        return node.id in type_checking_names

    if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
        return isinstance(node.value, ast.Name) and node.value.id in typing_module_names

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return any(
            _is_type_checking_test(value, type_checking_names, typing_module_names)
            for value in node.values
        )

    return False


class _PythonImportVisitor(ast.NodeVisitor):
    """按语句上下文收集 Python import。"""

    def __init__(self, tree: ast.AST):
        self.results: list[ParsedImport] = []
        self._type_checking_names, self._typing_module_names = _collect_type_checking_aliases(tree)
        self._is_type_only = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            asname = alias.asname
            self.results.append(ParsedImport(
                module=name,
                names=[asname or name.split(".")[-1]],
                is_relative=False,
                line_number=node.lineno,
                is_type_only=self._is_type_only,
            ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        level = node.level  # 0 = 绝对, 1 = from ., 2 = from ..
        is_relative = level > 0

        names = []
        for alias in node.names:
            if alias.name == "*":
                names.append("*")
            else:
                names.append(alias.asname or alias.name)

        self.results.append(ParsedImport(
            module=module,
            names=names,
            is_relative=is_relative,
            line_number=node.lineno,
            is_type_only=self._is_type_only,
        ))

    def visit_If(self, node: ast.If) -> None:
        body_is_type_only = self._is_type_only or _is_type_checking_test(
            node.test,
            self._type_checking_names,
            self._typing_module_names,
        )
        self._visit_with_type_only(node.body, body_is_type_only)
        self._visit_with_type_only(node.orelse, self._is_type_only)

    def _visit_with_type_only(self, statements: list[ast.stmt], is_type_only: bool) -> None:
        previous = self._is_type_only
        self._is_type_only = is_type_only
        try:
            for statement in statements:
                self.visit(statement)
        finally:
            self._is_type_only = previous


def _parse_python_regex(content: str) -> list[ParsedImport]:
    """Python 回退方案：增强正则（AST 解析失败时使用）"""
    results = []

    # from X import (Y, Z, ...) 多行支持
    for m in re.finditer(
        r"^from\s+(\.*)(\S+)\s+import\s+\(([^)]*)\)",
        content, re.MULTILINE | re.DOTALL
    ):
        dots, module, names_str = m.group(1), m.group(2), m.group(3)
        names = [n.strip().split(" as ")[0].strip()
                 for n in names_str.split(",") if n.strip()]
        line = content[:m.start()].count("\n") + 1
        results.append(ParsedImport(
            module=module, names=names,
            is_relative=len(dots) > 0, line_number=line,
        ))

    # from X import Y, Z (单行)
    for m in re.finditer(
        r"^from\s+(\.*)(\S+)\s+import\s+(.+)$",
        content, re.MULTILINE
    ):
        dots, module, names_str = m.group(1), m.group(2), m.group(3)
        if "(" in names_str:
            continue  # 已被上面的多行模式处理
        names = [n.strip().split(" as ")[0].strip()
                 for n in names_str.split(",") if n.strip()]
        line = content[:m.start()].count("\n") + 1
        results.append(ParsedImport(
            module=module, names=names,
            is_relative=len(dots) > 0, line_number=line,
        ))

    # import X, Y
    for m in re.finditer(r"^import\s+(\S+(?:\s*,\s*\S+)*)", content, re.MULTILINE):
        modules = [s.strip() for s in m.group(1).split(",")]
        line = content[:m.start()].count("\n") + 1
        for mod in modules:
            as_match = re.match(r"(\S+)\s+as\s+(\S+)", mod)
            if as_match:
                results.append(ParsedImport(
                    module=as_match.group(1),
                    names=[as_match.group(2)],
                    is_relative=False, line_number=line,
                ))
            else:
                results.append(ParsedImport(
                    module=mod,
                    names=[mod.split(".")[-1]],
                    is_relative=False, line_number=line,
                ))

    return results


# ============================================================
# JS/TS — 增强正则
# ============================================================

_JS_NAMED_IMPORT_RE = re.compile(
    r"""import\s+(?P<type>type\s+)?(?:(?P<default>[\w$]+)\s*,\s*)?\{(?P<named>[^}]*)\}\s*from\s*['"](?P<module>[^'"]+)['"]""",
    re.MULTILINE,
)

_JS_NAMESPACE_IMPORT_RE = re.compile(
    r"""import\s+(?P<type>type\s+)?\*\s+as\s+(?P<namespace>[\w$]+)\s+from\s*['"](?P<module>[^'"]+)['"]""",
    re.MULTILINE,
)

_JS_DEFAULT_IMPORT_RE = re.compile(
    r"""import\s+(?P<type>type\s+)?(?P<default>[\w$]+)\s+from\s*['"](?P<module>[^'"]+)['"]""",
    re.MULTILINE,
)

_JS_SIDE_EFFECT_IMPORT_RE = re.compile(
    r"""import\s*['"](?P<module>[^'"]+)['"]""",
    re.MULTILINE,
)

_JS_EXPORT_FROM_RE = re.compile(
    r"""export\s+\{[^}]*\}\s*from\s*['"](?P<module>[^'"]+)['"]""",
    re.MULTILINE,
)

_JS_REQUIRE_RE = re.compile(
    r"""require\s*\(\s*['"](?P<module>[^'"]+)['"]\s*\)""",
    re.MULTILINE,
)

_JS_DYNAMIC_IMPORT_RE = re.compile(
    r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)


def _parse_js_ts(content: str) -> list[ParsedImport]:
    """解析 JS/TS import/require/export-from"""
    matches = []

    for m in _JS_NAMED_IMPORT_RE.finditer(content):
        module = m.group("module")
        names, all_named_type_only = _parse_js_named_imports(m.group("named"))
        default_import = m.group("default")
        if default_import:
            names.append(default_import)

        is_type_only = bool(m.group("type")) or (bool(names) and all_named_type_only and not default_import)
        matches.append((m.start(), ParsedImport(
            module=module,
            names=names,
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
            is_type_only=is_type_only,
        )))

    for m in _JS_NAMESPACE_IMPORT_RE.finditer(content):
        module = m.group("module")
        namespace = m.group("namespace")
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[namespace],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
            is_type_only=bool(m.group("type")),
        )))

    for m in _JS_DEFAULT_IMPORT_RE.finditer(content):
        module = m.group("module")
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[m.group("default")],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
            is_type_only=bool(m.group("type")),
        )))

    for m in _JS_SIDE_EFFECT_IMPORT_RE.finditer(content):
        module = m.group("module")
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
        )))

    for m in _JS_EXPORT_FROM_RE.finditer(content):
        module = m.group("module")
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
        )))

    for m in _JS_REQUIRE_RE.finditer(content):
        module = m.group("module")
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
        )))

    # Dynamic import() — 弱引用
    for m in _JS_DYNAMIC_IMPORT_RE.finditer(content):
        module = m.group(1)
        matches.append((m.start(), ParsedImport(
            module=module,
            names=[],
            is_relative=module.startswith("."),
            line_number=content[:m.start()].count("\n") + 1,
        )))

    matches.sort(key=lambda item: item[0])
    return [item[1] for item in matches]


def _parse_js_named_imports(named_imports: str) -> tuple[list[str], bool]:
    """解析 JS/TS 花括号导入，并判断是否全部为 type specifier。"""
    names = []
    type_flags = []

    for part in named_imports.split(","):
        raw_name = part.strip()
        if not raw_name:
            continue

        is_type_specifier = raw_name.startswith("type ")
        if is_type_specifier:
            raw_name = raw_name[5:].strip()

        name = raw_name.split(" as ")[0].strip().split(":")[0].strip()
        if name:
            names.append(name)
            type_flags.append(is_type_specifier)

    return names, bool(type_flags) and all(type_flags)


# ============================================================
# C/C++ — 增强正则
# ============================================================

_C_INCLUDE_RE = re.compile(r'#\s*include\s+["<]([^">]+)[">]')


def _parse_cpp(content: str) -> list[ParsedImport]:
    """解析 C/C++ #include"""
    results = []
    for m in _C_INCLUDE_RE.finditer(content):
        path = m.group(1)
        line = content[:m.start()].count("\n") + 1
        # 双引号形式一定是本地文件；尖括号形式只在项目内存在时才保留
        is_quoted = '"' in m.group(0).split("#include")[1].strip()[:1]
        results.append(ParsedImport(
            module=path,
            names=[],
            is_relative=is_quoted,
            line_number=line,
        ))
    return results


# ============================================================
# Go / Rust — 基础支持
# ============================================================

_GO_IMPORT_RE = re.compile(r'import\s+(?:\(\s*(.*?)\s*\)|"([^"]+)")', re.DOTALL)


def _parse_go(content: str) -> list[ParsedImport]:
    """解析 Go import"""
    results = []
    for m in _GO_IMPORT_RE.finditer(content):
        if m.group(1):  # 多行 import block
            for line_content in m.group(1).strip().split("\n"):
                line_content = line_content.strip().strip('"').strip()
                if line_content and not line_content.startswith("//"):
                    # 去掉别名
                    parts = line_content.rsplit("/", 1)
                    module = parts[-1].strip().strip('"') if parts else line_content
                    results.append(ParsedImport(
                        module=line_content, names=[module],
                        is_relative=False, line_number=0,
                    ))
        elif m.group(2):  # 单行 import
            module = m.group(2)
            results.append(ParsedImport(
                module=module, names=[module.rsplit("/", 1)[-1]],
                is_relative=False, line_number=0,
            ))
    return results


_RUST_USE_RE = re.compile(r"(?:pub\s+)?use\s+([\w:]+(?:\s*::\s*\{[^}]*\})?)")


def _parse_rust(content: str) -> list[ParsedImport]:
    """解析 Rust use 语句"""
    results = []
    for m in _RUST_USE_RE.finditer(content):
        path = m.group(1)
        parts = path.split("::")
        if parts and parts[0] in ("self", "super", "crate"):
            module = "::".join(parts[1:])
            is_relative = True
        else:
            module = path
            is_relative = False
        line = content[:m.start()].count("\n") + 1
        results.append(ParsedImport(
            module=module, names=[],
            is_relative=is_relative, line_number=line,
        ))
    return results


# ============================================================
# 统一入口
# ============================================================

_LANG_PARSERS = {
    ".py": ("python", _parse_python),
    ".pyw": ("python", _parse_python),
    ".js": ("js", _parse_js_ts),
    ".jsx": ("js", _parse_js_ts),
    ".mjs": ("js", _parse_js_ts),
    ".ts": ("ts", _parse_js_ts),
    ".tsx": ("ts", _parse_js_ts),
    ".mts": ("ts", _parse_js_ts),
    ".c": ("c", _parse_cpp),
    ".cc": ("c", _parse_cpp),
    ".cpp": ("c", _parse_cpp),
    ".cxx": ("c", _parse_cpp),
    ".h": ("c", _parse_cpp),
    ".hpp": ("c", _parse_cpp),
    ".hxx": ("c", _parse_cpp),
    ".go": ("go", _parse_go),
    ".rs": ("rust", _parse_rust),
}

SUPPORTED_EXTENSIONS = set(_LANG_PARSERS.keys())


def parse_imports(file_path: str, content: str) -> FileImports:
    """
    解析一个文件的所有 import。

    Args:
        file_path: 相对路径
        content: 文件内容

    Returns:
        FileImports 包含解析结果
    """
    ext = os.path.splitext(file_path)[1].lower()
    parser_info = _LANG_PARSERS.get(ext)

    if not parser_info:
        return FileImports(file_path=file_path, imports=[])

    lang, parser = parser_info
    try:
        imports = parser(content)
    except Exception as e:
        logger.debug(f"Import parser failed for {file_path}: {e}")
        imports = []

    return FileImports(file_path=file_path, imports=imports)

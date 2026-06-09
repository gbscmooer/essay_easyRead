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
    is_type_only: bool = False  # 是否在 TYPE_CHECKING 块中


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

    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                asname = alias.asname
                results.append(ParsedImport(
                    module=name,
                    names=[asname or name.split(".")[-1]],
                    is_relative=False,
                    line_number=node.lineno,
                ))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level  # 0 = 绝对, 1 = from ., 2 = from ..
            is_relative = level > 0

            names = []
            for alias in node.names:
                if alias.name == "*":
                    names.append("*")
                else:
                    names.append(alias.asname or alias.name)

            results.append(ParsedImport(
                module=module,
                names=names,
                is_relative=is_relative,
                line_number=node.lineno,
            ))

    return results


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

_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:(?:type)\s+)?(?:([^{}\n]+?)\s*,?\s*)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]"""
    r"""|import\s+(?:type\s+)?(?:(\w+)\s+from\s*)?['"]([^'"]+)['"]"""
    r"""|export\s+\{[^}]*\}\s*from\s*['"]([^'"]+)['"]"""
    r"""|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)

_JS_DYNAMIC_IMPORT_RE = re.compile(
    r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)


def _parse_js_ts(content: str) -> list[ParsedImport]:
    """解析 JS/TS import/require/export-from"""
    results = []

    for m in _JS_IMPORT_RE.finditer(content):
        named_imports = m.group(2)  # { foo, bar }
        module_from_named = m.group(3)
        default_import = m.group(4)
        module_from_default = m.group(5)
        module_from_export = m.group(6)
        module_from_require = m.group(7)

        module = module_from_named or module_from_default or module_from_export or module_from_require
        if not module:
            continue

        names = []
        if named_imports:
            for part in named_imports.split(","):
                name = part.strip().split(" as ")[0].strip().split(":")[0].strip()
                if name:
                    names.append(name)
        if default_import:
            names.append(default_import)

        line = content[:m.start()].count("\n") + 1
        results.append(ParsedImport(
            module=module,
            names=names,
            is_relative=module.startswith("."),
            line_number=line,
        ))

    # Dynamic import() — 弱引用
    for m in _JS_DYNAMIC_IMPORT_RE.finditer(content):
        module = m.group(1)
        line = content[:m.start()].count("\n") + 1
        results.append(ParsedImport(
            module=module,
            names=[],
            is_relative=module.startswith("."),
            line_number=line,
        ))

    return results


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

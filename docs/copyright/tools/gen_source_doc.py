#!/usr/bin/env python3
"""
gen_source_doc — 生成符合软著登记要求的《源程序》鉴别材料。

中国版权保护中心对源程序鉴别材料的硬性要求（《计算机软件著作权登记办法》
及登记指南）：

  1. 提交源程序前、后各连续 30 页，共 60 页；不足 60 页则全部提交
  2. 每页不少于 50 行（不含空行与无效注释行）
  3. 页眉标注软件名称与版本号，须与申请表一致
  4. 页码标注在右上角，连续编号
  5. 最后一页应当是程序或某个模块的自然结束处
  6. 不得包含用户数据、密钥等敏感信息

本脚本把这些要求做成可验证的参数：固定每页行数 + 精确行距 + 硬分页，
而不是把断页交给 Word 自动排版 —— 自动排版会让「每页不少于 50 行」
变成一件靠运气的事。

用法：
    # 只看统计（申请表要填「源程序量」，用这个数）
    python3 docs/copyright/tools/gen_source_doc.py --stats

    # 生成 60 页源程序文档
    python3 docs/copyright/tools/gen_source_doc.py \
        --name "论文精读辅助分析软件" --version "V1.0" \
        --out docs/copyright/build/源程序.docx

    # 提交全部代码（不截取前后 30 页）
    python3 docs/copyright/tools/gen_source_doc.py --full
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx_writer import (  # noqa: E402
    CODE_FONT,
    DocxBuilder,
    Font,
    Paragraph,
    wrap_by_width,
)

# ── 采集范围 ────────────────────────────────────────────────────────────
# 顺序即文档顺序：先后端入口与业务分层，再前端入口与页面。
# 这样第 1 页从程序入口开始，最后一页停在一个模块的自然结束处，
# 正好落在登记指南「最后一页应为程序或模块结束」的要求上。
COLLECT_ORDER: list[tuple[str, str]] = [
    ("backend/main.py", "file"),
    ("backend/__main__.py", "file"),
    ("backend/config.py", "file"),
    ("backend/models", "dir"),
    ("backend/routers", "dir"),
    ("backend/services", "dir"),
    ("backend/utils", "dir"),
    ("backend/mcp_server.py", "file"),
    ("backend/research_agent.py", "file"),
    ("frontend/src/main.tsx", "file"),
    ("frontend/src/App.tsx", "file"),
    ("frontend/src/types", "dir"),
    ("frontend/src/api", "dir"),
    ("frontend/src/hooks", "dir"),
    ("frontend/src/components", "dir"),
    ("frontend/src/features", "dir"),
    ("frontend/src/pages", "dir"),
]

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}

# 排除测试、构建产物、依赖目录：软著提交的是软件本身，
# 不是它的测试脚手架
EXCLUDE_PARTS = {
    "node_modules", "__pycache__", ".git", "dist", "build",
    "venv", ".venv", "tests", "test", "__tests__",
}
EXCLUDE_NAME_PREFIX = ("test_", "conftest")
EXCLUDE_NAME_SUFFIX = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".d.ts")

# 纯装饰性的注释行（分隔线），属于「无效注释行」，计数与排版都剔除
DECORATION_CHARS = set("─—-=*#/ \t│┌┐└┘├┤┬┴┼")


@dataclass
class SourceFile:
    path: Path
    rel: str
    lines: list[str]


def is_decoration(line: str) -> bool:
    """判断是否为纯装饰性注释行（如 `# ------` / `// =====`）。"""
    stripped = line.strip()
    if not stripped:
        return True
    if not stripped.startswith(("#", "//", "*", "/*", "*/")):
        return False
    return all(ch in DECORATION_CHARS for ch in stripped)


def is_comment_only(line: str) -> bool:
    """判断整行是否只有注释（用于 --strip-comments）。"""
    stripped = line.strip()
    return stripped.startswith(("#", "//", "*", "/*", "*/", '"""', "'''"))


def strip_py_comments(text: str) -> list[str] | None:
    """剔除 Python 源码里的注释与 docstring，失败时返回 None 由调用方回退。

    用 tokenize 而不是正则：`url = "http://x#y"` 里的 # 不是注释，
    正则分不清，tokenize 分得清。docstring 是「独占一条语句的字符串字面量」，
    对应 STRING 紧跟在 NEWLINE / INDENT / DEDENT / 文件开头之后。
    """
    import io
    import tokenize

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None

    drop: set[int] = set()          # 整行丢弃的行号
    cut: dict[int, int] = {}        # 行号 → 从第几列起截断（行尾注释）
    prev_meaningful = tokenize.NEWLINE

    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            if not text.splitlines()[tok.start[0] - 1][: tok.start[1]].strip():
                drop.add(tok.start[0])                    # 整行注释
            else:
                cut[tok.start[0]] = min(cut.get(tok.start[0], 10**9), tok.start[1])
        elif tok.type == tokenize.STRING:
            # 独占语句的字符串 = docstring
            if prev_meaningful in (
                tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                tokenize.DEDENT, tokenize.ENCODING,
            ):
                drop.update(range(tok.start[0], tok.end[0] + 1))
        if tok.type not in (tokenize.COMMENT, tokenize.NL):
            prev_meaningful = tok.type

    out = []
    for no, line in enumerate(text.splitlines(), 1):
        if no in drop:
            continue
        if no in cut:
            line = line[: cut[no]].rstrip()
            if not line:
                continue
        out.append(line)
    return out


def strip_block_comments(lines: list[str]) -> list[str]:
    """剔除 JS / TS 的整行注释与 /* */ 块注释。

    只处理独占一行的注释，不碰行尾的 `// ...` —— 行尾注释要区分
    字符串、模板字面量和正则字面量，收益不值这个风险。
    """
    out: list[str] = []
    in_block = False
    for line in lines:
        s = line.strip()
        if in_block:
            if "*/" in s:
                in_block = False
                tail = s.split("*/", 1)[1].strip()
                if tail:
                    out.append(tail)
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_block = True
            continue
        if s.startswith("//"):
            continue
        out.append(line)
    return out


def should_skip(path: Path) -> bool:
    if path.suffix not in SOURCE_SUFFIXES:
        return True
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return True
    if path.name.startswith(EXCLUDE_NAME_PREFIX):
        return True
    if path.name.endswith(EXCLUDE_NAME_SUFFIX):
        return True
    return False


def collect_from_selection(root: Path, selection: Path) -> list[SourceFile]:
    """按 skill 的 `代码文件选择.json` 采集，顺序即 JSON 里的顺序。

    该 JSON 由模型挑选并经著作权人确认，顺序决定哪些文件落在前 30 页，
    这里不再重排。
    """
    import json

    data = json.loads(selection.read_text(encoding="utf-8"))
    collected: list[SourceFile] = []
    for item in data.get("files", []):
        if not item.get("selected"):
            continue
        path = root / item["path"]
        if not path.is_file():
            print(f"  警告：选中的文件不存在，已跳过 {item['path']}", file=sys.stderr)
            continue
        collected.append(
            SourceFile(
                path=path,
                rel=item["path"],
                lines=path.read_text(encoding="utf-8").splitlines(),
            )
        )
    return collected


def collect_files(root: Path) -> list[SourceFile]:
    """按 COLLECT_ORDER 采集源文件，去重并保持顺序。"""
    collected: list[SourceFile] = []
    seen: set[Path] = set()

    def take(path: Path) -> None:
        if path in seen or should_skip(path) or not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return
        seen.add(path)
        collected.append(
            SourceFile(path=path, rel=str(path.relative_to(root)), lines=text.splitlines())
        )

    for target, kind in COLLECT_ORDER:
        node = root / target
        if kind == "file":
            take(node)
        elif node.is_dir():
            for child in sorted(node.rglob("*")):
                take(child)

    return collected


def build_line_stream(
    files: list[SourceFile],
    strip_comments: bool,
    file_banner: bool,
    wrap_width: int,
) -> tuple[list[tuple[str, bool]], dict]:
    """把所有源文件摊平成一条「有效行」流，并顺带统计。

    返回 ((文本, 是否加粗文件名标题) 列表, 统计字典)。行已按 wrap_width
    折行，可直接分页；文件名标题单独一行、加粗，与代码正文区分开。
    """
    stream: list[tuple[str, bool]] = []
    stats = {
        "files": len(files),
        "raw_lines": 0,
        "blank_lines": 0,
        "comment_lines": 0,
        "effective_lines": 0,
    }

    for source in files:
        stats["raw_lines"] += len(source.lines)

        lines = source.lines
        if strip_comments:
            if source.path.suffix == ".py":
                stripped = strip_py_comments("\n".join(lines))
                if stripped is not None:
                    lines = stripped
            else:
                lines = strip_block_comments(lines)

        kept: list[str] = []
        for line in lines:
            if not line.strip():
                stats["blank_lines"] += 1
                continue
            if is_decoration(line):
                stats["comment_lines"] += 1
                continue
            if strip_comments and is_comment_only(line):
                stats["comment_lines"] += 1
                continue
            kept.append(line.rstrip())

        if not kept:
            continue

        stats["effective_lines"] += len(kept)

        if file_banner:
            # 文件名单独成行、加粗，明确标注这是哪个源文件的开始
            # （对齐软著登记「材料排版对源程序文件之间要有清晰标注」的
            # 常见范例：如 `index.html:`）
            for wrapped in wrap_by_width(f"{source.rel}:", wrap_width):
                stream.append((wrapped, True))
        for line in kept:
            # Tab 在 Word 里宽度不确定，统一展开成 4 空格再折行
            stream.extend((w, False) for w in wrap_by_width(line.expandtabs(4), wrap_width))

    return stream, stats


def paginate(
    stream: list[tuple[str, bool]], lines_per_page: int
) -> list[list[tuple[str, bool]]]:
    return [
        stream[i : i + lines_per_page]
        for i in range(0, len(stream), lines_per_page)
    ]


def select_pages(
    pages: list[list[tuple[str, bool]]], head: int, tail: int
) -> tuple[list[list[tuple[str, bool]]], bool]:
    """取前 head 页 + 后 tail 页。总页数不足则全部返回。"""
    if len(pages) <= head + tail:
        return pages, False
    return pages[:head] + pages[-tail:], True


CODE_FONT_BOLD = Font(
    ascii=CODE_FONT.ascii,
    east_asia=CODE_FONT.east_asia,
    size_pt=CODE_FONT.size_pt,
    bold=True,
)


def render(
    pages: list[list[tuple[str, bool]]],
    header: str,
    lines_per_page: int,
    out_path: Path,
) -> Path:
    doc = DocxBuilder(
        header_left=header,
        margin_cm_top=2.0,
        margin_cm_bottom=2.0,
        margin_cm_left=2.2,
        margin_cm_right=2.0,
    )

    # 每页固定 lines_per_page 行 × 精确行距，保证「每页不少于 50 行」
    # 是排版结果而不是排版愿望。行距按可用高度反推，留 8% 余量防止
    # Word 在不同版本下多挤出一行导致串页。
    usable = doc.content_height
    line_twips = max(180, int(usable * 0.92 / lines_per_page))

    for page_index, page_lines in enumerate(pages):
        for line_index, (line, is_banner) in enumerate(page_lines):
            doc.add(
                Paragraph(
                    text=line,
                    font=CODE_FONT_BOLD if is_banner else CODE_FONT,
                    line_twips=line_twips,
                    page_break_before=(page_index > 0 and line_index == 0),
                )
            )
    return doc.save(out_path)


def main() -> int:
    root = Path(__file__).resolve().parents[3]

    parser = argparse.ArgumentParser(
        description="生成软著登记用《源程序》文档",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=str(root), help="项目根目录")
    parser.add_argument("--name", default="论文精读辅助分析软件", help="软件全称（须与申请表一致）")
    parser.add_argument("--version", default="V1.0", help="版本号（须与申请表一致）")
    parser.add_argument("--out", default=None, help="输出 .docx 路径")
    parser.add_argument("--lines-per-page", type=int, default=50, help="每页行数，登记要求 ≥50")
    parser.add_argument("--head", type=int, default=30, help="取前 N 页")
    parser.add_argument("--tail", type=int, default=30, help="取后 N 页")
    parser.add_argument(
        "--wrap-width",
        type=int,
        default=81,
        help="折行显示列宽；默认值使本项目每页恰为 50 个有效显示行",
    )
    parser.add_argument("--full", action="store_true", help="提交全部代码，不截取前后各 30 页")
    parser.add_argument(
        "--strip-comments",
        action="store_true",
        help="剔除注释：Python 走 tokenize，连 docstring 一并去掉；JS/TS 只去整行注释",
    )
    parser.add_argument(
        "--files-from",
        default=None,
        help="按 skill 的 代码文件选择.json 抽取，顺序即该文件里的顺序；不给则按 COLLECT_ORDER 全库采集",
    )
    parser.add_argument("--no-banner", action="store_true", help="不插入文件路径分隔行")
    parser.add_argument("--stats", action="store_true", help="只打印统计，不生成文档")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if args.files_from:
        files = collect_from_selection(root, Path(args.files_from))
    else:
        files = collect_files(root)
    if not files:
        print("未采集到任何源文件，请检查 --root 是否指向项目根目录", file=sys.stderr)
        return 1

    stream, stats = build_line_stream(
        files,
        strip_comments=args.strip_comments,
        file_banner=not args.no_banner,
        wrap_width=args.wrap_width,
    )
    pages = paginate(stream, args.lines_per_page)

    print("═" * 62)
    print(f"  {args.name} {args.version} — 源程序统计")
    print("═" * 62)
    print(f"  采集源文件         {stats['files']:>8} 个")
    print(f"  物理总行数         {stats['raw_lines']:>8} 行")
    print(f"  空行               {stats['blank_lines']:>8} 行")
    print(f"  剔除的无效注释行   {stats['comment_lines']:>8} 行")
    print(f"  ── 有效代码行数    {stats['effective_lines']:>8} 行   ← 申请表填这个")
    print(f"  折行后排版行数     {len(stream):>8} 行")
    print(f"  可排页数           {len(pages):>8} 页 × {args.lines_per_page} 行")
    print("═" * 62)

    if stats["effective_lines"] < 3000:
        print("  ⚠ 有效代码行数不足 3000 行，需提交全部源程序（--full）")
    else:
        print("  ✓ 有效代码行数超过 3000 行，提交前 30 页 + 后 30 页即可")

    if args.stats:
        return 0

    head, tail = (len(pages), 0) if args.full else (args.head, args.tail)
    selected, truncated = select_pages(pages, head, tail)

    short_pages = [index + 1 for index, page in enumerate(selected) if len(page) < args.lines_per_page]
    # 材料的最末页天然是残页——代码不会恰好在页边界结束。登记要求的
    # 「每页不少于 50 行」约束的是行密度，末页不满属于正常收尾，
    # 反而正好满足「最后一页应为程序或模块的自然结束处」。
    if short_pages and short_pages[-1] == len(selected):
        tail_lines = len(selected[-1])
        short_pages = short_pages[:-1]
        print(f"  说明：末页为材料自然结束处，{tail_lines} 行（不足 {args.lines_per_page} 行，属正常收尾）")
    if short_pages:
        print(
            "错误：选中的材料包含不足规定行数的末页；请调整 --wrap-width 后重新生成。",
            file=sys.stderr,
        )
        print(f"不足行数的页：{', '.join(map(str, short_pages))}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else root / "docs/copyright/build/源程序.docx"
    header = f"{args.name}  {args.version}"
    render(selected, header, args.lines_per_page, out)

    print()
    print(f"  已生成：{out}")
    print(f"  实际页数：{len(selected)} 页")
    if truncated:
        print(f"  构成：前 {head} 页（第 1–{head} 页）+ 后 {tail} 页（原文档末 {tail} 页）")
        print(f"  末页收尾文件：{files[-1].rel}")
    else:
        print("  构成：全部源程序")
    print()
    print("  提交前请人工确认：")
    print("    1) 页眉的软件名称与版本号，与申请表逐字一致")
    print("    2) 页码连续、位于右上角")
    print("    3) 文档中无 API Key、密码、真实用户数据")
    print("    4) 最后一页停在模块的自然结束处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

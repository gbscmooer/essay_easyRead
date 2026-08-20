#!/usr/bin/env python3
"""
gen_manual_docx — 把《用户手册》Markdown 转成符合软著登记要求的 .docx。

登记指南对说明书类鉴别材料的要求：

  1. 封面标注软件全称、版本号、著作权人，且与申请表逐字一致
  2. 页眉标注软件名称与版本号，页码标注在右上角
  3. 每页不少于 30 行（含图片的页面可豁免）
  4. 前、后各连续 30 页，不足 60 页则全部提交
  5. 需含操作步骤与界面截图

脚本负责 1、2、4 与排版；截图需人工补入（Markdown 中以【截图 x-x】占位，
转换后渲染成带边框的占位框，直接把图贴进框内即可）。

用法：
    python3 docs/copyright/tools/gen_manual_docx.py \
        --name "论文精读辅助分析软件" \
        --version "V1.0" \
        --owner "张三" \
        --out docs/copyright/build/用户手册.docx
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx_writer import (  # noqa: E402
    DocxBuilder,
    Font,
    Paragraph,
    Table,
    cm,
)

# ── 字体方案 ────────────────────────────────────────────────────────────
F_COVER_TITLE = Font(ascii="Arial", east_asia="黑体", size_pt=26, bold=True)
F_COVER_SUB = Font(ascii="Arial", east_asia="黑体", size_pt=18)
F_COVER_META = Font(ascii="Times New Roman", east_asia="宋体", size_pt=14)
F_H1 = Font(ascii="Arial", east_asia="黑体", size_pt=16, bold=True)
F_H2 = Font(ascii="Arial", east_asia="黑体", size_pt=13, bold=True)
F_H3 = Font(ascii="Arial", east_asia="黑体", size_pt=11.5, bold=True)
F_BODY = Font(ascii="Times New Roman", east_asia="宋体", size_pt=10.5)
F_BOLD = Font(ascii="Times New Roman", east_asia="黑体", size_pt=10.5, bold=True)
F_CODE = Font(ascii="Consolas", east_asia="宋体", size_pt=9)
F_TABLE = Font(ascii="Times New Roman", east_asia="宋体", size_pt=9.5)
F_SHOT = Font(ascii="Arial", east_asia="黑体", size_pt=10, bold=True)
F_TOC1 = Font(ascii="Arial", east_asia="黑体", size_pt=11, bold=True)
F_TOC2 = Font(ascii="Times New Roman", east_asia="宋体", size_pt=10)

LINE = 300          # 正文行距 15pt，保证每页 ≥30 行
SHOT_RE = re.compile(r"^【截图\s*([\d\-—]+)】\s*(.*)$")


def parse_inline(text: str, base: Font) -> list[tuple[str, Font]]:
    """解析 **粗体** 与 `等宽`，返回 (文本, 字体) 序列。"""
    bold = Font(ascii=base.ascii, east_asia="黑体", size_pt=base.size_pt, bold=True)
    mono = Font(ascii="Consolas", east_asia="宋体", size_pt=base.size_pt - 0.5)

    runs: list[tuple[str, Font]] = []
    for piece in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            runs.append((piece[2:-2], bold))
        elif piece.startswith("`") and piece.endswith("`"):
            runs.append((piece[1:-1], mono))
        else:
            runs.append((piece, base))
    return runs or [("", base)]


def strip_md(text: str) -> str:
    """去掉行内标记，用于目录与表格单元格。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\([^)]*\)", r"\1", text)
    return text.strip()


class ManualRenderer:
    def __init__(self, doc: DocxBuilder) -> None:
        self.doc = doc
        self.headings: list[tuple[int, str]] = []

    # -- 各类块 --
    def heading(self, level: int, text: str, first: bool = False) -> None:
        font, before, after = {
            1: (F_H1, 0, 220),
            2: (F_H2, 280, 140),
            3: (F_H3, 220, 110),
        }[level]
        self.doc.add(
            Paragraph(
                text=text,
                font=font,
                space_before=before,
                space_after=after,
                outline_level=level - 1,
                page_break_before=(level == 1 and not first),
            )
        )
        self.headings.append((level, text))

    def body(self, text: str) -> None:
        self.doc.add(
            Paragraph(
                runs=parse_inline(text, F_BODY),
                font=F_BODY,
                line_twips=LINE,
                space_after=60,
                first_line_indent=cm(0.74),  # 首行缩进两字符
                align="both",
            )
        )

    def bullet(self, text: str, ordered_index: int | None = None) -> None:
        marker = f"{ordered_index}. " if ordered_index else "· "
        self.doc.add(
            Paragraph(
                runs=[(marker, F_BODY)] + parse_inline(text, F_BODY),
                font=F_BODY,
                line_twips=LINE,
                space_after=40,
                indent_left=cm(0.74),
            )
        )

    def code_block(self, lines: list[str]) -> None:
        for line in lines or [""]:
            self.doc.add(
                Paragraph(
                    text="    " + line,
                    font=F_CODE,
                    line_twips=260,
                    shading="F5F5F5",
                    indent_left=cm(0.5),
                )
            )
        self.doc.add(Paragraph(font=F_BODY, line_twips=120))

    def table(self, rows: list[list[str]]) -> None:
        self.doc.add(
            Table(
                rows=[[strip_md(c) for c in row] for row in rows],
                width_twips=self.doc.content_width,
                font=F_TABLE,
            )
        )

    def screenshot(self, number: str, caption: str) -> None:
        """渲染截图占位框：贴图时把图片直接粘进框内即可。"""
        self.doc.add(Paragraph(font=F_BODY, line_twips=120))
        # 空行把占位框撑到约 7 cm 高，正好够贴一张 1440×900 的界面截图
        placeholder = "\n".join(["", "", "【此处插入截图】", "", "", "", ""])
        self.doc.add(
            Table(
                rows=[[placeholder]],
                width_twips=self.doc.content_width,
                font=Font(ascii="Arial", east_asia="黑体", size_pt=10),
                header_fill="FAFAFA",
            )
        )
        self.doc.add(
            Paragraph(
                runs=[(f"图 {number}  {caption}", F_SHOT)],
                font=F_SHOT,
                align="center",
                space_before=60,
                space_after=160,
                line_twips=LINE,
            )
        )

    # -- 封面与目录 --
    def cover(self, name: str, version: str, owner: str, date: str) -> None:
        for _ in range(6):
            self.doc.add(Paragraph(font=F_BODY, line_twips=LINE))
        self.doc.add(
            Paragraph(text=name, font=F_COVER_TITLE, align="center", space_after=400)
        )
        self.doc.add(
            Paragraph(text="用 户 手 册", font=F_COVER_SUB, align="center", space_after=1200)
        )
        for label, value in (
            ("版　本　号：", version),
            ("著作权人：", owner),
            ("编写日期：", date),
        ):
            self.doc.add(
                Paragraph(
                    text=f"{label}{value}",
                    font=F_COVER_META,
                    align="center",
                    space_after=200,
                    line_twips=400,
                )
            )
        self.doc.add_page_break()

    def toc(self) -> None:
        """正文渲染完成后回填目录，插到封面之后。"""
        blocks: list[Paragraph] = [
            Paragraph(
                text="目　　录",
                font=F_H1,
                align="center",
                space_after=300,
                line_twips=LINE,
            )
        ]
        for level, text in self.headings:
            if level > 2:
                continue
            blocks.append(
                Paragraph(
                    text=text,
                    font=F_TOC1 if level == 1 else F_TOC2,
                    line_twips=LINE,
                    space_after=50,
                    indent_left=0 if level == 1 else cm(0.9),
                    space_before=80 if level == 1 else 0,
                )
            )
        blocks.append(Paragraph(page_break_before=True, font=Font(size_pt=1), line_twips=20))
        return blocks


def parse_markdown(md: str, renderer: ManualRenderer) -> None:
    """按行扫描 Markdown，逐块交给 renderer 渲染。

    只覆盖手册实际用到的语法子集：标题、段落、有序 / 无序列表、
    表格、围栏代码块、水平线、截图占位。
    """
    lines = md.split("\n")
    i = 0
    first_heading = True
    ordered_counter = 0

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        # 空行 / 水平线
        if not stripped or stripped in ("---", "***", "___"):
            ordered_counter = 0
            i += 1
            continue

        # 引用块整块跳过（手册里的引用块是给编写者看的提示，不进正式文档）
        if stripped.startswith(">"):
            while i < len(lines) and (lines[i].strip().startswith(">") or not lines[i].strip()):
                if not lines[i].strip() and i + 1 < len(lines) and not lines[i + 1].strip().startswith(">"):
                    break
                i += 1
            continue

        # 围栏代码块
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].rstrip())
                i += 1
            i += 1
            renderer.code_block(block)
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]
        ):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not re.match(r"^[\s:\-]+$", "".join(cells)):
                    rows.append(cells)
                i += 1
            renderer.table(rows)
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 3)
            title = strip_md(m.group(2))
            renderer.heading(level, title, first=first_heading)
            first_heading = False
            i += 1
            continue

        # 截图占位
        m = SHOT_RE.match(stripped)
        if m:
            renderer.screenshot(m.group(1), strip_md(m.group(2)))
            i += 1
            continue

        # 列表
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            renderer.bullet(m.group(1))
            i += 1
            continue
        m = re.match(r"^(\d+)[.、]\s+(.*)$", stripped)
        if m:
            ordered_counter += 1
            renderer.bullet(m.group(2), ordered_index=ordered_counter)
            i += 1
            continue

        # 普通段落
        ordered_counter = 0
        renderer.body(stripped)
        i += 1


def main() -> int:
    root = Path(__file__).resolve().parents[3]

    parser = argparse.ArgumentParser(description="生成软著登记用《用户手册》文档")
    parser.add_argument("--src", default=str(root / "docs/copyright/用户手册.md"))
    parser.add_argument("--name", default="论文精读辅助分析软件", help="软件全称（须与申请表一致）")
    parser.add_argument("--version", default="V1.0", help="版本号（须与申请表一致）")
    parser.add_argument("--owner", default="＿＿＿＿＿＿", help="著作权人（须与申请表一致）")
    parser.add_argument("--date", default=None, help="编写日期，默认今天")
    parser.add_argument("--out", default=str(root / "docs/copyright/build/用户手册.docx"))
    parser.add_argument("--head", type=int, default=30, help="截取前 N 页时的 N（仅用于提示）")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"找不到源文件：{src}", file=sys.stderr)
        return 1

    today = args.date or datetime.date.today().strftime("%Y 年 %m 月 %d 日")

    doc = DocxBuilder(
        header_left=f"{args.name}  {args.version}  用户手册",
        margin_cm_top=2.4,
        margin_cm_bottom=2.2,
        margin_cm_left=2.6,
        margin_cm_right=2.2,
    )
    renderer = ManualRenderer(doc)

    # 封面用命令行参数生成，不取 Markdown 里的占位封面 ——
    # 这三项必须与申请表逐字一致，让它只有一个来源
    renderer.cover(args.name, args.version, args.owner, today)
    cover_len = len(doc.blocks)

    # Markdown 里第 1 章之前是占位封面与目录，正文从第一个 "# 第" 开始
    md = src.read_text(encoding="utf-8")
    body_start = md.find("\n# 第")
    if body_start > 0:
        md = md[body_start + 1 :]
    parse_markdown(md, renderer)

    # 目录依赖正文扫描出的标题，所以最后生成、插回封面之后
    doc.blocks[cover_len:cover_len] = renderer.toc()

    out = Path(args.out)
    doc.save(out)

    body_paras = sum(1 for b in doc.blocks if isinstance(b, Paragraph))
    tables = sum(1 for b in doc.blocks if isinstance(b, Table))
    shots = md.count("【截图")

    print("═" * 62)
    print(f"  {args.name} {args.version} — 用户手册")
    print("═" * 62)
    print(f"  一级标题     {sum(1 for l, _ in renderer.headings if l == 1):>6} 个")
    print(f"  二级标题     {sum(1 for l, _ in renderer.headings if l == 2):>6} 个")
    print(f"  段落         {body_paras:>6} 段")
    print(f"  表格         {tables:>6} 个（含截图占位框）")
    print(f"  截图占位     {shots:>6} 处   ← 需人工补图")
    print("═" * 62)
    print(f"  已生成：{out}")
    print()
    print("  提交前请人工完成：")
    print("    1) 用实际界面截图替换每个占位框，并核对图题")
    print("    2) 用 Word 打开后确认总页数；超过 60 页则只提交前 30 页 + 后 30 页")
    print("    3) 核对封面与页眉的软件名称、版本号、著作权人与申请表逐字一致")
    print("    4) 确认每页正文不少于 30 行（含图页面可豁免）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

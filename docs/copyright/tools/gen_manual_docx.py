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
    Image,
    Paragraph,
    Raw,
    Table,
    cm,
    escape,
)

# ── 字体方案 ────────────────────────────────────────────────────────────
# 字号对齐《文档鉴别材料模板》：封面 28/26pt，章标题 22pt，节标题 14pt，
# 正文小四（12pt）。模板里节标题与正文同为 12pt，只靠加粗区分，层次偏弱，
# 这里给节标题留 14pt，其余照模板。
F_COVER_TITLE = Font(ascii="Arial", east_asia="Heiti SC", size_pt=28, bold=True)
F_COVER_SUB = Font(ascii="Arial", east_asia="Heiti SC", size_pt=26, bold=True)
F_COVER_META = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=16)
F_H1 = Font(ascii="Arial", east_asia="Heiti SC", size_pt=22, bold=True)
F_H2 = Font(ascii="Arial", east_asia="Heiti SC", size_pt=14, bold=True)
F_H3 = Font(ascii="Arial", east_asia="Heiti SC", size_pt=12, bold=True)
F_BODY = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=12)
F_BOLD = Font(ascii="Times New Roman", east_asia="Heiti SC", size_pt=12, bold=True)
F_CODE = Font(ascii="Consolas", east_asia="Songti SC", size_pt=10)
F_TABLE = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=10.5)
F_SHOT = Font(ascii="Arial", east_asia="Heiti SC", size_pt=10.5, bold=True)
F_TOC1 = Font(ascii="Arial", east_asia="Heiti SC", size_pt=12, bold=True)
F_TOC2 = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=11)

# 正文行距固定 18pt（360 twip）。版心高度 = A4 − 上 2.4cm − 下 2.2cm
# = 14230 twip，14230 ÷ 360 ≈ 39 行/页，与字体度量无关，稳定满足
# 「文档每页不少于 30 行」。行距若设成「1.5 倍」这类相对值，实际行高随
# 字体的 hhea 度量浮动（宋体系数 1.40，10.5pt 时只有约 31 行），没有余量。
LINE = 360
CODE_LINE = 280     # 代码块行距 14pt
SHOT_RE = re.compile(r"^【截图\s*([\d\-—]+)】\s*(.*)$")

# 截图按编号命名：截图 5-3 对应 screenshots/5-3.png。缺图时渲染占位框，
# 便于先出稿再逐张补图。
SHOT_EXTS = (".png", ".jpg", ".jpeg")


def parse_inline(text: str, base: Font) -> list[tuple[str, Font]]:
    """解析 **粗体** 与 `等宽`，返回 (文本, 字体) 序列。"""
    bold = Font(ascii=base.ascii, east_asia="Heiti SC", size_pt=base.size_pt, bold=True)
    mono = Font(ascii="Consolas", east_asia="Songti SC", size_pt=base.size_pt - 0.5)

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


def find_shot(directory: Path | None, number: str) -> Path | None:
    if not directory:
        return None
    for ext in SHOT_EXTS:
        candidate = directory / f"{number}{ext}"
        if candidate.exists():
            return candidate
    return None


def strip_md(text: str) -> str:
    """去掉行内标记，用于目录与表格单元格。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\([^)]*\)", r"\1", text)
    return text.strip()


class ManualRenderer:
    def __init__(self, doc: DocxBuilder, screenshots_dir: Path | None = None) -> None:
        self.doc = doc
        self.screenshots_dir = screenshots_dir
        self.headings: list[tuple[int, str]] = []

    # -- 各类块 --
    def heading(self, level: int, text: str, first: bool = False) -> None:
        font, before, after = {
            1: (F_H1, 0, 320),
            2: (F_H2, 320, 160),
            3: (F_H3, 240, 120),
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
                first_line_indent=cm(0.85),  # 首行缩进两字符（12pt × 2）
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
                indent_left=cm(0.85),
            )
        )

    def code_block(self, lines: list[str]) -> None:
        for line in lines or [""]:
            self.doc.add(
                Paragraph(
                    text="    " + line,
                    font=F_CODE,
                    line_twips=CODE_LINE,
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
        """插入截图；缺图时渲染占位框，图题照常输出。"""
        self.doc.add(Paragraph(font=F_BODY, line_twips=120))
        image_path = find_shot(self.screenshots_dir, number)
        if image_path:
            self.doc.add(Image.fit_png(image_path, self.doc.content_width - cm(0.2)))
        else:
            placeholder = "\n".join(["", "", f"【此处插入截图：{caption}】", "", "", "", ""])
            self.doc.add(
                Table(
                    rows=[[placeholder]],
                    width_twips=self.doc.content_width,
                    font=Font(ascii="Arial", east_asia="Heiti SC", size_pt=10),
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
        """正文渲染完成后回填目录，插到封面之后。

        目录写成真正的 TOC 域而不是一串静态文字：条目右侧带点线制表位与页码，
        与《文档鉴别材料模板》一致。域的缓存内容先写成"标题 + 点线"，Word/WPS
        打开时（settings.xml 里已置 updateFields）会把页码补齐。
        """
        right_tab = self.doc.content_width
        entries = [(lv, tx) for lv, tx in self.headings if lv <= 2]

        def entry_xml(level: int, text: str, prefix: str = "", suffix: str = "") -> str:
            font = F_TOC1 if level == 1 else F_TOC2
            indent = 0 if level == 1 else cm(0.9)
            before = 80 if level == 1 else 0
            return (
                "<w:p><w:pPr>"
                f'<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{right_tab}"/></w:tabs>'
                f'<w:spacing w:before="{before}" w:after="50" '
                f'w:line="{LINE}" w:lineRule="exact"/>'
                f'<w:ind w:left="{indent}" w:firstLine="0"/>'
                f"{font.rpr()}</w:pPr>"
                f"{prefix}"
                f'<w:r>{font.rpr()}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
                f"<w:r>{font.rpr()}<w:tab/></w:r>"
                f"{suffix}"
                "</w:p>"
            )

        # 域跨段落：begin/instrText/separate 挂在首条，end 挂在末条
        field_begin = (
            f'<w:r>{F_TOC1.rpr()}<w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
            f'<w:r>{F_TOC1.rpr()}<w:instrText xml:space="preserve">'
            'TOC \\o "1-2" \\h \\z \\u</w:instrText></w:r>'
            f'<w:r>{F_TOC1.rpr()}<w:fldChar w:fldCharType="separate"/></w:r>'
        )
        field_end = f'<w:r>{F_TOC1.rpr()}<w:fldChar w:fldCharType="end"/></w:r>'

        parts = [
            entry_xml(
                level,
                text,
                prefix=field_begin if i == 0 else "",
                suffix=field_end if i == len(entries) - 1 else "",
            )
            for i, (level, text) in enumerate(entries)
        ]

        return [
            Paragraph(
                text="目　　录",
                font=F_H1,
                align="center",
                space_after=300,
                line_twips=LINE,
            ),
            Raw("".join(parts)),
            Paragraph(page_break_before=True, font=Font(size_pt=1), line_twips=20),
        ]


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
    parser.add_argument(
        "--screenshots-dir",
        default=str(root / "docs/copyright/build/screenshots"),
        help="截图目录，文件按编号命名（如 5-3.png）；缺图时渲染占位框",
    )
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
    renderer = ManualRenderer(doc, Path(args.screenshots_dir))

    # 封面用命令行参数生成，不取 Markdown 里的占位封面 ——
    # 这三项必须与申请表逐字一致，让它只有一个来源
    renderer.cover(args.name, args.version, args.owner, today)
    cover_len = len(doc.blocks)

    # Markdown 开头是占位封面、编写提示与占位目录，正文从「前　　言」起；
    # 手册若没写前言就退回到第 1 章
    md = src.read_text(encoding="utf-8")
    for marker in ("\n# 前", "\n# 第"):
        body_start = md.find(marker)
        if body_start > 0:
            md = md[body_start + 1 :]
            break
    parse_markdown(md, renderer)

    # 目录依赖正文扫描出的标题，所以最后生成、插回封面之后
    doc.blocks[cover_len:cover_len] = renderer.toc()

    out = Path(args.out)
    doc.save(out)

    body_paras = sum(1 for b in doc.blocks if isinstance(b, Paragraph))
    tables = sum(1 for b in doc.blocks if isinstance(b, Table))
    shot_numbers = re.findall(r"^【截图\s*([\d\-—]+)】", md, re.MULTILINE)
    shots_dir = Path(args.screenshots_dir)
    missing_shots = [n for n in shot_numbers if not find_shot(shots_dir, n)]

    print("═" * 62)
    print(f"  {args.name} {args.version} — 用户手册")
    print("═" * 62)
    print(f"  一级标题     {sum(1 for l, _ in renderer.headings if l == 1):>6} 个")
    print(f"  二级标题     {sum(1 for l, _ in renderer.headings if l == 2):>6} 个")
    print(f"  段落         {body_paras:>6} 段")
    print(f"  表格         {tables:>6} 个")
    print(f"  已嵌入截图   {len(shot_numbers) - len(missing_shots):>6} 张")
    print(f"  缺失截图     {len(missing_shots):>6} 张")
    print("═" * 62)
    print(f"  已生成：{out}")
    print()
    if missing_shots:
        print(f"  缺图编号：{', '.join(missing_shots)}")
    print()
    print("  提交前检查：")
    print("    1) 确认每张实际界面截图与图题一致且不含密钥、账号或公网接口地址")
    print("    2) 确认总页数；超过 60 页则提交前连续 30 页与后连续 30 页")
    print("    3) 核对封面与页眉的软件名称、版本号、著作权人与申请表逐字一致")
    print("    4) 确认每页正文不少于 30 行（含图页面可按实际版式审查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

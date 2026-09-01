"""
docx_writer — 零依赖的最小 OOXML (.docx) 生成器。

只实现软著申请材料需要的那一小块 Word 能力，避免为了两个脚本去装
python-docx：

  * A4 单面、可配置页边距
  * 页眉左侧「软件名称 + 版本号」，右上角 PAGE 域自动页码
  * 等宽段落（源程序）与正文段落（说明书）两套字体
  * 硬分页
  * 简单表格

中国版权保护中心对鉴别材料的硬性排版要求就落在页眉、页码和每页行数上，
所以这里把「每页固定 N 行 + 精确行距 + 硬分页」做成可控参数，而不是交给
Word 自动断页 —— 自动断页会让「每页不少于 50 行」变成一件靠运气的事。
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ── 单位换算 ────────────────────────────────────────────────────────────
# Word 内部长度单位是 twip（1/20 磅，1 英寸 = 1440 twip）
TWIPS_PER_CM = 1440 / 2.54
A4_WIDTH_TWIPS = 11906
A4_HEIGHT_TWIPS = 16838

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def cm(value: float) -> int:
    """厘米 → twip。"""
    return int(round(value * TWIPS_PER_CM))


def escape(text: str) -> str:
    """XML 转义，并丢掉 XML 1.0 不允许的控制字符。

    源码里偶尔混进的 \x00-\x08 之类字符会让 Word 直接判定文档损坏，
    这里统一过滤，比事后排查「文件打不开」便宜得多。
    """
    out = []
    for ch in text:
        code = ord(ch)
        if code in (0x09, 0x0A, 0x0D) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD:
            if ch == "&":
                out.append("&amp;")
            elif ch == "<":
                out.append("&lt;")
            elif ch == ">":
                out.append("&gt;")
            elif ch == '"':
                out.append("&quot;")
            else:
                out.append(ch)
        # 其余控制字符直接丢弃
    return "".join(out)


def display_width(text: str) -> int:
    """按显示列宽计数：CJK 与全角标点算 2 列，其余算 1 列。

    用于源码折行 —— 中文注释按字符数折行会折得过宽，冲出页面右边界。
    """
    width = 0
    for ch in text:
        code = ord(ch)
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE6F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
            or 0x20000 <= code <= 0x3FFFD
        ):
            width += 2
        else:
            width += 1
    return width


def wrap_by_width(text: str, limit: int) -> list[str]:
    """按显示列宽硬折行，返回至少一行。"""
    if not text:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    used = 0
    for ch in text:
        w = display_width(ch)
        if used + w > limit and current:
            lines.append("".join(current))
            current, used = [], 0
        current.append(ch)
        used += w
    if current:
        lines.append("".join(current))
    return lines


# ── 字体配置 ────────────────────────────────────────────────────────────
@dataclass
class Font:
    """一套字体：ascii 走西文字体，eastAsia 走中文字体，size 为磅值。"""

    ascii: str = "Times New Roman"
    east_asia: str = "Songti SC"
    size_pt: float = 10.5
    bold: bool = False
    color: str | None = None

    def rpr(self) -> str:
        half_points = int(round(self.size_pt * 2))
        parts = [
            f'<w:rFonts w:ascii="{escape(self.ascii)}" w:hAnsi="{escape(self.ascii)}" '
            f'w:eastAsia="{escape(self.east_asia)}" w:cs="{escape(self.ascii)}"/>'
        ]
        if self.bold:
            parts.append("<w:b/><w:bCs/>")
        if self.color:
            parts.append(f'<w:color w:val="{self.color}"/>')
        parts.append(f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>')
        return "<w:rPr>" + "".join(parts) + "</w:rPr>"


CODE_FONT = Font(ascii="Consolas", east_asia="Songti SC", size_pt=9)
BODY_FONT = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=10.5)
HEADER_FONT = Font(ascii="Times New Roman", east_asia="Songti SC", size_pt=9)


# ── 文档块 ──────────────────────────────────────────────────────────────
@dataclass
class Raw:
    """直接写入的 WordprocessingML 片段。

    给 Paragraph 表达不了的结构留的出口——目前只有目录域（TOC field）用到：
    域要跨多个段落（begin 在首行、end 在末行），且条目需要右对齐点线制表位。
    """

    xml: str = ""

    def to_xml(self) -> str:
        return self.xml


@dataclass
class Paragraph:
    text: str = ""
    # runs 非空时优先于 text，用于一段里混排粗体 / 等宽等多种字体
    runs: list[tuple[str, "Font"]] = field(default_factory=list)
    font: Font = field(default_factory=lambda: BODY_FONT)
    align: str | None = None            # left / center / right / both
    line_twips: int | None = None       # 精确行距；None = 单倍行距
    space_before: int = 0               # twip
    space_after: int = 0                # twip
    indent_left: int = 0                # twip
    first_line_indent: int = 0          # twip
    page_break_before: bool = False
    outline_level: int | None = None    # 0=一级标题，用于 Word 导航窗格
    shading: str | None = None          # 底纹色，如 "F2F2F2"

    def to_xml(self) -> str:
        # w:pPr 的子元素必须严格按 CT_PPrBase 的 schema 顺序排列：
        # pageBreakBefore → pBdr → shd → tabs → spacing → ind → jc → outlineLvl → rPr
        # Word 对乱序容忍，LibreOffice 会直接判定文档无法加载。
        ppr = []
        if self.page_break_before:
            ppr.append("<w:pageBreakBefore/>")
        if self.shading:
            ppr.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{self.shading}"/>')
        spacing = f'w:before="{self.space_before}" w:after="{self.space_after}"'
        if self.line_twips:
            spacing += f' w:line="{self.line_twips}" w:lineRule="exact"'
        ppr.append(f"<w:spacing {spacing}/>")
        if self.indent_left or self.first_line_indent:
            ppr.append(
                f'<w:ind w:left="{self.indent_left}" w:firstLine="{self.first_line_indent}"/>'
            )
        if self.align:
            ppr.append(f'<w:jc w:val="{self.align}"/>')
        if self.outline_level is not None:
            ppr.append(f'<w:outlineLvl w:val="{self.outline_level}"/>')
        ppr.append(self.font.rpr())

        if self.runs:
            body = "".join(
                f"<w:r>{run_font.rpr()}"
                f'<w:t xml:space="preserve">{escape(run_text)}</w:t></w:r>'
                for run_text, run_font in self.runs
                if run_text
            )
        elif self.text:
            body = (
                f"<w:r>{self.font.rpr()}"
                f'<w:t xml:space="preserve">{escape(self.text)}</w:t></w:r>'
            )
        else:
            body = ""
        return f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{body}</w:p>'


@dataclass
class Table:
    """简单表格：首行为表头，等宽列。"""

    rows: list[list[str]]
    width_twips: int
    font: Font = field(default_factory=lambda: BODY_FONT)
    header_fill: str = "EFEFEF"

    def to_xml(self) -> str:
        if not self.rows:
            return ""
        col_count = max(len(r) for r in self.rows)
        col_w = self.width_twips // col_count
        grid = "".join(f'<w:gridCol w:w="{col_w}"/>' for _ in range(col_count))

        borders = (
            "<w:tblBorders>"
            + "".join(
                f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
            )
            + "</w:tblBorders>"
        )
        props = (
            "<w:tblPr>"
            f'<w:tblW w:w="{self.width_twips}" w:type="dxa"/>'
            f"{borders}"
            '<w:tblLayout w:type="fixed"/>'
            "</w:tblPr>"
        )

        body = []
        for row_index, row in enumerate(self.rows):
            cells = []
            is_header = row_index == 0
            cell_font = Font(
                ascii=self.font.ascii,
                east_asia=self.font.east_asia,
                size_pt=self.font.size_pt,
                bold=is_header,
            )
            for col in range(col_count):
                value = row[col] if col < len(row) else ""
                shd = (
                    f'<w:shd w:val="clear" w:color="auto" w:fill="{self.header_fill}"/>'
                    if is_header
                    else ""
                )
                # 单元格内的换行必须拆成多个段落：Word 不把 \n 当断行，
                # 截图占位框正是靠多段落把框撑到能贴图的高度
                paras = "".join(
                    Paragraph(
                        text=part,
                        font=cell_font,
                        space_before=20,
                        space_after=20,
                        align="center" if len(value.split("\n")) > 1 else None,
                    ).to_xml()
                    for part in value.split("\n")
                )
                cells.append(
                    f'<w:tc><w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/>{shd}'
                    f'<w:vAlign w:val="center"/></w:tcPr>{paras}</w:tc>'
                )
            trpr = "<w:trPr><w:tblHeader/></w:trPr>" if is_header else ""
            body.append(f"<w:tr>{trpr}{''.join(cells)}</w:tr>")

        # 表格后必须跟一个空段落，否则紧邻的表格会被 Word 合并
        return f"<w:tbl>{props}{'<w:tblGrid>' + grid + '</w:tblGrid>'}{''.join(body)}</w:tbl>" + (
            '<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="120" '
            'w:lineRule="exact"/></w:pPr></w:p>'
        )


def image_info(path: str | Path) -> tuple[int, int, str, str]:
    """Read PNG/JPEG dimensions and media metadata without Pillow."""
    raw = Path(path).read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
        width, height = struct.unpack(">II", raw[16:24])
        return width, height, "png", "image/png"
    if raw[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(raw):
            if raw[index] != 0xFF:
                index += 1
                continue
            while index < len(raw) and raw[index] == 0xFF:
                index += 1
            marker = raw[index]
            index += 1
            if marker in (0xD8, 0xD9):
                continue
            if index + 2 > len(raw):
                break
            length = struct.unpack(">H", raw[index : index + 2])[0]
            if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB or 0xCD <= marker <= 0xCF:
                if index + 7 <= len(raw):
                    height, width = struct.unpack(">HH", raw[index + 3 : index + 7])
                    return width, height, "jpg", "image/jpeg"
            index += max(length, 2)
    raise ValueError(f"不是受支持的 PNG/JPEG 文件：{path}")


@dataclass
class Image:
    """An inline PNG image sized to fit the current page content width."""

    path: Path
    width_twips: int
    height_twips: int
    doc_id: int = 1
    extension: str = "png"
    content_type: str = "image/png"

    @classmethod
    def fit_png(cls, path: str | Path, max_width_twips: int, doc_id: int = 1) -> "Image":
        path = Path(path)
        width, height, extension, content_type = image_info(path)
        width_twips = min(max_width_twips, max(1, int(max_width_twips)))
        height_twips = max(1, int(round(width_twips * height / width)))
        return cls(
            path=path,
            width_twips=width_twips,
            height_twips=height_twips,
            doc_id=doc_id,
            extension=extension,
            content_type=content_type,
        )

    def to_xml(self, rel_id: str) -> str:
        # Word/LibreOffice both understand this compact DrawingML inline image.
        cx = self.width_twips * 635
        cy = self.height_twips * 635
        name = escape(self.path.name)
        return (
            '<w:p><w:pPr><w:spacing w:before="80" w:after="80"/></w:pPr>'
            '<w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{self.doc_id}" name="{name}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic>'
            f'<pic:nvPicPr><pic:cNvPr id="0" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r></w:p>'
        )


# ── 文档 ────────────────────────────────────────────────────────────────
class DocxBuilder:
    """把段落/表格攒起来，最后打包成 .docx。"""

    def __init__(
        self,
        header_left: str,
        margin_cm_top: float = 2.2,
        margin_cm_bottom: float = 2.2,
        margin_cm_left: float = 2.2,
        margin_cm_right: float = 2.2,
        show_page_number: bool = True,
        page_number_in_footer: bool = False,
        header_align: str | None = None,
    ) -> None:
        self.header_left = header_left
        self.show_page_number = show_page_number
        # 《文档鉴别材料模板》把页码放在页脚（"3 of 5"），页眉只留"软件全称 + 版本号 + 使用说明"。
        # 代码鉴别材料仍按登记指南把页码放在右上角，所以这里做成开关而不是直接改默认行为。
        self.page_number_in_footer = page_number_in_footer
        self.header_align = header_align
        self.margin_top = cm(margin_cm_top)
        self.margin_bottom = cm(margin_cm_bottom)
        self.margin_left = cm(margin_cm_left)
        self.margin_right = cm(margin_cm_right)
        self.blocks: list[Paragraph | Table | Image | Raw] = []

    @property
    def content_width(self) -> int:
        """正文可用宽度（twip），表格和页眉右对齐制表位都按它算。"""
        return A4_WIDTH_TWIPS - self.margin_left - self.margin_right

    @property
    def content_height(self) -> int:
        """正文可用高度（twip），用于估算每页能放几行。"""
        return A4_HEIGHT_TWIPS - self.margin_top - self.margin_bottom

    # -- 内容 --
    def add(self, block: Paragraph | Table | Image) -> None:
        self.blocks.append(block)

    def add_paragraph(self, text: str = "", **kwargs) -> None:
        self.blocks.append(Paragraph(text=text, **kwargs))

    def add_table(self, rows: list[list[str]], font: Font | None = None) -> None:
        self.blocks.append(
            Table(rows=rows, width_twips=self.content_width, font=font or BODY_FONT)
        )

    def add_page_break(self) -> None:
        """插入硬分页（下一段从新页开始）。"""
        self.blocks.append(
            Paragraph(
                text="",
                page_break_before=True,
                line_twips=20,
                font=Font(size_pt=1),
            )
        )

    # -- 打包 --
    def _header_xml(self) -> str:
        # 软件名称+版本号居中，页码仍在右上角——用「居中制表位 + 右制表位」
        # 一行三栏实现，而不是把标题真的左对齐：登记指南只要求页码在右上角，
        # 标题居中是纯排版偏好，两者不冲突。
        tab_pos = self.content_width
        center_pos = tab_pos // 2
        if self.page_number_in_footer:
            jc = f'<w:jc w:val="{self.header_align}"/>' if self.header_align else ""
            return (
                _XML_DECL
                + f'<w:hdr xmlns:w="{_NS_W}" xmlns:r="{_NS_R}">'
                + "<w:p><w:pPr>"
                + '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="BFBFBF"/></w:pBdr>'
                + '<w:spacing w:before="0" w:after="60"/>'
                + jc
                + HEADER_FONT.rpr()
                + "</w:pPr>"
                + f'<w:r>{HEADER_FONT.rpr()}<w:t xml:space="preserve">{escape(self.header_left)}</w:t></w:r>'
                + "</w:p></w:hdr>"
            )

        runs = [
            f"<w:r>{HEADER_FONT.rpr()}<w:tab/></w:r>",
            f'<w:r>{HEADER_FONT.rpr()}<w:t xml:space="preserve">{escape(self.header_left)}</w:t></w:r>',
        ]
        if self.show_page_number:
            runs.append(f"<w:r>{HEADER_FONT.rpr()}<w:tab/></w:r>")
            runs.append(
                f'<w:r>{HEADER_FONT.rpr()}<w:fldChar w:fldCharType="begin"/></w:r>'
                f'<w:r>{HEADER_FONT.rpr()}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                f'<w:r>{HEADER_FONT.rpr()}<w:fldChar w:fldCharType="separate"/></w:r>'
                f"<w:r>{HEADER_FONT.rpr()}<w:t>1</w:t></w:r>"
                f'<w:r>{HEADER_FONT.rpr()}<w:fldChar w:fldCharType="end"/></w:r>'
            )
        return (
            _XML_DECL
            + f'<w:hdr xmlns:w="{_NS_W}" xmlns:r="{_NS_R}">'
            + "<w:p><w:pPr>"
            # pBdr 必须排在 tabs 之前，见 Paragraph.to_xml 的同类说明
            + '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="BFBFBF"/></w:pBdr>'
            + (
                f'<w:tabs><w:tab w:val="center" w:pos="{center_pos}"/>'
                f'<w:tab w:val="right" w:pos="{tab_pos}"/></w:tabs>'
            )
            + '<w:spacing w:before="0" w:after="60"/>'
            + HEADER_FONT.rpr()
            + "</w:pPr>"
            + "".join(runs)
            + "</w:p></w:hdr>"
        )

    def _footer_xml(self) -> str:
        """页脚："N of M"，右对齐。M 用 NUMPAGES 域，Word 打开时自动补齐。"""
        f = HEADER_FONT

        def field(name: str, cached: str) -> str:
            return (
                f'<w:r>{f.rpr()}<w:fldChar w:fldCharType="begin"/></w:r>'
                f'<w:r>{f.rpr()}<w:instrText xml:space="preserve"> {name} </w:instrText></w:r>'
                f'<w:r>{f.rpr()}<w:fldChar w:fldCharType="separate"/></w:r>'
                f"<w:r>{f.rpr()}<w:t>{cached}</w:t></w:r>"
                f'<w:r>{f.rpr()}<w:fldChar w:fldCharType="end"/></w:r>'
            )

        return (
            _XML_DECL
            + f'<w:ftr xmlns:w="{_NS_W}" xmlns:r="{_NS_R}">'
            + "<w:p><w:pPr>"
            + '<w:spacing w:before="60" w:after="0"/>'
            + '<w:jc w:val="right"/>'
            + f.rpr()
            + "</w:pPr>"
            + field("PAGE", "1")
            + f'<w:r>{f.rpr()}<w:t xml:space="preserve"> of </w:t></w:r>'
            + field("NUMPAGES", "1")
            + "</w:p></w:ftr>"
        )

    def _document_xml(self) -> str:
        body_parts = []
        image_index = 0
        for block in self.blocks:
            if isinstance(block, Image):
                image_index += 1
                block.doc_id = image_index
                body_parts.append(block.to_xml(f"rId{3 + image_index}"))
            else:
                body_parts.append(block.to_xml())
        body = "".join(body_parts)
        sect = (
            "<w:sectPr>"
            '<w:headerReference w:type="default" r:id="rId2"/>'
            + ('<w:footerReference w:type="default" r:id="rIdFtr"/>' if self.page_number_in_footer else "")
            +
            f'<w:pgSz w:w="{A4_WIDTH_TWIPS}" w:h="{A4_HEIGHT_TWIPS}"/>'
            f'<w:pgMar w:top="{self.margin_top}" w:right="{self.margin_right}" '
            f'w:bottom="{self.margin_bottom}" w:left="{self.margin_left}" '
            'w:header="600" w:footer="600" w:gutter="0"/>'
            '<w:docGrid w:linePitch="312"/>'
            "</w:sectPr>"
        )
        return (
            _XML_DECL
            + f'<w:document xmlns:w="{_NS_W}" xmlns:r="{_NS_R}" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            + f"<w:body>{body}{sect}</w:body></w:document>"
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content_types = (
            _XML_DECL
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="jpg" ContentType="image/jpeg"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
            + ('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>' if self.page_number_in_footer else "")
            +
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            "</Types>"
        )
        root_rels = (
            _XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        )
        image_blocks = [block for block in self.blocks if isinstance(block, Image)]
        image_rels = "".join(
            f'<Relationship Id="rId{index + 4}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image{index}.{image.extension}"/>'
            for index, image in enumerate(image_blocks)
        )
        doc_rels = (
            _XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
            + (
                '<Relationship Id="rIdFtr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                if self.page_number_in_footer
                else ""
            )
            + image_rels
            + "</Relationships>"
        )
        styles = (
            _XML_DECL
            + f'<w:styles xmlns:w="{_NS_W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="Songti SC"/>'
            '<w:sz w:val="21"/><w:szCs w:val="21"/>'
            "</w:rPr></w:rPrDefault>"
            '<w:pPrDefault><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr></w:pPrDefault>'
            "</w:docDefaults>"
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/></w:style>'
            "</w:styles>"
        )
        settings = (
            _XML_DECL
            + f'<w:settings xmlns:w="{_NS_W}">'
            '<w:zoom w:percent="100"/>'
            '<w:defaultTabStop w:val="420"/>'
            '<w:updateFields w:val="true"/>'
            "<w:compat/>"
            "</w:settings>"
        )

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", root_rels)
            zf.writestr("word/_rels/document.xml.rels", doc_rels)
            zf.writestr("word/document.xml", self._document_xml())
            zf.writestr("word/styles.xml", styles)
            zf.writestr("word/header1.xml", self._header_xml())
            if self.page_number_in_footer:
                zf.writestr("word/footer1.xml", self._footer_xml())
            zf.writestr("word/settings.xml", settings)
            for index, image in enumerate(image_blocks):
                zf.write(image.path, f"word/media/image{index}.{image.extension}")
        return path

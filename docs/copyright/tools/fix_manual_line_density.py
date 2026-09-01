#!/usr/bin/env python3
"""
fix_manual_line_density — 把 skill 生成的操作手册 Word 改成确定的每页行数。

《计算机软件著作权登记办法》要求文档鉴别材料**每页不少于 30 行**。
SoftwareCopyright-Skill 的 build_docx_from_md.py 用 python-docx 的模板默认排版：

    <w:spacing w:after="200" w:line="276" w:lineRule="auto"/>

即 1.15 倍行距 + 段后 10 磅。「倍数行距」的实际行高由字体的 hhea 度量决定，
宋体的系数是 1.400，10.5pt 正文单倍行高 14.7 磅，1.15 倍即 16.9 磅：

    版心 = A4 高 16838 − 上 1440 − 下 1440 = 13958 twip = 698 磅
    698 ÷ 16.9 ≈ 41 行，再扣掉每段 10 磅段后（每页 6~8 段）→ 实际 33~36 行

刚好压在 30 行线上，换台机器、换个字体或换个 PDF 导出器就可能掉到 29 行。
本脚本把 docDefaults 改成**固定行距**并取消段后：

    <w:spacing w:after="0" w:line="400" w:lineRule="exact"/>

    698 ÷ 20 磅 = 34 行/页（比 30 行下限留 4 行余量）

固定行距与字体度量无关，行数变成确定的排版结果而不是渲染环境的函数。
含整幅截图的页面行数天然低于 30 行，按登记惯例可按实际版式审查。

用法：
    python3 docs/copyright/tools/fix_manual_line_density.py \
        "docs/copyright/软件著作权申请资料/正式资料/论文精读辅助分析软件_操作手册.docx"

每次重新运行 skill 的 build_docx_from_md.py 之后都要再跑一次本脚本。
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

# 固定行距 20 磅（400 twip），段前段后归零。
# 20 磅是「够宽松又稳过 30 行」的档位：版心 13958 twip ÷ 400 = 34 行/页，
# 比 30 行的下限留 4 行余量。压到 16 磅能排 43 行，但行挤在一起且页数被压缩，
# 对一份本来就不长的手册没有好处。
NEW_SPACING = '<w:spacing w:after="0" w:before="0" w:line="400" w:lineRule="exact"/>'
OLD_SPACING_RE = re.compile(r"<w:spacing[^/]*?/>")

# 正文字号 五号(21 半磅) → 小四(24 半磅)，与《文档鉴别材料模板》一致。
# 只动正文这一档，标题走各自的样式，不受影响。
BODY_SZ_FROM, BODY_SZ_TO = '21', '24' 


def patch(docx_path: Path) -> int:
    if not docx_path.exists():
        print(f"找不到文件：{docx_path}", file=sys.stderr)
        return 1

    backup = docx_path.with_suffix(".docx.bak")
    shutil.copy2(docx_path, backup)

    with zipfile.ZipFile(docx_path) as src:
        parts = {name: src.read(name) for name in src.namelist()}

    styles = parts["word/styles.xml"].decode("utf-8")
    default = re.search(r"<w:pPrDefault>.*?</w:pPrDefault>", styles, re.S)
    if not default:
        print("styles.xml 中没有 pPrDefault，未做修改", file=sys.stderr)
        return 1

    patched_default = OLD_SPACING_RE.sub(NEW_SPACING, default.group())
    if "w:lineRule=\"exact\"" not in patched_default:
        # 原本就没有 spacing 元素，直接插一个
        patched_default = patched_default.replace("<w:pPr>", f"<w:pPr>{NEW_SPACING}", 1)
    styles = styles.replace(default.group(), patched_default)
    parts["word/styles.xml"] = styles.encode("utf-8")

    # 段落上的直接格式会覆盖 docDefaults，一并归一
    document = parts["word/document.xml"].decode("utf-8")
    inline = len(OLD_SPACING_RE.findall(document))
    document = OLD_SPACING_RE.sub(NEW_SPACING, document)
    bumped = document.count(f'<w:sz w:val="{BODY_SZ_FROM}"/>')
    document = document.replace(
        f'<w:sz w:val="{BODY_SZ_FROM}"/>', f'<w:sz w:val="{BODY_SZ_TO}"/>'
    )
    parts["word/document.xml"] = document.encode("utf-8")
    styles = styles.replace(
        f'<w:sz w:val="{BODY_SZ_FROM}"/>', f'<w:sz w:val="{BODY_SZ_TO}"/>'
    )
    parts["word/styles.xml"] = styles.encode("utf-8")

    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in parts.items():
            out.writestr(name, data)

    content_height = 16838 - 1440 - 1440
    print(f"已修改：{docx_path}")
    print(f"  备份　　　　{backup.name}")
    print(f"  docDefaults 行距 → 固定 20 磅，段前段后 → 0")
    print(f"  段落内联行距 → 归一 {inline} 处")
    print(f"  正文字号 五号 → 小四（{bumped} 处）")
    print(f"  版心 {content_height} twip ÷ 400 = {content_height // 400} 行/页（确定值，≥30 行，余量 {content_height // 400 - 30} 行）")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    return max(patch(Path(a)) for a in sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

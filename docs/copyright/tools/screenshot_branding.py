#!/usr/bin/env python3
"""
screenshot_branding — 截图前临时把界面品牌与版本号切换到软著登记口径。

登记审查会核对「截图中的软件名称、版本号与申请表一致」，并要求软件中
不出现旧项目名（项目英文名、简称等）。本项目的界面上有两处冲突：

  1. 顶部品牌区固定显示英文标识与副标题，浏览器标签页同样是英文名
  2. 终端面板与版本弹窗显示 5.2.0，与申请表建议填写的 V1.0 不一致

这两处出现在几乎每一张界面截图里，是审查员一眼能看到的不一致。

本脚本只在截图期间临时替换这些字面量，截完立即还原，不进入主线代码，
也不改动项目自身的版本管理规范（VersionPopup 的更新日志逻辑不动）。

用法：
    # 1. 切换到登记口径
    python3 docs/copyright/tools/screenshot_branding.py --apply

    # 2. 启动软件、逐项截图（见 用户手册.md 附录 C）
    ./start.sh

    # 3. 还原
    python3 docs/copyright/tools/screenshot_branding.py --restore

--restore 通过 git 恢复这几个文件，因此请在工作区干净时使用；
脚本会先检查这些文件有没有未提交的改动，有则拒绝执行。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 受影响的文件与替换规则：(文件, [(原文, 替换模板)])
# 替换模板中的 {name} / {version} 由命令行参数注入
TARGETS: dict[str, list[tuple[str, str]]] = {
    "frontend/index.html": [
        ("<title>Easy Essay</title>", "<title>{name}</title>"),
    ],
    "frontend/src/components/Layout.tsx": [
        ('aria-label="查看 Easy Essay 版本信息"', 'aria-label="查看{name}版本信息"'),
        (
            '<span className="er-brand__mark" aria-hidden="true">ER</span>\n'
            "            <span><strong>EASY ESSAY</strong>"
            "<small>RESEARCH INTELLIGENCE</small></span>",
            '<span className="er-brand__mark" aria-hidden="true">论</span>\n'
            "            <span><strong>{name}</strong>"
            "<small>V{version}</small></span>",
        ),
    ],
    "frontend/src/components/VersionPopup.tsx": [
        ("const APP_VERSION = '5.2.0'", "const APP_VERSION = '{version}'"),
        ('alt="Easy Essay 几何标记"', 'alt="{name}标记"'),
        ('<h2 id="version-title">Easy Essay</h2>', '<h2 id="version-title">{name}</h2>'),
    ],
}


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def check_clean(root: Path, files: list[str]) -> bool:
    result = run_git(root, "status", "--porcelain", "--", *files)
    if result.stdout.strip():
        print("以下文件有未提交的改动，请先提交或暂存后再运行：", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return False
    return True


def apply_branding(root: Path, name: str, version: str) -> int:
    if not check_clean(root, list(TARGETS)):
        return 1

    changed = 0
    for rel, rules in TARGETS.items():
        path = root / rel
        if not path.exists():
            print(f"  跳过（文件不存在）：{rel}")
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for needle, template in rules:
            replacement = template.format(name=name, version=version)
            if needle not in text:
                print(f"  ⚠ 未匹配到字面量，请人工检查：{rel}")
                print(f"    期望内容：{needle.splitlines()[0][:60]}…")
                continue
            text = text.replace(needle, replacement)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"  ✓ 已替换：{rel}")

    print()
    print(f"已切换到登记口径：{name}  V{version}")
    print()
    print("接下来：")
    print("  1) 运行 ./start.sh 启动软件")
    print("  2) 按 用户手册.md 附录 C 的 37 项清单逐一截图")
    print("  3) 截图时确认左上角品牌区与终端面板显示的名称、版本号已是登记口径")
    print("  4) 截完执行 --restore 还原")
    return 0 if changed else 1


def restore(root: Path) -> int:
    result = run_git(root, "checkout", "--", *TARGETS)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print("已还原以下文件到最近一次提交的状态：")
    for rel in TARGETS:
        print(f"  · {rel}")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[3]

    parser = argparse.ArgumentParser(description="截图前临时切换界面品牌与版本号")
    parser.add_argument("--root", default=str(root))
    parser.add_argument("--name", default="论文精读辅助分析软件", help="软件全称，须与申请表一致")
    parser.add_argument("--version", default="1.0", help="版本号，不带 V 前缀")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true", help="切换到登记口径")
    group.add_argument("--restore", action="store_true", help="还原为主线代码")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        print(f"不是 git 仓库：{root}", file=sys.stderr)
        return 1

    return restore(root) if args.restore else apply_branding(root, args.name, args.version)


if __name__ == "__main__":
    raise SystemExit(main())

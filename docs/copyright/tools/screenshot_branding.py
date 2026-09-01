#!/usr/bin/env python3
"""
screenshot_branding — 截图前把界面版本号临时切到软著登记口径。

登记审查会核对「截图中的软件名称、版本号与申请表一致」。软件名称已在
主线代码中统一为中文全称，无需处理；只剩版本号一项对不上：

  界面（顶部标识、终端面板、版本弹窗）显示产品迭代号 5.2.x
  申请表登记版本号为 V1.0

产品迭代号受 docs/commit_regular.md 的版本规范约束，不能为软著改掉，
所以用本脚本在截图期间临时替换，截完立即还原。

用法：
    python3 docs/copyright/tools/screenshot_branding.py --apply
    ./start.sh                      # 逐项截图，见 docs/copyright/截图清单.md
    python3 docs/copyright/tools/screenshot_branding.py --restore

--restore 通过 git 恢复目标文件，因此 --apply 前要求工作区对这些文件是
干净的；脚本会先检查，有未提交改动则拒绝执行。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# 版本号的唯一定义处。TerminalPanel 与 Layout 都引用这个常量，
# 改这一处即可覆盖界面上全部三个显示位置。
VERSION_FILE = "frontend/src/components/VersionPopup.tsx"
VERSION_PATTERN = re.compile(r"(const APP_VERSION = ')([^']+)(')")

# 版本弹窗里另外两处不该进登记截图的内容，同在这个文件里：
#   1. 仓库链接——含真实 GitHub 账号，且与「未发表」的登记口径冲突。
#      置空后组件不渲染该行（VersionPopup 里已做条件渲染）。
#   2. 更新日志——主线文案写的是「统一中文软件名、去掉旧项目缩写」，
#      等于在截图里交代软件改过名，换成与登记版本对应的中性描述。
GITHUB_PATTERN = re.compile(r"(const GITHUB_URL = ')([^']*)(')")
RELEASE_PATTERN = re.compile(
    r"const CURRENT_RELEASE = \{.*?\n\}", re.DOTALL
)
RELEASE_REPLACEMENT = """const CURRENT_RELEASE = {
  date: '2026-08-21',
  changes: [
    '论文精读辅助分析软件 V1.0 首个登记版本',
    '含论文解析、论文库管理、代码仓库分析、工作区与深度研究五类功能',
  ],
}"""

TARGETS = [VERSION_FILE]


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def apply_version(root: Path, version: str) -> int:
    result = run_git(root, "status", "--porcelain", "--", *TARGETS)
    if result.stdout.strip():
        print("目标文件有未提交的改动，请先提交或暂存后再运行：", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    path = root / VERSION_FILE
    text = path.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(text)
    if not match:
        print(f"未在 {VERSION_FILE} 中找到 APP_VERSION 定义，请人工检查", file=sys.stderr)
        return 1

    current = match.group(2)
    text = VERSION_PATTERN.sub(rf"\g<1>{version}\g<3>", text)

    github = GITHUB_PATTERN.search(text)
    text = GITHUB_PATTERN.sub(r"\g<1>\g<3>", text)
    release_hit = RELEASE_PATTERN.search(text)
    if release_hit:
        text = RELEASE_PATTERN.sub(lambda _: RELEASE_REPLACEMENT, text, count=1)

    path.write_text(text, encoding="utf-8")

    print(f"  ✓ 版本号：{current} → {version}")
    if github and github.group(2):
        print("  ✓ 版本弹窗的仓库链接：已隐藏（含真实 GitHub 账号）")
    if release_hit:
        print("  ✓ 版本弹窗的更新日志：已换成登记版本的中性描述")
    print()
    print("界面上受影响的三处版本号显示：顶部品牌标识副标题、终端面板、版本弹窗")
    print()
    print("接下来：")
    print("  1) ./start.sh 启动软件，并准备好测试数据（空列表截图看不出功能）")
    print("  2) 按 docs/copyright/截图清单.md 的 38 项逐一截图")
    print("  3) 截完执行 --restore 还原")
    return 0


def restore(root: Path) -> int:
    result = run_git(root, "checkout", "--", *TARGETS)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print(f"已还原 {VERSION_FILE} 到最近一次提交的状态")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[3]

    parser = argparse.ArgumentParser(description="截图前临时切换界面版本号")
    parser.add_argument("--root", default=str(root))
    parser.add_argument("--version", default="1.0", help="登记版本号，不带 V 前缀")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true", help="切换到登记口径")
    group.add_argument("--restore", action="store_true", help="还原为主线版本号")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        print(f"不是 git 仓库：{root}", file=sys.stderr)
        return 1

    return restore(root) if args.restore else apply_version(root, args.version)


if __name__ == "__main__":
    raise SystemExit(main())

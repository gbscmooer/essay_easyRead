"""
代码分析服务

管理代码仓库的 clone、文件浏览和分析。
"""

import os
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 需要跳过的目录和文件
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info", ".idea", ".vscode",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg",
    ".mp4", ".mp3", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
}

# 最大文件大小（1MB）
MAX_FILE_SIZE = 1 * 1024 * 1024

# 文件树最大深度
MAX_TREE_DEPTH = 6


def get_code_download_dir() -> str:
    """获取代码下载目录"""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "code_downloads")
    os.makedirs(base, exist_ok=True)
    return base


async def clone_repository(url: str, paper_id: int) -> str:
    """
    Clone 代码仓库到本地。

    Args:
        url: 仓库 URL
        paper_id: 论文 ID（用于目录命名）

    Returns:
        clone 后的本地路径

    Raises:
        Exception: clone 失败
    """
    # 规范化 URL：确保有 https:// 前缀和 .git 后缀
    if not url.startswith("http"):
        url = "https://" + url
    if not url.endswith(".git"):
        url = url.rstrip("/") + ".git"

    target_dir = os.path.join(get_code_download_dir(), str(paper_id))

    # 如果已存在，先删除
    if os.path.exists(target_dir):
        import shutil
        shutil.rmtree(target_dir, ignore_errors=True)

    # 浅 clone，只获取最新代码
    cmd = [
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        url,
        target_dir,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git clone 失败: {error_msg}")

    return target_dir


def build_file_tree(repo_path: str, max_depth: int = MAX_TREE_DEPTH) -> dict:
    """
    遍历目录，构建文件树结构。

    Args:
        repo_path: 仓库根目录
        max_depth: 最大遍历深度

    Returns:
        文件树字典 {"name": ..., "type": "dir"|"file", "children": [...]}
    """
    repo_path = os.path.abspath(repo_path)

    def _build(current_path: str, depth: int) -> dict:
        name = os.path.basename(current_path)
        rel_path = os.path.relpath(current_path, repo_path)

        if os.path.isdir(current_path):
            children = []
            if depth < max_depth:
                try:
                    entries = sorted(os.listdir(current_path))
                except PermissionError:
                    entries = []
                for entry in entries:
                    if entry in SKIP_DIRS or entry.startswith("."):
                        continue
                    child_path = os.path.join(current_path, entry)
                    child = _build(child_path, depth + 1)
                    if child:
                        children.append(child)
            return {
                "name": name,
                "type": "dir",
                "path": rel_path,
                "children": children,
            }
        else:
            # 检查文件扩展名
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXTENSIONS:
                return None

            # 检查文件大小
            try:
                size = os.path.getsize(current_path)
            except OSError:
                return None
            if size > MAX_FILE_SIZE:
                return None

            return {
                "name": name,
                "type": "file",
                "path": rel_path,
                "size": size,
            }

    tree = _build(repo_path, 0)
    # 确保根节点是目录
    if tree and tree["type"] == "dir":
        return tree
    return {"name": os.path.basename(repo_path), "type": "dir", "path": ".", "children": [tree] if tree else []}


def read_file_content(repo_path: str, rel_path: str, max_lines: int = 500) -> str:
    """
    读取文件内容。

    Args:
        repo_path: 仓库根目录
        rel_path: 相对路径
        max_lines: 最大行数

    Returns:
        文件内容字符串

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 跕径不安全
    """
    # 安全检查：防止路径穿越
    full_path = os.path.realpath(os.path.join(repo_path, rel_path))
    if not full_path.startswith(os.path.realpath(repo_path)):
        raise ValueError("不安全的文件路径")

    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"文件不存在: {rel_path}")

    # 检查是否为二进制文件
    try:
        with open(full_path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return "[二进制文件，无法显示]"
    except Exception:
        pass

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    lines.append(f"\n... [文件过长，已截断前 {max_lines} 行]")
                    break
                lines.append(line)
            return "".join(lines)
    except Exception as e:
        return f"[读取失败: {e}]"


def select_core_files(file_tree: dict, repo_path: str, max_files: int = 30) -> list[str]:
    """
    智能选取核心文件用于 AI 分析。

    优先级：
    1. README* 文件
    2. 配置文件 (setup.py, pyproject.toml, package.json, requirements.txt)
    3. 主入口文件 (main.py, app.py, train.py, run.py)
    4. 模型相关文件 (model*.py, net*.py, network*.py)
    5. 其他 Python/JS/TS 文件（按大小排序，小文件优先）

    Returns:
        文件相对路径列表
    """
    # 收集所有文件
    all_files = []

    def _collect(node: dict):
        if node["type"] == "file":
            all_files.append(node["path"])
        elif node.get("children"):
            for child in node["children"]:
                _collect(child)

    _collect(file_tree)

    # 分类
    readme_files = []
    config_files = []
    entry_files = []
    model_files = []
    other_files = []

    for f in all_files:
        name = os.path.basename(f).lower()
        if name.startswith("readme"):
            readme_files.append(f)
        elif name in ("setup.py", "pyproject.toml", "package.json", "requirements.txt", "setup.cfg", "Makefile", "Dockerfile"):
            config_files.append(f)
        elif name in ("main.py", "app.py", "train.py", "run.py", "test.py", "index.py", "index.ts", "index.js"):
            entry_files.append(f)
        elif any(pattern in name for pattern in ("model", "net", "network", "module", "backbone", "head", "loss")):
            model_files.append(f)
        else:
            other_files.append(f)

    # 合并，按优先级排序
    selected = readme_files + config_files + entry_files + model_files

    # 如果还不够，从 other_files 中补充（按文件大小排序，小文件优先）
    if len(selected) < max_files:
        remaining = max_files - len(selected)
        # 按大小排序
        other_with_size = []
        for f in other_files:
            full = os.path.join(repo_path, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            other_with_size.append((f, size))
        other_with_size.sort(key=lambda x: x[1])
        selected.extend(f for f, _ in other_with_size[:remaining])

    return selected[:max_files]


def analyze_imports(repo_path: str, files: list[str]) -> list[dict]:
    """
    静态分析 import 语句，构建模块依赖图。

    支持 Python (import/from), JS/TS (import/require), C++ (#include)。

    Returns:
        [{"source": "model.py", "target": "utils.py", "imports": ["func1", "func2"]}]
    """
    import re

    edges = []

    for rel_path in files:
        full_path = os.path.join(repo_path, rel_path)
        if not os.path.isfile(full_path):
            continue

        ext = os.path.splitext(rel_path)[1].lower()
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50000)  # 只读前 50KB
        except Exception:
            continue

        imported = []

        if ext == ".py":
            # Python: import X / from X import Y
            for m in re.finditer(r"^(?:from\s+(\S+)\s+import\s+(.+)|import\s+(\S+))", content, re.MULTILINE):
                if m.group(1):  # from X import Y
                    module = m.group(1)
                    names = [n.strip().split(" as ")[0] for n in m.group(2).split(",")]
                    imported.append({"module": module, "names": names})
                elif m.group(3):  # import X
                    imported.append({"module": m.group(3), "names": []})

        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            # JS/TS: import X from 'Y' / require('Y')
            for m in re.finditer(r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""", content):
                module = m.group(1) or m.group(2)
                if module and not module.startswith("."):
                    continue  # 只关注本地导入
                imported.append({"module": module or "", "names": []})

        elif ext in (".cpp", ".cc", ".c", ".h", ".hpp"):
            # C/C++: #include "file"
            for m in re.finditer(r'#include\s+"([^"]+)"', content):
                imported.append({"module": m.group(1), "names": []})

        # 将导入转换为边
        for imp in imported:
            module = imp["module"]
            if not module:
                continue
            # 尝试解析为本地文件
            target = _resolve_module_to_file(module, rel_path, repo_path)
            if target:
                edges.append({
                    "source": rel_path,
                    "target": target,
                    "imports": imp["names"][:5],  # 最多5个
                })

    return edges


def _resolve_module_to_file(module: str, from_file: str, repo_path: str) -> Optional[str]:
    """尝试将模块名解析为本地文件路径"""
    from pathlib import Path

    # Python 模块: "models.backbone" -> "models/backbone.py"
    if "." in module and "/" not in module and "\\" not in module:
        parts = module.split(".")
        candidate = os.path.join(*parts) + ".py"
        full = os.path.join(repo_path, os.path.dirname(from_file), candidate)
        if os.path.isfile(full):
            return os.path.relpath(full, repo_path)

        candidate_dir = os.path.join(*parts, "__init__.py")
        full = os.path.join(repo_path, os.path.dirname(from_file), candidate_dir)
        if os.path.isfile(full):
            return os.path.relpath(full, repo_path)

    # 相对路径导入: "./utils" or "../lib/helper"
    if module.startswith("."):
        base_dir = os.path.dirname(from_file)
        # 去掉开头的点
        dots = len(module) - len(module.lstrip("."))
        rel = module[dots:]
        for ext in ("", ".py", ".js", ".ts", "/__init__.py", "/index.js", "/index.ts"):
            candidate = os.path.join(base_dir, ".." * (dots - 1), rel + ext)
            candidate = os.path.normpath(candidate)
            full = os.path.join(repo_path, candidate)
            if os.path.isfile(full):
                return candidate

    return None


# ============================================================
# AI 分析函数
# ============================================================

def _format_file_contents(file_contents: dict[str, str], max_total_chars: int = 80000) -> str:
    """格式化文件内容为 prompt 文本，限制总长度"""
    parts = []
    total = 0
    for path, content in file_contents.items():
        header = f"\n{'='*60}\n文件: {path}\n{'='*60}\n"
        entry = header + content
        if total + len(entry) > max_total_chars:
            parts.append(f"\n... [剩余文件已省略]")
            break
        parts.append(entry)
        total += len(entry)
    return "\n".join(parts)


async def ai_analyze_data_flow(
    file_contents: dict[str, str],
    paper_title: str,
    api_key: str, base_url: str, model: str,
) -> str:
    """AI 分析代码数据流"""
    from .ai_analyzer import _create_client

    formatted = _format_file_contents(file_contents)
    prompt = (
        f'你是一个代码分析专家。以下是论文"{paper_title}"的开源实现代码库中的核心文件内容。\n\n'
        f"请分析这些代码，描述数据从输入到输出的完整流转过程。\n\n"
        f"要求：\n"
        f"1. 从数据加载/预处理开始，到最终输出/后处理结束\n"
        f"2. 每一步说明：输入是什么、做了什么处理、输出是什么\n"
        f"3. 标注关键的类名和函数名\n"
        f"4. 用清晰的编号步骤描述（Step 1, Step 2, ...）\n"
        f"5. 如果有训练和推理两条路径，分别描述\n"
        f"6. 总长度控制在 500-800 字\n\n"
        f"以下是代码文件内容：\n{formatted}"
    )

    client = _create_client(api_key, base_url)
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=4096,
            system="你是一个专业的代码分析助手。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    finally:
        await client.close()
    return message.content[0].text.strip()


async def ai_analyze_paper_code_mapping(
    file_contents: dict[str, str],
    paper_title: str,
    paper_one_sentence: str,
    existing_code_mapping: list[dict],
    api_key: str, base_url: str, model: str,
) -> list[dict]:
    """AI 将论文概念映射到具体代码文件"""
    import json as json_mod
    from .ai_analyzer import _create_client

    formatted = _format_file_contents(file_contents)

    # 格式化已有的 code_mapping
    mappings_text = ""
    if existing_code_mapping:
        mappings_text = "\n".join(
            f"- {m.get('concept', '')}" for m in existing_code_mapping
        )
    else:
        mappings_text = "（无）"

    prompt = (
        f'你是一个学术代码分析专家。论文"{paper_title}"的核心贡献是：{paper_one_sentence}\n\n'
        f"论文中已识别的关键概念：\n{mappings_text}\n\n"
        f"以下是该论文开源实现的核心代码文件。请将上述论文概念映射到具体的代码文件和函数。\n\n"
        f"输出 JSON 数组，每个元素格式：\n"
        f'{{"concept": "论文概念名", "file": "代码文件相对路径", "function": "类名.方法名或函数名", "explanation": "这段代码如何实现论文中的概念（2-3句话）"}}\n\n'
        f"要求：\n"
        f"1. 每个论文概念都要映射到具体的代码位置\n"
        f"2. 如果一个概念对应多个文件，选择最核心的那个\n"
        f"3. function 字段格式：ClassName.method_name 或 module.function_name\n"
        f"4. explanation 要说明代码与论文公式的对应关系\n\n"
        f"只输出 JSON 数组，不要添加其他文字。\n\n"
        f"以下是代码文件内容：\n{formatted}"
    )

    client = _create_client(api_key, base_url)
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=4096,
            system="你是一个学术代码分析助手。只输出 JSON 数组，不要其他文字。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    finally:
        await client.close()

    content = message.content[0].text.strip()
    # 提取 JSON
    if "```" in content:
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            content = content[start:end]

    try:
        result = json_mod.loads(content)
        if isinstance(result, list):
            return result
    except json_mod.JSONDecodeError:
        pass
    return []


async def ai_summarize_readme(
    readme_content: str,
    api_key: str, base_url: str, model: str,
) -> str:
    """AI 总结 README"""
    from .ai_analyzer import _create_client

    prompt = (
        f"请总结以下 README 内容，输出格式：\n\n"
        f"1. **项目简介**：一句话说明这个项目做什么\n"
        f"2. **环境要求**：Python 版本、主要依赖、GPU 要求等\n"
        f"3. **安装方法**：pip install、conda 等安装步骤\n"
        f"4. **运行方法**：训练/推理/测试的命令\n"
        f"5. **项目结构**：主要目录和文件的作用（3-5 个要点）\n\n"
        f"要求简洁实用，每个要点 1-2 行。\n\n"
        f"README 内容：\n{readme_content[:10000]}"
    )

    client = _create_client(api_key, base_url)
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=2048,
            system="你是一个技术文档总结助手。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    finally:
        await client.close()
    return message.content[0].text.strip()

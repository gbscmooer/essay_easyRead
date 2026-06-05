"""
代码分析服务

管理代码仓库的 clone、文件浏览和分析。
"""

import os
import json
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional
from collections import defaultdict

from .import_parser import parse_imports, SUPPORTED_EXTENSIONS, ParsedImport

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


def read_notebook_content(repo_path: str, rel_path: str) -> list[dict]:
    """
    读取并解析 Jupyter Notebook (.ipynb) 文件。

    Args:
        repo_path: 仓库根目录
        rel_path: 相对路径

    Returns:
        解析后的 cell 列表: [{cell_type, source, outputs, execution_count}]

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 路径不安全或文件格式错误
    """
    full_path = os.path.realpath(os.path.join(repo_path, rel_path))
    if not full_path.startswith(os.path.realpath(repo_path)):
        raise ValueError("不安全的文件路径")

    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"文件不存在: {rel_path}")

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            nb = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"无效的 Notebook JSON: {e}")
    except Exception as e:
        raise ValueError(f"读取失败: {e}")

    raw_cells = nb.get("cells", [])
    cells = []
    for cell in raw_cells:
        cell_type = cell.get("cell_type", "raw")
        source_parts = cell.get("source", [])
        source = "".join(source_parts) if isinstance(source_parts, list) else str(source_parts)
        execution_count = cell.get("execution_count")

        outputs = []
        for out in cell.get("outputs", []):
            output_type = out.get("output_type", "")
            processed = {"output_type": output_type}

            if output_type in ("stream", "execute_result", "error"):
                text_parts = out.get("text", [])
                processed["text"] = "".join(text_parts) if isinstance(text_parts, list) else str(text_parts)
                if output_type == "execute_result":
                    data = out.get("data", {})
                    processed["data"] = {
                        k: (v if isinstance(v, str) else "".join(v) if isinstance(v, list) else str(v))
                        for k, v in data.items()
                    }
                if output_type == "error":
                    processed["ename"] = out.get("ename", "")
                    processed["evalue"] = out.get("evalue", "")
            elif output_type == "display_data":
                data = out.get("data", {})
                processed["data"] = {
                    k: (v if isinstance(v, str) else "".join(v) if isinstance(v, list) else str(v))
                    for k, v in data.items()
                }

            outputs.append(processed)

        cells.append({
            "cell_type": cell_type,
            "source": source,
            "outputs": outputs,
            "execution_count": execution_count,
        })

    return cells


def _detect_package_root(repo_path: str) -> str:
    """检测 Python 包根目录"""
    # 1. 从 setup.py/pyproject.toml 推断
    for cfg in ("pyproject.toml", "setup.py", "setup.cfg"):
        cfg_path = os.path.join(repo_path, cfg)
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(10000)
                # 查找 package_dir 或 packages 配置
                import re
                m = re.search(r'package_dir\s*=\s*\{[^}]*:\s*["\']([^"\']+)["\']', content)
                if m:
                    candidate = os.path.join(repo_path, m.group(1))
                    if os.path.isdir(candidate):
                        return candidate
            except Exception:
                pass

    # 2. 查找包含 __init__.py 的最顶层目录
    for entry in sorted(os.listdir(repo_path)):
        init_path = os.path.join(repo_path, entry, "__init__.py")
        if os.path.isfile(init_path):
            return repo_path

    # 3. 查找 src/ 目录
    src_dir = os.path.join(repo_path, "src")
    if os.path.isdir(src_dir):
        for entry in os.listdir(src_dir):
            if os.path.isfile(os.path.join(src_dir, entry, "__init__.py")):
                return src_dir

    return repo_path


def _resolve_module_to_file(module: str, from_file: str, repo_path: str, package_root: str = None) -> Optional[str]:
    """尝试将模块名解析为本地文件路径（增强版）"""
    if package_root is None:
        package_root = repo_path

    # 相对导入: ".utils", "..lib.helper"
    if module.startswith("."):
        base_dir = os.path.dirname(from_file)
        dots = len(module) - len(module.lstrip("."))
        rel = module[dots:]
        for ext in ("", ".py", ".js", ".ts", ".jsx", ".tsx", "/__init__.py", "/index.js", "/index.ts", "/index.jsx", "/index.tsx"):
            candidate = os.path.join(base_dir, ".." * (dots - 1), rel + ext)
            candidate = os.path.normpath(candidate)
            full = os.path.join(repo_path, candidate)
            if os.path.isfile(full):
                return candidate
        return None

    # Python 点号模块: "models.backbone"
    if "." in module and "/" not in module and "\\" not in module:
        parts = module.split(".")
        # 从 from_file 位置查找
        for base in (os.path.dirname(from_file), ""):
            for suffix in (".py", "/__init__.py"):
                candidate = os.path.join(*parts) + suffix
                full = os.path.join(repo_path, base, candidate)
                if os.path.isfile(full):
                    return os.path.relpath(full, repo_path)
        # 从包根目录查找
        for suffix in (".py", "/__init__.py"):
            candidate = os.path.join(*parts) + suffix
            full = os.path.join(package_root, candidate)
            if os.path.isfile(full):
                return os.path.relpath(full, repo_path)

    # Python 绝对导入（无点号）: "models" → 查找 models.py 或 models/__init__.py
    if "." not in module and "/" not in module:
        for base in (os.path.dirname(from_file), ""):
            for suffix in (".py", "/__init__.py"):
                candidate = module + suffix
                full = os.path.join(repo_path, base, candidate)
                if os.path.isfile(full):
                    return os.path.relpath(full, repo_path)
        # 从包根目录查找
        for suffix in (".py", "/__init__.py"):
            candidate = module + suffix
            full = os.path.join(package_root, candidate)
            if os.path.isfile(full):
                return os.path.relpath(full, repo_path)

    # JS/TS 相对路径（不以 . 开头但包含 / 的本地路径）
    if "/" in module and not module.startswith("@"):
        for ext in ("", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"):
            candidate = module + ext
            full = os.path.join(repo_path, os.path.dirname(from_file), candidate)
            if os.path.isfile(full):
                return os.path.relpath(full, repo_path)

    return None


def _detect_cycles(edges: list[dict]) -> set[tuple[str, str]]:
    """检测依赖图中的环路，返回循环边的 (source, target) 集合"""
    adj = defaultdict(set)
    for edge in edges:
        adj[edge["source"]].add(edge["target"])

    cycle_edges = set()
    visited = set()
    rec_stack = set()
    path = []

    def dfs(node: str):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                # 找到环路，标记路径上的所有边
                idx = path.index(neighbor)
                for i in range(idx, len(path) - 1):
                    cycle_edges.add((path[i], path[i + 1]))
                cycle_edges.add((path[-1], neighbor))

        path.pop()
        rec_stack.discard(node)

    for node in adj:
        if node not in visited:
            dfs(node)

    return cycle_edges


def select_core_files(file_tree: dict, repo_path: str, max_files: int = 50) -> list[str]:
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


def analyze_imports(repo_path: str, files: list[str]) -> tuple[list[dict], list[dict]]:
    """
    静态分析 import 语句，构建模块依赖图。

    使用 AST 解析 Python，增强正则解析 JS/TS/C++/Go/Rust。
    新增循环检测和耦合指标。

    Returns:
        (edges, node_metrics)
        edges: [{"source", "target", "imports", "circular", "line"}]
        node_metrics: [{"file", "fan_in", "fan_out"}]
    """
    edges = []
    package_root = _detect_package_root(repo_path)

    for rel_path in files:
        full_path = os.path.join(repo_path, rel_path)
        if not os.path.isfile(full_path):
            continue

        ext = os.path.splitext(rel_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(100000)
        except Exception:
            continue

        file_imports = parse_imports(rel_path, content)

        for imp in file_imports.imports:
            if not imp.module:
                continue
            target = _resolve_module_to_file(imp.module, rel_path, repo_path, package_root)
            if target and target != rel_path:
                edges.append({
                    "source": rel_path,
                    "target": target,
                    "imports": imp.names,
                    "line": imp.line_number,
                    "circular": False,
                })

    # 去重
    seen = set()
    unique_edges = []
    for edge in edges:
        key = (edge["source"], edge["target"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)
        else:
            # 合并 imports
            for existing in unique_edges:
                if (existing["source"], existing["target"]) == key:
                    for name in edge["imports"]:
                        if name not in existing["imports"]:
                            existing["imports"].append(name)
                    break

    # 循环检测
    cycle_edges = _detect_cycles(unique_edges)
    for edge in unique_edges:
        if (edge["source"], edge["target"]) in cycle_edges:
            edge["circular"] = True

    # 耦合指标
    fan_in = defaultdict(int)
    fan_out = defaultdict(int)
    all_files = set()
    for edge in unique_edges:
        fan_in[edge["target"]] += 1
        fan_out[edge["source"]] += 1
        all_files.add(edge["source"])
        all_files.add(edge["target"])

    node_metrics = [
        {"file": f, "fan_in": fan_in.get(f, 0), "fan_out": fan_out.get(f, 0)}
        for f in sorted(all_files)
    ]

    return unique_edges, node_metrics


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

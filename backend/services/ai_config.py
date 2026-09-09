"""Application configuration repository.

``llm`` is the single source of truth for every text-generation call.  The
legacy ``analysis`` and ``translate`` sections are read as migration inputs and
mirrored in memory for older callers, but they no longer select providers.
"""

import os
import json
import copy
import logging
import tempfile
from typing import Optional

import yaml

try:
    import tomllib
except ImportError:
    tomllib = None

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_JSON_PATH = os.path.join(PROJECT_ROOT, "data", "ai_config.json")
DEFAULT_YAML_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

# 候选配置文件发现列表（按优先级降序）：优先使用根目录下的 YAML / TOML
CANDIDATE_CONFIG_PATHS = [
    os.path.join(PROJECT_ROOT, "config.yaml"),
    os.path.join(PROJECT_ROOT, "config.yml"),
    os.path.join(PROJECT_ROOT, "config.toml"),
    os.path.join(PROJECT_ROOT, "data", "config.yaml"),
    os.path.join(PROJECT_ROOT, "data", "config.yml"),
    os.path.join(PROJECT_ROOT, "data", "config.toml"),
    os.path.join(PROJECT_ROOT, "data", "ai_config.yaml"),
    DEFAULT_JSON_PATH,
]

# 保持模块级 CONFIG_PATH 变量，兼顾既有单测 monkeypatch 与历史代码
CONFIG_PATH = DEFAULT_YAML_PATH if os.path.isfile(DEFAULT_YAML_PATH) else DEFAULT_JSON_PATH

# 根目录全局 LLM 密钥配置文件路径
DEFAULT_CONFIG_KEY_PATH = os.path.join(PROJECT_ROOT, "config_key")
CANDIDATE_CONFIG_KEY_PATHS = [
    os.path.join(PROJECT_ROOT, "config_key"),
    os.path.join(PROJECT_ROOT, "config_key.yaml"),
    os.path.join(PROJECT_ROOT, "config_key.yml"),
    os.path.join(PROJECT_ROOT, "config_key.json"),
]
CONFIG_KEY_PATH = DEFAULT_CONFIG_KEY_PATH

RELATIONS_METHOD = "weighted_rrf_v1"
_LEGACY_RELATION_WEIGHTS = {
    "topic": 0.18, "purpose": 0.24, "method": 0.16, "term": 0.14,
    "dataset": 0.06, "baseline": 0.04, "bibcoupling": 0.12, "cocitation": 0.06,
}
_LEGACY_RETRIEVAL_WEIGHTS = {
    "recall": {"pdf_ref": 1.0, "backward": 1.0, "forward": 0.9, "recommend": 0.85, "graph_ppr": 0.8},
    "hybrid_retrieval": {"dense": 1.0, "bm25": 0.95, "graph_ppr": 0.8},
}

DEFAULT_CONFIG = {
    "llm": {
        "provider": "openai",
        "api_format": "openai_chat",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 16384,
        # 全局通用 LLM 默认思考强度：off | low | medium | high
        "thinking_effort": "off",
        "timeout_seconds": 150,
    },
    "translate": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "system_prompt": "你是一个专业的学术翻译助手。将用户提供的英文论文片段翻译为中文。只输出翻译结果，不要添加任何解释。保持学术术语的准确性。",
        "temperature": 0.3,
        "max_tokens": 2048,
        # 翻译不需要推理，默认关闭思考；用户可在设置页手动调高
        "thinking_effort": "off",
    },
    "analysis": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 16384,
        # 思考强度：off | low | medium | high（详见 ai_analyzer.resolve_thinking）。
        # 仅对支持的推理模型生效，其余模型沿用服务商默认。
        "thinking_effort": "medium",
    },
    # 解读系统提示词：内置(builtin)只读，用户可创建可编辑副本并选用替换
    "analysis_prompt": {
        "active": "builtin",   # "builtin" | "custom:<id>"
        "custom": [],          # [{id, name, content}]
    },
    "ui": {
        "highlight_duration": 3000,
        "zoom_step": 0.2,
        "zoom_min": 0.4,
        "zoom_max": 3.0,
        "pinch_zoom_step": 0.02,
        "theme": "light",
        "font": "sans",
        "custom_font": "",
        "custom_themes": [],
    },
    "embedding": {
        "provider": "local",
        "model": "BAAI/bge-m3",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "batch_size": 32,
        "device": "auto",
        "max_input_tokens": 7000,   # 远程单条输入 token 截断上限（适配 bge-m3 8192；512 模型设 ~480）
        "max_input_chars": 0,       # 可选硬字符上限（0=不启用）
    },
    "research": {
        # auto | scopus | openalex | semantic_scholar | local
        # auto：有 Elsevier API Key 首选 Scopus（机构订阅全学科），否则 OpenAlex
        "provider": "auto",
        "email": "",                  # OpenAlex / Unpaywall polite pool 邮箱（可选，更稳定）
        "api_key": "",                # Semantic Scholar API key（可选）
        "elsevier_api_key": "",       # Elsevier / ScienceDirect API key（可选，校外需配置）
        "download_concurrency": 4,    # 多线程并发下载数（默认 4）
        "map_density": 8.0,           # 旧配置兼容字段；原始 Disparity Filter 不使用目标度数
        "backbone_alpha": 0.05,       # 局部显著性阈值，不是准确率
        "backbone_method": "mutual_knn_with_disparity_annotation_v1",
        "link_distance": 50,          # 知识图谱节点边长基准（d3 默认 30）
        "auto_digest": True,          # 深度研究时自动「图谱速读」未解读论文（轻量，补全可解释 facet 关系）
        # discovery_v2：多路召回 + RRF 融合扩展（backward/forward/recommend/pdf_ref）
        # 发布门禁通过前保持实验性关闭；用户可在设置页显式启用。
        "discovery_v2": False,
        # 领域认知综合（stage_synthesize）：图谱后注入节点角色/引用因果/流派/全景简报
        "synthesize": {
            "enabled": True,
        },
        "recall": {
            "method": RELATIONS_METHOD,
            "enabled_sources": ["backward", "forward", "recommend", "pdf_ref", "graph_ppr"],
            "rrf_k": 60,
            "weights": {
                "pdf_ref": 1.0,
                "backward": 1.0,
                "forward": 1.0,
                "recommend": 1.0,
                "graph_ppr": 1.0,
            },
            "candidate_limit": 50,
        },
        "hybrid_retrieval": {
            "method": RELATIONS_METHOD,
            "rrf_k": 60,
            "weights": {
                "dense": 1.0,
                "bm25": 1.0,
                "graph_ppr": 1.0,
            },
            "graph": {
                "ppr_alpha": 0.85,
                "top_k": 50,
                "bridge_weight_factor": 0.0,
            },
        },
    },
    "reranker": {
        "enabled": True,
        "provider": "siliconflow",
        "model": "BAAI/bge-reranker-v2-m3",
        "api_key": "",            # 空时回退到 embedding 的 api_key
        "base_url": "",           # 空时回退到 embedding 的 base_url
        "candidate_k": 30,        # 向量检索召回数（送入 rerank 的池子大小）
        "top_n": 5,               # rerank 后送入 LLM 的条数
    },
    # 八通道 RRF，等权默认值；手动权重表示全体通道上的相对偏好。
    "relations": {
        "method": RELATIONS_METHOD,
        "calibrate": False,  # 兼容旧客户端；不再参与计算
        "weights": {name: 0.125 for name in _LEGACY_RELATION_WEIGHTS},
    },
    "arxiv_expand": {
        # 是否启用引用列表扩展（解析 seed 论文参考文献并联网下载）
        "enabled": True,
        # 引用解析后端：grobid（结构化，默认）| regex（正则，零依赖回退）
        "parser": "grobid",
        # GROBID 服务地址（parser=grobid 时使用），标准 Docker 端口 8070
        "grobid_url": "http://localhost:8070",
        # 按正文引用频次取 top_k（频次越高越核心）
        "top_k": 5,
        # 是否允许 LLM 提取没有标题的引用（仅 regex 后端、且引用无标题时才需要）
        "use_llm_title": True,
        # 每篇 seed 论文最多进行 LLM/arXiv/CrossRef 标题搜索的次数上限
        "max_llm_refs": 15,
    },
    "findpapers": {
        # 是否启用 findpapers 多源学术搜索引擎
        # 启用后替代 Semantic Scholar 进行相关论文搜索和 PDF 下载
        "enabled": False,
        # 启用的数据库列表（免费数据库默认开启，需 API key 的需额外配置）
        # 可选搜索库：arxiv, openalex, pubmed, semantic_scholar, ieee, scopus, wos
        # 注意：crossref 仅用于数据丰富（enrichment），不可作为搜索库
        "databases": ["arxiv", "openalex", "pubmed", "semantic_scholar"],
        # 搜索结果数量上限
        "max_results": 10,
    },
}


def _ensure_dir():
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)


def _mask_api_key(key: str) -> str:
    """掩码 API 密钥，仅显示最后 4 个字符"""
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    return "***" + key[-4:]


# 需要「GET 掩码 + POST 保留」处理的密钥字段名（api_key 及其变体）
_SECRET_KEY_NAMES = ("api_key", "elsevier_api_key")


def mask_config(config: dict) -> dict:
    """返回配置深拷贝，所有密钥字段（api_key / elsevier_api_key 等）被掩码"""
    masked = copy.deepcopy(config)
    for section in masked.values():
        if isinstance(section, dict):
            for key_name in _SECRET_KEY_NAMES:
                if key_name in section:
                    section[key_name] = _mask_api_key(section[key_name])
    return masked


def is_masked_key(value: str) -> bool:
    """判断一个 api_key 是否是掩码占位（GET /config 回显的 ***xxxx），而非真实 key。

    前端表单回填的就是掩码值；若原样保存/拿去测试会毁掉真 key、导致鉴权失败。
    """
    if not value:
        return False
    if value.startswith("***"):
        return True
    return set(value) == {"*"}    # 短 key 被掩成全 *


def merge_preserving_keys(new_config: dict, prior_config: dict) -> dict:
    """保存配置时，若某段的密钥字段是掩码占位，则保留盘上原有真实 key。

    这样前端「只改了别的设置、api_key 字段还是回显的 ***xxxx」时不会把真 key 覆盖掉。
    用户确实想清空 key 时会传空串（不是掩码），照常清空。
    """
    out = copy.deepcopy(new_config)
    disk_llm_key = load_config_key().get("api_key") or ""
    for name, section in out.items():
        if isinstance(section, dict):
            for key_name in _SECRET_KEY_NAMES:
                if key_name in section and is_masked_key(section.get(key_name, "")):
                    prior_val = (prior_config.get(name) or {}).get(key_name, "")
                    if prior_val and not is_masked_key(prior_val):
                        section[key_name] = prior_val
                    elif name in ("llm", "analysis", "translate") and key_name == "api_key" and disk_llm_key and not is_masked_key(disk_llm_key):
                        section[key_name] = disk_llm_key
                    else:
                        section[key_name] = prior_val
    return out


def _deep_merge_dict(base: dict, override: dict) -> dict:
    """递归合并配置段，保留嵌套默认值（如 research.recall.weights）。"""
    out = copy.deepcopy(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge_dict(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _sync_unified_llm(config: dict, saved: Optional[dict] = None) -> None:
    """Resolve one canonical LLM section and mirror legacy aliases in memory.

    Explicit ``llm`` wins.  For pre-migration files the old ``analysis`` section
    wins over ``translate`` so existing paper-analysis deployments keep their
    endpoint.  Protocol is persisted explicitly; URL inference is only a legacy
    migration fallback.
    """
    if not isinstance(config, dict):
        return
    source = saved if isinstance(saved, dict) else config
    explicit_llm = source.get("llm") if isinstance(source.get("llm"), dict) else None
    legacy_analysis = source.get("analysis") if isinstance(source.get("analysis"), dict) else None
    legacy_translate = source.get("translate") if isinstance(source.get("translate"), dict) else None
    canonical_source = explicit_llm or legacy_analysis or legacy_translate or {}

    from .llm.models import LLMConfig

    merged = _deep_merge_dict(DEFAULT_CONFIG["llm"], canonical_source)
    if "api_format" not in canonical_source:
        merged["api_format"] = ""
    if "provider" not in canonical_source:
        merged["provider"] = "custom"
    # Preserve an already resolved secret from the merged config when the raw
    # canonical section contains an empty placeholder.
    existing = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    if not merged.get("api_key") and existing.get("api_key"):
        merged["api_key"] = existing["api_key"]
    resolved = LLMConfig.from_mapping(merged).to_dict()
    config["llm"] = resolved

    llm_fields = tuple(resolved.keys())
    analysis = config.setdefault("analysis", copy.deepcopy(DEFAULT_CONFIG["analysis"]))
    translate = config.setdefault("translate", copy.deepcopy(DEFAULT_CONFIG["translate"]))
    for field_name in llm_fields:
        analysis[field_name] = copy.deepcopy(resolved[field_name])
        translate[field_name] = copy.deepcopy(resolved[field_name])


def get_active_config_path() -> str:
    """获取当前生效的配置文件绝对路径。
    如果 CONFIG_PATH 被外部测试 monkeypatch 修改且指定了特定路径，遵从外部指定；
    否则依次检查根目录 config.yaml / config.yml / config.toml / data/ 目录。
    若都不存在，默认优先返回项目根目录下的 config.yaml。
    """
    global CONFIG_PATH
    if CONFIG_PATH not in (DEFAULT_JSON_PATH, DEFAULT_YAML_PATH):
        return CONFIG_PATH
    for p in CANDIDATE_CONFIG_PATHS:
        if os.path.isfile(p):
            return p
    return DEFAULT_YAML_PATH


def get_active_config_key_path() -> str:
    """获取当前生效的 config_key 文件绝对路径。
    如果 CONFIG_KEY_PATH 被外部测试 monkeypatch 修改且指定了特定路径，遵从外部指定；
    否则依次检查根目录 config_key / config_key.yaml / config_key.yml / config_key.json。
    若都不存在，默认优先返回项目根目录下的 config_key。
    """
    global CONFIG_KEY_PATH
    if CONFIG_KEY_PATH != DEFAULT_CONFIG_KEY_PATH:
        return CONFIG_KEY_PATH
    for p in CANDIDATE_CONFIG_KEY_PATHS:
        if os.path.isfile(p):
            return p
    return DEFAULT_CONFIG_KEY_PATH


def load_config_key(path: Optional[str] = None) -> dict:
    """从根目录 config_key 中加载全局 LLM 模型配置。
    支持 YAML、JSON 格式，支持嵌套 llm 段或顶层扁平键值对。
    """
    target_path = path or get_active_config_key_path()
    # 保护机制：若在 pytest 环境下且未指定自定义 key 文件，不读取根目录真实 key，避免干扰单测断言
    if "PYTEST_CURRENT_TEST" in os.environ and os.path.abspath(target_path) == os.path.abspath(DEFAULT_CONFIG_KEY_PATH):
        return {}
    if not os.path.isfile(target_path):
        return {}
    try:
        data = read_file_config(target_path)
        if not isinstance(data, dict):
            return {}
        llm_cfg: dict = {}
        if isinstance(data.get("llm"), dict):
            llm_cfg.update(data["llm"])
        valid_keys = (
            "provider", "api_format", "api_key", "base_url", "model",
            "temperature", "max_tokens", "thinking_effort", "timeout_seconds",
        )
        for k in valid_keys:
            if k in data and data[k] is not None and data[k] != "":
                llm_cfg[k] = data[k]
            elif k in data and k not in llm_cfg:
                llm_cfg[k] = data[k]
        return llm_cfg
    except Exception as e:
        logger.warning(f"Failed to load config_key from {target_path}: {e}")
        return {}


def save_config_key(llm_data: dict, path: Optional[str] = None) -> str:
    """Atomically persist only the global LLM secret in ``config_key``.

    ``load_config_key`` still accepts legacy files containing endpoint/model
    fields, but new writes avoid duplicating non-secret provider state.
    """
    target_path = path or get_active_config_key_path()
    # 保护机制：pytest 测试期间若未显式为测试设置单独的 CONFIG_KEY_PATH，禁止污染或覆盖真实的根目录 config_key
    if "PYTEST_CURRENT_TEST" in os.environ and os.path.abspath(target_path) == os.path.abspath(DEFAULT_CONFIG_KEY_PATH):
        logger.debug("Skipping write to root config_key during pytest run")
        return target_path
    dir_name = os.path.dirname(target_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    clean_llm = {"api_key": llm_data.get("api_key", "")}

    header = (
        "# ==============================================================================\n"
        "# 全局通用 LLM 模型与 API Key 配置文件 (config_key)\n"
        "# ------------------------------------------------------------------------------\n"
        "# 说明：\n"
        "# 1. 本文件位于项目根目录，由设置页面与系统统一读写，只保存全局大模型密钥。\n"
        "# 2. 本项目绝不将 API Key 存入数据库；密钥独立保存在本文件中，隔离于公开配置。\n"
        "# 3. 全局通用：论文精读、划词翻译、领域探索、问句拆解与图谱速读均默认复用该配置。\n"
        "# 4. 本文件已加入 .gitignore，切勿提交至任何公开 Git 代码仓库！\n"
        "# ==============================================================================\n\n"
    )

    content_data = {"llm": clean_llm}

    yaml_body = yaml.safe_dump(content_data, allow_unicode=True, sort_keys=False)
    final_content = header + yaml_body

    fd, tmp_path = tempfile.mkstemp(dir=dir_name or None, suffix=".tmp", prefix="config_key_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(final_content)
        os.replace(tmp_path, target_path)
        logger.info(f"Global LLM config_key saved to {target_path}")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target_path


def get_config_file_info() -> dict:
    """获取当前配置文件元信息（供设置页展示和导入导出使用）。"""
    active_path = get_active_config_path()
    active_key_path = get_active_config_key_path()
    rel_path = os.path.relpath(active_path, PROJECT_ROOT)
    rel_key_path = os.path.relpath(active_key_path, PROJECT_ROOT)
    fmt = "yaml" if active_path.endswith((".yaml", ".yml")) else ("toml" if active_path.endswith(".toml") else "json")
    return {
        "active_path": rel_path,
        "full_path": active_path,
        "exists": os.path.isfile(active_path),
        "format": fmt,
        "supported_formats": ["yaml", "toml", "json"],
        "key_path": rel_key_path,
        "key_file_exists": os.path.isfile(active_key_path),
    }


def parse_config_content(content: str, fmt: Optional[str] = None, path: Optional[str] = None) -> dict:
    """解析配置文本内容，支持 YAML、TOML 与 JSON。"""
    target_fmt = (fmt or "").lower().strip()
    if not target_fmt and path:
        if path.endswith((".yaml", ".yml")):
            target_fmt = "yaml"
        elif path.endswith(".toml"):
            target_fmt = "toml"
        elif path.endswith(".json"):
            target_fmt = "json"

    data = None
    if target_fmt in ("yaml", "yml"):
        try:
            data = yaml.safe_load(content)
        except Exception as e:
            logger.warning(f"Failed to parse as YAML: {e}")
    elif target_fmt == "toml" and tomllib:
        try:
            data = tomllib.loads(content)
        except Exception as e:
            logger.warning(f"Failed to parse as TOML: {e}")
    elif target_fmt == "json":
        try:
            data = json.loads(content)
        except Exception as e:
            logger.warning(f"Failed to parse as JSON: {e}")

    # 若未指定格式或首选格式解析失败，智能回退尝试
    if not isinstance(data, dict):
        try:
            d = yaml.safe_load(content)
            if isinstance(d, dict):
                data = d
        except Exception:
            pass
    if not isinstance(data, dict) and tomllib:
        try:
            d = tomllib.loads(content)
            if isinstance(d, dict):
                data = d
        except Exception:
            pass
    if not isinstance(data, dict):
        try:
            d = json.loads(content)
            if isinstance(d, dict):
                data = d
        except Exception:
            pass

    return data if isinstance(data, dict) else {}


def read_file_config(path: str) -> dict:
    """从指定文件读取配置"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return parse_config_content(content, path=path)
    except Exception as e:
        logger.warning(f"Failed to read config from {path}: {e}")
        return {}


def _to_toml_val(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    elif isinstance(val, list):
        items = [_to_toml_val(x) for x in val]
        return f"[{', '.join(items)}]"
    return f'"{val}"'


def dump_toml(data: dict, prefix: str = "") -> str:
    """简易纯 Python TOML 序列化"""
    lines = []
    subtables = []
    for k, v in data.items():
        if isinstance(v, dict):
            subtables.append((k, v))
        else:
            lines.append(f"{k} = {_to_toml_val(v)}")
    for sub_k, sub_v in subtables:
        header = f"{prefix}.{sub_k}" if prefix else sub_k
        lines.append("")
        lines.append(f"[{header}]")
        sub_res = dump_toml(sub_v, header)
        if sub_res:
            lines.append(sub_res)
    return "\n".join(lines).strip()


def dump_file_config(config: dict, path: str) -> None:
    """将配置原子写入文件（支持 .yaml, .yml, .toml, .json）"""
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name or None, suffix=".tmp", prefix="ai_config_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if path.endswith((".yaml", ".yml")):
                yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
            elif path.endswith(".toml"):
                f.write(dump_toml(config))
                f.write("\n")
            else:
                json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config(path: Optional[str] = None) -> dict:
    """加载配置。默认读取当前生效的配置文件（项目下的 config.yaml 或 data/ai_config.json），
    并从项目根目录的 config_key 注入全局 LLM API Key。旧版 config_key 中的
    模型与端点字段仅在主配置没有 llm 段时作为迁移来源。
    """
    target_path = path or get_active_config_path()
    raw_saved: dict = {}
    if not os.path.exists(target_path):
        config = _deep_copy_default()
    else:
        try:
            raw_saved = read_file_config(target_path)
            saved = _migrate_relations_config(raw_saved)
            # 合并默认值（防止缺少字段；嵌套段如 recall / relations.weights 递归合并）
            config = {}
            for key in DEFAULT_CONFIG:
                default_section = DEFAULT_CONFIG[key]
                saved_section = saved.get(key, {})
                if isinstance(default_section, dict) and isinstance(saved_section, dict):
                    config[key] = _deep_merge_dict(default_section, saved_section)
                else:
                    config[key] = saved_section if key in saved else copy.deepcopy(default_section)
        except Exception as e:
            logger.warning(f"Failed to load config from {target_path}: {e}")
            config = _deep_copy_default()

    # 优先从根目录 config_key 读取全局 LLM 模型配置并覆盖注入
    abs_target = os.path.abspath(target_path)
    is_standard_path = abs_target in (os.path.abspath(DEFAULT_YAML_PATH), os.path.abspath(DEFAULT_JSON_PATH))
    should_load_key = True
    if not is_standard_path:
        has_local_key = os.path.isfile(os.path.join(os.path.dirname(abs_target), "config_key"))
        key_explicitly_set = CONFIG_KEY_PATH != DEFAULT_CONFIG_KEY_PATH
        if not has_local_key and not key_explicitly_set:
            should_load_key = False

    key_config: dict = {}
    if should_load_key:
        key_config = load_config_key()
        if key_config:
            if "llm" not in config or not isinstance(config["llm"], dict):
                config["llm"] = copy.deepcopy(DEFAULT_CONFIG["llm"])
            for k, v in key_config.items():
                if v is not None and v != "":
                    config["llm"][k] = v
                elif k not in config["llm"]:
                    config["llm"][k] = v

    llm_migration_source = copy.deepcopy(raw_saved)
    if key_config:
        raw_llm = raw_saved.get("llm") if isinstance(raw_saved.get("llm"), dict) else None
        key_has_provider_state = any(
            key in key_config and key_config.get(key) not in (None, "")
            for key in (
                "provider", "api_format", "base_url", "model", "temperature",
                "max_tokens", "thinking_effort", "timeout_seconds",
            )
        )
        if raw_llm is not None:
            # The main config is the canonical source for provider state.  A
            # secret-only config_key must not replace it with LLM defaults on
            # every reload; only inject the separately stored API key.
            merged_raw_llm = copy.deepcopy(raw_llm)
            if "api_key" in key_config:
                merged_raw_llm["api_key"] = key_config.get("api_key", "")
            llm_migration_source["llm"] = merged_raw_llm
        elif key_has_provider_state:
            # Backward compatibility for legacy installations where
            # config_key still owned the complete provider configuration.
            llm_migration_source["llm"] = copy.deepcopy(key_config)
    _sync_unified_llm(config, saved=llm_migration_source or config)
    return config


def _deep_copy_default() -> dict:
    """深拷贝默认配置"""
    return copy.deepcopy(DEFAULT_CONFIG)


def _migrate_relations_config(saved: dict) -> dict:
    """Migrate recognized shipped defaults in memory; never write on load."""
    migrated = copy.deepcopy(saved)
    relations = migrated.get("relations")
    if isinstance(relations, dict):
        # Match before merging: partial maps and explicit versioned preferences
        # must not be mistaken for the old shipped eight-channel defaults.
        if not relations.get("method") and relations.get("weights") == _LEGACY_RELATION_WEIGHTS:
            relations["weights"] = copy.deepcopy(DEFAULT_CONFIG["relations"]["weights"])
        relations["method"] = RELATIONS_METHOD
        relations["calibrate"] = False
    research = migrated.get("research")
    if isinstance(research, dict):
        if not research.get("backbone_method") and research.get("backbone_alpha") == 0.25:
            research["backbone_alpha"] = 0.05
        research["backbone_method"] = "mutual_knn_with_disparity_annotation_v1"
        for name, legacy in _LEGACY_RETRIEVAL_WEIGHTS.items():
            section = research.get(name)
            if not isinstance(section, dict):
                continue
            if not section.get("method") and section.get("weights") == legacy:
                section["weights"] = {channel: 1.0 for channel in legacy}
            section["method"] = RELATIONS_METHOD
    return migrated


def save_config(config: dict, path: Optional[str] = None) -> None:
    """保存 AI 配置（原子写入）。
    若包含 llm 段，自动将 API Key 写入项目根目录下的 config_key 文件中；
    主配置文件（config.yaml 或 data/ai_config.json）中敏感 key 清空，彻底与数据库和公开配置解耦。
    """
    config = copy.deepcopy(config)
    if isinstance(config, dict):
        _sync_unified_llm(config)

    target_path = path or get_active_config_path()
    abs_target = os.path.abspath(target_path)
    is_standard_path = abs_target in (os.path.abspath(DEFAULT_YAML_PATH), os.path.abspath(DEFAULT_JSON_PATH))

    # 1. 保存到根目录 config_key
    llm_sec = config.get("llm") if isinstance(config, dict) else None
    if isinstance(llm_sec, dict):
        has_local_key = os.path.isfile(os.path.join(os.path.dirname(abs_target), "config_key"))
        key_explicitly_set = CONFIG_KEY_PATH != DEFAULT_CONFIG_KEY_PATH
        if is_standard_path or has_local_key or key_explicitly_set:
            try:
                save_config_key(llm_sec)
            except Exception as e:
                logger.warning(f"Failed to save to config_key: {e}")

    # 2. 主配置只持久化一个 ``llm`` 段。analysis/translate 仅作为旧配置的
    # 读取迁移入口，不能继续形成三份会漂移的 provider 配置。
    main_config = copy.deepcopy(config)
    main_config.pop("analysis", None)
    main_config.pop("translate", None)
    if is_standard_path and isinstance(main_config, dict):
        if "llm" in main_config and isinstance(main_config["llm"], dict):
            main_config["llm"]["api_key"] = ""

    dump_file_config(main_config, target_path)
    logger.info(f"AI config saved to {target_path}")

    # 保持向后兼容：如果主配置文件不是 data/ai_config.json，同步写一份 json 以确保下游工具平滑过渡
    if abs_target != os.path.abspath(DEFAULT_JSON_PATH) and is_standard_path:
        try:
            dump_file_config(main_config, DEFAULT_JSON_PATH)
        except Exception as e:
            logger.debug(f"Sync to fallback json failed: {e}")


def export_config(fmt: str = "yaml", mask_keys: bool = False) -> tuple[str, str, str]:
    """导出系统配置。返回 (content, filename, media_type)"""
    raw_cfg = load_config()
    cfg = mask_config(raw_cfg) if mask_keys else copy.deepcopy(raw_cfg)
    cfg.pop("analysis", None)
    cfg.pop("translate", None)
    fmt = (fmt or "yaml").lower().strip()
    if fmt == "json":
        content = json.dumps(cfg, ensure_ascii=False, indent=2)
        filename = "config.json"
        media_type = "application/json; charset=utf-8"
    elif fmt == "toml":
        header = (
            "# ============================================================\n"
            "# 论文精读助手配置文件 (TOML)\n"
            "# 可直接置于项目根目录作为 config.toml 生效，支持多机迁移与备份\n"
            "# ============================================================\n\n"
        )
        content = header + dump_toml(cfg) + "\n"
        filename = "config.toml"
        media_type = "application/toml; charset=utf-8"
    else:
        header = (
            "# ============================================================\n"
            "# 论文精读助手配置文件 (YAML)\n"
            "# 可直接置于项目根目录作为 config.yaml 生效，支持多机迁移与备份\n"
            "# ============================================================\n\n"
        )
        content = header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        filename = "config.yaml"
        media_type = "text/yaml; charset=utf-8"
    return content, filename, media_type


def import_config(content: str, fmt: Optional[str] = None) -> dict:
    """导入并解析配置文本（支持 YAML / TOML / JSON），合并后原子持久化。"""
    parsed = parse_config_content(content, fmt=fmt)
    if not parsed:
        raise ValueError("无法解析配置文件内容，请确保为合法的 YAML、TOML 或 JSON 格式")

    current = load_config()
    clean_parsed = merge_preserving_keys(parsed, current)
    # Resolve from the key-preserved payload; resolving from ``parsed`` here
    # would reintroduce a masked *** placeholder after it was safely replaced.
    _sync_unified_llm(clean_parsed, saved=clean_parsed)
    merged = _deep_merge_dict(current, clean_parsed)
    save_config(merged)
    return merged


def get_llm_config() -> dict:
    """获取全局统一 LLM 模型配置（单 key/单模型全局通用）"""
    loaded = load_config()
    raw_sec = loaded.get("llm") or loaded.get("analysis") or loaded.get("translate") or {}
    cfg = dict(raw_sec)

    # 如果配置没有填写 api_key，回退到环境变量中的统一 LLM 配置
    if not cfg.get("api_key"):
        from ..config import settings

        saved_llm: dict = {}
        active_path = get_active_config_path()
        if os.path.exists(active_path):
            try:
                raw = read_file_config(active_path)
                if isinstance(raw, dict):
                    raw_llm = raw.get("llm") or raw.get("analysis") or raw.get("translate") or {}
                    if isinstance(raw_llm, dict):
                        saved_llm = raw_llm
            except (OSError, ValueError):
                pass

        cfg["api_key"] = settings.llm_api_key
        default_base_url = DEFAULT_CONFIG["llm"]["base_url"]
        default_model = DEFAULT_CONFIG["llm"]["model"]
        if (not saved_llm.get("base_url") or saved_llm.get("base_url") == default_base_url) and settings.llm_base_url:
            cfg["base_url"] = settings.llm_base_url
        if (not saved_llm.get("model") or saved_llm.get("model") == default_model) and settings.llm_model:
            cfg["model"] = settings.llm_model
        # Only a genuinely absent file inherits protocol metadata from env.
        # A legacy saved section has already been inferred by _sync_unified_llm
        # and must not be overwritten by unrelated environment defaults.
        if not saved_llm and settings.llm_provider:
            cfg["provider"] = settings.llm_provider
        if not saved_llm and settings.llm_api_format:
            cfg["api_format"] = settings.llm_api_format
    from .llm.models import LLMConfig
    return LLMConfig.from_mapping(cfg).to_dict()


def get_translate_config() -> dict:
    """获取翻译模型配置（统一接入全局 LLM 配置，附带翻译系统提示词）"""
    cfg = dict(get_llm_config())
    cfg["system_prompt"] = DEFAULT_CONFIG["translate"]["system_prompt"]
    return cfg


def get_analysis_config() -> dict:
    """获取解读模型配置（统一接入全局 LLM 配置）"""
    return get_llm_config()


def get_analysis_prompt_config() -> dict:
    """获取解读提示词配置（active + custom 列表）"""
    saved = load_config().get("analysis_prompt", {})
    return {**DEFAULT_CONFIG["analysis_prompt"], **saved}


def get_active_analysis_prompt() -> str:
    """解析当前生效的解读系统提示词。

    active="builtin" → 返回 ai_analyzer 内置 SYSTEM_PROMPT；
    active="custom:<id>" → 返回对应自定义副本的 content；找不到则回退内置。
    """
    from .ai_analyzer import SYSTEM_PROMPT
    cfg = get_analysis_prompt_config()
    active = cfg.get("active", "builtin")
    if isinstance(active, str) and active.startswith("custom:"):
        cid = active.split(":", 1)[1]
        for t in cfg.get("custom", []):
            if t.get("id") == cid and (t.get("content") or "").strip():
                return t["content"]
    return SYSTEM_PROMPT


def get_embedding_config() -> dict:
    """获取 Embedding 模型配置"""
    return load_config().get("embedding", DEFAULT_CONFIG["embedding"])


def get_research_config() -> dict:
    """获取深度研究 Provider 配置"""
    return load_config().get("research", DEFAULT_CONFIG["research"])


def get_recall_config() -> dict:
    """获取多路召回 / RRF 融合配置（research.recall 段）。"""
    research = get_research_config()
    default_recall = DEFAULT_CONFIG["research"].get("recall", {})
    saved = research.get("recall") or {}
    merged = _deep_merge_dict(default_recall, saved)
    # 旧配置可能缺少 enabled_sources / weights 子键
    if not merged.get("enabled_sources"):
        merged["enabled_sources"] = list(default_recall.get("enabled_sources", []))
    return merged


def get_relations_config() -> dict:
    """RRF 方法版本和用户通道偏好；自动校准字段只用于旧客户端兼容。"""
    saved = load_config().get("relations") or {}
    weights = {**DEFAULT_CONFIG["relations"]["weights"], **(saved.get("weights") or {})}
    return {"method": RELATIONS_METHOD, "weights": weights, "calibrate": False}


def get_arxiv_expand_config() -> dict:
    """获取 arXiv 引用列表扩展配置"""
    saved = load_config().get("arxiv_expand", {})
    return {**DEFAULT_CONFIG["arxiv_expand"], **saved}


def get_findpapers_config() -> dict:
    """获取 findpapers 多源搜索配置"""
    saved = load_config().get("findpapers", {})
    return {**DEFAULT_CONFIG["findpapers"], **saved}


def get_reranker_config() -> dict:
    """获取重排序模型配置。
    api_key/base_url 留空时自动回退到 embedding 的同字段（默认共用 siliconflow 账号）。
    """
    full = load_config()
    cfg = dict(full.get("reranker", DEFAULT_CONFIG["reranker"]))
    if not cfg.get("api_key"):
        cfg["api_key"] = full.get("embedding", {}).get("api_key", "")
    if not cfg.get("base_url"):
        cfg["base_url"] = full.get("embedding", {}).get("base_url", "")
    return cfg

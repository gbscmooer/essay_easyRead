"""
AI 配置服务

管理翻译和解读两套 AI 模型配置，存储在 JSON 文件中。
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
    "translate": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "system_prompt": "你是一个专业的学术翻译助手。将用户提供的英文论文片段翻译为中文。只输出翻译结果，不要添加任何解释。保持学术术语的准确性。",
        "temperature": 0.3,
        "max_tokens": 2048,
    },
    "analysis": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 16384,
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
    for name, section in out.items():
        if isinstance(section, dict):
            for key_name in _SECRET_KEY_NAMES:
                if key_name in section and is_masked_key(section.get(key_name, "")):
                    section[key_name] = (prior_config.get(name) or {}).get(key_name, "")
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


def get_config_file_info() -> dict:
    """获取当前配置文件元信息（供设置页展示和导入导出使用）。"""
    active_path = get_active_config_path()
    rel_path = os.path.relpath(active_path, PROJECT_ROOT)
    fmt = "yaml" if active_path.endswith((".yaml", ".yml")) else ("toml" if active_path.endswith(".toml") else "json")
    return {
        "active_path": rel_path,
        "full_path": active_path,
        "exists": os.path.isfile(active_path),
        "format": fmt,
        "supported_formats": ["yaml", "toml", "json"],
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
    """加载配置。默认读取当前生效的配置文件（项目下的 config.yaml 或 data/ai_config.json）。"""
    target_path = path or get_active_config_path()
    if not os.path.exists(target_path):
        return _deep_copy_default()
    try:
        saved = read_file_config(target_path)
        saved = _migrate_relations_config(saved)
        # 合并默认值（防止缺少字段；嵌套段如 recall / relations.weights 递归合并）
        config = {}
        for key in DEFAULT_CONFIG:
            default_section = DEFAULT_CONFIG[key]
            saved_section = saved.get(key, {})
            if isinstance(default_section, dict) and isinstance(saved_section, dict):
                config[key] = _deep_merge_dict(default_section, saved_section)
            else:
                config[key] = saved_section if key in saved else copy.deepcopy(default_section)
        return config
    except Exception as e:
        logger.warning(f"Failed to load config from {target_path}: {e}")
        return _deep_copy_default()


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
    """保存 AI 配置（原子写入）。默认保存至当前生效配置文件，若当前为根目录 YAML 则同时同步一份 JSON 到 data/ai_config.json。"""
    target_path = path or get_active_config_path()
    dump_file_config(config, target_path)
    logger.info(f"AI config saved to {target_path}")

    # 保持向后兼容：如果主配置文件不是 data/ai_config.json，同步写一份 json 以确保下游工具平滑过渡
    if os.path.abspath(target_path) != os.path.abspath(DEFAULT_JSON_PATH):
        try:
            dump_file_config(config, DEFAULT_JSON_PATH)
        except Exception as e:
            logger.debug(f"Sync to fallback json failed: {e}")


def export_config(fmt: str = "yaml", mask_keys: bool = False) -> tuple[str, str, str]:
    """导出系统配置。返回 (content, filename, media_type)"""
    raw_cfg = load_config()
    cfg = mask_config(raw_cfg) if mask_keys else copy.deepcopy(raw_cfg)
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
    merged = _deep_merge_dict(current, clean_parsed)
    save_config(merged)
    return merged


def get_translate_config() -> dict:
    """获取翻译模型配置"""
    return load_config().get("translate", DEFAULT_CONFIG["translate"])


def get_analysis_config() -> dict:
    """获取解读模型配置"""
    cfg = dict(load_config().get("analysis", DEFAULT_CONFIG["analysis"]))
    # 如果解读配置没有填写 api_key，回退到环境变量中的统一 LLM 配置
    if not cfg.get("api_key"):
        from ..config import settings

        # load_config 会把缺失字段补成 DEFAULT_CONFIG，因此仅看合并后的
        # base_url/model 无法区分“用户已保存”与“只是内置默认值”。读取原始段来
        # 保留 UI 中显式保存的模型，同时让全新安装真正使用 LLM_* 环境变量。
        saved_analysis: dict = {}
        active_path = get_active_config_path()
        if os.path.exists(active_path):
            try:
                raw = read_file_config(active_path)
                raw_analysis = raw.get("analysis", {}) if isinstance(raw, dict) else {}
                if isinstance(raw_analysis, dict):
                    saved_analysis = raw_analysis
            except (OSError, ValueError):
                pass

        cfg["api_key"] = settings.llm_api_key
        if not saved_analysis.get("base_url"):
            cfg["base_url"] = settings.llm_base_url
        if not saved_analysis.get("model"):
            cfg["model"] = settings.llm_model
    return cfg


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

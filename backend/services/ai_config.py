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

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "ai_config.json")

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
        "base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
        "model": "mimo-v2.5-pro",
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
        "provider": "openalex",       # openalex | semantic_scholar | local
        "email": "",                  # OpenAlex polite pool 邮箱（可选，更稳定）
        "api_key": "",                # Semantic Scholar API key（可选）
        "similarity_threshold": 0.9,  # 图谱相似边阈值（已弃用，相似边改用多尺度骨架提取）
        "knn_k": 8,                   # 已弃用：旧的全局 kNN 条数；现由 backbone_alpha 取代
        "backbone_alpha": 0.25,       # 多尺度骨架显著性 α（Disparity Filter）：越小越稀越精炼
        "link_distance": 50,          # 知识图谱节点边长基准（d3 默认 30）
        "auto_digest": True,          # 深度研究时自动「图谱速读」未解读论文（轻量，补全可解释 facet 关系）
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
    "similarity": {
        # 论文级相似度计算 provider，对应 backend/services/deep_research/similarity/REGISTRY
        # 可选：unified（默认）/ bge_centroid / bibcoupling / hybrid / facet_weighted / cross_encoder
        "provider": "unified",
        # 每个 provider 的私有配置写在与 name 同名的子对象里：
        # unified：给每篇论文挑最佳主题向量（ChromaDB 质心 > title/abstract embedding），
        #   全量连接成研究地图；已解读论文对再挂可解释 facet 分解 detail。
        #   去噪用互近邻（graph_builder 统一负责），weights 透传给 facet 分解。
        "unified": {
            "weights": {
                "purpose": 0.5,    # 研究目的
                "method": 0.2,     # 方法
                "baseline": 0.15,  # 对比基线
                "dataset": 0.05,   # 数据集
            },
        },
        # facet_weighted：可解释的多维度相似度，权重会自动归一化到 1
        "facet_weighted": {
            "weights": {
                "purpose": 0.5,    # 研究目的
                "method": 0.2,     # 方法
                "baseline": 0.15,  # 对比基线
                "dataset": 0.05,   # 数据集
            },
            "use_embedding": True,  # purpose/method 用 embedding 余弦；False 走纯词重叠
        },
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


def mask_config(config: dict) -> dict:
    """返回配置深拷贝，所有 api_key 字段被掩码"""
    masked = copy.deepcopy(config)
    for section in masked.values():
        if isinstance(section, dict) and "api_key" in section:
            section["api_key"] = _mask_api_key(section["api_key"])
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
    """保存配置时，若某段 api_key 是掩码占位，则保留盘上原有真实 key。

    这样前端「只改了别的设置、api_key 字段还是回显的 ***xxxx」时不会把真 key 覆盖掉。
    用户确实想清空 key 时会传空串（不是掩码），照常清空。
    """
    out = copy.deepcopy(new_config)
    for name, section in out.items():
        if isinstance(section, dict) and "api_key" in section and is_masked_key(section.get("api_key", "")):
            section["api_key"] = (prior_config.get(name) or {}).get("api_key", "")
    return out


def load_config() -> dict:
    """加载配置"""
    if not os.path.exists(CONFIG_PATH):
        return _deep_copy_default()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 合并默认值（防止缺少字段）
        config = {}
        for key in DEFAULT_CONFIG:
            config[key] = {**DEFAULT_CONFIG[key], **saved.get(key, {})}
        return config
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")
        return _deep_copy_default()


def _deep_copy_default() -> dict:
    """深拷贝默认配置"""
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    """保存 AI 配置（原子写入：先写临时文件，再 os.replace 原子替换）"""
    _ensure_dir()
    dir_name = os.path.dirname(CONFIG_PATH)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp", prefix="ai_config_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
        logger.info("AI config saved")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_translate_config() -> dict:
    """获取翻译模型配置"""
    return load_config().get("translate", DEFAULT_CONFIG["translate"])


def get_analysis_config() -> dict:
    """获取解读模型配置"""
    cfg = load_config().get("analysis", DEFAULT_CONFIG["analysis"])
    # 如果解读配置没有填写 api_key，回退到环境变量中的默认值
    if not cfg.get("api_key"):
        from ..config import settings
        cfg["api_key"] = settings.mimo_api_key
        if not cfg.get("base_url"):
            cfg["base_url"] = settings.mimo_base_url
        if not cfg.get("model"):
            cfg["model"] = settings.mimo_model
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


def get_similarity_config() -> dict:
    """获取论文级相似度计算 Provider 配置"""
    return load_config().get("similarity", DEFAULT_CONFIG["similarity"])


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

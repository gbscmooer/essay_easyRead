"""
AI 配置服务

管理翻译和解读两套 AI 模型配置，存储在 JSON 文件中。
"""

import os
import json
import logging
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
        "system_prompt": "",
        "temperature": 0.1,
        "max_tokens": 16384,
    },
    "ui": {
        "highlight_duration": 3000,
        "zoom_step": 0.2,
        "zoom_min": 0.4,
        "zoom_max": 3.0,
        "pinch_zoom_step": 0.02,
        "theme": "punk",
        "font": "sans",
        "custom_font": "",
    },
    "embedding": {
        "provider": "local",
        "model": "BAAI/bge-m3",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "batch_size": 32,
        "device": "auto",
    },
}


def _ensure_dir():
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)


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
    """保存 AI 配置"""
    _ensure_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.info("AI config saved")


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


def get_ui_config() -> dict:
    """获取 UI 配置"""
    return load_config().get("ui", DEFAULT_CONFIG["ui"])


def get_embedding_config() -> dict:
    """获取 Embedding 模型配置"""
    return load_config().get("embedding", DEFAULT_CONFIG["embedding"])

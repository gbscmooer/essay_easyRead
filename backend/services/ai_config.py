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
    },
    "analysis": {
        "api_key": "",
        "base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
        "model": "mimo-v2.5-pro",
        "system_prompt": "",
    },
}


def _ensure_dir():
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)


def load_config() -> dict:
    """加载 AI 配置"""
    if not os.path.exists(CONFIG_PATH):
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 合并默认值（防止缺少字段）
        config = {}
        for key in DEFAULT_CONFIG:
            config[key] = {**DEFAULT_CONFIG[key], **saved.get(key, {})}
        return config
    except Exception as e:
        logger.warning(f"Failed to load AI config: {e}")
        return DEFAULT_CONFIG.copy()


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

"""
应用配置

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
LLM 统一为 api_key + base_url + model；协议仅两种：
OpenAI 兼容 或 Anthropic 兼容（由 base_url 是否含 anthropic 判定）。
"""

import os
from pydantic_settings import BaseSettings

# 项目根目录（backend/ 的上级目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_path(path: str) -> str:
    """将相对路径解析为绝对路径（基于项目根目录）"""
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


def _apply_legacy_llm_env() -> None:
    """兼容旧环境变量名，映射到统一的 LLM_*。"""
    aliases = (
        ("LLM_API_KEY", ("MIMO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")),
        ("LLM_BASE_URL", ("MIMO_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")),
        ("LLM_MODEL", ("MIMO_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL")),
    )
    for canonical, legacy_keys in aliases:
        if os.environ.get(canonical):
            continue
        for key in legacy_keys:
            value = os.environ.get(key)
            if value:
                os.environ[canonical] = value
                break


_apply_legacy_llm_env()


class Settings(BaseSettings):
    """
    应用配置项

    环境变量优先，.env 文件次之，代码默认值最后。
    字段名自动映射大写环境变量，如 LLM_API_KEY → llm_api_key。
    """

    # 统一 LLM 配置（OpenAI 兼容或 Anthropic 兼容，由 base_url 判定协议）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # 数据库（相对路径会自动解析为项目根目录下的绝对路径）
    database_url: str = "sqlite:///./data/essay_read.db"

    # 文件上传
    upload_dir: str = "./uploads"  # PDF 存储目录
    max_file_size: int = 50 * 1024 * 1024  # 50MB

    # 服务器
    host: str = "127.0.0.1"
    port: int = 8000

    # 开发模式：开启后 uvicorn 自动热重载
    dev: bool = False

    # CORS
    frontend_origin: str = "http://localhost:5173"  # Vite 默认端口

    # Deep Research
    chroma_dir: str = "./data/chroma"  # ChromaDB 持久化目录

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def resolved_database_url(self) -> str:
        """返回绝对路径的数据库 URL"""
        url = self.database_url
        if url.startswith("sqlite:///"):
            path_part = url[len("sqlite:///"):]
            abs_path = _resolve_path(path_part)
            return f"sqlite:///{abs_path}"
        return url

    @property
    def resolved_upload_dir(self) -> str:
        """返回绝对路径的上传目录"""
        return _resolve_path(self.upload_dir)

    @property
    def resolved_chroma_dir(self) -> str:
        """返回绝对路径的 ChromaDB 目录"""
        return _resolve_path(self.chroma_dir)


# 全局单例，整个应用使用此实例
settings = Settings()

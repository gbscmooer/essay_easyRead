"""
应用配置

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
LLM 统一为 api_key + base_url + model；协议仅两种：
OpenAI 兼容 或 Anthropic 兼容（由 base_url 是否含 anthropic 判定）。
"""

import os
from typing import Mapping, Optional, Union

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上级目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LEGACY_LLM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LLM_API_KEY", ("MIMO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")),
    ("LLM_BASE_URL", ("MIMO_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")),
    ("LLM_MODEL", ("MIMO_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL")),
)

EnvFileArg = Union[str, os.PathLike[str], list[Union[str, os.PathLike[str]]], tuple[Union[str, os.PathLike[str]], ...], None]


def _resolve_path(path: str) -> str:
    """将相对路径解析为绝对路径（基于项目根目录）"""
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


def _resolve_dotenv_path(env_file: Optional[str] = None) -> Optional[str]:
    """定位待读取的 .env 路径（优先项目根目录）。"""
    if env_file:
        return env_file if os.path.isfile(env_file) else None
    candidates = (
        os.path.join(_PROJECT_ROOT, ".env"),
        os.path.join(os.getcwd(), ".env"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _load_dotenv_values(env_file: Optional[str] = None) -> Mapping[str, Optional[str]]:
    path = _resolve_dotenv_path(env_file)
    if not path:
        return {}
    try:
        from dotenv import dotenv_values

        return dotenv_values(path)
    except Exception:
        return {}


def _apply_legacy_llm_env(
    env_file: Optional[str] = None,
    *,
    use_project_dotenv: bool = True,
) -> None:
    """兼容旧环境变量名，映射到统一的 LLM_*。

    同时读取进程环境变量与 .env。空字符串的 LLM_*（常见于从 .env.example
    复制后未填写）不得挡住 MIMO_*/OPENAI_*/ANTHROPIC_* 里的真实值。
    映射写入 os.environ 后，pydantic-settings 会优先采用进程环境，避免
    AliasChoices 选中空的 LLM_* 占位键。

    ``use_project_dotenv=False`` 供单测隔离：只看进程环境，不读仓库根目录 .env。
    """
    if env_file is not None:
        file_values = _load_dotenv_values(env_file)
    elif use_project_dotenv:
        file_values = _load_dotenv_values(None)
    else:
        file_values = {}

    def _lookup(key: str) -> Optional[str]:
        value = os.environ.get(key)
        if value:
            return value
        file_value = file_values.get(key)
        if file_value:
            return file_value
        return None

    for canonical, legacy_keys in _LEGACY_LLM_ALIASES:
        existing = _lookup(canonical)
        if existing:
            if not os.environ.get(canonical):
                os.environ[canonical] = existing
            continue
        for key in legacy_keys:
            value = _lookup(key)
            if value:
                os.environ[canonical] = value
                break


_apply_legacy_llm_env()


def _env_file_to_path(env_file: EnvFileArg) -> Optional[str]:
    if env_file is None:
        return None
    if isinstance(env_file, (list, tuple)):
        for item in env_file:
            if item is not None:
                return str(item)
        return None
    return str(env_file)


class Settings(BaseSettings):
    """
    应用配置项

    环境变量优先，.env 文件次之，代码默认值最后。
    字段名自动映射大写环境变量，如 LLM_API_KEY → llm_api_key。
    """

    def __init__(self, **kwargs):
        if "_env_file" in kwargs:
            path = _env_file_to_path(kwargs.get("_env_file"))
            if path is not None:
                _apply_legacy_llm_env(path)
        else:
            _apply_legacy_llm_env()
        super().__init__(**kwargs)

    # 统一 LLM 配置（OpenAI 兼容或 Anthropic 兼容，由 base_url 判定协议）
    llm_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "LLM_API_KEY",
            "MIMO_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
    )
    llm_base_url: str = Field(
        "https://api.openai.com/v1",
        validation_alias=AliasChoices(
            "LLM_BASE_URL",
            "MIMO_BASE_URL",
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
        ),
    )
    llm_model: str = Field(
        "gpt-4o-mini",
        validation_alias=AliasChoices(
            "LLM_MODEL",
            "MIMO_MODEL",
            "OPENAI_MODEL",
            "ANTHROPIC_MODEL",
        ),
    )

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

    # Academic & Paper Download
    elsevier_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "ELSEVIER_API_KEY",
            "SCOPUS_API_KEY",
        ),
    )
    research_email: str = Field(
        "",
        validation_alias=AliasChoices(
            "RESEARCH_EMAIL",
            "OPENALEX_EMAIL",
            "UNPAYWALL_EMAIL",
        ),
    )

    # Research Agent Harness（默认离线/确定性；显式开启后 parse_goal 使用一次 LLM）
    research_agent_planner_enabled: bool = False
    research_agent_planner_max_tokens: int = 320
    research_agent_planner_timeout_seconds: float = 30.0

    # ``extra=ignore`` is required during the MIMO_* -> LLM_* migration:
    # existing .env files can contain removed settings unrelated to these aliases.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

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

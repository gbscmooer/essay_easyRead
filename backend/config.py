"""
应用配置

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
所有配置项都有合理的默认值，仅 API Key 为必需。
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    应用配置项

    环境变量优先，.env 文件次之，代码默认值最后。
    字段名自动映射大写环境变量，如 MIMO_API_KEY → mimo_api_key。
    """

    # MiMo API 配置
    mimo_api_key: str  # 必需，从环境变量 MIMO_API_KEY 读取
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.5-pro"

    # 数据库
    database_url: str = "sqlite:///./data/essay_read.db"

    # 文件上传
    upload_dir: str = "./uploads"  # PDF 存储目录
    max_file_size: int = 50 * 1024 * 1024  # 50MB

    # 服务器
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS
    frontend_origin: str = "http://localhost:5173"  # Vite 默认端口

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局单例，整个应用使用此实例
settings = Settings()

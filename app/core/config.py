from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Stock Scanner"
    app_env: Literal["development", "testing", "production"] = "development"
    app_debug: bool = False
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url_async: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ai_stock_scanner"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/ai_stock_scanner"
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    nse_base_url: str = "https://www.nseindia.com"
    nse_bhavcopy_url: str = (
        "https://www.nseindia.com/content/historical/EQUITIES/{year}/{month}/"
        "cm{day:02d}{month}{year}bhav.csv.zip"
    )
    raw_data_dir: Path = Field(default=Path("data/raw/nse"))
    download_timeout: int = 30
    max_upload_size: int = 50 * 1024 * 1024
    base_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    eod_scheduler_enabled: bool = False
    eod_scheduler_timezone: str = "Asia/Kolkata"
    eod_scheduler_hour: int = 18
    eod_scheduler_minute: int = 30
    eod_scheduler_max_instances: int = 1
    eod_scheduler_coalesce: bool = True

    ai_provider: str = "mock"
    ai_timeout: int = 60
    ai_cache_ttl: int = 3600
    ai_enabled: bool = True
    ai_ollama_url: str = "http://localhost:11434"
    ai_ollama_model: str = "llama3.1"
    ai_deepseek_api_key: str = ""
    ai_deepseek_model: str = "deepseek-chat"
    ai_deepseek_base_url: str = "https://api.deepseek.com"
    ai_openai_api_key: str = ""
    ai_openai_model: str = "gpt-4o-mini"
    ai_openai_base_url: str = "https://api.openai.com/v1"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

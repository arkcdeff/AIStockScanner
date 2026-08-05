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

    notification_provider: str = "mock"
    notification_timeout: int = 15

    notifications_enabled: bool = False
    notification_max_attempts: int = 3
    notification_retry_minutes: int = 15
    notification_batch_size: int = 100
    notification_stale_minutes: int = 30

    notification_delivery_job_enabled: bool = False
    notification_delivery_interval_minutes: int = 5
    notification_delivery_max_instances: int = 1
    notification_delivery_coalesce: bool = True

    daily_summary_enabled: bool = False
    daily_summary_hour: int = 20
    daily_summary_minute: int = 0
    daily_summary_timezone: str = "Asia/Kolkata"
    daily_summary_max_instances: int = 1
    daily_summary_coalesce: bool = True

    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""
    telegram_html_mode: bool = True
    telegram_max_message_length: int = 4096

    email_host: str = ""
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from: str = ""
    email_use_tls: bool = False
    email_use_starttls: bool = True
    email_timeout: int = 15

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

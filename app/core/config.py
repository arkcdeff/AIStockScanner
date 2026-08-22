from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Stock Scanner"
    app_env: Literal["development", "testing", "production"] = "development"
    app_debug: bool = False
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url_async: str = Field()
    database_url_sync: str = Field()
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

    # Multi-source market data & Upstox read-only provider configuration
    upstox_analytics_token: str = Field(default="", validation_alias="UPSTOX_ANALYTICS_TOKEN")
    upstox_base_url: str = Field(default="https://api.upstox.com/v2")
    upstox_v3_base_url: str = Field(default="https://api.upstox.com/v3")
    upstox_v3_feed_authorize_url: str = Field(default="https://api.upstox.com/v3/feed/market-data-feed/authorize")
    upstox_ws_url: str = Field(default="wss://api.upstox.com/v3/feed/market-data-feed")
    live_mode: str = Field(default="LIVE_READ_ONLY")

    freshness_live_seconds: float = 5.0
    freshness_delayed_seconds: float = 30.0
    freshness_stale_seconds: float = 30.0

    eod_scheduler_enabled: bool = False
    eod_scheduler_timezone: str = "Asia/Kolkata"
    eod_scheduler_hour: int = 18
    eod_scheduler_minute: int = 30
    eod_scheduler_max_instances: int = 1
    eod_scheduler_coalesce: bool = True
    eod_scheduler_retry_enabled: bool = True
    eod_scheduler_max_retries: int = 3
    eod_scheduler_retry_minutes: int = 15
    eod_scheduler_run_scanners: bool = True
    eod_scheduler_auto_catchup: bool = False
    eod_scheduler_catchup_max_days: int = 7

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

    # Market Intelligence Scheduler
    market_intelligence_premarket_enabled: bool = False
    market_intelligence_premarket_hour: int = 8
    market_intelligence_premarket_minute: int = 30
    market_intelligence_premarket_timezone: str = "Asia/Kolkata"
    market_intelligence_premarket_max_instances: int = 1
    market_intelligence_premarket_coalesce: bool = True

    market_intelligence_postmarket_enabled: bool = False
    market_intelligence_postmarket_hour: int = 16
    market_intelligence_postmarket_minute: int = 15
    market_intelligence_postmarket_timezone: str = "Asia/Kolkata"
    market_intelligence_postmarket_max_instances: int = 1
    market_intelligence_postmarket_coalesce: bool = True

    market_intelligence_snapshot_enabled: bool = False
    market_intelligence_snapshot_hour: int = 17
    market_intelligence_snapshot_minute: int = 0
    market_intelligence_snapshot_timezone: str = "Asia/Kolkata"
    market_intelligence_snapshot_max_instances: int = 1
    market_intelligence_snapshot_coalesce: bool = True

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

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_days: int = Field(default=30, ge=1, le=365)
    password_hash_scheme: Literal["argon2id"] = "argon2id"
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)

    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    access_cookie_name: str = "access_token"
    refresh_cookie_name: str = "refresh_token"
    csrf_cookie_name: str = "csrf_token"
    csrf_header_name: str = "x-csrf-token"
    csrf_secret_key: SecretStr = SecretStr("")

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

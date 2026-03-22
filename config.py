"""
Configuração centralizada via pydantic-settings.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Banco de dados (OLTP) ───────────────────────────────────────────────
    database_url: PostgresDsn = Field(default=..., description="URL PostgreSQL asyncpg")
    database_pool_size: int = Field(default=20, ge=1, le=100)
    database_max_overflow: int = Field(default=40, ge=0)
    database_echo: bool = Field(default=False)

    # ── TimescaleDB (séries temporais) ──────────────────────────────────────
    timescale_url: PostgresDsn | None = Field(
        default=None,
        description="URL TimescaleDB (market_data). None = desabilitado.",
    )
    timescale_pool_size: int = Field(default=10, ge=1, le=50)
    timescale_max_overflow: int = Field(default=20, ge=0)

    # ── Segurança ───────────────────────────────────────────────────────────
    app_secret_key: str = Field(default=..., min_length=16)

    # ── Fintz ───────────────────────────────────────────────────────────────
    fintz_api_key: str = Field(default="")
    fintz_base_url: str = Field(default="https://api.fintz.com.br")
    fintz_request_timeout: int = Field(default=30, ge=5)
    fintz_max_retries: int = Field(default=3, ge=0, le=10)

    # ── Event Processor ─────────────────────────────────────────────────────
    event_processor_concurrency: int = Field(default=10, ge=1, le=100)
    event_max_retries: int = Field(default=5, ge=0, le=20)
    event_retry_base_delay: float = Field(default=1.0, ge=0.0)
    event_idempotency_ttl: int = Field(default=3600, ge=60)

    # ── Observabilidade ─────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    metrics_enabled: bool = Field(default=True)
    tracing_enabled: bool = Field(default=False)
    tracing_otlp_endpoint: str = Field(default="http://localhost:4317")

    # ── Armazenamento (E:\finanalytics_data bind-mounted em /data) ──────────
    data_dir: str = Field(default="/data")

    # ── GPU / Processamento (i9-14900K + 2x RTX 4090) ───────────────────────
    cuda_visible_devices: str = Field(default="1")     # GPU2 dedicada
    polars_max_threads: int = Field(default=16, ge=1, le=32)

    # ── Runtime ─────────────────────────────────────────────────────────────
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level deve ser um de {allowed}")
        return upper

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        if v not in ("json", "text"):
            raise ValueError("log_format deve ser 'json' ou 'text'")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def timescale_enabled(self) -> bool:
        return self.timescale_url is not None

    @property
    def data_dir_raw(self) -> str:
        return f"{self.data_dir}/raw"

    @property
    def data_dir_processed(self) -> str:
        return f"{self.data_dir}/processed"

    @property
    def data_dir_logs(self) -> str:
        return f"{self.data_dir}/logs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

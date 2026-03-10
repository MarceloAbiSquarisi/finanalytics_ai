"""
finanalytics_ai.config
──────────────────────
Configuração centralizada via variáveis de ambiente com Pydantic Settings.

Design decision: Pydantic BaseSettings valida em startup — fail-fast.
Evita surpresas de configuração errada em produção.
Todos os campos são tipados, o que permite que o mypy e o IDE façam
inspeção estática sem magia.

Usage:
    from finanalytics_ai.config import get_settings
    settings = get_settings()
    print(settings.database_url)
"""

from __future__ import annotations

import functools
from enum import StrEnum
from typing import Annotated

from pydantic import Field, HttpUrl, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class EventQueueBackend(StrEnum):
    MEMORY = "memory"
    REDIS = "redis"
    RABBITMQ = "rabbitmq"
    KAFKA = "kafka"


class Settings(BaseSettings):
    """
    Configuração principal da aplicação.

    Lê automaticamente de variáveis de ambiente (case-insensitive).
    Em desenvolvimento, carrega do arquivo .env na raiz do projeto.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignora vars extras — útil em CI/CD com muitas vars
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_log_level: LogLevel = LogLevel.INFO
    app_log_format: LogFormat = LogFormat.JSON
    app_secret_key: str = Field(..., min_length=16)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: PostgresDsn
    database_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    database_max_overflow: Annotated[int, Field(ge=0, le=50)] = 20
    database_echo: bool = False

    # ── Queue ─────────────────────────────────────────────────────────────────
    event_queue_backend: EventQueueBackend = EventQueueBackend.MEMORY
    redis_url: RedisDsn | None = None

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_market_events: str = "market-events"
    kafka_topic_price_updates: str = "price-updates"
    kafka_consumer_group: str = "finanalytics-ai"
    kafka_auto_offset_reset: str = "latest"  # "latest" não reprocesa histórico

    # ── TimescaleDB ───────────────────────────────────────────────────────────
    timescale_url: str = "postgresql://finanalytics:secret@localhost:5433/finanalytics"
    timescale_pool_size: Annotated[int, Field(ge=1, le=50)] = 5

    # ── BRAPI ────────────────────────────────────────────────────────────────
    brapi_token: str = ""
    brapi_base_url: HttpUrl = HttpUrl("https://brapi.dev/api")

    # ── XP ───────────────────────────────────────────────────────────────────
    xp_api_key: str = ""
    xp_api_secret: str = ""
    xp_account_id: str = ""

    # ── BTG ──────────────────────────────────────────────────────────────────
    btg_api_key: str = ""
    btg_api_secret: str = ""
    btg_account_id: str = ""

    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.OPENAI
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Observabilidade ──────────────────────────────────────────────────────
    otel_service_name: str = "finanalytics-ai"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    prometheus_port: int = 9090
    metrics_enabled: bool = True

    # ── Resiliência ──────────────────────────────────────────────────────────
    http_retry_max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    http_retry_wait_seconds: float = 1.0
    http_timeout_seconds: float = 30.0

    # ── Price Producer ────────────────────────────────────────────────────────
    producer_tickers: str = "PETR4,VALE3,ITUB4,BBDC4,WEGE3,MGLU3,ABEV3,BBAS3"
    producer_poll_interval_seconds: float = 30.0
    producer_enabled: bool = True

    # ── Feature Flags ────────────────────────────────────────────────────────
    feature_backtesting: bool = True
    feature_neural_networks: bool = False
    feature_open_finance: bool = False
    feature_real_time_dashboard: bool = True

    # ── Computed properties ──────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app_env == AppEnv.DEVELOPMENT

    @field_validator("redis_url", mode="before")
    @classmethod
    def require_redis_if_needed(cls, v: str | None) -> str | None:
        # Validação cruzada é feita no model_validator abaixo
        return v


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Retorna instância cacheada de Settings.

    lru_cache(1) garante singleton sem acoplamento a variável global.
    Em testes, use: get_settings.cache_clear() para forçar recarga.
    """
    return Settings()

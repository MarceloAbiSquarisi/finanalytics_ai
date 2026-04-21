"""
Configuracao da camada de aplicacao via variaveis de ambiente.

Decisao: Pydantic BaseSettings aqui — e a borda do sistema, nao o dominio.
BaseSettings valida e documenta as configuracoes em tempo de inicializacao,
levantando erros claros antes do servico comecar a processar eventos.

Alternative considerada: dataclass + os.getenv() manual.
Rejeitada porque: sem validacao automatica de tipos, sem .env file support,
mais verboso sem ganho arquitetural real.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class EventProcessorConfig(BaseSettings):
    """
    Configuracao completa do Event Processor.
    Todas as variaveis comecam com EVENT_PROCESSOR_ para evitar colisao.
    """

    # Concorrencia
    concurrency: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Numero maximo de eventos processados em paralelo",
    )

    # Retry
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximo de tentativas antes de mover para dead-letter",
    )
    retry_base_delay: float = Field(
        default=1.0,
        gt=0,
        description="Delay base em segundos para backoff exponencial",
    )
    retry_max_delay: float = Field(
        default=30.0,
        gt=0,
        description="Delay maximo em segundos entre retries",
    )

    # Idempotency
    idempotency_ttl: int = Field(
        default=86400,
        ge=60,
        description="TTL da chave de idempotencia em segundos (padrao: 24h)",
    )
    idempotency_key_prefix: str = Field(
        default="evt_idem",
        description="Prefixo das chaves no Redis",
    )

    # Cleanup
    cleanup_retention_days: int = Field(
        default=30,
        ge=1,
        description="Dias para reter event_records completed",
    )

    # Observabilidade
    metrics_enabled: bool = Field(default=True)
    tracing_enabled: bool = Field(default=True)

    @field_validator("retry_max_delay")
    @classmethod
    def max_delay_must_exceed_base(cls, v: float, info: object) -> float:
        # Acessamos via info.data para compatibilidade com Pydantic v2
        data = getattr(info, "data", {})
        base = data.get("retry_base_delay", 1.0)
        if v < base:
            raise ValueError(f"retry_max_delay ({v}) deve ser >= retry_base_delay ({base})")
        return v

    model_config = {
        "env_prefix": "EVENT_PROCESSOR_",
        "env_file": ".env",
        "extra": "ignore",  # ignora variaveis desconhecidas do .env
    }

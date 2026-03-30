"""
PriceUpdateRule — persiste atualizacao de preco no TimescaleDB.

Responsabilidade: receber um evento price.update validado e persistir
no TimescaleDB (tabela fintz_cotacoes_ts ou ohlc_bars dependendo do contexto).

Diferenca de PriceValidationRule:
- PriceValidationRule: valida se o preco e plausivel (circuit breaker)
- PriceUpdateRule: persiste o preco validado (efeito colateral)

Ordem no pipeline: PriceValidationRule ANTES de PriceUpdateRule.
Se a validacao falhar, a atualizacao nao ocorre.

Decisao sobre persistencia: usa asyncpg diretamente (nao SQLAlchemy)
para compatibilidade com o pool TimescaleDB existente no projeto.
O TimescaleDB esta na porta 5433 (separado do PostgreSQL principal).

Fallback: se o TimescaleDB nao estiver disponivel, o preco e persistido
apenas no PostgreSQL principal via SqlEventRepository (ja feito pelo servico).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from finanalytics_ai.domain.events.models import DomainEvent, ProcessingResult
from finanalytics_ai.domain.events.value_objects import EventType

logger = structlog.get_logger(__name__)

_APPLIES_TO = EventType.PRICE_UPDATE


class PriceUpdateRule:
    """
    Persiste preco no TimescaleDB.

    timescale_pool: pool asyncpg conectado ao TimescaleDB (porta 5433).
    Se None, a regra registra um warning e retorna sucesso sem persistir.
    Isso permite o pipeline rodar sem TimescaleDB em desenvolvimento.
    """

    name = "price_update"

    def __init__(self, timescale_pool: Any | None = None) -> None:
        self._pool = timescale_pool

    def applies_to(self, event: DomainEvent) -> bool:
        return event.payload.event_type == _APPLIES_TO

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        data = event.payload.data
        ticker: str | None = data.get("ticker")
        price: float | None = data.get("price")
        volume: float | None = data.get("volume")
        raw_ts: str | None = data.get("timestamp")

        if not ticker or price is None:
            return ProcessingResult.failure(
                event.event_id,
                "PriceUpdateRule: ticker ou price ausente no payload",
            )

        if self._pool is None:
            logger.warning(
                "price_update.no_timescale_pool",
                ticker=ticker,
                event_id=str(event.event_id),
            )
            return ProcessingResult.success(
                event.event_id,
                {"persisted": False, "reason": "timescale_pool nao configurado"},
            )

        try:
            ts = _parse_timestamp(raw_ts)
            await self._persist(ticker, price, volume or 0.0, ts, data)
            logger.debug(
                "price_update.persisted",
                ticker=ticker,
                price=price,
                event_id=str(event.event_id),
            )
            return ProcessingResult.success(
                event.event_id,
                {"persisted": True, "ticker": ticker, "price": price},
            )

        except Exception as exc:
            # Erro de persistencia e transitorio — o EventProcessorService
            # vai retry baseado na excecao TransientError levantada pelo handler
            from finanalytics_ai.domain.events.exceptions import DatabaseError
            raise DatabaseError(
                f"Falha ao persistir preco de {ticker}: {exc}",
                event_id=event.event_id,
                original=exc,
            ) from exc

    async def _persist(
        self,
        ticker: str,
        price: float,
        volume: float,
        ts: datetime,
        data: dict[str, Any],
    ) -> None:
        """Insere na tabela de cotacoes do TimescaleDB."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fintz_cotacoes_ts
                    (time, ticker, preco_fechamento, volume_financeiro, fonte)
                VALUES (, , , , )
                ON CONFLICT (time, ticker) DO UPDATE
                    SET preco_fechamento   = EXCLUDED.preco_fechamento,
                        volume_financeiro  = EXCLUDED.volume_financeiro,
                        fonte              = EXCLUDED.fonte
                """,
                ts,
                ticker,
                price,
                volume,
                data.get("source", "profit_dll"),
            )


def _parse_timestamp(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(tz=UTC)
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return datetime.now(tz=UTC)

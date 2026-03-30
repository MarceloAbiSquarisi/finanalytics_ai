"""
Exemplo concreto de BusinessRule: validacao de atualizacao de preco.

Demonstra o padrao correto:
1. applies_to() sincrono e rapido -- sem IO
2. apply() async -- pode consultar banco
3. Retorna ProcessingResult para erros de negocio esperados
4. Levanta excecao apenas para falhas de infraestrutura
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from finanalytics_ai.domain.events.models import DomainEvent, ProcessingResult
from finanalytics_ai.domain.events.value_objects import EventType

logger = structlog.get_logger(__name__)

MAX_PRICE_CHANGE_PCT = 20.0

# Type alias -- evita o problema "object not callable" que surge ao usar 'object'
GetLastPrice = Callable[[str], Awaitable[float | None]]


class PriceValidationRule:
    """
    Valida atualizacoes de preco contra limiares de variacao aceitavel.
    Injecao de dependencia: recebe callable para buscar preco anterior.
    """

    name = "price_validation"

    def __init__(self, get_last_price: GetLastPrice) -> None:
        self._get_last_price = get_last_price

    def applies_to(self, event: DomainEvent) -> bool:
        return event.payload.event_type == EventType.PRICE_UPDATE

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        data = event.payload.data
        ticker: str | None = data.get("ticker")
        new_price: float | None = data.get("price")
        log = logger.bind(rule=self.name, event_id=str(event.event_id), ticker=ticker)

        if not ticker or new_price is None:
            return ProcessingResult.failure(
                event.event_id,
                "Payload invalido: 'ticker' e 'price' sao obrigatorios",
            )

        if new_price <= 0:
            return ProcessingResult.failure(
                event.event_id,
                f"Preco invalido: {new_price} <= 0",
            )

        last_price = await self._get_last_price(ticker)
        if last_price is not None and last_price > 0:
            pct_change = abs((new_price - last_price) / last_price) * 100
            if pct_change > MAX_PRICE_CHANGE_PCT:
                log.warning(
                    "price_validation.circuit_breaker",
                    pct_change=round(pct_change, 2),
                    last_price=last_price,
                    new_price=new_price,
                )
                return ProcessingResult.failure(
                    event.event_id,
                    f"Variacao suspeita: {pct_change:.1f}% (max: {MAX_PRICE_CHANGE_PCT}%)",
                )

        log.debug("price_validation.passed", new_price=new_price)
        return ProcessingResult.success(event.event_id, {"validated_price": new_price})

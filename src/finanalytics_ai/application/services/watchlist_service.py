"""
finanalytics_ai.application.services.watchlist_service
────────────────────────────────────────────────────────
Serviço de Watchlist com avaliação de alertas inteligentes.

Responsabilidades:
  - CRUD de itens da watchlist
  - Enriquecimento com cotações em tempo real (BRAPI/Yahoo)
  - Adição e remoção de SmartAlerts
  - evaluate_all(): avalia todos os alertas ativos e retorna
    os que dispararam (usado por polling periódico ou SSE)

Design decisions:

  Enriquecimento lazy vs eager:
    get_watchlist() faz get_quote em paralelo (asyncio.gather) para
    todos os tickers. Com Semaphore(5) evita burst excessivo na BRAPI.
    Trade-off: latência maior na primeira carga, mas dados sempre frescos.
    Alternativa seria cache curto (30s) — optamos por simplicidade primeiro.

  evaluate_all() retorna apenas os disparados:
    O caller (rota SSE ou endpoint de polling) decide como notificar.
    Service não tem efeito colateral de notificação — princípio SRP.

  Dados OHLC para indicadores:
    SmartAlerts técnicos (RSI, MA) precisam de histórico.
    Buscamos get_ohlc_bars("3mo") que dá ~60 barras — suficiente para
    RSI(14) e MA(20) com warmup adequado.
    Cache por ticker em memória durante a avaliação (evita N requests
    para N alertas do mesmo ticker).

  Idempotência em add_item:
    Se ticker já está na watchlist do usuário, retorna o item existente
    ao invés de duplicar. Comportamento documentado no contrato.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.domain.watchlist.entities import (
    SmartAlert,
    SmartAlertConfig,
    SmartAlertResult,
    SmartAlertStatus,
    SmartAlertType,
    WatchlistItem,
    evaluate_smart_alert,
)

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import (
        WatchlistRepository,
    )

logger = structlog.get_logger(__name__)

_MAX_ITEMS_PER_USER = 50
_MAX_ALERTS_PER_ITEM = 8
_OHLC_RANGE = "3mo"
_FETCH_SEMAPHORE = 5


class WatchlistError(Exception):
    pass


class WatchlistService:
    def __init__(self, repo: WatchlistRepository, market_client: Any) -> None:
        self._repo = repo
        self._market = market_client

    # ── CRUD de itens ─────────────────────────────────────────────────────────

    async def add_item(
        self,
        user_id: str,
        ticker: str,
        note: str = "",
        tags: list[str] | None = None,
    ) -> WatchlistItem:
        """
        Adiciona ticker à watchlist. Idempotente: se já existe, retorna existente.
        """
        ticker = ticker.upper().strip()
        existing = await self._repo.find_by_user_and_ticker(user_id, ticker)
        if existing:
            return existing

        all_items = await self._repo.find_by_user(user_id)
        if len(all_items) >= _MAX_ITEMS_PER_USER:
            raise WatchlistError(f"Limite de {_MAX_ITEMS_PER_USER} itens atingido.")

        item = WatchlistItem(
            ticker=ticker,
            user_id=user_id,
            note=note,
            tags=tags or [],
        )
        await self._repo.save_item(item)
        logger.info("watchlist.item.added", user_id=user_id, ticker=ticker)
        return item

    async def remove_item(self, user_id: str, item_id: str) -> None:
        item = await self._repo.find_item(item_id)
        if not item or item.user_id != user_id:
            raise WatchlistError("Item não encontrado.")
        await self._repo.delete_item(item_id)
        logger.info("watchlist.item.removed", user_id=user_id, item_id=item_id)

    async def update_item(
        self,
        user_id: str,
        item_id: str,
        note: str | None = None,
        tags: list[str] | None = None,
    ) -> WatchlistItem:
        item = await self._repo.find_item(item_id)
        if not item or item.user_id != user_id:
            raise WatchlistError("Item não encontrado.")
        if note is not None:
            item.note = note
        if tags is not None:
            item.tags = tags
        await self._repo.save_item(item)
        return item

    async def get_watchlist(self, user_id: str) -> list[WatchlistItem]:
        """
        Retorna watchlist enriquecida com cotações em tempo real.
        Paralelo com Semaphore para não sobrecarregar a API.
        """
        items = await self._repo.find_by_user(user_id)
        if not items:
            return []

        sem = asyncio.Semaphore(_FETCH_SEMAPHORE)

        async def _enrich(item: WatchlistItem) -> WatchlistItem:
            async with sem:
                try:
                    from finanalytics_ai.domain.value_objects.money import Ticker

                    quote = await self._market.get_quote_full(Ticker(item.ticker))
                    item.current_price = float(quote.get("regularMarketPrice", 0) or 0)
                    item.change_pct = float(quote.get("regularMarketChangePercent", 0) or 0)
                    item.volume = int(quote.get("regularMarketVolume", 0) or 0)
                    item.high_52w = float(quote.get("fiftyTwoWeekHigh", 0) or 0) or None
                    item.low_52w = float(quote.get("fiftyTwoWeekLow", 0) or 0) or None
                    item.last_updated_at = datetime.utcnow()
                except Exception as exc:
                    logger.warning("watchlist.enrich.failed", ticker=item.ticker, error=str(exc))
            return item

        enriched = await asyncio.gather(*[_enrich(i) for i in items])
        return list(enriched)

    # ── Smart Alerts ──────────────────────────────────────────────────────────

    async def add_smart_alert(
        self,
        user_id: str,
        item_id: str,
        alert_type: str,
        config: dict[str, Any] | None = None,
        note: str = "",
    ) -> SmartAlert:
        item = await self._repo.find_item(item_id)
        if not item or item.user_id != user_id:
            raise WatchlistError("Item não encontrado.")

        active = [a for a in item.smart_alerts if a.status != SmartAlertStatus.DELETED]
        if len(active) >= _MAX_ALERTS_PER_ITEM:
            raise WatchlistError(f"Limite de {_MAX_ALERTS_PER_ITEM} alertas por item atingido.")

        try:
            at = SmartAlertType(alert_type)
        except ValueError:
            valid = [t.value for t in SmartAlertType]
            raise WatchlistError(f"Tipo inválido: {alert_type}. Válidos: {valid}") from None

        cfg = SmartAlertConfig(**(config or {})) if config else SmartAlertConfig()
        alert = SmartAlert(
            ticker=item.ticker,
            user_id=user_id,
            alert_type=at,
            config=cfg,
            note=note,
        )
        await self._repo.save_alert(alert, item_id)
        logger.info("watchlist.alert.added", ticker=item.ticker, type=alert_type)
        return alert

    async def remove_smart_alert(self, user_id: str, alert_id: str) -> None:
        await self._repo.delete_alert(alert_id)
        logger.info("watchlist.alert.removed", alert_id=alert_id)

    # ── Avaliação de alertas ──────────────────────────────────────────────────

    async def evaluate_all(self, user_id: str) -> list[SmartAlertResult]:
        """
        Avalia todos os alertas ativos da watchlist do usuário.
        Retorna apenas os que dispararam.

        Usa cache de barras OHLC por ticker para não buscar múltiplas
        vezes o mesmo ativo quando há mais de um alerta configurado.
        """
        items = await self._repo.find_by_user(user_id)
        if not items:
            return []

        # Collect unique tickers que têm alertas avaliáveis
        tickers_with_alerts: dict[str, list[tuple[SmartAlert, str]]] = {}
        for item in items:
            evaluatable = [a for a in item.smart_alerts if a.is_evaluatable()]
            if evaluatable:
                tickers_with_alerts[item.ticker] = [(a, item.item_id) for a in evaluatable]

        if not tickers_with_alerts:
            return []

        # Busca OHLC e preço atual em paralelo (1 request por ticker)
        sem = asyncio.Semaphore(3)

        async def _fetch_data(ticker: str) -> tuple[str, list[dict], float]:
            async with sem:
                try:
                    from finanalytics_ai.domain.value_objects.money import Ticker as T

                    bars = await self._market.get_ohlc_bars(T(ticker), range_period=_OHLC_RANGE)
                    price = await self._market.get_quote(T(ticker))
                    return ticker, bars, float(price.amount)
                except Exception as exc:
                    logger.warning("watchlist.eval.fetch_failed", ticker=ticker, error=str(exc))
                    return ticker, [], 0.0

        fetches = await asyncio.gather(*[_fetch_data(t) for t in tickers_with_alerts])

        triggered: list[SmartAlertResult] = []

        for ticker, bars, current_price in fetches:
            if not bars or current_price == 0.0:
                continue

            for alert, item_id in tickers_with_alerts[ticker]:
                result = evaluate_smart_alert(alert, bars, current_price)
                if result.triggered:
                    alert.mark_triggered()
                    await self._repo.save_alert(alert, item_id)
                    triggered.append(result)
                    logger.info(
                        "watchlist.alert.triggered",
                        ticker=ticker,
                        alert_type=alert.alert_type.value,
                        message=result.message,
                    )

        return triggered

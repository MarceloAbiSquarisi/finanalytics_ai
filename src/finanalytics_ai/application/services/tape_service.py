"""
finanalytics_ai.application.services.tape_service
---------------------------------------------------
Tape Reading â€” analise do fluxo de negocios em tempo real via ProfitDLL.

O Tape Reading e a leitura dos negocios executados no book de ordens:
  - Cada negocio tem: preco, volume, agressor (comprador ou vendedor)
  - Agressor comprador: quem bateu na venda (pressao compradora)
  - Agressor vendedor: quem bateu na compra (pressao vendedora)

Metricas calculadas:
  1. Ratio C/V: volume comprador / volume vendedor (> 1 = pressao compradora)
  2. Saldo de Fluxo: volume_compra - volume_venda acumulado
  3. Negocios por nivel de preco: identifica suporte/resistencia
  4. Velocidade: negocios por minuto (aceleracao = institucional)
  5. Agressao: percentual de negocios por agressores compradores

Trade types da ProfitDLL:
  0 = Direto (nao identificado)
  1 = Agressor Comprador (bateu na venda)
  2 = Agressor Vendedor (bateu na compra)
  3 = Cross (interno corretora)

Design:
  - TapeBuffer: janela deslizante de ultimos N negocios por ticker
  - Metricas calculadas em tempo real sem I/O (dominio puro)
  - SSE para streaming ao frontend
  - Suporte a multiplos tickers simultaneamente
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Any

# Janela de analise
MAX_TRADES_PER_TICKER = 500
METRICS_WINDOW_SECONDS = 60  # janela de 1 minuto para metricas


@dataclass
class TradeTick:
    """Um negocio executado no tape."""

    ticker: str
    price: float
    volume: float
    quantity: int
    trade_type: int  # 0=direto, 1=agressor_compra, 2=agressor_venda, 3=cross
    buy_agent: int
    sell_agent: int
    timestamp: float  # Unix timestamp
    trade_number: int = 0

    @property
    def is_buyer_aggressor(self) -> bool:
        return self.trade_type == 1

    @property
    def is_seller_aggressor(self) -> bool:
        return self.trade_type == 2

    @property
    def side_label(self) -> str:
        if self.trade_type == 1:
            return "C"
        if self.trade_type == 2:
            return "V"
        return "D"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 2),
            "volume": round(self.volume, 0),
            "quantity": self.quantity,
            "side": self.side_label,
            "trade_type": self.trade_type,
            "buy_agent": self.buy_agent,
            "sell_agent": self.sell_agent,
            "timestamp": self.timestamp,
            "trade_number": self.trade_number,
        }


@dataclass
class TapeMetrics:
    """Metricas calculadas para um ticker."""

    ticker: str
    last_price: float
    total_trades: int
    total_volume: float
    vol_compra: float  # volume agressor comprador
    vol_venda: float  # volume agressor vendedor
    ratio_cv: float  # vol_compra / vol_venda
    saldo_fluxo: float  # vol_compra - vol_venda
    pct_agressao_compra: float  # % de negocios por comprador
    trades_por_min: float  # velocidade do fluxo
    preco_max: float
    preco_min: float
    nivel_dominante: str  # "COMPRADOR" | "VENDEDOR" | "EQUILIBRIO"
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "last_price": round(self.last_price, 2),
            "total_trades": self.total_trades,
            "total_volume": round(self.total_volume, 0),
            "vol_compra": round(self.vol_compra, 0),
            "vol_venda": round(self.vol_venda, 0),
            "ratio_cv": round(self.ratio_cv, 3),
            "saldo_fluxo": round(self.saldo_fluxo, 0),
            "pct_agressao_compra": round(self.pct_agressao_compra, 1),
            "trades_por_min": round(self.trades_por_min, 1),
            "preco_max": round(self.preco_max, 2),
            "preco_min": round(self.preco_min, 2),
            "nivel_dominante": self.nivel_dominante,
            "updated_at": self.updated_at,
        }


class TapeService:
    """
    Servico de Tape Reading em tempo real.

    Recebe ticks do ProfitDLL via on_tick() e mantem
    metricas atualizadas por ticker.

    Uso:
        tape = TapeService()

        # No worker do ProfitDLL:
        tape.on_tick(tick)

        # No endpoint SSE:
        async for event in tape.stream(ticker):
            yield event
    """

    def __init__(self) -> None:
        # Buffer de trades por ticker
        self._trades: dict[str, deque[TradeTick]] = defaultdict(
            lambda: deque(maxlen=MAX_TRADES_PER_TICKER)
        )
        # Metricas calculadas
        self._metrics: dict[str, TapeMetrics] = {}
        # Subscribers SSE por ticker
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # Tickers ativos
        self._active_tickers: set[str] = set()
        self._confluence_engine: ConflunceEngine = ConflunceEngine()

    async def start_redis_consumer(self, redis_url: str = "redis://redis:6379/0") -> None:
        """
        Consome ticks publicados pelo profit_market_worker via Redis pub/sub.
        Deve ser chamado no lifespan do FastAPI (app.py).
        Roda em background task — cancela sozinho quando o app fecha.
        """
        import asyncio
        import json

        import structlog as _structlog

        _log = _structlog.get_logger(__name__)
        try:
            import redis.asyncio as aioredis
        except ImportError:
            return

        client = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe("tape:ticks")
        _log.info("tape_service.redis_consumer_started", url=redis_url)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    self.on_tick(data)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("tape:ticks")
            await client.aclose()
            _log.info("tape_service.redis_consumer_stopped")

    def on_tick(self, tick: Any) -> None:
        """
        Processa um tick recebido do ProfitDLL.
        Pode receber PriceTick (dataclass) ou dict.
        """
        try:
            if isinstance(tick, dict):
                data = tick.get("data", tick)
                t = TradeTick(
                    ticker=str(data.get("ticker", "")),
                    price=float(data.get("price", 0)),
                    volume=float(data.get("volume", 0)),
                    quantity=int(data.get("quantity", 0)),
                    trade_type=int(data.get("trade_type", 0)),
                    buy_agent=int(data.get("buy_agent", 0)),
                    sell_agent=int(data.get("sell_agent", 0)),
                    timestamp=time.time(),
                    trade_number=int(data.get("trade_number", 0)),
                )
            else:
                # PriceTick dataclass
                t = TradeTick(
                    ticker=getattr(tick, "ticker", ""),
                    price=float(getattr(tick, "price", 0)),
                    volume=float(getattr(tick, "volume", 0)),
                    quantity=int(getattr(tick, "quantity", 0)),
                    trade_type=int(getattr(tick, "trade_type", 0)),
                    buy_agent=int(getattr(tick, "buy_agent", 0)),
                    sell_agent=int(getattr(tick, "sell_agent", 0)),
                    timestamp=time.time(),
                    trade_number=int(getattr(tick, "trade_number", 0)),
                )

            if not t.ticker or t.price <= 0:
                return

            self._trades[t.ticker].append(t)
            self._active_tickers.add(t.ticker)
            metrics = self._calc_metrics(t.ticker)
            self._metrics[t.ticker] = metrics

            # Broadcast para subscribers SSE
            self._broadcast(t.ticker, t, metrics)

        except Exception:
            pass

    def _calc_metrics(self, ticker: str) -> TapeMetrics:
        """Calcula metricas do tape para um ticker."""
        trades = list(self._trades[ticker])
        if not trades:
            return self._empty_metrics(ticker)

        now = time.time()
        window = [t for t in trades if now - t.timestamp <= METRICS_WINDOW_SECONDS]

        total_trades = len(window)
        total_vol = sum(t.volume for t in window)
        vol_c = sum(t.volume for t in window if t.is_buyer_aggressor)
        vol_v = sum(t.volume for t in window if t.is_seller_aggressor)

        ratio_cv = vol_c / vol_v if vol_v > 0 else (99.0 if vol_c > 0 else 1.0)
        saldo = vol_c - vol_v
        trades_c = sum(1 for t in window if t.is_buyer_aggressor)
        pct_c = trades_c / total_trades * 100 if total_trades > 0 else 50.0

        # Velocidade: trades por minuto
        if len(window) >= 2:
            dt = window[-1].timestamp - window[0].timestamp
            tpm = len(window) / (dt / 60) if dt > 0 else 0
        else:
            tpm = 0

        prices = [t.price for t in window]
        last_price = trades[-1].price

        if ratio_cv > 1.2:
            nivel = "COMPRADOR"
        elif ratio_cv < 0.8:
            nivel = "VENDEDOR"
        else:
            nivel = "EQUILIBRIO"

        return TapeMetrics(
            ticker=ticker,
            last_price=last_price,
            total_trades=total_trades,
            total_volume=total_vol,
            vol_compra=vol_c,
            vol_venda=vol_v,
            ratio_cv=ratio_cv,
            saldo_fluxo=saldo,
            pct_agressao_compra=pct_c,
            trades_por_min=tpm,
            preco_max=max(prices) if prices else last_price,
            preco_min=min(prices) if prices else last_price,
            nivel_dominante=nivel,
            updated_at=datetime.now(UTC).isoformat(),
        )

    def _empty_metrics(self, ticker: str) -> TapeMetrics:
        return TapeMetrics(
            ticker=ticker,
            last_price=0,
            total_trades=0,
            total_volume=0,
            vol_compra=0,
            vol_venda=0,
            ratio_cv=1.0,
            saldo_fluxo=0,
            pct_agressao_compra=50,
            trades_por_min=0,
            preco_max=0,
            preco_min=0,
            nivel_dominante="EQUILIBRIO",
            updated_at=datetime.now(UTC).isoformat(),
        )

    def _broadcast(self, ticker: str, trade: TradeTick, metrics: TapeMetrics) -> None:
        """Envia tick e metricas para todos os subscribers do ticker."""
        dead = []
        for q in self._subscribers.get(ticker, []):
            try:
                q.put_nowait(
                    {
                        "trade": trade.to_dict(),
                        "metrics": metrics.to_dict(),
                        "confluence": self._confluence_engine.evaluate(
                            ticker=ticker,
                            ratio_cv=metrics.ratio_cv,
                            saldo_fluxo=metrics.saldo_fluxo,
                            trades_por_min=metrics.trades_por_min,
                            total_trades=metrics.total_trades,
                            vol_compra=metrics.vol_compra,
                            vol_venda=metrics.vol_venda,
                        ).to_dict(),
                    }
                )
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._subscribers[ticker].remove(q)
            except ValueError:
                pass

    def subscribe(self, ticker: str) -> asyncio.Queue:
        """Retorna uma Queue para receber eventos SSE de um ticker."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[ticker].append(q)
        return q

    def unsubscribe(self, ticker: str, q: asyncio.Queue) -> None:
        try:
            self._subscribers[ticker].remove(q)
        except ValueError:
            pass

    def get_metrics(self, ticker: str) -> TapeMetrics | None:
        return self._metrics.get(ticker.upper())

    def get_recent_trades(self, ticker: str, limit: int = 50) -> list[dict]:
        trades = list(self._trades.get(ticker.upper(), []))
        return [t.to_dict() for t in reversed(trades[-limit:])]

    def get_active_tickers(self) -> list[str]:
        return sorted(self._active_tickers)

    def get_all_metrics(self) -> list[dict]:
        return [m.to_dict() for m in self._metrics.values()]

    # â”€â”€ Simulador (para testes sem mercado aberto) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def simulate(
        self,
        tickers: list[str],
        duration_seconds: int = 60,
        tps: float = 5.0,
    ) -> None:
        """
        Simula fluxo de negocios para testes.
        Gera ticks sinteticos com pressao compradora/vendedora alternando.
        """
        import math
        import random

        base_prices = {t: 48.0 + i * 10 for i, t in enumerate(tickers)}
        interval = 1.0 / tps
        end_time = time.time() + duration_seconds
        trade_num = 1000

        while time.time() < end_time:
            for ticker in tickers:
                price = base_prices[ticker]
                # Random walk
                price += random.gauss(0, 0.05)
                price = round(max(price, 1.0), 2)
                base_prices[ticker] = price

                # Simula pressao compradora em ciclos
                cycle = math.sin(time.time() / 30)
                prob_buy = 0.5 + cycle * 0.2
                trade_type = 1 if random.random() < prob_buy else 2

                vol = random.randint(100, 5000)

                tick = TradeTick(
                    ticker=ticker,
                    price=price,
                    volume=float(vol),
                    quantity=vol // 100,
                    trade_type=trade_type,
                    buy_agent=random.randint(1, 999),
                    sell_agent=random.randint(1, 999),
                    timestamp=time.time(),
                    trade_number=trade_num,
                )
                trade_num += 1
                self.on_tick(tick)

            await asyncio.sleep(interval)


from finanalytics_ai.domain.tape.confluence import ConflunceEngine

"""
HttpCandleFetcher — adapter sync sobre /api/v1/marketdata/candles/{ticker}.

Satisfaz o `CandleFetcher` Protocol consumido pela `MLSignalsStrategy`,
`TsmomMlOverlayStrategy` e `_evaluate_pairs` no auto_trader_worker.
Reuso entre strategies + worker pairs flow.

Decisões:
- Sync (httpx.Client, não AsyncClient) — strategies rodam dentro de
  `evaluate()` síncrono, e o worker sequencializa pares.
- timeout 10s — endpoint /candles tem fallback chain (Decisão 20: DB →
  Yahoo → BRAPI), pode levar segundos no pior caso.
- Falha silenciosa retorna None — caller (strategy/worker) decide skip.
- `fetch_closes` retorna list[float] (worker pares); `fetch_bars` retorna
  list[dict] com OHLC completo (strategies que precisam de high/low p/ ATR).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_RANGE_PERIOD = "1y"


class HttpCandleFetcher:
    """CandleFetcher Protocol via /api/v1/marketdata/candles/{ticker}."""

    def __init__(
        self,
        base_url: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        range_period: str = DEFAULT_RANGE_PERIOD,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._range_period = range_period

    def _fetch_raw_bars(
        self, ticker: str, range_period: str | None = None
    ) -> list[dict[str, Any]] | None:
        """Faz o GET e devolve bars crus (sem filtro de close). None em qualquer falha."""
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                r = client.get(
                    f"{self._base_url}/api/v1/marketdata/candles/{ticker}",
                    params={"range_period": range_period or self._range_period},
                )
                r.raise_for_status()
                data = r.json()
            return data.get("bars") or data.get("candles") or []
        except Exception as exc:
            logger.warning("candle_fetch.failed", ticker=ticker, error=str(exc))
            return None

    def fetch_closes(self, ticker: str, n: int) -> list[float] | None:
        """Retorna últimos `n` closes ou None em qualquer falha (network/parse/empty)."""
        bars = self._fetch_raw_bars(ticker)
        if bars is None:
            return None
        closes = [float(b["close"]) for b in bars if b.get("close")]
        return closes[-n:] if closes else None

    def fetch_bars(
        self, ticker: str, n: int, range_period: str | None = None
    ) -> list[dict[str, Any]] | None:
        """Retorna últimos `n` bars completos (dicts com OHLC) ou None em falha/empty.

        Usado por strategies que precisam de high/low/close para ATR (Wilder),
        diferente de `fetch_closes` que só extrai closes.
        """
        bars = self._fetch_raw_bars(ticker, range_period=range_period)
        if not bars:
            return None
        return bars[-n:]

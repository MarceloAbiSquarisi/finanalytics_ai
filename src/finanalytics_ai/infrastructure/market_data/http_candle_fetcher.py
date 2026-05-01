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
"""

from __future__ import annotations

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

    def fetch_closes(self, ticker: str, n: int) -> list[float] | None:
        """Retorna últimos `n` closes ou None em qualquer falha (network/parse/empty)."""
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                r = client.get(
                    f"{self._base_url}/api/v1/marketdata/candles/{ticker}",
                    params={"range_period": self._range_period},
                )
                r.raise_for_status()
                data = r.json()
            bars = data.get("bars") or data.get("candles") or []
            closes = [float(b["close"]) for b in bars if b.get("close")]
            return closes[-n:] if closes else None
        except Exception as exc:
            logger.warning("candle_fetch.failed", ticker=ticker, error=str(exc))
            return None

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

        NOTA: usa `/marketdata/candles` (default 5m). Para strategies que
        precisam de bars DAILY (TSMOM 252d, MLSignals long-lookback), usar
        `fetch_daily_bars` que cae no `/candles_daily` com UNION cross-source.
        """
        bars = self._fetch_raw_bars(ticker, range_period=range_period)
        if not bars:
            return None
        return bars[-n:]

    def fetch_daily_bars(self, ticker: str, n: int) -> list[dict[str, Any]] | None:
        """Retorna até `n` daily bars via `/marketdata/candles_daily` (UNION
        profit_daily_bars + ohlc_1m + fintz_cotacoes_ts com dedup ASC).

        Usado por TSMOM (252-day momentum) e estratégias com lookback longo
        onde nenhuma fonte sozinha cobre — Fintz freezou 2025-11-03 e
        profit_daily_bars/ohlc_1m só tem dados pós-freeze esparsos.

        Convenção de retorno: list[dict] com keys (ts, open, high, low, close,
        volume) ordenados ASC (mais antigo primeiro). Compatível com loops
        que indexam `bars[-(lookback + 1)]` para preço de N dias atrás.

        Mapeamento p/ compat com `fetch_bars`: `time` é mapeado de `ts`
        (timestamp ISO ou epoch) para que strategies que checam `b['time']`
        ou `b['close']` funcionem sem mudança.
        """
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                r = client.get(
                    f"{self._base_url}/api/v1/marketdata/candles_daily/{ticker}",
                    params={"n": n},
                )
                r.raise_for_status()
                data = r.json()
            candles = data.get("candles") or []
        except Exception as exc:
            logger.warning("candle_fetch_daily.failed", ticker=ticker, error=str(exc))
            return None
        if not candles:
            return None
        # Normaliza chave: ts -> também expor como `time` p/ compat com
        # strategies que iteram bars[i]["time"] (vem do endpoint /candles 5m).
        normalized = []
        for c in candles:
            ts = c.get("ts") or c.get("time")
            normalized.append(
                {
                    "ts": ts,
                    "time": ts,
                    "open": c.get("open"),
                    "high": c.get("high"),
                    "low": c.get("low"),
                    "close": c.get("close"),
                    "volume": c.get("volume"),
                }
            )
        return normalized[-n:]

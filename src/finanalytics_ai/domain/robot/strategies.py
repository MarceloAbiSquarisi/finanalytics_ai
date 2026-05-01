"""
Strategy implementations para auto_trader_worker (R1 Phase 2).

MVP: 1 strategy real (MLSignalsStrategy) + dummy. Phase 2 do robot foca em
LIGAR sinais ML existentes ao executor; refinamento (R2 TSMOM, R3 pares,
R4 ORB) vem em sessoes futuras.

Convencao de retorno (Strategy.evaluate):
  {
    "action": "BUY"|"SELL"|"HOLD"|"SKIP",
    "payload": {
      # Quando BUY/SELL:
      "quantity": int,                # lots inteiros (lot_size aplicado)
      "price": float | None,          # None = market order
      "order_type": "limit"|"market"|"stop",
      "take_profit": float | None,    # OCO TP (se SL tambem dado)
      "stop_loss": float | None,
      "is_daytrade": bool,            # default True (DayTrade no DLL)
      # Sempre:
      "reason": str,                  # log explicativo
      "snapshot": {...},              # contexto p/ audit (preco, sinal_ml, vol)
    }
  }

Strategies referem-se a sinais externos (signal_ml, momentum_252d, etc.) via
fetch sync — implementadas com http GET. Caching futuro pode entrar em
domain/robot/cache.py se necessidade.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any

import httpx
import structlog

from finanalytics_ai.domain.robot.risk import (
    DEFAULT_KELLY_FRACTION,
    DEFAULT_TARGET_VOL,
    annualize_vol,
    compute_atr,
    compute_atr_levels,
    position_size_vol_target,
    realized_vol_daily,
)

logger = structlog.get_logger(__name__)

API_BASE_URL = os.environ.get("AUTO_TRADER_API_URL", "http://api:8000")


# ── Heartbeat (sanity, sempre HOLD) ───────────────────────────────────────────


class DummyHeartbeatStrategy:
    """Sempre HOLD. Util pra validar pipeline sem disparar trade."""

    name = "dummy_heartbeat"

    def evaluate(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "HOLD",
            "payload": {
                "reason": "dummy_heartbeat_ok",
                "ticker": ticker,
                "snapshot": {"ts": datetime.now(UTC).isoformat()},
            },
        }


# ── ML Signals (R1.P2 strategy real) ─────────────────────────────────────────


class MLSignalsStrategy:
    """
    Consume `/api/v1/ml/signals` e converte em ordens com sizing vol-target +
    ATR-based TP/SL.

    Para cada ticker em config.tickers:
      1. Buscar item em SignalsResponse (cache de 1 batch por evaluate cycle).
      2. Se signal != BUY ou SELL -> HOLD.
      3. Buscar bars recentes p/ ATR + vol (cache nao implementado, busca a cada).
      4. Risk Engine: position_size_vol_target -> qty.
      5. ATR levels -> TP/SL.

    Config esperado em robot_strategies.config_json:
      {
        "tickers": ["PETR4", "VALE3"],
        "capital_per_strategy": 50000,
        "target_vol_annual": 0.15,
        "kelly_fraction": 0.25,
        "max_position_pct": 0.10,
        "atr_period": 14,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.0,
        "vol_lookback_days": 20,
        "min_sharpe_filter": 0.0
      }
    """

    name = "ml_signals"

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or API_BASE_URL
        self._signals_cache: dict[str, dict[str, Any]] = {}
        self._cache_ts: datetime | None = None
        # 60s TTL: dentro do mesmo loop tick (default 60s) o cache fica quente.
        self._cache_ttl_sec = 60

    def evaluate(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        ticker = ticker.upper()
        capital = float(context.get("capital_per_strategy", 10_000.0))
        target_vol = float(context.get("target_vol_annual", DEFAULT_TARGET_VOL))
        kelly = float(context.get("kelly_fraction", DEFAULT_KELLY_FRACTION))
        max_pct = float(context.get("max_position_pct", 0.10))
        atr_period = int(context.get("atr_period", 14))
        atr_sl_mult = float(context.get("atr_sl_mult", 2.0))
        atr_tp_mult = float(context.get("atr_tp_mult", 3.0))
        vol_lookback = int(context.get("vol_lookback_days", 20))

        # 1. Pegar sinal ML
        signal_item = self._fetch_signal(ticker)
        if signal_item is None or signal_item.get("error"):
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"no_signal_for_{ticker}: "
                    + str(signal_item.get("error") if signal_item else "missing"),
                    "snapshot": {"ticker": ticker},
                },
            }

        ml_signal = (signal_item.get("signal") or "").upper()
        if ml_signal not in ("BUY", "SELL"):
            return {
                "action": "HOLD",
                "payload": {
                    "reason": f"ml_signal_is_{ml_signal or 'none'}",
                    "snapshot": {"ticker": ticker, "ml_signal": ml_signal},
                },
            }

        # 2. Pegar bars recentes p/ ATR + vol
        bars = self._fetch_bars(ticker, max(vol_lookback, atr_period) + 5)
        if not bars or len(bars) < max(vol_lookback, atr_period) + 1:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"insufficient_bars ({len(bars) if bars else 0})",
                    "snapshot": {"ticker": ticker, "ml_signal": ml_signal},
                },
            }

        last_close = float(bars[-1].get("close", 0))
        if last_close <= 0:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": "zero_last_close",
                    "snapshot": {"ticker": ticker},
                },
            }

        # 3. Vol annual (lookback retornos diarios)
        closes = [float(b["close"]) for b in bars[-(vol_lookback + 1) :]]
        rets = [
            (closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)) if closes[i - 1] > 0
        ]
        vol_annual = annualize_vol(realized_vol_daily(rets))

        # 4. ATR + niveis
        atr = compute_atr(bars, period=atr_period)
        tp, sl = compute_atr_levels(
            entry=last_close,
            side=ml_signal,
            atr=atr,
            sl_mult=atr_sl_mult,
            tp_mult=atr_tp_mult,
        )

        # 5. Risk Engine sizing
        sl_distance = abs(last_close - sl) if sl else None
        sizing = position_size_vol_target(
            capital=capital,
            price=last_close,
            realized_vol_annual=vol_annual,
            target_vol=target_vol,
            kelly_fraction=kelly,
            max_position_pct=max_pct,
            sl_distance=sl_distance,
            lot_size=1,
        )

        if sizing.blocked:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"risk_blocked: {sizing.reason}",
                    "snapshot": {
                        "ticker": ticker,
                        "ml_signal": ml_signal,
                        "vol_annual": vol_annual,
                        "atr": atr,
                        "last_close": last_close,
                    },
                },
            }

        return {
            "action": ml_signal,
            "payload": {
                "quantity": sizing.qty,
                "price": None,  # market order
                "order_type": "market",
                "take_profit": tp,
                "stop_loss": sl,
                "is_daytrade": context.get("is_daytrade", True),
                "reason": f"ml_signal={ml_signal} sized via vol_target",
                "snapshot": {
                    "ticker": ticker,
                    "ml_signal": ml_signal,
                    "predicted_return_pct": signal_item.get("predicted_return_pct"),
                    "th_buy": signal_item.get("th_buy"),
                    "th_sell": signal_item.get("th_sell"),
                    "best_sharpe": signal_item.get("best_sharpe"),
                    "last_close": last_close,
                    "atr": atr,
                    "vol_annual": vol_annual,
                    "qty": sizing.qty,
                    "notional": sizing.notional,
                    "capital_at_risk": sizing.capital_at_risk,
                    "tp": tp,
                    "sl": sl,
                },
            },
        }

    # ── Helpers privados ─────────────────────────────────────────────────────

    def _fetch_signal(self, ticker: str) -> dict[str, Any] | None:
        """Busca sinal ML; cache TTL 60s. Retorna SignalItem ou None."""
        now = datetime.now(UTC)
        cache_stale = (
            self._cache_ts is None or (now - self._cache_ts).total_seconds() > self._cache_ttl_sec
        )
        if cache_stale:
            try:
                with httpx.Client(timeout=10.0) as client:
                    r = client.get(f"{self._base_url}/api/v1/ml/signals?limit=500")
                    r.raise_for_status()
                    data = r.json()
                self._signals_cache = {it["ticker"].upper(): it for it in data.get("items", [])}
                self._cache_ts = now
            except Exception as exc:
                logger.warning("ml_signals.cache_refresh_failed", error=str(exc))
                if not self._signals_cache:
                    return None  # sem cache stale tambem
        return self._signals_cache.get(ticker)

    def _fetch_bars(self, ticker: str, n: int) -> list[dict[str, Any]] | None:
        """Busca daily bars do /api/v1/marketdata/candles/{ticker} (range '3mo')."""
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self._base_url}/api/v1/marketdata/candles/{ticker}",
                    params={"range_period": "3mo"},
                )
                r.raise_for_status()
                data = r.json()
            bars = data.get("bars") or data.get("candles") or []
            return bars[-n:] if bars else None
        except Exception as exc:
            logger.warning("ml_signals.bars_fetch_failed", ticker=ticker, error=str(exc))
            return None

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

from datetime import UTC, datetime, timedelta
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
from finanalytics_ai.infrastructure.market_data.http_candle_fetcher import (
    HttpCandleFetcher,
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
        "min_sharpe_filter": 0.0,
        "lot_size": 100   # B3 stocks default (futures unitarios usam 1)
      }
    Smoke 04/mai descobriu: sem lot_size correto, broker B3 rejeita
    silenciosamente "Risco Simulador: Quantidade da ordem deve ser
    multiplo do lote" (PETR4 lote=100). position_size_vol_target ja
    arredonda; passar lot_size=100 garante qty correta.
    """

    name = "ml_signals"

    def __init__(
        self,
        base_url: str | None = None,
        candle_fetcher: HttpCandleFetcher | None = None,
    ) -> None:
        self._base_url = base_url or API_BASE_URL
        self._candle_fetcher = candle_fetcher or HttpCandleFetcher(self._base_url)
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
            lot_size=int(context.get("lot_size", 100)),
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
        """Busca sinal ML; cache TTL 60s. Retorna SignalItem ou None.

        Bug #1 (smoke 05/mai): httpx.Client SYNC bloqueando event loop async
        do auto_trader. Timeout original 10s × N tickers × M strategies =
        ciclo aparentemente travado por minutos. Reduzido pra 5s + retry
        anti-loop: se cache_refresh_failed, marca _cache_ts pra evitar tentar
        de novo nos proximos N tickers da mesma iteracao (cache stale window
        ainda continua 60s; logica de retry preservada).
        """
        now = datetime.now(UTC)
        cache_stale = (
            self._cache_ts is None or (now - self._cache_ts).total_seconds() > self._cache_ttl_sec
        )
        if cache_stale:
            try:
                # Smoke 05/mai: 5s era marginal (API ~4.5s p99). Bump pra 15s
                # pra dar margem na primeira chamada (cold cache). Cache TTL 60s
                # significa que so' a 1a request por minuto paga esse tempo.
                with httpx.Client(timeout=15.0) as client:
                    r = client.get(f"{self._base_url}/api/v1/ml/signals?limit=500")
                    r.raise_for_status()
                    data = r.json()
                self._signals_cache = {it["ticker"].upper(): it for it in data.get("items", [])}
                self._cache_ts = now
            except Exception as exc:
                logger.warning("ml_signals.cache_refresh_failed", error=str(exc))
                # Mesmo em failure, atualizar _cache_ts pra evitar bombardear
                # o endpoint nos proximos tickers da iter atual (cooldown 30s).
                # Sem isso, 1 timeout 5s vira 30s × N_tickers × M_strategies
                # de slow path quase invisivel ate proximo heartbeat (10 iters).
                self._cache_ts = now - timedelta(seconds=self._cache_ttl_sec - 30)
                if not self._signals_cache:
                    return None  # sem cache stale tambem
        return self._signals_cache.get(ticker)

    def _fetch_bars(
        self, ticker: str, n: int, range_period: str = "3mo"
    ) -> list[dict[str, Any]] | None:
        """Busca daily bars via HttpCandleFetcher (DRY com worker pairs flow).

        Usa endpoint `/candles_daily` (UNION profit_daily_bars + ohlc_1m +
        fintz_cotacoes_ts com dedup) para garantir cobertura de lookbacks
        longos (>3mo). O parâmetro `range_period` é mantido para compat
        retro mas não afeta a query (o endpoint daily ignora).
        """
        return self._candle_fetcher.fetch_daily_bars(ticker, n=n)


# ── TSMOM ∩ ML Overlay (R2) ──────────────────────────────────────────────────


class TsmomMlOverlayStrategy(MLSignalsStrategy):
    """
    Time-Series Momentum overlay sobre MLSignals (Moskowitz/Ooi/Pedersen 2012).

    Filtro de regime: usa o sinal ML como base e SO trada quando o momentum
    de 252 dias uteis concorda com a direcao. Reduz whipsaws em mean-reverting
    regimes onde ML solo costuma errar.

    Logica:
      mom_sign = sign(close_today / close_252d_ago - 1)
      BUY ML  + mom_sign >= 0 -> trada (full size do MLSignals.sizing)
      SELL ML + mom_sign <= 0 -> trada
      ML e momentum divergem  -> SKIP com reason

    Usa range_period='1y' (~252 bars) para puxar tudo numa chamada e roda o
    pipeline herdado de MLSignals em cima do mesmo dataset.

    Edge documentado: TSMOM Sharpe 0.7-1.2 cross-asset (Moskowitz 2012);
    replicado em B3 (Hosp Brasil 2018). Sobreposicao com ML reduz drawdown.

    Config esperado em robot_strategies.config_json: idem MLSignalsStrategy +
      "momentum_lookback_days": 252  # default Moskowitz
    """

    name = "tsmom_ml_overlay"

    def evaluate(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        ticker = ticker.upper()
        momentum_lookback = int(context.get("momentum_lookback_days", 252))

        # 1. Sinal ML
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

        # 2. Bars 1y para momentum + sizing num so fetch
        atr_period = int(context.get("atr_period", 14))
        vol_lookback = int(context.get("vol_lookback_days", 20))
        n_needed = max(momentum_lookback + 5, vol_lookback + 5, atr_period + 5)
        bars = self._fetch_bars(ticker, n=n_needed, range_period="1y")
        if not bars or len(bars) < momentum_lookback + 1:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"insufficient_bars_for_momentum "
                    f"({len(bars) if bars else 0} < {momentum_lookback + 1})",
                    "snapshot": {"ticker": ticker, "ml_signal": ml_signal},
                },
            }

        # 3. Sign do retorno 252d
        close_today = float(bars[-1].get("close", 0) or 0)
        close_lookback = float(bars[-(momentum_lookback + 1)].get("close", 0) or 0)
        if close_today <= 0 or close_lookback <= 0:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": "zero_close_in_momentum_window",
                    "snapshot": {"ticker": ticker, "ml_signal": ml_signal},
                },
            }
        momentum_ret = (close_today / close_lookback) - 1.0
        # Sign zero (raro, exato 0%) trata como neutro -> nao concorda nem com BUY nem SELL.
        if momentum_ret > 0:
            mom_sign = 1
        elif momentum_ret < 0:
            mom_sign = -1
        else:
            mom_sign = 0

        # 4. Concordance check
        disagree = (ml_signal == "BUY" and mom_sign <= 0) or (ml_signal == "SELL" and mom_sign >= 0)
        if disagree:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"tsmom_disagree: ml={ml_signal} mom={momentum_ret:+.2%}",
                    "snapshot": {
                        "ticker": ticker,
                        "ml_signal": ml_signal,
                        "momentum_252d_ret": momentum_ret,
                        "momentum_sign": mom_sign,
                        "lookback_days": momentum_lookback,
                    },
                },
            }

        # 5. Concordam: roda sizing + ATR levels (mesma logica MLSignals) e
        # enriquece payload com info do momentum.
        capital = float(context.get("capital_per_strategy", 10_000.0))
        target_vol = float(context.get("target_vol_annual", DEFAULT_TARGET_VOL))
        kelly = float(context.get("kelly_fraction", DEFAULT_KELLY_FRACTION))
        max_pct = float(context.get("max_position_pct", 0.10))
        atr_sl_mult = float(context.get("atr_sl_mult", 2.0))
        atr_tp_mult = float(context.get("atr_tp_mult", 3.0))

        closes = [float(b["close"]) for b in bars[-(vol_lookback + 1) :]]
        rets = [
            (closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)) if closes[i - 1] > 0
        ]
        vol_annual = annualize_vol(realized_vol_daily(rets))

        atr = compute_atr(bars, period=atr_period)
        tp, sl = compute_atr_levels(
            entry=close_today,
            side=ml_signal,
            atr=atr,
            sl_mult=atr_sl_mult,
            tp_mult=atr_tp_mult,
        )

        sl_distance = abs(close_today - sl) if sl else None
        sizing = position_size_vol_target(
            capital=capital,
            price=close_today,
            realized_vol_annual=vol_annual,
            target_vol=target_vol,
            kelly_fraction=kelly,
            max_position_pct=max_pct,
            sl_distance=sl_distance,
            lot_size=int(context.get("lot_size", 100)),
        )

        if sizing.blocked:
            return {
                "action": "SKIP",
                "payload": {
                    "reason": f"risk_blocked: {sizing.reason}",
                    "snapshot": {
                        "ticker": ticker,
                        "ml_signal": ml_signal,
                        "momentum_252d_ret": momentum_ret,
                        "vol_annual": vol_annual,
                        "atr": atr,
                        "last_close": close_today,
                    },
                },
            }

        return {
            "action": ml_signal,
            "payload": {
                "quantity": sizing.qty,
                "price": None,
                "order_type": "market",
                "take_profit": tp,
                "stop_loss": sl,
                "is_daytrade": context.get("is_daytrade", True),
                "reason": f"ml+tsmom concordant: {ml_signal} mom={momentum_ret:+.2%}",
                "snapshot": {
                    "ticker": ticker,
                    "ml_signal": ml_signal,
                    "momentum_252d_ret": momentum_ret,
                    "momentum_sign": mom_sign,
                    "momentum_lookback_days": momentum_lookback,
                    "predicted_return_pct": signal_item.get("predicted_return_pct"),
                    "th_buy": signal_item.get("th_buy"),
                    "th_sell": signal_item.get("th_sell"),
                    "best_sharpe": signal_item.get("best_sharpe"),
                    "last_close": close_today,
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


# ── R4 ORB WINFUT + filtro DI1 (fundação 03/mai) ──────────────────────────────
#
# Opening Range Breakout em mini-índice (WINFUT). Defer 7-10d na primeira
# proposta; scaffold criado pra ser preenchido com lógica completa quando
# user destravar a sprint R4. Hoje serve como esqueleto testável.
#
# Plano de implementação (não-MVP):
#   1. Define janela "open range" (default: 1ª 30min do pregão B3, 10:00-10:30 BRT).
#      Computa OR_high = max(high de bars 1m em [open, open+30min]) e
#      OR_low = min(low). Tipicamente 1m bars de WINFUT.
#   2. Após open+30min, monitor próximo break:
#        BUY se close 1m > OR_high (com filtro)
#        SELL se close 1m < OR_low (com filtro)
#   3. Filtro DI1 (trade só quando regime macro consistente):
#        Long ONLY se DI1 1y/5y slope positivo (curva em steepening = expansão
#        macroeconômica → favoráveis pra ações/equities futuros).
#        Short ONLY se slope negativo (curva em flattening/inversion → contração).
#        Source: rates_features_daily.slope_1y_5y do dia anterior.
#   4. Stops: ATR-based (ex: 1.5×ATR_14 1m); TP 2×ATR ou trailing.
#   5. Close mandatório no end-of-day (16:30 BRT) — DayTrade pure.
#
# Pré-requisitos pra implementação completa:
#   - Bars 1m WINFUT dispoíveis em ohlc_1m (já temos via tick_to_ohlc backfill).
#   - rates_features_daily.slope_1y_5y populado (já automatizado via
#     yield_curves_refresh_job).
#   - Ciclo do auto_trader_worker rodando intraday (hoje roda 60s default; pode
#     baixar pra 30s pra reagir mais rápido a breakouts).


class ORBStrategy:
    """Opening Range Breakout em WINFUT com filtro DI1.

    SCAFFOLD — lógica completa defer 7-10d. Hoje retorna SKIP com reason
    indicando que strategy ainda não foi implementada. Estrutura pronta pra
    ser preenchida quando R4 for ativado.

    Config esperado em robot_strategies.config_json:
        {
          "ticker": "WINFUT",                    # alias resolvido pra contrato vigente
          "or_window_minutes": 30,               # janela do opening range
          "session_start_brt": "10:00",          # abertura B3
          "session_end_brt": "16:30",            # close mandatório DayTrade
          "atr_period": 14,                      # ATR pra stops
          "atr_stop_mult": 1.5,                  # stop = entry +/- mult*ATR
          "atr_tp_mult": 2.0,                    # take_profit
          "di1_filter": true,                    # exige slope 1y_5y consistente
          "di1_slope_threshold": 0.001,          # |slope| min pra trade
          "max_position_pct": 0.10,              # do capital_per_strategy
          "capital_per_strategy": 50000          # WINFUT margem inicial ~R$50k/contrato
        }
    """

    name = "orb_winfut_di1"

    def __init__(self) -> None:
        # Reusa fetcher daily pra rates_features_daily (R4 vai precisar bars 1m
        # de outro endpoint quando lógica entrar)
        self._candle_fetcher: HttpCandleFetcher | None = None
        self._base_url = API_BASE_URL

    def evaluate(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        """SCAFFOLD — retorna SKIP. Implementação completa defer.

        Quando ativado, vai computar:
          1. OR_high/low dos primeiros 30min
          2. Aguardar close 1m fora do range
          3. Validar filtro DI1 (slope 1y_5y consistente com direção)
          4. Computar size via Risk Engine (Kelly + vol target)
          5. Retornar BUY/SELL com TP+SL ATR-based + is_daytrade=True
        """
        ticker_u = ticker.upper()
        return {
            "action": "SKIP",
            "payload": {
                "reason": "orb_strategy_not_implemented_yet (R4 defer 7-10d)",
                "snapshot": {
                    "ticker": ticker_u,
                    "config": context,
                    "scaffold_status": "ready_for_implementation",
                },
            },
        }

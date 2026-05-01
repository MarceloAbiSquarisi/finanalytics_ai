#!/usr/bin/env python3
"""
auto_trader_worker — Robo de trade autonomo (R1 MVP scaffold).

90% da infra existe: sinais ML calibrados, OCO multi-level com trailing,
GTD enforcement, flatten_ticker, prometheus, alertas. Este worker liga o
elo final: sinal -> ordem (com risk gate + kill switch + audit log).

Estado atual: SCAFFOLD. Estrategias dummy "log only" — NUNCA envia ordem
real ao DLL. Habilitar trade real exige implementacao explicita em
StrategyRegistry e flag env AUTO_TRADER_DRY_RUN=false.

Arquitetura:

  main loop (asyncio, configuravel via SCHEDULE_INTERVAL_SEC)
    1. tick: chk kill switch (robot_risk_state.paused)
    2. para cada Strategy enabled em robot_strategies:
        a. evaluate(context) -> Action (BUY/SELL/HOLD/SKIP)
        b. log em robot_signals_log (sempre, mesmo em SKIP)
        c. se BUY/SELL e DRY_RUN=false: risk check + POST /agent/order/send
        d. log em robot_orders_intent + UPDATE signal com local_order_id
    3. heartbeat em robot_signals_log (debug "worker vivo")

Config env:
  AUTO_TRADER_ENABLED       — gating do worker inteiro (default false; CI safe)
  AUTO_TRADER_DRY_RUN       — true (default) = nunca envia ordem real
  SCHEDULE_INTERVAL_SEC     — periodicidade do loop (default 60)
  PROFIT_AGENT_URL          — base URL do agent (default http://host.docker.internal:8002)
  PROFIT_TIMESCALE_DSN      — timescale DSN (psycopg2 style)

Kill switch:
  robot_risk_state.paused = true bloqueia novas entradas IMEDIATAMENTE.
  Posicoes abertas NAO sao zeradas (responsabilidade de OCO/SL existentes).
  Ops sobe via PUT /api/v1/robot/resume (sudo).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
import json
import os
import signal
import sys
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

# ── Config via env ────────────────────────────────────────────────────────────

ENABLED = os.environ.get("AUTO_TRADER_ENABLED", "false").lower() == "true"
DRY_RUN = os.environ.get("AUTO_TRADER_DRY_RUN", "true").lower() == "true"
INTERVAL_SEC = int(os.environ.get("SCHEDULE_INTERVAL_SEC", "60"))
AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
# Phase 2: dispatcher fala com o proxy FastAPI (NAO direto com o agent), pra
# usar AccountService injection automatico de _account_broker_id/account_id.
API_BASE_URL = os.environ.get("AUTO_TRADER_API_URL", "http://api:8000")
TRADE_ENV = os.environ.get("AUTO_TRADER_TRADE_ENV", "simulation")  # simulation|production
DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@finanalytics_timescale:5432/market_data",
)

# Heartbeat a cada N iteracoes (debug "worker vivo" no signals_log)
HEARTBEAT_EVERY = int(os.environ.get("AUTO_TRADER_HEARTBEAT_EVERY", "5"))

# ── Pairs trading (R3.2.B.2) ─────────────────────────────────────────────────
PAIRS_ENABLED = os.environ.get("PAIRS_TRADING_ENABLED", "false").lower() == "true"
# Capital alocado por pair trade (split em 2 legs aproximadamente iguais).
PAIRS_CAPITAL_PER_PAIR = float(os.environ.get("PAIRS_CAPITAL_PER_PAIR", "10000"))
# Lookback de testes feitos no ultimo screening (Bonferroni alpha_eff).
# Default 28 = combinacoes de 8 tickers da watchlist do scripts/cointegration_screen.
PAIRS_N_TESTED = int(os.environ.get("PAIRS_N_TESTED", "28"))


# ── Tipos / Protocol ──────────────────────────────────────────────────────────


class Action:
    """Decisao de uma Strategy. Usar string-enum manual p/ persistir direto em TEXT."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SKIP = "SKIP"  # razao em payload.reason


class Strategy(Protocol):
    """Plugin protocol — implementacao em domain/robot/strategies/."""

    name: str

    def evaluate(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Retorna dict {action: BUY|SELL|HOLD|SKIP, payload: {...}}.

        payload deve incluir motivo quando SKIP, params da ordem (qty, price,
        TP, SL) quando BUY/SELL, e snapshot do contexto (preco, sinal_ml, etc.)
        para auditoria.
        """
        ...


# ── Strategy implementations ─────────────────────────────────────────────────
#
# Implementacoes vivem em domain/robot/strategies.py — testaveis em isolamento.

from finanalytics_ai.domain.robot.strategies import (
    DummyHeartbeatStrategy,
    MLSignalsStrategy,
    TsmomMlOverlayStrategy,
)

# Registry — adicionar R3/R4 aqui quando chegarem.
STRATEGY_REGISTRY: dict[str, Strategy] = {
    "dummy_heartbeat": DummyHeartbeatStrategy(),
    "ml_signals": MLSignalsStrategy(),
    "tsmom_ml_overlay": TsmomMlOverlayStrategy(),
}


# ── DB helpers (psycopg2 sync — worker tem 1 thread) ──────────────────────────


def _get_conn():
    """Conexao psycopg2 nova a cada uso. Pool seria over-engineering p/ 1 worker."""
    import psycopg2

    return psycopg2.connect(DSN)


def is_paused() -> tuple[bool, str | None]:
    """Le kill switch da row de hoje em robot_risk_state. Default false se nao existe."""
    today = date.today()
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT paused, paused_reason FROM robot_risk_state WHERE date = %s",
                (today,),
            )
            row = cur.fetchone()
            if row is None:
                return (False, None)
            return (bool(row[0]), row[1])
    except Exception as exc:
        logger.warning("auto_trader.kill_switch_read_failed", error=str(exc))
        return (False, None)  # fail-open p/ heartbeat continuar; trade real chk again


def fetch_enabled_strategies() -> list[dict[str, Any]]:
    """Le robot_strategies WHERE enabled=true. Retorna list de dicts."""
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, config_json, account_id "
                "FROM robot_strategies WHERE enabled = TRUE ORDER BY id"
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "config": r[2] or {},
                    "account_id": r[3],
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("auto_trader.strategies_read_failed", error=str(exc))
        return []


def log_signal(
    *,
    strategy_id: int | None,
    strategy_name: str | None,
    ticker: str | None,
    action: str,
    sent_to_dll: bool = False,
    local_order_id: int | None = None,
    reason_skipped: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int | None:
    """INSERT em robot_signals_log. Retorna id ou None em erro."""
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO robot_signals_log
                (strategy_id, strategy_name, ticker, action, sent_to_dll,
                 local_order_id, reason_skipped, payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    strategy_id,
                    strategy_name,
                    ticker,
                    action,
                    sent_to_dll,
                    local_order_id,
                    reason_skipped,
                    json.dumps(payload) if payload else None,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as exc:
        logger.warning("auto_trader.signal_log_failed", error=str(exc))
        return None


# ── Main loop ────────────────────────────────────────────────────────────────


_shutdown = False


def _handle_signal(signum: int, frame: Any) -> None:
    global _shutdown
    logger.info("auto_trader.shutdown_requested", signum=signum)
    _shutdown = True


async def _evaluate_strategies(iteration: int) -> None:
    """Roda 1 iteracao do strategy loop."""
    paused, reason = is_paused()
    if paused:
        logger.info("auto_trader.paused", reason=reason)
        # Ainda heartbeat para "worker vivo + paused" — auditoria
        if iteration % HEARTBEAT_EVERY == 0:
            log_signal(
                strategy_id=None,
                strategy_name=None,
                ticker=None,
                action="HEARTBEAT",
                reason_skipped=f"paused: {reason or 'manual'}",
                payload={"iteration": iteration},
            )
        return

    enabled = fetch_enabled_strategies()
    if not enabled:
        if iteration % HEARTBEAT_EVERY == 0:
            log_signal(
                strategy_id=None,
                strategy_name=None,
                ticker=None,
                action="HEARTBEAT",
                reason_skipped="no_strategies_enabled",
                payload={"iteration": iteration, "dry_run": DRY_RUN},
            )
        return

    for strat_row in enabled:
        impl = STRATEGY_REGISTRY.get(strat_row["name"])
        if impl is None:
            log_signal(
                strategy_id=strat_row["id"],
                strategy_name=strat_row["name"],
                ticker=None,
                action=Action.SKIP,
                reason_skipped="strategy_impl_not_registered",
                payload={"strategy_db": strat_row},
            )
            continue

        tickers = strat_row["config"].get("tickers", [])
        for ticker in tickers:
            try:
                result = impl.evaluate(ticker, strat_row["config"])
            except Exception as exc:
                logger.error(
                    "auto_trader.strategy_evaluate_failed",
                    strategy=strat_row["name"],
                    ticker=ticker,
                    error=str(exc),
                )
                log_signal(
                    strategy_id=strat_row["id"],
                    strategy_name=strat_row["name"],
                    ticker=ticker,
                    action=Action.SKIP,
                    reason_skipped=f"evaluate_exception: {exc}",
                )
                continue

            action = result.get("action", Action.SKIP)
            payload = result.get("payload", {})

            # Trade real bloqueado em DRY_RUN — log only com reason
            if action in (Action.BUY, Action.SELL) and DRY_RUN:
                log_signal(
                    strategy_id=strat_row["id"],
                    strategy_name=strat_row["name"],
                    ticker=ticker,
                    action=action,
                    sent_to_dll=False,
                    reason_skipped="dry_run_mode",
                    payload=payload,
                )
                continue

            # Phase 2: dispatch real para o proxy FastAPI -> profit_agent.
            # Strategy retorna em payload: quantity, price (None=market),
            # order_type, take_profit, stop_loss. Risk Engine ja foi chamado
            # pelo Strategy.evaluate antes (decisao composta).
            if action in (Action.BUY, Action.SELL):
                qty = payload.get("quantity")
                if not qty or qty <= 0:
                    log_signal(
                        strategy_id=strat_row["id"],
                        strategy_name=strat_row["name"],
                        ticker=ticker,
                        action=action,
                        sent_to_dll=False,
                        reason_skipped="missing_or_zero_quantity",
                        payload=payload,
                    )
                    continue

                # 1. Log signal PRIMEIRO p/ ter signal_log_id (FK do intent)
                signal_log_id = log_signal(
                    strategy_id=strat_row["id"],
                    strategy_name=strat_row["name"],
                    ticker=ticker,
                    action=action,
                    sent_to_dll=False,  # Updated apos dispatch
                    reason_skipped=None,
                    payload=payload,
                )
                if signal_log_id is None:
                    logger.error(
                        "auto_trader.signal_log_failed_skip_dispatch",
                        strategy=strat_row["name"],
                        ticker=ticker,
                    )
                    continue

                # 2. Dispatch via proxy FastAPI
                from finanalytics_ai.workers.auto_trader_dispatcher import dispatch_order

                try:
                    dispatch_result = await dispatch_order(
                        dsn=DSN,
                        base_url=API_BASE_URL,
                        signal_log_id=signal_log_id,
                        strategy_id=strat_row["id"],
                        ticker=ticker,
                        side=action.lower(),
                        quantity=int(qty),
                        price=payload.get("price"),
                        order_type=payload.get("order_type", "market"),
                        take_profit=payload.get("take_profit"),
                        stop_loss=payload.get("stop_loss"),
                        is_daytrade=payload.get("is_daytrade", True),
                        env=TRADE_ENV,
                    )
                    logger.info(
                        "auto_trader.dispatched",
                        ticker=ticker,
                        side=action,
                        ok=dispatch_result.get("ok"),
                        local_order_id=dispatch_result.get("local_order_id"),
                        cl_ord_id=dispatch_result.get("cl_ord_id"),
                    )
                except Exception as exc:
                    logger.error(
                        "auto_trader.dispatch_exception",
                        ticker=ticker,
                        error=str(exc),
                    )
                continue

            # HOLD/SKIP — log normal
            log_signal(
                strategy_id=strat_row["id"],
                strategy_name=strat_row["name"],
                ticker=ticker,
                action=action,
                sent_to_dll=False,
                reason_skipped=payload.get("reason"),
                payload=payload,
            )


# ── Pairs trading helpers (R3.2.B.2) ─────────────────────────────────────────
#
# Mantemos in-memory dict de posicoes — sobrevive vida do processo, NAO
# sobrevive restart. R3.3+ adicionara robot_pair_positions table p/ persistir.
# Pratica: smoke live -> validar -> tabela.

_pair_positions: dict[str, Any] = {}  # pair_key -> PairPosition


class _InMemoryPositionState:
    """Adapter para o PositionState Protocol."""

    def get(self, pair_key: str):
        from finanalytics_ai.domain.pairs import PairPosition

        return _pair_positions.get(pair_key, PairPosition.NONE)


class _HttpCandleFetcher:
    """CandleFetcher Protocol via /api/v1/marketdata/candles/{ticker}."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def fetch_closes(self, ticker: str, n: int) -> list[float] | None:
        import httpx

        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self._base_url}/api/v1/marketdata/candles/{ticker}",
                    params={"range_period": "1y"},
                )
                r.raise_for_status()
                data = r.json()
            bars = data.get("bars") or data.get("candles") or []
            closes = [float(b["close"]) for b in bars if b.get("close")]
            return closes[-n:] if closes else None
        except Exception as exc:
            logger.warning("pairs.candle_fetch_failed", ticker=ticker, error=str(exc))
            return None


def _compute_leg_quantities(*, capital: float, price_a: float, price_b: float) -> tuple[int, int]:
    """
    Aloca metade do capital em cada leg (dollar-neutral approx).
    qty_a = floor((capital/2) / price_a). Mesma logica p/ B.
    Retorna (0, 0) se inputs invalidos.
    """
    if capital <= 0 or price_a <= 0 or price_b <= 0:
        return (0, 0)
    half = capital / 2.0
    qty_a = int(half // price_a)
    qty_b = int(half // price_b)
    return (qty_a, qty_b)


async def _evaluate_pairs(iteration: int) -> None:
    """
    Avalia pares cointegrados ativos e dispara dual-leg dispatch quando
    Z-score cruza thresholds. Apenas em pregao real (DRY_RUN=false) e
    PAIRS_ENABLED=true.
    """
    if not PAIRS_ENABLED:
        return

    # Imports diferidos p/ nao impactar boot quando desabilitado
    from finanalytics_ai.application.services.pairs_trading_service import (
        evaluate_active_pairs,
    )
    from finanalytics_ai.domain.pairs import PairAction, PairPosition
    from finanalytics_ai.infrastructure.database.repositories.pairs_repository import (
        PsycopgPairsRepository,
    )
    from finanalytics_ai.workers.auto_trader_dispatcher import dispatch_pair_order

    repo = PsycopgPairsRepository(DSN)
    candles = _HttpCandleFetcher(API_BASE_URL)
    pos_state = _InMemoryPositionState()

    try:
        evaluations = evaluate_active_pairs(
            repo=repo,
            candles=candles,
            position_state=pos_state,
            n_pairs_tested=PAIRS_N_TESTED,
        )
    except Exception as exc:
        logger.error("pairs.evaluate_failed", error=str(exc))
        return

    if not evaluations:
        if iteration % HEARTBEAT_EVERY == 0:
            logger.info("pairs.no_active_pairs")
        return

    for ev in evaluations:
        # SEMPRE log a evaluation pra audit (mesmo NONE)
        logger.info(
            "pairs.evaluation",
            pair_key=ev.snapshot.get("pair_key"),
            action=ev.action.value,
            z=ev.z,
            reason=ev.reason,
            blocked=ev.blocked_by_filter,
        )

        if ev.action == PairAction.NONE:
            continue

        if DRY_RUN:
            logger.info("pairs.dry_run_skip_dispatch", action=ev.action.value)
            continue

        pair_key = ev.snapshot.get("pair_key", "")

        # Para CLOSE/STOP, leg sides invertem a posicao corrente
        if ev.action in (PairAction.CLOSE, PairAction.STOP):
            if ev.current_position == PairPosition.SHORT_SPREAD:
                # Era short A + long B; reverte: buy A + sell B
                side_a, side_b = "buy", "sell"
            elif ev.current_position == PairPosition.LONG_SPREAD:
                side_a, side_b = "sell", "buy"
            else:
                logger.warning(
                    "pairs.close_without_position",
                    pair_key=pair_key,
                    current_position=ev.current_position.value,
                )
                continue
        else:
            # OPEN_*: leg_a_side e leg_b_side ja vem populados pelo service
            side_a = ev.leg_a_side or ""
            side_b = ev.leg_b_side or ""
            if not side_a or not side_b:
                logger.error("pairs.open_missing_legs", pair_key=pair_key)
                continue

        # Position sizing precisa preco corrente — usa ultimo close das candles
        # (caching naive mas suficiente p/ MVP — orderbook real fica em R3.2.B.3+)
        closes_a = candles.fetch_closes(ev.pair.ticker_a, 1) or []
        closes_b = candles.fetch_closes(ev.pair.ticker_b, 1) or []
        if not closes_a or not closes_b:
            logger.error("pairs.dispatch_skip_missing_price", pair_key=pair_key)
            continue
        price_a, price_b = closes_a[-1], closes_b[-1]
        qty_a, qty_b = _compute_leg_quantities(
            capital=PAIRS_CAPITAL_PER_PAIR, price_a=price_a, price_b=price_b
        )
        if qty_a == 0 or qty_b == 0:
            logger.warning(
                "pairs.dispatch_skip_zero_qty",
                pair_key=pair_key,
                qty_a=qty_a,
                qty_b=qty_b,
            )
            continue

        # Dispatch dual-leg
        try:
            result = await dispatch_pair_order(
                base_url=API_BASE_URL,
                pair_key=pair_key,
                ticker_a=ev.pair.ticker_a,
                side_a=side_a,
                quantity_a=qty_a,
                ticker_b=ev.pair.ticker_b,
                side_b=side_b,
                quantity_b=qty_b,
                action=ev.action.value,
                env=TRADE_ENV,
            )
        except Exception as exc:
            logger.error(
                "pairs.dispatch_exception",
                pair_key=pair_key,
                error=str(exc),
            )
            continue

        if result.get("naked_leg"):
            logger.error(
                "pairs.naked_leg_alert",
                pair_key=pair_key,
                naked_leg=result["naked_leg"],
                error=result.get("error"),
            )
            # Nao atualiza state — caller manual deve tratar
            continue

        if result.get("ok"):
            # Update in-memory position
            if ev.action == PairAction.OPEN_SHORT_SPREAD:
                _pair_positions[pair_key] = PairPosition.SHORT_SPREAD
            elif ev.action == PairAction.OPEN_LONG_SPREAD:
                _pair_positions[pair_key] = PairPosition.LONG_SPREAD
            elif ev.action in (PairAction.CLOSE, PairAction.STOP):
                _pair_positions.pop(pair_key, None)
            logger.info(
                "pairs.dispatched",
                pair_key=pair_key,
                action=ev.action.value,
                new_position=_pair_positions.get(pair_key, PairPosition.NONE).value
                if hasattr(_pair_positions.get(pair_key, PairPosition.NONE), "value")
                else "NONE",
            )


async def main() -> int:
    if not ENABLED:
        logger.info("auto_trader.disabled_via_env")
        # Heartbeat unico p/ provar que container subiu, depois fica idle
        log_signal(
            strategy_id=None,
            strategy_name=None,
            ticker=None,
            action="HEARTBEAT",
            reason_skipped="AUTO_TRADER_ENABLED=false",
            payload={"booted_at": datetime.now(UTC).isoformat()},
        )
        # Sleep forever — container nao morre, mas tambem nao processa nada
        while not _shutdown:
            await asyncio.sleep(60)
        return 0

    logger.info(
        "auto_trader.starting",
        dry_run=DRY_RUN,
        interval=INTERVAL_SEC,
        agent_url=AGENT_URL,
    )

    iteration = 0
    while not _shutdown:
        iteration += 1
        try:
            await _evaluate_strategies(iteration)
        except Exception as exc:
            logger.error("auto_trader.loop_iteration_failed", error=str(exc))

        # Pairs trading flow (gated separadamente — pode rodar isolado das
        # strategies per-ticker pra debug)
        try:
            await _evaluate_pairs(iteration)
        except Exception as exc:
            logger.error("auto_trader.pairs_iteration_failed", error=str(exc))

        try:
            await asyncio.sleep(INTERVAL_SEC)
        except asyncio.CancelledError:
            break

    logger.info("auto_trader.stopped", total_iterations=iteration)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    sys.exit(asyncio.run(main()))

"""Rotas read-only para a UI do trading-engine.

Lê do schema `trading_engine_orders` via role `trading_engine_reader`.
Sem mutações — toda escrita fica no trading-engine. Auth obrigatório
(get_current_user).

GET /api/v1/trading-engine/orders          — orders 24h (paginadas)
GET /api/v1/trading-engine/trade-journal   — trades fechados + agregados
GET /api/v1/trading-engine/trade-journal/equity-curve — equity rolling
GET /api/v1/trading-engine/trade-journal/by-hour      — pnl/n_trades por hora BRT
GET /api/v1/trading-engine/trade-journal/by-setup     — pnl/win_rate por setup
GET /api/v1/trading-engine/engine-events   — audit feed (filtrável por tipo)
GET /api/v1/trading-engine/backtests       — runs persistidas
GET /api/v1/trading-engine/backtests/{id}  — detalhe + equity curve
GET /api/v1/trading-engine/strategies      — catálogo + agregados de uso
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.dependencies import (
    get_current_user,
    get_trading_engine_db_session,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/trading-engine", tags=["trading-engine"])


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


@router.get("/orders")
async def list_orders(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=1, le=1000),
    status_filter: str | None = Query(None, alias="status"),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Orders das últimas N horas (default 24h, máx 7d)."""
    where_status = "AND status = :status_filter" if status_filter else ""
    query = text(
        f"""
        SELECT id, broker_id, strategy, symbol, side, status,
               entry_price, stop_price, target_price, qty,
               filled_price, filled_qty, confidence, rationale,
               submitted_at, filled_at, created_at, updated_at
        FROM trading_engine_orders.orders
        WHERE submitted_at > NOW() - make_interval(hours => :hours)
        {where_status}
        ORDER BY submitted_at DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {"hours": hours, "limit": limit}
    if status_filter:
        params["status_filter"] = status_filter
    result = await session.execute(query, params)
    rows = [_row_to_dict(r) for r in result]
    return {"hours": hours, "count": len(rows), "orders": rows}


@router.get("/trade-journal")
async def list_trade_journal(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Trades fechados + agregados (PnL acumulado, hit rate, n_trades)."""
    agg_q = text(
        """
        SELECT
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE is_winner) AS n_wins,
            COALESCE(SUM(pnl), 0) AS pnl_total,
            COALESCE(AVG(pnl), 0) AS pnl_avg,
            COALESCE(AVG(pnl) FILTER (WHERE is_winner), 0) AS avg_win,
            COALESCE(AVG(pnl) FILTER (WHERE is_winner = false), 0) AS avg_loss,
            MIN(entry_date) AS first_entry,
            MAX(COALESCE(exit_date, entry_date)) AS last_exit
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND is_complete = true
        """
    )
    rows_q = text(
        """
        SELECT id, ticker, direction, entry_date, exit_date,
               entry_price, exit_price, quantity, pnl, pnl_pct,
               is_winner, setup, timeframe, external_order_id
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND is_complete = true
        ORDER BY COALESCE(exit_date, entry_date) DESC
        LIMIT :limit
        """
    )
    agg_row = (await session.execute(agg_q, {"days": days})).first()
    agg = _row_to_dict(agg_row) if agg_row else {}
    n_trades = agg.get("n_trades") or 0
    n_wins = agg.get("n_wins") or 0
    agg["hit_rate"] = (n_wins / n_trades) if n_trades else 0.0
    rows = [_row_to_dict(r) for r in await session.execute(rows_q, {"days": days, "limit": limit})]
    return {"days": days, "aggregates": agg, "trades": rows}


@router.get("/trade-journal/equity-curve")
async def trade_journal_equity_curve(
    days: int = Query(30, ge=1, le=365),
    strategy: str | None = Query(None, description="filtra por setup (= strategy name no engine)"),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Curva de equity (running sum de pnl) por trade fechado em ordem cronológica.

    Útil pra plotar em produção o equivalente do `pnl_curve` do BacktestReport.
    Apenas trades com `exit_date` e `pnl` preenchidos entram (i.e. roundtrip
    fechado). Retorna lista vazia com 0 trades (UI não quebra).
    """
    filters = [
        "entry_date > NOW() - make_interval(days => :days)",
        "exit_date IS NOT NULL",
        "pnl IS NOT NULL",
    ]
    params: dict[str, Any] = {"days": days}
    if strategy:
        filters.append("setup = :strategy")
        params["strategy"] = strategy
    where = "WHERE " + " AND ".join(filters)
    query = text(
        f"""
        SELECT
            exit_date AS ts,
            SUM(pnl) OVER (ORDER BY exit_date, id ROWS UNBOUNDED PRECEDING) AS equity,
            pnl,
            setup AS strategy
        FROM trading_engine_orders.trade_journal
        {where}
        ORDER BY exit_date, id
        """
    )
    rows = [_row_to_dict(r) for r in await session.execute(query, params)]
    return {"days": days, "strategy": strategy, "count": len(rows), "points": rows}


@router.get("/trade-journal/by-hour")
async def trade_journal_by_hour(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Distribuição de trades por hora BRT — n_trades, pnl_total, win_rate.

    Hora extraída de `entry_date` convertida para `America/Sao_Paulo`. Cobre
    o pregão B3 (10-17h BRT) com naturalidade. Hours sem trades não aparecem.
    """
    query = text(
        """
        SELECT
            EXTRACT(HOUR FROM (entry_date AT TIME ZONE 'America/Sao_Paulo'))::int AS hour_brt,
            COUNT(*)                                                 AS n_trades,
            COUNT(*) FILTER (WHERE is_winner)                        AS n_wins,
            COALESCE(SUM(pnl), 0)                                    AS pnl_total,
            COALESCE(AVG(pnl), 0)                                    AS pnl_avg
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND pnl IS NOT NULL
        GROUP BY hour_brt
        ORDER BY hour_brt
        """
    )
    rows = []
    for r in await session.execute(query, {"days": days}):
        d = _row_to_dict(r)
        n = d.get("n_trades") or 0
        w = d.get("n_wins") or 0
        d["win_rate"] = (w / n) if n else 0.0
        rows.append(d)
    return {"days": days, "count": len(rows), "buckets": rows}


@router.get("/trade-journal/by-setup")
async def trade_journal_by_setup(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Agregados por valor da coluna `setup` (= strategy name no engine).

    Útil pra comparar performance entre estratégias em produção. Retorna
    array ordenado por pnl_total DESC. Setups sem trades fechados não
    aparecem.
    """
    query = text(
        """
        SELECT
            setup,
            COUNT(*)                                          AS n_trades,
            COUNT(*) FILTER (WHERE is_winner)                 AS n_wins,
            COALESCE(SUM(pnl), 0)                             AS pnl_total,
            COALESCE(AVG(pnl), 0)                             AS pnl_avg,
            COALESCE(AVG(pnl) FILTER (WHERE is_winner), 0)    AS avg_win,
            COALESCE(AVG(pnl) FILTER (WHERE NOT is_winner), 0) AS avg_loss,
            MAX(COALESCE(exit_date, entry_date))              AS last_trade_at
        FROM trading_engine_orders.trade_journal
        WHERE entry_date > NOW() - make_interval(days => :days)
          AND pnl IS NOT NULL
          AND setup IS NOT NULL
        GROUP BY setup
        ORDER BY pnl_total DESC NULLS LAST
        """
    )
    rows = []
    for r in await session.execute(query, {"days": days}):
        d = _row_to_dict(r)
        n = d.get("n_trades") or 0
        w = d.get("n_wins") or 0
        d["win_rate"] = (w / n) if n else 0.0
        rows.append(d)
    return {"days": days, "count": len(rows), "setups": rows}


@router.get("/engine-events")
async def list_engine_events(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(500, ge=1, le=5000),
    event_type: str | None = Query(None),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Audit feed (event_type filtrável)."""
    where_type = "AND event_type = :event_type" if event_type else ""
    query = text(
        f"""
        SELECT ts, event_type, key, payload
        FROM trading_engine_orders.engine_events
        WHERE ts > NOW() - make_interval(hours => :hours)
        {where_type}
        ORDER BY ts DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {"hours": hours, "limit": limit}
    if event_type:
        params["event_type"] = event_type
    rows = [_row_to_dict(r) for r in await session.execute(query, params)]
    return {"hours": hours, "count": len(rows), "events": rows}


@router.get("/backtests")
async def list_backtests(
    limit: int = Query(50, ge=1, le=500),
    strategy: str | None = Query(None),
    symbol: str | None = Query(None),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Lista backtests persistidos (sem equity curve — leve)."""
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if strategy:
        filters.append("strategy = :strategy")
        params["strategy"] = strategy
    if symbol:
        filters.append("symbol = :symbol")
        params["symbol"] = symbol
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = text(
        f"""
        SELECT run_id, strategy, symbol, timeframe, from_ts, to_ts,
               git_sha, pnl_total, sharpe, calmar, max_drawdown,
               hit_rate, profit_factor, payoff, n_trades,
               duration_ms, created_at
        FROM trading_engine_orders.backtest_runs
        {where}
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    rows = [_row_to_dict(r) for r in await session.execute(query, params)]
    return {"count": len(rows), "backtests": rows}


@router.get("/strategies")
async def list_strategies(
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Catálogo de estratégias do engine + agregados de backtests."""
    query = text(
        """
        SELECT
            sc.name,
            sc.timeframe,
            sc.description,
            sc.params_json,
            sc.source_file,
            sc.git_sha,
            sc.updated_at,
            COALESCE(b.n_runs, 0) AS n_runs,
            b.last_run_at,
            b.avg_pnl,
            b.avg_hit_rate,
            b.avg_sharpe,
            b.total_trades
        FROM trading_engine_orders.strategies_catalog sc
        LEFT JOIN (
            SELECT
                strategy,
                COUNT(*)              AS n_runs,
                MAX(created_at)       AS last_run_at,
                AVG(pnl_total)        AS avg_pnl,
                AVG(hit_rate)         AS avg_hit_rate,
                AVG(sharpe)           AS avg_sharpe,
                SUM(n_trades)         AS total_trades
            FROM trading_engine_orders.backtest_runs
            GROUP BY strategy
        ) b ON b.strategy = sc.name
        ORDER BY sc.name
        """
    )
    rows = [_row_to_dict(r) for r in await session.execute(query)]
    return {"count": len(rows), "strategies": rows}


@router.get("/backtests/{run_id}")
async def get_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Detalhe completo de um backtest (inclui equity_curve + params).

    Extrai `metrics_advanced` do `params_json` e expõe top-level — o engine
    persiste `advanced` (DAYTRADE §3-4), `roc_auc` e `deflated_sharpe` lá
    como bucket interno (sem migration). UI plota direto.
    """
    import json

    query = text(
        """
        SELECT *
        FROM trading_engine_orders.backtest_runs
        WHERE run_id = :run_id
        """
    )
    row = (await session.execute(query, {"run_id": run_id})).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"backtest run_id={run_id} não encontrado",
        )
    out = _row_to_dict(row)
    # asyncpg/sqlalchemy podem retornar JSONB como str OU dict — normaliza.
    params = out.get("params_json")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}
    if isinstance(params, dict):
        metrics_adv = params.pop("metrics_advanced", None)
        out["params"] = params
        if isinstance(metrics_adv, dict):
            out["advanced"] = metrics_adv.get("advanced")
            out["roc_auc"] = metrics_adv.get("roc_auc")
            out["deflated_sharpe"] = metrics_adv.get("deflated_sharpe")
        else:
            out["advanced"] = None
            out["roc_auc"] = None
            out["deflated_sharpe"] = None
        out.pop("params_json", None)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Validation runs (DAYTRADE_EVALUATION §2.1) — Etapa B do plano UI
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/validation-runs")
async def list_validation_runs(
    strategy: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Lista validation runs (read-only via DB)."""
    where_clauses = []
    bind: dict[str, Any] = {"limit": limit}
    if strategy:
        where_clauses.append("strategy = :strategy")
        bind["strategy"] = strategy
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = text(f"""
        SELECT run_id, strategy, symbol, timeframe, status,
               overall_passes, promotion_target, duration_ms,
               created_at, completed_at, error_msg
        FROM trading_engine_orders.validation_runs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit
    """)  # noqa: S608 — placeholders nomeados
    rows = (await session.execute(query, bind)).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


@router.get("/validation-runs/{run_id}")
async def get_validation_run(
    run_id: str,
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Detalhe de validation run + verdict_json + markdown."""
    import json

    query = text("""
        SELECT *
        FROM trading_engine_orders.validation_runs
        WHERE run_id = :run_id
    """)
    row = (await session.execute(query, {"run_id": run_id})).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"validation run_id={run_id} não encontrado",
        )
    out = _row_to_dict(row)
    # JSONB normalize
    for key in ("sweep_factors", "verdict_json"):
        v = out.get(key)
        if isinstance(v, str):
            try:
                out[key] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                out[key] = None
    return out


@router.post("/validation-runs", status_code=status.HTTP_201_CREATED)
async def post_validation_run(
    payload: dict[str, Any],
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Proxy HTTP pro engine UI (que roda o pipeline inline + persiste).

    Este endpoint NÃO toca o DB diretamente — encaminha pro trading-engine
    que tem o orquestrador (`run_validation_async`) e devolve verdict +
    markdown. UI consome a resposta direto.

    Requer settings.trading_engine_url + trading_engine_auth_token.
    """
    import httpx

    from finanalytics_ai.config import get_settings

    settings = get_settings()
    if not settings.trading_engine_url or not settings.trading_engine_auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "trading_engine_url ou trading_engine_auth_token não configurados — "
                "set TRADING_ENGINE_URL + TRADING_ENGINE_AUTH_TOKEN no .env"
            ),
        )
    target = f"{settings.trading_engine_url.rstrip('/')}/api/v1/validation-runs"
    headers = {"Authorization": f"Bearer {settings.trading_engine_auth_token}"}

    # Timeout generoso — pipeline pode levar minutos (WFA + sweep)
    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=5.0)) as client:
        try:
            resp = await client.post(target, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("validation_proxy.network_error", target=target, err=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"engine inacessível: {exc}",
            ) from exc

    if resp.status_code >= 400:
        logger.warning(
            "validation_proxy.upstream_error",
            status=resp.status_code, body=resp.text[:500],
        )
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Execution quality (S3 do roadmap DAYTRADE) — read-only via DB
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/execution-quality/{strategy}/snapshots")
async def list_execution_quality_snapshots(
    strategy: str,
    days: int = Query(60, ge=1, le=365),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Snapshots de monitoring (rolling Sharpe/PF/DD/drift) populados pelo
    monitoring_worker (cron diário) em engine_metrics_daily."""
    import json
    query = text("""
        SELECT
            snapshot_date, strategy,
            rolling_sharpe_60d, rolling_pf_60d,
            current_drawdown_pct, historical_max_drawdown_pct,
            consecutive_days_negative_sharpe, consecutive_days_below_cdi,
            distribution_drift_p_value, slippage_realized_vs_modeled_ratio,
            fill_ratio, p50_latency_ms, p95_latency_ms,
            adverse_selection_ratio,
            live_pnl_60d, backtest_pnl_60d_mean, backtest_pnl_60d_std,
            alerts, created_at
        FROM trading_engine_orders.engine_metrics_daily
        WHERE strategy = :strategy
        ORDER BY snapshot_date DESC
        LIMIT :limit
    """)
    rows = (await session.execute(query, {"strategy": strategy, "limit": days})).fetchall()
    items = []
    for r in rows:
        d = _row_to_dict(r)
        # alerts vem JSONB → str ou list dependendo do driver
        if isinstance(d.get("alerts"), str):
            try:
                d["alerts"] = json.loads(d["alerts"])
            except (json.JSONDecodeError, TypeError):
                d["alerts"] = []
        items.append(d)
    return {"strategy": strategy, "n": len(items), "items": items}


@router.get("/execution-quality/{strategy}/recent-samples")
async def list_execution_quality_samples(
    strategy: str,
    limit: int = Query(500, ge=1, le=5000),
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Samples raw de execution_latency_samples pra timeline de latência/slippage."""
    query = text("""
        SELECT order_id, strategy, symbol, side,
               tick_observed_at, signal_generated_at, order_submitted_at,
               order_filled_at, decision_latency_ms, submission_latency_ms,
               confirmation_latency_ms, total_latency_ms,
               slippage_target_to_filled, created_at
        FROM trading_engine_orders.execution_latency_samples
        WHERE strategy = :strategy
          AND order_filled_at >= NOW() - (:days || ' days')::INTERVAL
        ORDER BY order_filled_at DESC NULLS LAST
        LIMIT :limit
    """)
    rows = (await session.execute(
        query, {"strategy": strategy, "days": days, "limit": limit},
    )).fetchall()
    return {
        "strategy": strategy,
        "days": days,
        "n": len(rows),
        "items": [_row_to_dict(r) for r in rows],
    }


@router.get("/execution-quality/{strategy}/aggregate")
async def get_execution_quality_aggregate(
    strategy: str,
    days: int = Query(60, ge=1, le=365),
    session: AsyncSession = Depends(get_trading_engine_db_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Aggregate p50/p95/p99 latência + slippage by hour. Calculado via SQL
    pra evitar pull de samples × N pro Python."""
    latency_q = text("""
        SELECT
            COUNT(*) AS n,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY total_latency_ms) AS p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms) AS p95,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY total_latency_ms) AS p99,
            MAX(total_latency_ms) AS max_ms,
            AVG(total_latency_ms) AS mean_ms
        FROM trading_engine_orders.execution_latency_samples
        WHERE strategy = :strategy
          AND order_filled_at >= NOW() - (:days || ' days')::INTERVAL
          AND total_latency_ms IS NOT NULL
    """)
    by_hour_q = text("""
        SELECT
            EXTRACT(HOUR FROM order_filled_at AT TIME ZONE 'America/Sao_Paulo')::int AS hour,
            COUNT(*) AS n,
            AVG(slippage_target_to_filled) AS mean_signed,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY slippage_target_to_filled) AS p95,
            MAX(slippage_target_to_filled) AS max_signed
        FROM trading_engine_orders.execution_latency_samples
        WHERE strategy = :strategy
          AND order_filled_at >= NOW() - (:days || ' days')::INTERVAL
          AND slippage_target_to_filled IS NOT NULL
        GROUP BY hour
        ORDER BY hour
    """)
    lat = (await session.execute(latency_q, {"strategy": strategy, "days": days})).first()
    by_hour = (await session.execute(by_hour_q, {"strategy": strategy, "days": days})).fetchall()
    return {
        "strategy": strategy,
        "days": days,
        "latency": _row_to_dict(lat) if lat else None,
        "slippage_by_hour": [_row_to_dict(r) for r in by_hour],
    }

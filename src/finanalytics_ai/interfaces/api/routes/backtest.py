"""
Rotas de backtesting.

GET  /api/v1/backtest/strategies          — lista estratégias disponíveis
POST /api/v1/backtest/run                 — executa backtest
GET  /api/v1/backtest/run                 — executa via query params (para o dashboard)

Design: aceita GET com query params para facilitar chamadas diretas do
frontend sem montar body JSON — UX mais simples no dashboard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.backtest_service import BacktestError, BacktestService
from finanalytics_ai.infrastructure.cache.dependencies import rate_limit

if TYPE_CHECKING:
    from finanalytics_ai.application.services.optimizer_service import OptimizerService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    ticker: str = Field(..., example="PETR4")
    strategy: str = Field("combined", example="rsi|macd|combined")
    range_period: str = Field("3mo", example="1mo|3mo|6mo|1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    # Parâmetros opcionais por estratégia
    rsi_period: int | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None
    macd_fast: int | None = None
    macd_slow: int | None = None
    macd_signal: int | None = None


def _build_strategy_params(req: BacktestRequest) -> dict[str, Any]:
    """Monta dict de parâmetros apenas com os campos fornecidos."""
    mapping: dict[str, Any] = {}
    if req.rsi_period is not None:
        mapping["period"] = req.rsi_period
    if req.rsi_oversold is not None:
        mapping["oversold"] = req.rsi_oversold
    if req.rsi_overbought is not None:
        mapping["overbought"] = req.rsi_overbought
    if req.macd_fast is not None:
        mapping["fast"] = req.macd_fast
    if req.macd_slow is not None:
        mapping["slow"] = req.macd_slow
    if req.macd_signal is not None:
        mapping["signal_period"] = req.macd_signal

    # Combined usa prefixo rsi_/macd_
    if req.strategy == "combined":
        combined: dict[str, Any] = {}
        if req.rsi_period is not None:
            combined["rsi_period"] = req.rsi_period
        if req.rsi_oversold is not None:
            combined["rsi_oversold"] = req.rsi_oversold
        if req.rsi_overbought is not None:
            combined["rsi_overbought"] = req.rsi_overbought
        if req.macd_fast is not None:
            combined["macd_fast"] = req.macd_fast
        if req.macd_slow is not None:
            combined["macd_slow"] = req.macd_slow
        if req.macd_signal is not None:
            combined["macd_signal"] = req.macd_signal
        return combined

    return mapping


def _get_service(request: Request) -> BacktestService:
    svc = getattr(request.app.state, "backtest_service", None)
    if svc is None:
        raise HTTPException(503, "BacktestService não inicializado")
    return svc


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/strategies")
async def list_strategies() -> dict[str, Any]:
    """Lista estratégias disponíveis com seus parâmetros padrão."""
    return {
        "strategies": [
            {
                "id": "rsi",
                "name": "RSI Reversal",
                "description": "Compra na saída de sobrevenda, vende na sobrecompra",
                "params": {
                    "period": {"default": 14, "type": "int", "min": 5, "max": 50},
                    "oversold": {"default": 30.0, "type": "float", "min": 10, "max": 45},
                    "overbought": {"default": 70.0, "type": "float", "min": 55, "max": 90},
                },
            },
            {
                "id": "macd",
                "name": "MACD Crossover",
                "description": "Cruzamento da linha MACD com a Signal line",
                "params": {
                    "fast": {"default": 12, "type": "int", "min": 3, "max": 50},
                    "slow": {"default": 26, "type": "int", "min": 10, "max": 100},
                    "signal_period": {"default": 9, "type": "int", "min": 3, "max": 20},
                },
            },
            {
                "id": "combined",
                "name": "RSI + MACD Combined",
                "description": "Confirmação dupla: BUY só quando ambos concordam",
                "params": {
                    "rsi_period": {"default": 14, "type": "int"},
                    "rsi_oversold": {"default": 30.0, "type": "float"},
                    "rsi_overbought": {"default": 70.0, "type": "float"},
                    "macd_fast": {"default": 12, "type": "int"},
                    "macd_slow": {"default": 26, "type": "int"},
                    "macd_signal": {"default": 9, "type": "int"},
                },
            },
        ]
    }


@router.post("/run")
async def run_backtest_post(
    body: BacktestRequest,
    request: Request,
) -> dict[str, Any]:
    """Executa backtest via POST JSON."""
    return await _execute(request, body)


@router.get("/run")
async def run_backtest_get(
    request: Request,
    ticker: str = Query(...),
    strategy: str = Query("combined"),
    range_period: str = Query("3mo"),
    initial_capital: float = Query(10_000.0),
    position_size: float = Query(1.0),
    commission_pct: float = Query(0.001),
    rsi_period: int | None = Query(None),
    rsi_oversold: float | None = Query(None),
    rsi_overbought: float | None = Query(None),
    macd_fast: int | None = Query(None),
    macd_slow: int | None = Query(None),
    macd_signal: int | None = Query(None),
) -> dict[str, Any]:
    """Executa backtest via GET query params (para o dashboard)."""
    body = BacktestRequest(
        ticker=ticker,
        strategy=strategy,
        range_period=range_period,
        initial_capital=initial_capital,
        position_size=position_size,
        commission_pct=commission_pct,
        rsi_period=rsi_period,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
    )
    return await _execute(request, body)


async def _execute(request: Request, body: BacktestRequest) -> dict[str, Any]:
    service = _get_service(request)
    try:
        result = await service.run(
            ticker=body.ticker.upper(),
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            strategy_params=_build_strategy_params(body) or None,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("backtest.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno ao executar backtest") from exc


# ── Optimize ──────────────────────────────────────────────────────────────────


class OptimizeRequest(BaseModel):
    ticker: str = Field(..., example="PETR4")
    strategy: str = Field("rsi", example="rsi|macd|combined|bollinger|ema_cross|momentum")
    range_period: str = Field("1y", example="6mo|1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    objective: str = Field("sharpe", example="sharpe|return|calmar|win_rate|profit_factor")
    top_n: int = Field(10, ge=1, le=20)


def _get_optimizer(request: Request) -> OptimizerService:
    svc = getattr(request.app.state, "optimizer_service", None)
    if svc is None:
        raise HTTPException(503, "OptimizerService nao inicializado")
    return svc


@router.post("/optimize")
async def optimize_strategy(
    body: OptimizeRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=5, window=60)),
) -> dict[str, Any]:
    """
    Otimiza parametros de uma estrategia via grid search.

    Executa todos os parametros no espaco predefinido e retorna
    os top_n melhores de acordo com o objetivo escolhido.

    Objetivos: sharpe | return | calmar | win_rate | profit_factor
    """
    svc = _get_optimizer(request)
    try:
        result = await svc.optimize(
            ticker=body.ticker.upper(),
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            top_n=body.top_n,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("optimize.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na otimizacao") from exc


@router.get("/optimize")
async def optimize_strategy_get(
    request: Request,
    ticker: str = Query(...),
    strategy: str = Query("rsi"),
    range_period: str = Query("1y"),
    initial_capital: float = Query(10_000.0),
    position_size: float = Query(1.0),
    commission_pct: float = Query(0.001),
    objective: str = Query("sharpe"),
    top_n: int = Query(10),
) -> dict[str, Any]:
    """Otimiza via GET query params."""
    body = OptimizeRequest(
        ticker=ticker,
        strategy=strategy,
        range_period=range_period,
        initial_capital=initial_capital,
        position_size=position_size,
        commission_pct=commission_pct,
        objective=objective,
        top_n=top_n,
    )
    svc = _get_optimizer(request)
    try:
        result = await svc.optimize(
            ticker=body.ticker.upper(),
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            top_n=body.top_n,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("optimize.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na otimizacao") from exc


# ── Walk-Forward ──────────────────────────────────────────────────────────────


class WalkForwardRequest(BaseModel):
    ticker: str = Field(..., example="PETR4")
    strategy: str = Field("rsi", example="rsi|macd|combined|bollinger|ema_cross|momentum")
    range_period: str = Field("2y", example="1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    objective: str = Field("sharpe", example="sharpe|return|calmar|win_rate|profit_factor")
    n_splits: int = Field(3, ge=2, le=6)
    oos_pct: float = Field(0.3, ge=0.1, le=0.5)
    anchored: bool = Field(False)


def _get_walkforward(request: Request):  # type: ignore[return]
    svc = getattr(request.app.state, "walkforward_service", None)
    if svc is None:
        raise HTTPException(503, "WalkForwardService nao inicializado")
    return svc


@router.post("/walkforward")
async def run_walkforward(
    body: WalkForwardRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=5, window=60)),
) -> dict[str, Any]:
    """
    Executa walk-forward validation para detectar overfitting.

    Divide os dados em n_splits folds. Para cada fold:
      - IN-SAMPLE:  otimiza parametros via grid search
      - OUT-OF-SAMPLE: valida com os melhores parametros encontrados

    Retorna metricas de robustez: efficiency_ratio, consistency, degradation.
    """
    svc = _get_walkforward(request)
    try:
        result = await svc.run(
            ticker=body.ticker.upper(),
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            n_splits=body.n_splits,
            oos_pct=body.oos_pct,
            anchored=body.anchored,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("walkforward.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no walk-forward") from exc


@router.get("/walkforward")
async def run_walkforward_get(
    request: Request,
    ticker: str = Query(...),
    strategy: str = Query("rsi"),
    range_period: str = Query("2y"),
    initial_capital: float = Query(10_000.0),
    position_size: float = Query(1.0),
    commission_pct: float = Query(0.001),
    objective: str = Query("sharpe"),
    n_splits: int = Query(3),
    oos_pct: float = Query(0.3),
    anchored: bool = Query(False),
) -> dict[str, Any]:
    """Walk-forward via GET query params."""
    body = WalkForwardRequest(
        ticker=ticker,
        strategy=strategy,
        range_period=range_period,
        initial_capital=initial_capital,
        position_size=position_size,
        commission_pct=commission_pct,
        objective=objective,
        n_splits=n_splits,
        oos_pct=oos_pct,
        anchored=anchored,
    )
    svc = _get_walkforward(request)
    try:
        result = await svc.run(
            ticker=body.ticker.upper(),
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            n_splits=body.n_splits,
            oos_pct=body.oos_pct,
            anchored=body.anchored,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("walkforward.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no walk-forward") from exc


# ── Multi-Ticker Compare ───────────────────────────────────────────────────────

from finanalytics_ai.domain.backtesting.multi_ticker import MAX_TICKERS

if TYPE_CHECKING:
    from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService


class MultiTickerRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=MAX_TICKERS)
    strategy: str = Field("rsi")
    range_period: str = Field("1y")
    initial_capital: float = Field(10_000.0, gt=0)
    position_size: float = Field(1.0, gt=0, le=1.0)
    commission_pct: float = Field(0.001, ge=0, le=0.05)
    objective: str = Field("sharpe")
    top_n: int = Field(5, ge=1, le=10)


def _get_multi_ticker(request: Request) -> MultiTickerService:
    svc = getattr(request.app.state, "multi_ticker_service", None)
    if svc is None:
        raise HTTPException(503, "MultiTickerService nao inicializado")
    return svc


@router.post("/multi")
async def compare_multi_ticker(
    body: MultiTickerRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=5, window=60)),
) -> dict[str, Any]:
    """
    Compara a mesma estrategia em multiplos tickers via grid search.

    Executa a otimizacao em paralelo (max 3 requests simultaneas a BRAPI)
    e retorna ranking consolidado com metricas de consistencia.

    Max tickers: 10. Timeout estimado: ~2s por ticker.
    """
    svc = _get_multi_ticker(request)
    try:
        result = await svc.compare(
            tickers=body.tickers,
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            top_n=body.top_n,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("multi_ticker.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no comparativo multi-ticker") from exc


@router.get("/multi")
async def compare_multi_ticker_get(
    request: Request,
    tickers: str = Query(..., description="Tickers separados por virgula: PETR4,VALE3,ITUB4"),
    strategy: str = Query("rsi"),
    range_period: str = Query("1y"),
    initial_capital: float = Query(10_000.0),
    position_size: float = Query(1.0),
    commission_pct: float = Query(0.001),
    objective: str = Query("sharpe"),
    top_n: int = Query(5),
) -> dict[str, Any]:
    """Compara via GET — tickers como string separada por virgula."""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    body = MultiTickerRequest(
        tickers=ticker_list,
        strategy=strategy,
        range_period=range_period,
        initial_capital=initial_capital,
        position_size=position_size,
        commission_pct=commission_pct,
        objective=objective,
        top_n=top_n,
    )
    svc = _get_multi_ticker(request)
    try:
        result = await svc.compare(
            tickers=body.tickers,
            strategy_name=body.strategy,
            range_period=body.range_period,
            initial_capital=body.initial_capital,
            position_size=body.position_size,
            commission_pct=body.commission_pct,
            objective=body.objective,
            top_n=body.top_n,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("multi_ticker.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no comparativo multi-ticker") from exc

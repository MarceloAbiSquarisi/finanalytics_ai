"""
Rotas de backtesting.

GET  /api/v1/backtest/strategies          — lista estratégias disponíveis
POST /api/v1/backtest/run                 — executa backtest
GET  /api/v1/backtest/run                 — executa via query params (para o dashboard)

Design: aceita GET com query params para facilitar chamadas diretas do
frontend sem montar body JSON — UX mais simples no dashboard.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.backtest_service import BacktestError, BacktestService
from finanalytics_ai.infrastructure.cache.dependencies import rate_limit
from finanalytics_ai.application.services.optimizer_service import OptimizerService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])

# ── Schemas ───────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    ticker: str = Field(..., description="PETR4")
    strategy: str = Field("combined", description="rsi|macd|combined|pin_bar|setup_91|...")
    range_period: str = Field("3mo", description="1mo|3mo|6mo|1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    # Parâmetros legados (retrocompatibilidade)
    rsi_period: int | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None
    macd_fast: int | None = None
    macd_slow: int | None = None
    macd_signal: int | None = None
    # Parâmetros genéricos: JSON string com dict de params da estratégia
    # ex: '{"wick_ratio":0.65,"trend_filter":true}'
    strategy_params_json: str | None = Field(None, description="JSON dict de params")

def _build_strategy_params(req: BacktestRequest) -> dict[str, Any]:
    """
    Monta dict de parâmetros da estratégia.

    Prioridade:
      1. strategy_params_json (parâmetros genéricos via JSON)
      2. Campos legados rsi_period / macd_fast etc. (retrocompatibilidade)
    """
    import json

    # Parâmetros genéricos via JSON (novas estratégias)
    if req.strategy_params_json:
        try:
            return json.loads(req.strategy_params_json)
        except (json.JSONDecodeError, ValueError):
            pass  # Fallback para campos legados

    # Campos legados (retrocompatibilidade RSI/MACD/Combined)
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
    """Lista todas as 19 estratégias disponíveis com parâmetros padrão."""
    return {
        "strategies": [
            # ── Osciladores ──────────────────────────────────────────────────
            {"id": "rsi", "name": "RSI Reversal", "category": "Oscilador",
             "description": "Compra na saída de sobrevenda, vende na sobrecompra",
             "params": {"period": {"default": 14, "min": 5, "max": 50},
                        "oversold": {"default": 30.0, "min": 10, "max": 45},
                        "overbought": {"default": 70.0, "min": 55, "max": 90}}},
            {"id": "macd", "name": "MACD Crossover", "category": "Oscilador",
             "description": "Cruzamento MACD/Signal line",
             "params": {"fast": {"default": 12, "min": 3, "max": 50},
                        "slow": {"default": 26, "min": 10, "max": 100},
                        "signal_period": {"default": 9, "min": 3, "max": 20}}},
            {"id": "combined", "name": "RSI + MACD", "category": "Oscilador",
             "description": "Confirmação dupla: BUY só quando ambos concordam",
             "params": {"rsi_period": {"default": 14}, "rsi_oversold": {"default": 30.0},
                        "rsi_overbought": {"default": 70.0}, "macd_fast": {"default": 12},
                        "macd_slow": {"default": 26}, "macd_signal": {"default": 9}}},
            {"id": "bollinger", "name": "Bollinger Bands", "category": "Oscilador",
             "description": "Reversão nas bandas de Bollinger",
             "params": {"period": {"default": 20}, "std_dev": {"default": 2.0}}},
            {"id": "momentum", "name": "Momentum (ROC)", "category": "Oscilador",
             "description": "Rate of Change cruza o zero",
             "params": {"period": {"default": 10}, "rsi_filter": {"default": 65.0}}},
            # ── Médias ──────────────────────────────────────────────────────
            {"id": "ema_cross", "name": "EMA Cross", "category": "Media",
             "description": "Golden/death cross de EMAs",
             "params": {"fast": {"default": 9}, "slow": {"default": 21}}},
            {"id": "hilo", "name": "Hilo Activator", "category": "Media",
             "description": "Média de máximas/mínimas como sinal e trailing",
             "params": {"period": {"default": 8}}},
            # ── Price Action ─────────────────────────────────────────────────
            {"id": "pin_bar", "name": "Pin Bar", "category": "Price Action",
             "description": "Rejeição de nível — Hammer/Shooting Star",
             "params": {"wick_ratio": {"default": 0.6}, "trend_filter": {"default": True},
                        "trend_period": {"default": 50}}},
            {"id": "inside_bar", "name": "Inside Bar", "category": "Price Action",
             "description": "Compressão de volatilidade + rompimento",
             "params": {"trend_filter": {"default": True}, "trend_period": {"default": 21}}},
            {"id": "engulfing", "name": "Engulfing", "category": "Price Action",
             "description": "Absorção — candle atual engole o anterior",
             "params": {"body_ratio": {"default": 1.1}, "volume_filter": {"default": False}}},
            {"id": "fakey", "name": "Fakey", "category": "Price Action",
             "description": "Inside bar + falso rompimento + reversão",
             "params": {"confirm_bars": {"default": 1}}},
            # ── Clássicos BR ─────────────────────────────────────────────────
            {"id": "setup_91", "name": "Setup 9.1 (Stormer)", "category": "Classico BR",
             "description": "EMA 9/21 + candle de sinal acima da máxima anterior",
             "params": {"fast_period": {"default": 9}, "slow_period": {"default": 21},
                        "rsi_filter": {"default": 70.0}}},
            {"id": "larry_williams", "name": "Larry Williams", "category": "Classico BR",
             "description": "Compra na mínima anterior em uptrend",
             "params": {"trend_fast": {"default": 9}, "trend_slow": {"default": 21},
                        "lookback": {"default": 1}}},
            {"id": "turtle_soup", "name": "Turtle Soup", "category": "Classico BR",
             "description": "Falso rompimento de N-period high/low",
             "params": {"lookback": {"default": 20}, "confirm_bars": {"default": 2}}},
            # ── Tendência / Rompimento ────────────────────────────────────────
            {"id": "breakout", "name": "Breakout Range", "category": "Tendencia",
             "description": "Rompimento do canal de Donchian N períodos",
             "params": {"period": {"default": 20}, "atr_filter": {"default": True}}},
            {"id": "pullback_trend", "name": "Pullback in Trend", "category": "Tendencia",
             "description": "Retração para zona neutra de RSI em tendência",
             "params": {"trend_fast": {"default": 9}, "trend_slow": {"default": 21},
                        "pullback_low": {"default": 40.0}, "pullback_high": {"default": 60.0}}},
            {"id": "first_pullback", "name": "First Pullback", "category": "Tendencia",
             "description": "Primeira retração após barra de força",
             "params": {"strength_ratio": {"default": 0.6}, "ema_period": {"default": 9}}},
            # ── Outros ───────────────────────────────────────────────────────
            {"id": "gap_and_go", "name": "Gap and Go", "category": "Outro",
             "description": "Continuação de gap na direção da abertura",
             "params": {"gap_pct": {"default": 0.5}, "volume_filter": {"default": True}}},
            {"id": "bollinger_squeeze", "name": "Bollinger Squeeze", "category": "Outro",
             "description": "Contração extrema de volatilidade + expansão",
             "params": {"squeeze_threshold": {"default": 0.05},
                        "lookback_squeeze": {"default": 5}}},
        ]
    }

@router.post("/run")
async def run_backtest_post(
    body: BacktestRequest,
    request: Request
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
    strategy_params_json: str | None = Query(None, description="JSON dict de params")
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
        strategy_params_json=strategy_params_json
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
            strategy_params=_build_strategy_params(body) or None
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("backtest.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno ao executar backtest") from exc

# ── Optimize ──────────────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    ticker: str = Field(..., description="PETR4")
    strategy: str = Field("rsi", description="rsi|macd|combined|bollinger|ema_cross|momentum")
    range_period: str = Field("1y", description="6mo|1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    objective: str = Field("sharpe", description="sharpe|return|calmar|win_rate|profit_factor")
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
    _rl: None = Depends(rate_limit(limit=5, window=60))
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
            top_n=body.top_n
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
    top_n: int = Query(10)
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
        top_n=top_n
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
            top_n=body.top_n
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("optimize.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na otimizacao") from exc

# ── Walk-Forward ──────────────────────────────────────────────────────────────

class WalkForwardRequest(BaseModel):
    ticker: str = Field(..., description="PETR4")
    strategy: str = Field("rsi", description="rsi|macd|combined|bollinger|ema_cross|momentum")
    range_period: str = Field("2y", description="1y|2y")
    initial_capital: float = Field(10_000.0, ge=100.0)
    position_size: float = Field(1.0, ge=0.1, le=1.0)
    commission_pct: float = Field(0.001, ge=0.0, le=0.05)
    objective: str = Field("sharpe", description="sharpe|return|calmar|win_rate|profit_factor")
    n_splits: int = Field(3, ge=2, le=6)
    oos_pct: float = Field(0.3, ge=0.1, le=0.5)
    anchored: bool = Field(False)

def _get_walkforward(request: Request):
    svc = getattr(request.app.state, "walkforward_service", None)
    if svc is None:
        raise HTTPException(503, "WalkForwardService nao inicializado")
    return svc

@router.post("/walkforward")
async def run_walkforward(
    body: WalkForwardRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=5, window=60))
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
            anchored=body.anchored
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
    anchored: bool = Query(False)
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
        anchored=anchored
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
            anchored=body.anchored
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("walkforward.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no walk-forward") from exc

# ── Multi-Ticker Compare ───────────────────────────────────────────────────────

from finanalytics_ai.domain.backtesting.multi_ticker import MAX_TICKERS
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
    _rl: None = Depends(rate_limit(limit=5, window=60))
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
            top_n=body.top_n
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
    top_n: int = Query(5)
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
        top_n=top_n
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
            top_n=body.top_n
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error("multi_ticker.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no comparativo multi-ticker") from exc

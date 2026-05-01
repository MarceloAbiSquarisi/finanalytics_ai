"""
Schemas Pydantic do predict_mvp — extraidos em 01/mai/2026.

Models de request/response do endpoint /api/v1/ml/{predict_mvp,signals,
signal_history,metrics,predict_ensemble}. Movidos pra modulo proprio
reduz predict_mvp.py + facilita reuso por testes/clients.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class CalibrationInfo(BaseModel):
    th_buy: float
    th_sell: float
    horizon_days: int
    best_sharpe: float | None = None
    best_return_pct: float | None = None
    best_trades: int | None = None
    best_win_rate: float | None = None
    calibrated_at: str | None = None




class PredictResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    ticker: str
    reference_date: date
    predicted_log_return: float = Field(..., description="log(close[t+1]/close[t])")
    predicted_return_pct: float = Field(..., description="(exp(log_ret) - 1) * 100")
    model_file: str
    model_trained_at: str | None = None
    model_metrics: dict[str, Any] | None = None
    features_used: dict[str, Any]
    signal: str | None = Field(None, description="BUY | SELL | HOLD | None (se sem calibração)")
    signal_method: str | None = Field(
        None,
        description=(
            "scaled_linear_1d: th_buy/th_sell calibrados em horizon_days "
            "(ex 21d) divididos por horizon_days para comparação com "
            "prediction 1d — aproximação de primeira ordem"
        ),
    )
    calibration: CalibrationInfo | None = None




class SignalItem(BaseModel):
    ticker: str
    asset_class: str | None = None
    signal: str | None = None
    predicted_log_return: float | None = None
    predicted_return_pct: float | None = None
    reference_date: date | None = None
    th_buy: float | None = None
    th_sell: float | None = None
    horizon_days: int | None = None
    best_sharpe: float | None = None
    # N5b (28/abr): fundamentals FII (dy_ttm em %, p_vp adimensional). Vem
    # do snapshot mais recente em fii_fundamentals para asset_class='fii'.
    # null para acoes/ETFs (nao aplica).
    dy_ttm: float | None = None
    p_vp: float | None = None
    error: str | None = None




class SignalsResponse(BaseModel):
    count: int
    buy: int
    sell: int
    hold: int
    errors: int
    items: list[SignalItem]




class HistoryItem(BaseModel):
    snapshot_date: date
    ticker: str
    signal: str
    predicted_log_return: float | None = None
    predicted_return_pct: float | None = None
    th_buy: float | None = None
    th_sell: float | None = None
    horizon_days: int | None = None
    best_sharpe: float | None = None
    signal_method: str | None = None




class ChangeItem(BaseModel):
    ticker: str
    snapshot_date: date
    prev_signal: str | None = None
    curr_signal: str
    prev_date: date | None = None
    best_sharpe: float | None = None




class MLMetrics(BaseModel):
    config_count: int = Field(..., description="Linhas em ticker_ml_config")
    pickle_count: int = Field(..., description="Pickles MVP h21 disponiveis em models/")
    drift_count: int = Field(..., description="Tickers calibrados sem pickle (config - pickle)")
    drift_tickers: list[str] = Field(default_factory=list, description="Ate 10 tickers em drift")
    last_calibration_at: str | None = Field(
        None, description="ticker_ml_config.MAX(updated_at) ISO"
    )
    last_snapshot_at: str | None = Field(None, description="signal_history.MAX(snapshot_date) ISO")
    snapshot_age_days: int | None = Field(None, description="Dias desde o ultimo snapshot")
    latest_pickle_age_days: int | None = Field(
        None, description="Idade do pickle mais recente em models/"
    )
    signals_24h: dict[str, int] = Field(
        default_factory=dict, description="Contagem BUY/SELL/HOLD nas ultimas 24h em signal_history"
    )




class EnsembleHorizonItem(BaseModel):
    horizon_days: int
    model_file: str
    predicted_log_return: float
    predicted_return_pct: float
    weight: float = Field(..., description="Peso usado no ensemble (sharpe-based ou uniform)")
    sharpe: float | None = None




class EnsembleResponse(BaseModel):
    ticker: str
    reference_date: date
    ensemble_log_return: float
    ensemble_return_pct: float
    weighting: str = Field(
        ..., description="'sharpe' se todos models tem sharpe; 'uniform' caso contrario"
    )
    horizons: list[EnsembleHorizonItem]
    signal: str | None = None
    signal_method: str | None = None
    calibration: CalibrationInfo | None = None



"""
finanalytics_ai.application.ml.ml_service

Orquestrador: Feature Pipeline + Forecaster + Risk Estimator.

Fluxo:
  compute_features(tickers, date) ->
    Para cada ticker:
      1. Busca OHLC window do DB
      2. Computa features tecnicas
      3. Busca indicators Fintz (PIT)
      4. Salva em ml_features

  run_forecasts(tickers, date) ->
    1. Carrega features de ml_features
    2. Carrega modelo serializado (ou treina novo)
    3. Gera P10/P50/P90 por ticker
    4. Salva em ml_forecasts

  run_risk(tickers, date) ->
    1. Busca retornos historicos
    2. VaR historico, parametrico (t), GARCH, Monte Carlo
    3. Salva em ml_risk

Decisao: servico statefull com modelo em memoria.
  O forecaster e treinado uma vez e reutilizado por N previsoes.
  Retreino semanal via scheduler_worker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import structlog

from finanalytics_ai.application.ml.feature_pipeline import build_features_from_ohlc
from finanalytics_ai.application.ml.return_forecaster import QuantileForecaster, TrainingDataRow
from finanalytics_ai.domain.ml.entities import ReturnForecast

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.ml.feature_repo import SqlFeatureRepository

log = structlog.get_logger(__name__)


class MLService:
    """
    Servico principal de ML probabilistico.

    forecaster e lazy — treinado no primeiro uso ou via force_retrain.
    """

    def __init__(self, repo: SqlFeatureRepository) -> None:
        self._repo = repo
        self._forecaster: QuantileForecaster | None = None
        self._model_version = "lgbm-quantile-v1"

    async def compute_features(
        self,
        tickers: list[str],
        reference_date: date | None = None,
    ) -> int:
        """
        Computa e persiste features para todos os tickers.
        Retorna numero de tickers processados com sucesso.
        """
        ref = reference_date or date.today()
        ibov_rets = await self._repo.get_ibov_returns(ref)

        processed = 0
        for ticker in tickers:
            try:
                ohlc = await self._repo.get_ohlc_window(ticker, ref)
                if len(ohlc) < 30:
                    log.debug("ml_service.insufficient_ohlc", ticker=ticker, n=len(ohlc))
                    continue

                closes = [float(r["close"]) for r in ohlc if r["close"] is not None]
                volumes = [float(r["volume"]) if r["volume"] is not None else None for r in ohlc]
                fundamental = await self._repo.get_fundamental_features(ticker, ref)

                features = build_features_from_ohlc(
                    ticker=ticker,
                    date=datetime.combine(ref, datetime.min.time()).replace(tzinfo=UTC),
                    closes=closes,
                    volumes=volumes,
                    ibov_rets=ibov_rets,
                    fundamental=fundamental,
                )
                await self._repo.upsert_features([features])
                processed += 1

            except Exception as exc:
                log.warning("ml_service.feature_error", ticker=ticker, error=str(exc))

        log.info("ml_service.features_computed", total=len(tickers), processed=processed)
        return processed

    async def run_forecasts(
        self,
        tickers: list[str],
        forecast_date: date | None = None,
        horizon_days: int = 21,
        force_retrain: bool = False,
    ) -> list[ReturnForecast]:
        """
        Gera previsoes P10/P50/P90 para os tickers.

        Se o modelo nao existe ou force_retrain=True, treina antes.
        O treino usa TODOS os dados historicos de ml_features (walk-forward).
        """
        ref = forecast_date or date.today()

        if self._forecaster is None or force_retrain:
            await self._train_forecaster(horizon_days)

        if self._forecaster is None:
            log.error("ml_service.forecaster_unavailable")
            return []

        forecasts: list[ReturnForecast] = []
        for ticker in tickers:
            try:
                ohlc = await self._repo.get_ohlc_window(ticker, ref)
                if len(ohlc) < 30:
                    continue
                closes = [float(r["close"]) for r in ohlc if r["close"] is not None]
                volumes = [float(r["volume"]) if r["volume"] is not None else None for r in ohlc]
                ibov_rets = await self._repo.get_ibov_returns(ref)
                fundamental = await self._repo.get_fundamental_features(ticker, ref)
                features = build_features_from_ohlc(
                    ticker,
                    datetime.now(UTC),
                    closes,
                    volumes,
                    ibov_rets,
                    fundamental,
                )
                result = self._forecaster.predict(
                    features.__dict__,
                    horizon_days=horizon_days,
                )
                if result is None:
                    continue
                p10, p50, p90, prob_pos = result
                forecasts.append(
                    ReturnForecast(
                        ticker=ticker,
                        forecast_date=datetime.combine(ref, datetime.min.time()).replace(
                            tzinfo=UTC
                        ),
                        horizon_days=horizon_days,
                        p10=round(p10, 4),
                        p50=round(p50, 4),
                        p90=round(p90, 4),
                        prob_positive=round(prob_pos, 3),
                        model_version=self._model_version,
                    )
                )
            except Exception as exc:
                log.warning("ml_service.forecast_error", ticker=ticker, error=str(exc))

        if forecasts:
            await self._repo.save_forecasts(forecasts)
            log.info("ml_service.forecasts_persisted", count=len(forecasts))

        log.info("ml_service.forecasts_done", total=len(tickers), forecasted=len(forecasts))
        return forecasts

    async def _train_forecaster(self, horizon_days: int) -> None:
        """Treina o forecaster com dados historicos de ml_features."""
        log.info("ml_service.training_forecaster", horizon=horizon_days)
        # Por simplicidade: busca features diretamente do DB via query raw
        sql_text = f"""
            SELECT f.ticker, f.date,
                   f.ret_5d, f.ret_21d, f.ret_63d, f.volatility_21d,
                   f.rsi_14, f.beta_60d, f.volume_ratio_21d,
                   f.pe, f.pvp, f.roe, f.roic, f.ev_ebitda,
                   f.debt_ebitda, f.net_margin, f.revenue_growth,
                   future.preco_fechamento_ajustado / current_.preco_fechamento_ajustado - 1
                       AS target_{horizon_days}d
            FROM ml_features f
            JOIN fintz_cotacoes current_
                ON current_.ticker = f.ticker AND current_.data = f.date
            JOIN fintz_cotacoes future
                ON future.ticker = f.ticker
                AND future.data = (
                    SELECT data FROM fintz_cotacoes
                    WHERE ticker = f.ticker AND data > f.date
                    ORDER BY data ASC
                    OFFSET {horizon_days - 1} LIMIT 1
                )
            ORDER BY f.date ASC
        """
        from sqlalchemy import text

        rows = await self._repo._session.execute(text(sql_text))
        training_data = []
        for row in rows.mappings():
            training_data.append(
                TrainingDataRow(
                    features={
                        "ret_5d": row.get("ret_5d"),
                        "ret_21d": row.get("ret_21d"),
                        "ret_63d": row.get("ret_63d"),
                        "volatility_21d": row.get("volatility_21d"),
                        "rsi_14": row.get("rsi_14"),
                        "beta_60d": row.get("beta_60d"),
                        "volume_ratio_21d": row.get("volume_ratio_21d"),
                        "pe": row.get("pe"),
                        "pvp": row.get("pvp"),
                        "roe": row.get("roe"),
                        "roic": row.get("roic"),
                        "ev_ebitda": row.get("ev_ebitda"),
                        "debt_ebitda": row.get("debt_ebitda"),
                        "net_margin": row.get("net_margin"),
                        "revenue_growth": row.get("revenue_growth"),
                    },
                    target_21d=row.get(f"target_{horizon_days}d") if horizon_days == 21 else None,
                    target_63d=row.get(f"target_{horizon_days}d") if horizon_days == 63 else None,
                )
            )

        if len(training_data) < 50:
            log.warning("ml_service.insufficient_training_data", n=len(training_data))
            return

        self._forecaster = QuantileForecaster(n_estimators=300)
        metrics = self._forecaster.train(training_data, horizon_days=horizon_days)
        log.info("ml_service.trained", metrics=metrics, n_samples=len(training_data))

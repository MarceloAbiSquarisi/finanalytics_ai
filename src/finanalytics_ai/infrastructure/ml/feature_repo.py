"""
finanalytics_ai.infrastructure.ml.feature_repo

Repositorio de features ML: queries SQL sobre Fintz + cotacoes.

Queries criticas de performance:
  - OHLC: busca window_days+buffer dias por ticker — indexado por (ticker, data)
  - Indicadores: DISTINCT ON (ticker) para obter valor PIT mais recente
    antes da data alvo — evita lookahead bias

Point-in-time (PIT) semantics:
  Indicadores Fintz tem data_publicacao — o valor SÓ é conhecido após essa data.
  Usamos: WHERE data_publicacao <= :reference_date ORDER BY data_publicacao DESC
  Isso garante que o modelo de treino nao usa dados futuros (data leakage).
"""
from __future__ import annotations

import math
from datetime import datetime, date
from typing import Any

import numpy as np
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.ml.entities import TickerFeatures

log = structlog.get_logger(__name__)

# Janelas padrao (dias uteis aproximados)
WINDOW_RSI     = 14
WINDOW_VOL     = 21
WINDOW_BETA    = 60
WINDOW_RET_MAX = 65   # maior janela de retorno + buffer
IBOV_TICKER    = "IBOV"


class SqlFeatureRepository:
    """
    Computa e persiste features por ticker/data.

    Design: usa asyncpg via SQLAlchemy — nao Polars aqui porque
    a query ja faz o trabalho pesado no PostgreSQL.
    Polars e usado no feature_pipeline.py para transformacoes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_ohlc_window(
        self,
        ticker: str,
        reference_date: date,
        window_days: int = WINDOW_RET_MAX,
    ) -> list[dict[str, Any]]:
        """Retorna serie OHLC para calculo de features tecnicas."""
        sql = text("""
            SELECT data, preco_fechamento_ajustado AS close,
                   volume_negociado AS volume
            FROM fintz_cotacoes
            WHERE ticker = :ticker
              AND data <= :ref_date
              AND data >= :ref_date - INTERVAL ':window days'
            ORDER BY data ASC
        """.replace(":window", str(window_days * 2)))  # buffer 2x para RSI warm-up
        rows = await self._session.execute(sql, {
            "ticker": ticker,
            "ref_date": reference_date,
        })
        return [dict(r) for r in rows.mappings()]

    async def get_fundamental_features(
        self,
        ticker: str,
        reference_date: date,
    ) -> dict[str, float | None]:
        """
        Retorna ultimo valor PIT de cada indicador antes da reference_date.
        Indicadores Fintz: ROE, P/L, P/VP, ROIC, EV/EBITDA, Divida/EBITDA,
                           Margem_Liquida, Crescimento_Receita_1A
        """
        indicadores = [
            "ROE", "P_L", "P_VP", "ROIC",
            "EV_EBITDA", "DividaLiquida_EBITDA",
            "MargemLiquida", "GiroAtivos",
        ]
        placeholders = ", ".join(f"'{i}'" for i in indicadores)
        sql = text(f"""
            SELECT DISTINCT ON (indicador)
                indicador, valor
            FROM fintz_indicadores
            WHERE ticker = :ticker
              AND indicador IN ({placeholders})
              AND data_publicacao <= :ref_date
            ORDER BY indicador, data_publicacao DESC
        """)
        rows = await self._session.execute(sql, {
            "ticker": ticker,
            "ref_date": reference_date,
        })
        result: dict[str, float | None] = {k: None for k in indicadores}
        for row in rows.mappings():
            v = row["valor"]; result[row["indicador"]] = float(v) if v is not None else None
        return result

    async def get_ibov_returns(
        self,
        reference_date: date,
        window_days: int = WINDOW_BETA,
    ) -> list[float]:
        """Retornos diarios do Ibovespa para calculo de beta."""
        sql = text("""
            SELECT preco_fechamento_ajustado AS close
            FROM fintz_cotacoes
            WHERE ticker = 'IBOV'
              AND data <= :ref_date
              AND data >= :ref_date - INTERVAL ':window days'
            ORDER BY data ASC
        """.replace(":window", str(window_days * 2)))
        rows = await self._session.execute(sql, {"ref_date": reference_date})
        closes = [float(r[0]) for r in rows if r[0] is not None]
        return _to_returns(closes)

    async def upsert_features(self, features: list[TickerFeatures]) -> None:
        """Upsert em ml_features (ON CONFLICT DO UPDATE)."""
        if not features:
            return
        for f in features:
            sql = text("""
                INSERT INTO ml_features
                    (ticker, date, ret_5d, ret_21d, ret_63d, volatility_21d,
                     rsi_14, beta_60d, volume_ratio_21d,
                     pe, pvp, roe, roic, ev_ebitda, debt_ebitda,
                     net_margin, revenue_growth, updated_at)
                VALUES
                    (:ticker, :date, :ret_5d, :ret_21d, :ret_63d, :vol21,
                     :rsi, :beta, :vol_ratio,
                     :pe, :pvp, :roe, :roic, :ev_ebitda, :debt_ebitda,
                     :margin, :growth, NOW())
                ON CONFLICT (ticker, date) DO UPDATE SET
                    ret_5d=EXCLUDED.ret_5d, ret_21d=EXCLUDED.ret_21d,
                    ret_63d=EXCLUDED.ret_63d, volatility_21d=EXCLUDED.volatility_21d,
                    rsi_14=EXCLUDED.rsi_14, beta_60d=EXCLUDED.beta_60d,
                    volume_ratio_21d=EXCLUDED.volume_ratio_21d,
                    pe=EXCLUDED.pe, pvp=EXCLUDED.pvp, roe=EXCLUDED.roe,
                    roic=EXCLUDED.roic, ev_ebitda=EXCLUDED.ev_ebitda,
                    debt_ebitda=EXCLUDED.debt_ebitda, net_margin=EXCLUDED.net_margin,
                    revenue_growth=EXCLUDED.revenue_growth, updated_at=NOW()
            """)
            fund = f
            await self._session.execute(sql, {
                "ticker": f.ticker, "date": f.date.date() if hasattr(f.date, "date") else f.date,
                "ret_5d": f.ret_5d, "ret_21d": f.ret_21d, "ret_63d": f.ret_63d,
                "vol21": f.volatility_21d, "rsi": f.rsi_14, "beta": f.beta_60d,
                "vol_ratio": f.volume_ratio_21d,
                "pe": f.pe, "pvp": f.pvp, "roe": f.roe, "roic": f.roic,
                "ev_ebitda": f.ev_ebitda, "debt_ebitda": f.debt_ebitda,
                "margin": f.net_margin, "growth": f.revenue_growth,
            })
        await self._session.commit()


    async def save_forecasts(self, forecasts: list) -> None:
        """Persiste previsoes P10/P50/P90 em ml_forecasts (upsert)."""
        if not forecasts:
            return
        from sqlalchemy import text as _text
        for f in forecasts:
            sql = _text("""
                INSERT INTO ml_forecasts
                    (ticker, forecast_date, horizon_days, p10, p50, p90,
                     prob_positive, model_version)
                VALUES
                    (:ticker, :forecast_date, :horizon_days, :p10, :p50, :p90,
                     :prob_positive, :model_version)
                ON CONFLICT (ticker, forecast_date, horizon_days)
                DO UPDATE SET
                    p10=EXCLUDED.p10, p50=EXCLUDED.p50, p90=EXCLUDED.p90,
                    prob_positive=EXCLUDED.prob_positive,
                    model_version=EXCLUDED.model_version
            """)
            fd = f.forecast_date
            fdate = fd.date() if hasattr(fd, "date") else fd
            await self._session.execute(sql, {
                "ticker": f.ticker,
                "forecast_date": fdate,
                "horizon_days": f.horizon_days,
                "p10": f.p10, "p50": f.p50, "p90": f.p90,
                "prob_positive": f.prob_positive,
                "model_version": f.model_version,
            })
        await self._session.commit()

    async def save_risk_metrics(self, metrics_list: list) -> None:
        """Persiste metricas VaR/CVaR em ml_risk (upsert)."""
        if not metrics_list:
            return
        from sqlalchemy import text as _text
        for m in metrics_list:
            sql = _text("""
                INSERT INTO ml_risk (
                    ticker, date, window_days,
                    var_95_historical, cvar_95_historical,
                    var_95_parametric, cvar_95_parametric,
                    t_degrees_of_freedom,
                    var_95_garch, cvar_95_garch, garch_vol_forecast,
                    var_95_mc, cvar_95_mc, volatility_annual, updated_at
                ) VALUES (
                    :ticker, :date, :window_days,
                    :var_h, :cvar_h, :var_p, :cvar_p, :t_df,
                    :var_g, :cvar_g, :garch_vol,
                    :var_mc, :cvar_mc, :vol_ann, NOW()
                )
                ON CONFLICT (ticker, date, window_days) DO UPDATE SET
                    var_95_historical=EXCLUDED.var_95_historical,
                    cvar_95_historical=EXCLUDED.cvar_95_historical,
                    var_95_parametric=EXCLUDED.var_95_parametric,
                    cvar_95_parametric=EXCLUDED.cvar_95_parametric,
                    t_degrees_of_freedom=EXCLUDED.t_degrees_of_freedom,
                    var_95_garch=EXCLUDED.var_95_garch,
                    cvar_95_garch=EXCLUDED.cvar_95_garch,
                    garch_vol_forecast=EXCLUDED.garch_vol_forecast,
                    var_95_mc=EXCLUDED.var_95_mc,
                    cvar_95_mc=EXCLUDED.cvar_95_mc,
                    volatility_annual=EXCLUDED.volatility_annual,
                    updated_at=NOW()
            """)
            md = m.date
            mdate = md.date() if hasattr(md, "date") else md
            await self._session.execute(sql, {
                "ticker": m.ticker, "date": mdate, "window_days": m.window_days,
                "var_h": m.var_95_historical, "cvar_h": m.cvar_95_historical,
                "var_p": m.var_95_parametric, "cvar_p": m.cvar_95_parametric,
                "t_df": m.t_degrees_of_freedom,
                "var_g": m.var_95_garch, "cvar_g": m.cvar_95_garch,
                "garch_vol": m.garch_volatility_forecast,
                "var_mc": m.var_95_mc, "cvar_mc": m.cvar_95_mc,
                "vol_ann": m.volatility_annual,
            })
        await self._session.commit()

def _to_returns(closes: list[float]) -> list[float]:
    if len(closes) < 2:
        return []
    return [(closes[i] - closes[i-1]) / closes[i-1]
            for i in range(1, len(closes)) if closes[i-1] != 0]

"""0012_ml_features

Tabelas para ML Probabilistico:
  ml_features  — features por ticker/data (tecnicas + fundamentais)
  ml_forecasts — previsoes de retorno P10/P50/P90
  ml_risk      — metricas de risco VaR/CVaR multi-camada

Decisao: PostgreSQL principal (nao TimescaleDB) porque:
  - Volume moderado: ~100 tickers x 1 update/dia = 36k rows/ano
  - Queries sao por ticker (filtro simples) — sem beneficio de hypertable
  - Joins com fintz_indicadores ficam no mesmo banco

Revisao: se volume crescer para intraday (ticks), migrar ml_risk para TimescaleDB.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_ml_features"
down_revision = "0010_financial_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ml_features ───────────────────────────────────────────────────────────
    op.create_table(
        "ml_features",
        sa.Column("ticker",          sa.String(20),   nullable=False),
        sa.Column("date",            sa.Date(),       nullable=False),
        # Technical
        sa.Column("ret_5d",          sa.Float(),      nullable=True),
        sa.Column("ret_21d",         sa.Float(),      nullable=True),
        sa.Column("ret_63d",         sa.Float(),      nullable=True),
        sa.Column("volatility_21d",  sa.Float(),      nullable=True),
        sa.Column("rsi_14",          sa.Float(),      nullable=True),
        sa.Column("beta_60d",        sa.Float(),      nullable=True),
        sa.Column("volume_ratio_21d",sa.Float(),      nullable=True),
        # Fundamental (Fintz PIT)
        sa.Column("pe",              sa.Float(),      nullable=True),
        sa.Column("pvp",             sa.Float(),      nullable=True),
        sa.Column("roe",             sa.Float(),      nullable=True),
        sa.Column("roic",            sa.Float(),      nullable=True),
        sa.Column("ev_ebitda",       sa.Float(),      nullable=True),
        sa.Column("debt_ebitda",     sa.Float(),      nullable=True),
        sa.Column("net_margin",      sa.Float(),      nullable=True),
        sa.Column("revenue_growth",  sa.Float(),      nullable=True),
        sa.Column("updated_at",      sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("ticker", "date"),
    )
    op.create_index("ix_ml_features_ticker", "ml_features", ["ticker"])
    op.create_index("ix_ml_features_date",   "ml_features", ["date"])

    # ── ml_forecasts ──────────────────────────────────────────────────────────
    op.create_table(
        "ml_forecasts",
        sa.Column("id",              sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("ticker",          sa.String(20),   nullable=False),
        sa.Column("forecast_date",   sa.Date(),       nullable=False),
        sa.Column("horizon_days",    sa.Integer(),    nullable=False),
        sa.Column("p10",             sa.Float(),      nullable=False),
        sa.Column("p50",             sa.Float(),      nullable=False),
        sa.Column("p90",             sa.Float(),      nullable=False),
        sa.Column("prob_positive",   sa.Float(),      nullable=False),
        sa.Column("model_version",   sa.String(50),   nullable=False),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ticker", "forecast_date", "horizon_days",
                            name="uq_ml_forecasts"),
    )
    op.create_index("ix_ml_forecasts_ticker_date",
                    "ml_forecasts", ["ticker", "forecast_date"])

    # ── ml_risk ───────────────────────────────────────────────────────────────
    op.create_table(
        "ml_risk",
        sa.Column("ticker",              sa.String(20), nullable=False),
        sa.Column("date",                sa.Date(),     nullable=False),
        sa.Column("window_days",         sa.Integer(),  nullable=False),
        sa.Column("var_95_historical",   sa.Float(),    nullable=False),
        sa.Column("cvar_95_historical",  sa.Float(),    nullable=False),
        sa.Column("var_95_parametric",   sa.Float(),    nullable=False),
        sa.Column("cvar_95_parametric",  sa.Float(),    nullable=False),
        sa.Column("t_degrees_of_freedom",sa.Float(),    nullable=False),
        sa.Column("var_95_garch",        sa.Float(),    nullable=True),
        sa.Column("cvar_95_garch",       sa.Float(),    nullable=True),
        sa.Column("garch_vol_forecast",  sa.Float(),    nullable=True),
        sa.Column("var_95_mc",           sa.Float(),    nullable=False),
        sa.Column("cvar_95_mc",          sa.Float(),    nullable=False),
        sa.Column("volatility_annual",   sa.Float(),    nullable=False),
        sa.Column("updated_at",          sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("ticker", "date", "window_days"),
    )
    op.create_index("ix_ml_risk_ticker", "ml_risk", ["ticker"])


def downgrade() -> None:
    op.drop_table("ml_risk")
    op.drop_table("ml_forecasts")
    op.drop_table("ml_features")

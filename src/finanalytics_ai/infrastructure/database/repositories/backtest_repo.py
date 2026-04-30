"""
Repositorio do historico de backtest_results (R5).

Modelo: backtest_results — armazena cada run grid_search/walk_forward
com idempotencia por config_hash (SHA256). Re-rodar mesmo config faz UPSERT
(updated_at + metrics atualizam, created_at preservado).

Convencao "config completo" para o hash:
  ticker, strategy, range_period, start_date, end_date,
  initial_capital, objective, slippage_applied, params

Mudanca em qualquer dimensao -> hash diferente -> nova row.

Spec viva: Melhorias.md secao R5 "Faltam".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    desc,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.infrastructure.database.connection import Base

logger = structlog.get_logger(__name__)


# ── Modelo ────────────────────────────────────────────────────────────────────


class BacktestResultModel(Base):
    __tablename__ = "backtest_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Config
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    range_period: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    initial_capital: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective: Mapped[str | None] = mapped_column(String(20), nullable=True)
    slippage_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Metricas core
    total_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bars_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Deflated Sharpe (LdP 2014)
    deflated_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_real: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_trials: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Payload completo (JSONB no Postgres; JSON no SQLite p/ tests)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    full_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "config_hash": self.config_hash,
            "user_id": self.user_id,
            "ticker": self.ticker,
            "strategy": self.strategy,
            "range_period": self.range_period,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "initial_capital": self.initial_capital,
            "objective": self.objective,
            "slippage_applied": bool(self.slippage_applied),
            "metrics": {
                "total_return_pct": self.total_return_pct,
                "sharpe_ratio": self.sharpe_ratio,
                "max_drawdown_pct": self.max_drawdown_pct,
                "win_rate_pct": self.win_rate_pct,
                "profit_factor": self.profit_factor,
                "calmar_ratio": self.calmar_ratio,
                "total_trades": self.total_trades,
                "bars_count": self.bars_count,
            },
            "deflated_sharpe": {
                "deflated_sharpe": self.deflated_sharpe,
                "prob_real": self.prob_real,
                "num_trials": self.num_trials,
                "sample_size": self.sample_size,
            }
            if self.deflated_sharpe is not None
            else None,
            "params": self.params_json,
            "full_result": self.full_result_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Hash do config ────────────────────────────────────────────────────────────


def compute_config_hash(
    *,
    ticker: str,
    strategy: str,
    range_period: str | None,
    start_date: str | None,
    end_date: str | None,
    initial_capital: float,
    objective: str,
    slippage_applied: bool,
    params: dict[str, Any] | None,
) -> str:
    """
    SHA256 sobre o JSON canonico do config completo. Mesmo config -> mesmo hash.

    Uso em UPSERT:
      h = compute_config_hash(...)
      repo.save_run(config_hash=h, ...)
        -> insere se hash novo, atualiza updated_at + metricas se hash existe

    Determinismo: sort_keys=True garante que ordem de chaves no params nao
    afeta o hash. Floats convertidos com 6 casas decimais (mais que suficiente
    para parametros tecnicos tipicos como 14, 0.001, 70.0, etc).
    """
    payload = {
        "ticker": (ticker or "").upper(),
        "strategy": strategy or "",
        "range_period": range_period or "",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "initial_capital": round(float(initial_capital), 6),
        "objective": objective or "",
        "slippage_applied": bool(slippage_applied),
        "params": _canonicalize(params or {}),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _canonicalize(d: dict[str, Any]) -> dict[str, Any]:
    """Normaliza dict p/ hash estavel (floats com round, sort de listas)."""
    out: dict[str, Any] = {}
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, float):
            out[k] = round(v, 6)
        elif isinstance(v, dict):
            out[k] = _canonicalize(v)
        elif isinstance(v, list):
            out[k] = sorted(v) if all(isinstance(x, (str, int, float)) for x in v) else v
        else:
            out[k] = v
    return out


# ── Repository ────────────────────────────────────────────────────────────────


class BacktestResultRepository:
    """CRUD assincrono para historico de backtest_results."""

    def __init__(self, session_factory: Any) -> None:
        self._session = session_factory

    async def save_run(
        self,
        *,
        config_hash: str,
        ticker: str,
        strategy: str,
        full_result: dict[str, Any],
        range_period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        initial_capital: float | None = None,
        objective: str | None = None,
        slippage_applied: bool = True,
        user_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """
        UPSERT por config_hash. Retorna (row_dict, created: bool).

        full_result: payload `OptimizationResult.to_dict()` ou similar contendo
          metricas e DSR. As colunas escalares sao extraidas para query
          eficiente; o JSON inteiro fica em full_result_json para drill-down.
        """
        # Extrai metricas escalares do payload
        metrics = (
            (full_result.get("top") or [{}])[0].get("metrics", {})
            if full_result.get("top")
            else full_result.get("metrics", {})
        )
        dsr = full_result.get("deflated_sharpe") or {}

        async with self._session() as session:
            existing = await session.execute(
                select(BacktestResultModel).where(BacktestResultModel.config_hash == config_hash)
            )
            row = existing.scalar_one_or_none()

            if row is None:
                row = BacktestResultModel(
                    id=str(uuid4()),
                    config_hash=config_hash,
                    user_id=user_id,
                    ticker=ticker.upper(),
                    strategy=strategy,
                    range_period=range_period,
                    start_date=date.fromisoformat(start_date) if start_date else None,
                    end_date=date.fromisoformat(end_date) if end_date else None,
                    initial_capital=initial_capital,
                    objective=objective,
                    slippage_applied=slippage_applied,
                )
                session.add(row)
                created = True
            else:
                created = False
                # config_hash garante que ticker/strategy/etc nao mudaram, entao
                # so atualizamos as metricas + payload + updated_at.

            # Metricas (sempre atualiza no UPSERT — re-run pode mudar com fix de bug)
            row.total_return_pct = metrics.get("total_return_pct")
            row.sharpe_ratio = metrics.get("sharpe_ratio")
            row.max_drawdown_pct = metrics.get("max_drawdown_pct")
            row.win_rate_pct = metrics.get("win_rate_pct")
            row.profit_factor = metrics.get("profit_factor")
            row.calmar_ratio = metrics.get("calmar_ratio")
            row.total_trades = metrics.get("total_trades")
            row.bars_count = full_result.get("bars_count") or full_result.get("total_runs")

            row.deflated_sharpe = dsr.get("deflated_sharpe") if dsr else None
            row.prob_real = dsr.get("prob_real") if dsr else None
            row.num_trials = dsr.get("num_trials") if dsr else None
            row.sample_size = dsr.get("sample_size") if dsr else None

            row.params_json = params or full_result.get("best_params") or {}
            row.full_result_json = full_result
            row.updated_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(row)
            return row.to_dict(), created

    async def get_by_hash(self, config_hash: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(BacktestResultModel).where(BacktestResultModel.config_hash == config_hash)
            )
            row = result.scalar_one_or_none()
            return row.to_dict() if row else None

    async def list(
        self,
        ticker: str | None = None,
        strategy: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            q = select(BacktestResultModel)
            if ticker:
                q = q.where(BacktestResultModel.ticker == ticker.upper())
            if strategy:
                q = q.where(BacktestResultModel.strategy == strategy)
            if user_id:
                q = q.where(BacktestResultModel.user_id == user_id)
            q = q.order_by(desc(BacktestResultModel.created_at)).limit(limit).offset(offset)
            result = await session.execute(q)
            return [r.to_dict() for r in result.scalars().all()]

    async def delete(self, config_hash: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                select(BacktestResultModel).where(BacktestResultModel.config_hash == config_hash)
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

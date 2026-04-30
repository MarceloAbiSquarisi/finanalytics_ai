"""Testes do BacktestResultRepository (R5 follow-up)."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
    BacktestResultModel,
    BacktestResultRepository,
    compute_config_hash,
)


@pytest_asyncio.fixture
async def session_factory():
    """SQLite in-memory — sem PostgreSQL necessario."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(BacktestResultModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class _Factory:
        def __call__(self):
            return factory()

    yield _Factory()
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(session_factory):
    return BacktestResultRepository(session_factory)


def _payload(**over: Any) -> dict[str, Any]:
    base = {
        "ticker": "PETR4",
        "strategy": "rsi",
        "range_period": "2015-01-01..2024-12-31",
        "start_date": "2015-01-01",
        "end_date": "2024-12-31",
        "initial_capital": 10_000.0,
        "objective": "sharpe",
        "slippage_applied": True,
    }
    base.update(over)
    return base


def _full_result(sharpe: float = 0.76, dsr: float = 0.31, prob: float = 0.62) -> dict[str, Any]:
    """Mocka payload de OptimizationResult.to_dict()."""
    return {
        "ticker": "PETR4",
        "strategy": "rsi",
        "total_runs": 30,
        "valid_runs": 30,
        "best_params": {"period": 21, "oversold": 35.0, "overbought": 75.0},
        "best_score": sharpe,
        "top": [
            {
                "rank": 1,
                "params": {"period": 21, "oversold": 35.0, "overbought": 75.0},
                "score": sharpe,
                "is_valid": True,
                "metrics": {
                    "total_return_pct": 751.1,
                    "sharpe_ratio": sharpe,
                    "max_drawdown_pct": 60.8,
                    "win_rate_pct": 100.0,
                    "profit_factor": 999.0,
                    "calmar_ratio": 12.35,
                    "total_trades": 6,
                },
            }
        ],
        "deflated_sharpe": {
            "deflated_sharpe": dsr,
            "prob_real": prob,
            "num_trials": 30,
            "sample_size": 2478,
            "observed_sharpe": sharpe,
        },
        "bars_count": 2479,
    }


# ── compute_config_hash ───────────────────────────────────────────────────────


class TestConfigHash:
    def test_same_config_same_hash(self) -> None:
        h1 = compute_config_hash(**_payload(), params={"period": 14})
        h2 = compute_config_hash(**_payload(), params={"period": 14})
        assert h1 == h2

    def test_different_ticker_different_hash(self) -> None:
        h1 = compute_config_hash(**_payload(ticker="PETR4"), params={"period": 14})
        h2 = compute_config_hash(**_payload(ticker="VALE3"), params={"period": 14})
        assert h1 != h2

    def test_different_params_different_hash(self) -> None:
        h1 = compute_config_hash(**_payload(), params={"period": 14})
        h2 = compute_config_hash(**_payload(), params={"period": 21})
        assert h1 != h2

    def test_param_order_irrelevant(self) -> None:
        # sort_keys garante que ordem de chaves no dict nao afeta hash
        h1 = compute_config_hash(**_payload(), params={"a": 1, "b": 2})
        h2 = compute_config_hash(**_payload(), params={"b": 2, "a": 1})
        assert h1 == h2

    def test_slippage_flag_changes_hash(self) -> None:
        h1 = compute_config_hash(**_payload(slippage_applied=True), params={})
        h2 = compute_config_hash(**_payload(slippage_applied=False), params={})
        assert h1 != h2

    def test_ticker_normalized_uppercase(self) -> None:
        h1 = compute_config_hash(**_payload(ticker="petr4"), params={})
        h2 = compute_config_hash(**_payload(ticker="PETR4"), params={})
        assert h1 == h2

    def test_hash_is_64_chars(self) -> None:
        h = compute_config_hash(**_payload(), params={})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ── save_run UPSERT ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSaveRun:
    async def test_first_save_creates(self, repo) -> None:
        h = compute_config_hash(**_payload(), params={"period": 21})
        row, created = await repo.save_run(
            config_hash=h,
            ticker="PETR4",
            strategy="rsi",
            full_result=_full_result(),
        )
        assert created is True
        assert row["config_hash"] == h
        assert row["ticker"] == "PETR4"
        assert row["metrics"]["sharpe_ratio"] == pytest.approx(0.76)
        assert row["deflated_sharpe"]["prob_real"] == pytest.approx(0.62)

    async def test_second_save_updates_not_duplicates(self, repo) -> None:
        h = compute_config_hash(**_payload(), params={"period": 21})
        # Primeira run
        row1, created1 = await repo.save_run(
            config_hash=h,
            ticker="PETR4",
            strategy="rsi",
            full_result=_full_result(sharpe=0.76, dsr=0.31, prob=0.62),
        )
        # Re-run com metricas diferentes (ex: bug fix mudou calculo)
        row2, created2 = await repo.save_run(
            config_hash=h,
            ticker="PETR4",
            strategy="rsi",
            full_result=_full_result(sharpe=0.85, dsr=0.50, prob=0.69),
        )
        assert created1 is True
        assert created2 is False
        assert row1["id"] == row2["id"]  # mesma row, atualizada
        assert row2["metrics"]["sharpe_ratio"] == pytest.approx(0.85)
        assert row2["deflated_sharpe"]["prob_real"] == pytest.approx(0.69)

        # Confirma que so existe 1 row no DB
        all_rows = await repo.list()
        assert len(all_rows) == 1

    async def test_extracts_metrics_from_top0(self, repo) -> None:
        h = compute_config_hash(**_payload(), params={"period": 21})
        row, _ = await repo.save_run(
            config_hash=h,
            ticker="PETR4",
            strategy="rsi",
            full_result=_full_result(),
        )
        assert row["metrics"]["total_trades"] == 6
        assert row["metrics"]["max_drawdown_pct"] == pytest.approx(60.8)


@pytest.mark.asyncio
class TestList:
    async def test_filter_by_ticker(self, repo) -> None:
        h_petr = compute_config_hash(**_payload(ticker="PETR4"), params={"p": 1})
        h_vale = compute_config_hash(**_payload(ticker="VALE3"), params={"p": 1})
        await repo.save_run(
            config_hash=h_petr, ticker="PETR4", strategy="rsi", full_result=_full_result()
        )
        await repo.save_run(
            config_hash=h_vale, ticker="VALE3", strategy="rsi", full_result=_full_result()
        )
        result = await repo.list(ticker="PETR4")
        assert len(result) == 1
        assert result[0]["ticker"] == "PETR4"

    async def test_filter_by_strategy(self, repo) -> None:
        h_rsi = compute_config_hash(**_payload(strategy="rsi"), params={"p": 1})
        h_macd = compute_config_hash(**_payload(strategy="macd"), params={"p": 1})
        await repo.save_run(
            config_hash=h_rsi, ticker="PETR4", strategy="rsi", full_result=_full_result()
        )
        await repo.save_run(
            config_hash=h_macd, ticker="PETR4", strategy="macd", full_result=_full_result()
        )
        result = await repo.list(strategy="macd")
        assert len(result) == 1
        assert result[0]["strategy"] == "macd"

    async def test_empty_filter_returns_all(self, repo) -> None:
        for i in range(3):
            h = compute_config_hash(**_payload(), params={"p": i})
            await repo.save_run(
                config_hash=h, ticker="PETR4", strategy="rsi", full_result=_full_result()
            )
        result = await repo.list()
        assert len(result) == 3


@pytest.mark.asyncio
class TestGetByHash:
    async def test_get_existing(self, repo) -> None:
        h = compute_config_hash(**_payload(), params={"p": 1})
        await repo.save_run(
            config_hash=h, ticker="PETR4", strategy="rsi", full_result=_full_result()
        )
        row = await repo.get_by_hash(h)
        assert row is not None
        assert row["config_hash"] == h

    async def test_get_nonexistent_returns_none(self, repo) -> None:
        row = await repo.get_by_hash("0" * 64)
        assert row is None


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_existing(self, repo) -> None:
        h = compute_config_hash(**_payload(), params={"p": 1})
        await repo.save_run(
            config_hash=h, ticker="PETR4", strategy="rsi", full_result=_full_result()
        )
        deleted = await repo.delete(h)
        assert deleted is True
        assert await repo.get_by_hash(h) is None

    async def test_delete_nonexistent(self, repo) -> None:
        assert await repo.delete("0" * 64) is False

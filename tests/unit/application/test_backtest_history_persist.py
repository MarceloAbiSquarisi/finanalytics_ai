"""
Persistencia automatica em backtest_results via Optimizer/WalkForward services.

Cobertura:
  - OptimizerService chama save_run com config_hash determinista
  - WalkForwardService persiste com strategy "wf:<name>" + params do WF
  - Repo None -> service nao tenta persistir (comportamento legado intacto)
  - Falha do repo -> service nao quebra (logged e continua)

Compartilha helper _bars com test_optimizer.py mas e auto-suficiente.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.services.optimizer_service import OptimizerService
from finanalytics_ai.application.services.walkforward_service import WalkForwardService
from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
    compute_config_hash,
)


def _bars(n: int = 200) -> list[dict[str, Any]]:
    """Bars sineticos com volatilidade suficiente para grid search produzir trades."""
    out = []
    for i in range(n):
        p = 100.0 + 15.0 * math.sin(i * 0.2) + i * 0.05
        out.append({
            "time": 1700_000_000 + i * 86400,
            "open": p,
            "high": p + 1.0,
            "low": p - 1.0,
            "close": p,
            "volume": 1_000_000.0,
        })
    return out


def _make_optimizer(bars: list[dict], repo: Any | None) -> tuple[OptimizerService, AsyncMock]:
    mock_market = AsyncMock()
    mock_market.get_ohlc_bars.return_value = bars
    return OptimizerService(mock_market, result_repo=repo), mock_market


def _make_walkforward(bars: list[dict], repo: Any | None) -> tuple[WalkForwardService, AsyncMock]:
    mock_market = AsyncMock()
    mock_market.get_ohlc_bars.return_value = bars
    return WalkForwardService(mock_market, result_repo=repo), mock_market


# ── OptimizerService persistence ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestOptimizerPersist:
    async def test_persists_when_repo_provided(self) -> None:
        repo = AsyncMock()
        repo.save_run.return_value = ({"id": "x"}, True)
        svc, _ = _make_optimizer(_bars(200), repo)

        await svc.optimize("PETR4", "rsi", range_period="1y")

        repo.save_run.assert_awaited_once()
        kwargs = repo.save_run.await_args.kwargs
        assert kwargs["ticker"] == "PETR4"
        assert kwargs["strategy"] == "rsi"
        assert kwargs["range_period"] == "1y"
        assert kwargs["objective"] == "sharpe"
        assert kwargs["slippage_applied"] is True
        # config_hash deve ser determinista para mesmo config + params
        expected_hash = compute_config_hash(
            ticker="PETR4",
            strategy="rsi",
            range_period="1y",
            start_date=None,
            end_date=None,
            initial_capital=10_000.0,
            objective="sharpe",
            slippage_applied=True,
            params=kwargs["params"],
        )
        assert kwargs["config_hash"] == expected_hash

    async def test_no_repo_no_persistence(self) -> None:
        svc, _ = _make_optimizer(_bars(200), repo=None)
        # Apenas verificar que o teste roda sem chamar save_run nem dar erro
        result = await svc.optimize("PETR4", "rsi", range_period="1y")
        assert result.ticker == "PETR4"

    async def test_repo_failure_does_not_break_response(self) -> None:
        repo = AsyncMock()
        repo.save_run.side_effect = RuntimeError("DB down")
        svc, _ = _make_optimizer(_bars(200), repo)

        # Nao deve propagar — best-effort
        result = await svc.optimize("PETR4", "rsi", range_period="1y")
        assert result.ticker == "PETR4"
        repo.save_run.assert_awaited_once()

    async def test_persists_full_result_including_dsr(self) -> None:
        repo = AsyncMock()
        repo.save_run.return_value = ({"id": "x"}, True)
        svc, _ = _make_optimizer(_bars(200), repo)

        await svc.optimize("PETR4", "rsi", range_period="1y")

        kwargs = repo.save_run.await_args.kwargs
        full = kwargs["full_result"]
        assert "top" in full
        assert "best_params" in full
        # DSR deve estar presente quando ha runs validos suficientes
        # (mais de 2 valid_runs + sample_size > 30)
        if full.get("valid_runs", 0) >= 2:
            assert "deflated_sharpe" in full


# ── WalkForwardService persistence ────────────────────────────────────────────


@pytest.mark.asyncio
class TestWalkForwardPersist:
    async def test_persists_with_wf_strategy_prefix(self) -> None:
        repo = AsyncMock()
        repo.save_run.return_value = ({"id": "x"}, True)
        svc, _ = _make_walkforward(_bars(400), repo)

        await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)

        repo.save_run.assert_awaited_once()
        kwargs = repo.save_run.await_args.kwargs
        # Strategy deve ser prefixada para nao colidir com runs grid_search
        assert kwargs["strategy"] == "wf:rsi"
        # Params devem refletir parametros do walk-forward, nao da estrategia
        assert "n_splits" in kwargs["params"]
        assert "oos_pct" in kwargs["params"]
        assert "anchored" in kwargs["params"]
        assert kwargs["params"]["n_splits"] == 3

    async def test_persisted_payload_has_synthetic_top_for_repo_compat(self) -> None:
        """save_run extrai metricas de top[0].metrics — WF deve montar essa shape."""
        repo = AsyncMock()
        repo.save_run.return_value = ({"id": "x"}, True)
        svc, _ = _make_walkforward(_bars(400), repo)

        await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)

        full = repo.save_run.await_args.kwargs["full_result"]
        assert "top" in full
        assert isinstance(full["top"], list) and full["top"]
        m = full["top"][0]["metrics"]
        # Todas as colunas escalares do BacktestResultModel
        assert "total_return_pct" in m
        assert "sharpe_ratio" in m
        assert "max_drawdown_pct" in m
        assert "win_rate_pct" in m
        assert "profit_factor" in m
        assert "total_trades" in m
        # Drilldown completo do walk-forward original deve ficar guardado
        assert "walkforward" in full
        assert "folds" in full["walkforward"]

    async def test_no_repo_no_persistence(self) -> None:
        svc, _ = _make_walkforward(_bars(400), repo=None)
        result = await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)
        assert result.ticker == "PETR4"

    async def test_repo_failure_does_not_break_response(self) -> None:
        repo = AsyncMock()
        repo.save_run.side_effect = RuntimeError("DB down")
        svc, _ = _make_walkforward(_bars(400), repo)

        result = await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)
        assert result.ticker == "PETR4"

    async def test_persists_dsr_aggregate_when_present(self) -> None:
        """Quando WF agrega DSR, payload sintetico inclui deflated_sharpe
        para o repo extrair em colunas escalares."""
        import math
        import random

        # Bars com zigzag denso + ruido — produz trades suficientes em OOS
        # para o DSR ser computado por fold.
        rnd = random.Random(42)
        bars = []
        for i in range(800):
            p = (
                100.0
                + 20.0 * math.sin(i * 0.5)
                + 8.0 * math.sin(i * 1.7)
                + rnd.uniform(-2.0, 2.0)
                + i * 0.02
            )
            bars.append({
                "time": 1700_000_000 + i * 86400,
                "open": p,
                "high": p + 1.5,
                "low": p - 1.5,
                "close": p,
                "volume": 1_000_000.0,
            })

        repo = AsyncMock()
        repo.save_run.return_value = ({"id": "x"}, True)
        svc, _ = _make_walkforward(bars, repo)

        result = await svc.run("PETR4", "rsi", range_period="2y", n_splits=3)
        # Sanity: o WF realmente agregou DSR
        if result.deflated_sharpe is None:
            pytest.skip("DSR nao agregado (bars insuficientes para gerar OOS DSR neste run)")

        kwargs = repo.save_run.await_args.kwargs
        full = kwargs["full_result"]
        assert "deflated_sharpe" in full
        dsr = full["deflated_sharpe"]
        # Repo extrai estas chaves para colunas escalares (BacktestResultModel)
        assert "deflated_sharpe" in dsr
        assert "prob_real" in dsr
        assert "num_trials" in dsr
        assert "sample_size" in dsr

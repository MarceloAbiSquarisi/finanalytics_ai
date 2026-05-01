"""
Endpoints de historico de backtest (R5 follow-up).

GET    /api/v1/backtest/history
GET    /api/v1/backtest/history/{config_hash}
DELETE /api/v1/backtest/history/{config_hash}

Estrategia: monta uma FastAPI minimalista so com o router de backtest e
poppula app.state.backtest_result_repo com mock. Foco e validar contrato
da rota (params, status, shape do JSON), nao logica do repo (ja coberta
em test_backtest_repo.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from finanalytics_ai.interfaces.api.routes.backtest import router as backtest_router


def _make_app(repo: Any) -> FastAPI:
    app = FastAPI()
    app.state.backtest_result_repo = repo
    app.include_router(backtest_router)
    return app


def _row(config_hash: str = "a" * 64, ticker: str = "PETR4") -> dict[str, Any]:
    """Mock de row dict como retornado pelo repo.to_dict()."""
    return {
        "id": "id-1",
        "config_hash": config_hash,
        "user_id": None,
        "ticker": ticker,
        "strategy": "rsi",
        "range_period": "1y",
        "start_date": None,
        "end_date": None,
        "initial_capital": 10_000.0,
        "objective": "sharpe",
        "slippage_applied": True,
        "metrics": {
            "total_return_pct": 12.5,
            "sharpe_ratio": 0.8,
            "max_drawdown_pct": 15.0,
            "win_rate_pct": 60.0,
            "profit_factor": 1.5,
            "calmar_ratio": 0.83,
            "total_trades": 12,
            "bars_count": 252,
        },
        "deflated_sharpe": {
            "deflated_sharpe": 0.31,
            "prob_real": 0.62,
            "num_trials": 30,
            "sample_size": 251,
        },
        "params": {"period": 14},
        "full_result": {"top": [{"rank": 1}]},
        "created_at": "2026-05-01T12:00:00+00:00",
        "updated_at": "2026-05-01T12:00:00+00:00",
    }


# ── GET /history ──────────────────────────────────────────────────────────────


class TestListHistory:
    def test_returns_compact_shape_without_full_result(self) -> None:
        repo = AsyncMock()
        repo.list.return_value = [_row(), _row(config_hash="b" * 64, ticker="VALE3")]
        client = TestClient(_make_app(repo))

        resp = client.get("/api/v1/backtest/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["limit"] == 50
        assert data["offset"] == 0
        # Cada item deve trazer metrics + DSR mas NAO full_result (volumoso)
        for item in data["items"]:
            assert "metrics" in item
            assert "deflated_sharpe" in item
            assert "full_result" not in item

    def test_filter_by_ticker_passed_to_repo(self) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        client = TestClient(_make_app(repo))

        client.get("/api/v1/backtest/history?ticker=PETR4&strategy=rsi&limit=10&offset=5")

        repo.list.assert_awaited_once_with(
            ticker="PETR4", strategy="rsi", limit=10, offset=5
        )

    def test_503_when_repo_not_initialized(self) -> None:
        app = FastAPI()
        app.state.backtest_result_repo = None
        app.include_router(backtest_router)
        client = TestClient(app)

        resp = client.get("/api/v1/backtest/history")
        assert resp.status_code == 503

    def test_limit_validation(self) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        client = TestClient(_make_app(repo))

        # limit > 200 e rejeitado pelo Query(le=200)
        resp = client.get("/api/v1/backtest/history?limit=500")
        assert resp.status_code == 422


# ── GET /history/{hash} ───────────────────────────────────────────────────────


class TestGetHistory:
    def test_returns_full_row(self) -> None:
        h = "a" * 64
        repo = AsyncMock()
        repo.get_by_hash.return_value = _row(config_hash=h)
        client = TestClient(_make_app(repo))

        resp = client.get(f"/api/v1/backtest/history/{h}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["config_hash"] == h
        # Drilldown DEVE incluir full_result
        assert "full_result" in data

    def test_404_when_not_found(self) -> None:
        repo = AsyncMock()
        repo.get_by_hash.return_value = None
        client = TestClient(_make_app(repo))

        resp = client.get("/api/v1/backtest/history/" + ("z" * 64))
        assert resp.status_code == 404


# ── DELETE /history/{hash} ────────────────────────────────────────────────────


class TestDeleteHistory:
    def test_returns_deleted_true(self) -> None:
        repo = AsyncMock()
        repo.delete.return_value = True
        client = TestClient(_make_app(repo))

        resp = client.delete("/api/v1/backtest/history/" + ("a" * 64))

        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_404_when_not_found(self) -> None:
        repo = AsyncMock()
        repo.delete.return_value = False
        client = TestClient(_make_app(repo))

        resp = client.delete("/api/v1/backtest/history/" + ("z" * 64))
        assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

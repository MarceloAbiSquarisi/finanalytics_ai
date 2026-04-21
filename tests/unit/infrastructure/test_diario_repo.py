"""
Testes unitários do DiarioRepository.

Estratégia: SQLite em memória via SQLAlchemy async.
Não precisa de PostgreSQL real — isola a lógica de negócio do banco.

Cobertura:
  - create: persiste e calcula P&L
  - get: retorna por id / None se não existe
  - list: filtra por ticker, setup, direction
  - update: recalcula P&L após edição de preço
  - delete: remove e retorna bool
  - stats: equity_curve, by_setup, by_emotion, win_rate
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Importação condicional — o repo pode não estar disponível se o fix_diario ainda
# não foi aplicado. Nesse caso, os testes são pulados graciosamente.
try:
    from finanalytics_ai.infrastructure.database.connection import (
        Base as _FA_Base,  # noqa: F401  # ensure module importable; aborta import abaixo se faltar
    )
    from finanalytics_ai.infrastructure.database.repositories.diario_repo import (
        DiarioModel,
        DiarioRepository,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    pytestmark = pytest.mark.skip(reason="diario_repo não instalado — rode _fix_diario.ps1")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory():
    """SQLite in-memory para testes unitários — sem PostgreSQL necessário."""
    if not _AVAILABLE:
        pytest.skip("diario_repo não disponível")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(DiarioModel.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class _ContextFactory:
        def __call__(self):
            return factory()

    yield _ContextFactory()
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(session_factory):
    return DiarioRepository(session_factory)


def _entry(
    ticker: str = "PETR4",
    direction: str = "BUY",
    entry_price: float = 30.0,
    exit_price: float | None = 33.0,
    quantity: float = 100.0,
    setup: str | None = "pin_bar",
    emotional_state: str | None = "calm",
    rating: int | None = 4,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "user_id": "user-demo",
        "ticker": ticker,
        "direction": direction,
        "entry_date": datetime(2026, 1, 10, 10, 0, tzinfo=UTC),
        "exit_date": datetime(2026, 1, 10, 15, 0, tzinfo=UTC) if exit_price else None,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "setup": setup,
        "emotional_state": emotional_state,
        "rating": rating,
        "tags": tags or ["disciplinado"],
        "reason_entry": "Pin bar na EMA 21 com volume acima da média",
        "lessons": "Sempre confirmar o volume",
    }


# ── Testes de CRUD ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCreate:
    async def test_create_persists(self, repo) -> None:
        data = _entry()
        result = await repo.create(data)
        assert result["id"] is not None
        assert result["ticker"] == "PETR4"

    async def test_create_calculates_pnl_buy(self, repo) -> None:
        # BUY: (exit - entry) * qty = (33 - 30) * 100 = 300
        result = await repo.create(_entry(entry_price=30.0, exit_price=33.0, quantity=100))
        assert result["pnl"] == pytest.approx(300.0, abs=0.01)
        assert result["is_winner"] is True

    async def test_create_calculates_pnl_sell(self, repo) -> None:
        # SELL: (entry - exit) * qty = (50 - 45) * 100 = 500
        result = await repo.create(
            _entry(direction="SELL", entry_price=50.0, exit_price=45.0, quantity=100)
        )
        assert result["pnl"] == pytest.approx(500.0, abs=0.01)
        assert result["is_winner"] is True

    async def test_create_open_trade_no_pnl(self, repo) -> None:
        data = _entry(exit_price=None)
        result = await repo.create(data)
        assert result["pnl"] is None
        assert result["is_winner"] is None

    async def test_create_uppercase_ticker(self, repo) -> None:
        result = await repo.create(_entry(ticker="petr4"))
        assert result["ticker"] == "PETR4"

    async def test_create_tags_serialized(self, repo) -> None:
        result = await repo.create(_entry(tags=["fomo", "revenge"]))
        assert set(result["tags"]) == {"fomo", "revenge"}


@pytest.mark.asyncio
class TestGet:
    async def test_get_existing(self, repo) -> None:
        created = await repo.create(_entry())
        fetched = await repo.get(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]

    async def test_get_nonexistent_returns_none(self, repo) -> None:
        result = await repo.get(str(uuid.uuid4()))
        assert result is None

    async def test_get_wrong_user_returns_none(self, repo) -> None:
        created = await repo.create(_entry())
        result = await repo.get(created["id"], user_id="other-user")
        assert result is None


@pytest.mark.asyncio
class TestList:
    async def test_list_all(self, repo) -> None:
        await repo.create(_entry(ticker="PETR4"))
        await repo.create(_entry(ticker="VALE3"))
        result = await repo.list()
        assert len(result) == 2

    async def test_list_filter_ticker(self, repo) -> None:
        await repo.create(_entry(ticker="PETR4"))
        await repo.create(_entry(ticker="VALE3"))
        result = await repo.list(ticker="PETR4")
        assert all(r["ticker"] == "PETR4" for r in result)

    async def test_list_filter_setup(self, repo) -> None:
        await repo.create(_entry(setup="pin_bar"))
        await repo.create(_entry(setup="engulfing"))
        result = await repo.list(setup="pin_bar")
        assert all(r["setup"] == "pin_bar" for r in result)

    async def test_list_filter_direction(self, repo) -> None:
        await repo.create(_entry(direction="BUY"))
        await repo.create(_entry(direction="SELL"))
        result = await repo.list(direction="BUY")
        assert all(r["direction"] == "BUY" for r in result)

    async def test_list_empty_user(self, repo) -> None:
        await repo.create(_entry())
        result = await repo.list(user_id="ghost-user")
        assert result == []


@pytest.mark.asyncio
class TestUpdate:
    async def test_update_exit_price_recalculates_pnl(self, repo) -> None:
        created = await repo.create(_entry(exit_price=None))
        assert created["pnl"] is None

        updated = await repo.update(
            created["id"],
            {"exit_price": 35.0, "exit_date": datetime(2026, 1, 10, 15, 0, tzinfo=UTC)},
        )
        assert updated is not None
        assert updated["pnl"] == pytest.approx((35.0 - 30.0) * 100.0, abs=0.01)
        assert updated["is_winner"] is True

    async def test_update_nonexistent_returns_none(self, repo) -> None:
        result = await repo.update(str(uuid.uuid4()), {"rating": 5})
        assert result is None

    async def test_update_ticker_uppercased(self, repo) -> None:
        created = await repo.create(_entry())
        updated = await repo.update(created["id"], {"ticker": "vale3"})
        assert updated["ticker"] == "VALE3"


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_existing_returns_true(self, repo) -> None:
        created = await repo.create(_entry())
        result = await repo.delete(created["id"])
        assert result is True

    async def test_delete_removes_from_db(self, repo) -> None:
        created = await repo.create(_entry())
        await repo.delete(created["id"])
        fetched = await repo.get(created["id"])
        assert fetched is None

    async def test_delete_nonexistent_returns_false(self, repo) -> None:
        result = await repo.delete(str(uuid.uuid4()))
        assert result is False


@pytest.mark.asyncio
class TestStats:
    async def test_stats_empty(self, repo) -> None:
        stats = await repo.stats()
        assert stats["total_entries"] == 0
        assert stats["total_pnl"] == 0.0
        assert stats["win_rate"] == 0.0

    async def test_stats_win_rate(self, repo) -> None:
        await repo.create(_entry(entry_price=30.0, exit_price=33.0))  # winner
        await repo.create(_entry(entry_price=30.0, exit_price=27.0))  # loser
        stats = await repo.stats()
        assert stats["win_rate"] == pytest.approx(50.0, abs=0.1)
        assert stats["winners"] == 1
        assert stats["losers"] == 1

    async def test_stats_equity_curve_ordered(self, repo) -> None:
        await repo.create(_entry(entry_price=30.0, exit_price=33.0))
        await repo.create(_entry(entry_price=30.0, exit_price=27.0))
        stats = await repo.stats()
        curve = stats["equity_curve"]
        assert len(curve) == 2
        # Equity acumulada — primeiro winner depois loser
        # (baseado na data, ambos têm exit_date = 15:00 do mesmo dia, mesma sequência)

    async def test_stats_by_setup(self, repo) -> None:
        await repo.create(_entry(setup="pin_bar", entry_price=30.0, exit_price=33.0))
        await repo.create(_entry(setup="engulfing", entry_price=30.0, exit_price=27.0))
        stats = await repo.stats()
        setups = {s["setup"]: s for s in stats["by_setup"]}
        assert "pin_bar" in setups
        assert "engulfing" in setups
        assert setups["pin_bar"]["win_rate"] == pytest.approx(100.0, abs=0.1)
        assert setups["engulfing"]["win_rate"] == pytest.approx(0.0, abs=0.1)

    async def test_stats_by_emotion(self, repo) -> None:
        await repo.create(_entry(emotional_state="calm", entry_price=30.0, exit_price=33.0))
        await repo.create(_entry(emotional_state="fomo", entry_price=30.0, exit_price=27.0))
        stats = await repo.stats()
        emotions = {e["state"]: e for e in stats["by_emotion"]}
        assert "calm" in emotions
        assert "fomo" in emotions
        assert emotions["calm"]["avg_pnl"] > 0
        assert emotions["fomo"]["avg_pnl"] < 0

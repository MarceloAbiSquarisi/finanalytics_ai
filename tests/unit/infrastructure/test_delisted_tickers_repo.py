"""Testes do delisted_tickers_repo (R5 step 2 — survivorship bias)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finanalytics_ai.infrastructure.database.repositories.delisted_tickers_repo import (
    DelistedTickerModel,
    DelistingInfo,
    get_delisting_info,
    list_delisted_in_range,
)


@pytest_asyncio.fixture
async def session():
    """SQLite in-memory + tabela b3_delisted_tickers vazia."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(DelistedTickerModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession, **fields) -> None:
    """Helper p/ inserir 1 row na tabela."""
    now = datetime.now(UTC)
    base = {
        "ticker": "TEST3",
        "cnpj": None,
        "razao_social": None,
        "delisting_date": date(2023, 6, 15),
        "delisting_reason": "OUTRO",
        "last_known_price": 50.0,
        "last_known_date": date(2023, 6, 15),
        "source": "FINTZ",
        "notes": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(fields)
    session.add(DelistedTickerModel(**base))
    await session.commit()


# ── get_delisting_info ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_delisting_info_found(session: AsyncSession) -> None:
    await _seed(session, ticker="ENBR3", delisting_date=date(2023, 8, 21), last_known_price=24.08)
    info = await get_delisting_info(session, "ENBR3")
    assert info is not None
    assert info.ticker == "ENBR3"
    assert info.delisting_date == date(2023, 8, 21)
    assert info.last_known_price == pytest.approx(24.08)
    assert info.source == "FINTZ"
    assert info.is_high_confidence is True


@pytest.mark.asyncio
async def test_get_delisting_info_not_found(session: AsyncSession) -> None:
    info = await get_delisting_info(session, "PETR4")
    assert info is None


@pytest.mark.asyncio
async def test_get_delisting_info_skips_unk_placeholder(session: AsyncSession) -> None:
    """Tickers UNK_<cnpj> da CVM nao tem ticker real — devem retornar None."""
    await _seed(
        session,
        ticker="UNK_12345678901234",
        delisting_date=None,
        last_known_price=None,
        source="CVM",
    )
    info = await get_delisting_info(session, "UNK_12345678901234")
    assert info is None


@pytest.mark.asyncio
async def test_get_delisting_info_skips_when_delisting_date_null(
    session: AsyncSession,
) -> None:
    """Row com delisting_date NULL nao e' utilizavel pelo engine."""
    await _seed(session, ticker="PARTIAL", delisting_date=None)
    info = await get_delisting_info(session, "PARTIAL")
    assert info is None


@pytest.mark.asyncio
async def test_get_delisting_info_case_insensitive(session: AsyncSession) -> None:
    await _seed(session, ticker="VIIA3", delisting_date=date(2023, 9, 19))
    info = await get_delisting_info(session, "viia3")
    assert info is not None
    assert info.ticker == "VIIA3"


@pytest.mark.asyncio
async def test_is_high_confidence_only_for_fintz(session: AsyncSession) -> None:
    """Apenas source='FINTZ' marca high_confidence."""
    info_cvm = DelistingInfo(
        ticker="X",
        delisting_date=date(2023, 1, 1),
        last_known_price=None,
        source="CVM",
    )
    info_fintz = DelistingInfo(
        ticker="Y",
        delisting_date=date(2023, 1, 1),
        last_known_price=None,
        source="FINTZ",
    )
    assert info_cvm.is_high_confidence is False
    assert info_fintz.is_high_confidence is True


# ── list_delisted_in_range ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_delisted_in_range_returns_only_within(session: AsyncSession) -> None:
    await _seed(session, ticker="A1", delisting_date=date(2023, 5, 10))
    await _seed(session, ticker="A2", delisting_date=date(2023, 8, 21))
    await _seed(session, ticker="A3", delisting_date=date(2024, 11, 14))
    await _seed(session, ticker="OLD", delisting_date=date(2018, 1, 1))

    rows = await list_delisted_in_range(session, date(2023, 1, 1), date(2024, 12, 31))
    tickers = {r.ticker for r in rows}
    assert tickers == {"A1", "A2", "A3"}
    assert "OLD" not in tickers


@pytest.mark.asyncio
async def test_list_delisted_in_range_excludes_unk_placeholders(
    session: AsyncSession,
) -> None:
    await _seed(session, ticker="REAL3", delisting_date=date(2023, 5, 10))
    await _seed(
        session,
        ticker="UNK_98765432109876",
        delisting_date=date(2023, 5, 10),
        source="CVM",
    )
    rows = await list_delisted_in_range(session, date(2023, 1, 1), date(2023, 12, 31))
    tickers = {r.ticker for r in rows}
    assert "REAL3" in tickers
    assert "UNK_98765432109876" not in tickers


@pytest.mark.asyncio
async def test_list_delisted_in_range_only_high_confidence(
    session: AsyncSession,
) -> None:
    await _seed(
        session,
        ticker="FINTZ_TICKER",
        delisting_date=date(2023, 5, 10),
        source="FINTZ",
    )
    await _seed(
        session,
        ticker="MANUAL_TICKER",
        delisting_date=date(2023, 6, 1),
        source="MANUAL",
    )
    rows_all = await list_delisted_in_range(
        session, date(2023, 1, 1), date(2023, 12, 31), only_high_confidence=False
    )
    rows_high = await list_delisted_in_range(
        session, date(2023, 1, 1), date(2023, 12, 31), only_high_confidence=True
    )
    assert {r.ticker for r in rows_all} == {"FINTZ_TICKER", "MANUAL_TICKER"}
    assert {r.ticker for r in rows_high} == {"FINTZ_TICKER"}


@pytest.mark.asyncio
async def test_list_delisted_in_range_empty_result(session: AsyncSession) -> None:
    rows = await list_delisted_in_range(session, date(2023, 1, 1), date(2023, 12, 31))
    assert rows == []

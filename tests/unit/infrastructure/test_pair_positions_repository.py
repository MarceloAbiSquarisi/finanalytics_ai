"""
Testes do PairPositionsRepository (R3.2.B.3) — psycopg2 mockado.

Cobertura: contrato do Protocol + invariantes.
- get sem row -> NONE
- get com row -> PairPosition correta
- all snapshot
- upsert NONE -> ValueError
- upsert valido (insert path)
- upsert com last_cl_ord_id None preserva valor existente (UPDATE path)
- delete

Mocks: substitui `psycopg2.connect` via patch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from finanalytics_ai.domain.pairs.strategy_logic import PairPosition
from finanalytics_ai.infrastructure.database.repositories.pair_positions_repository import (
    PsycopgPairPositionsRepository,
)


def _mock_connect(fetchone_value=None, fetchall_value=None):
    """Helper: cria psycopg2.connect mock com cursor configurado."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_value
    cur.fetchall.return_value = fetchall_value or []
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    return conn, cur


# ── get ──────────────────────────────────────────────────────────────────────


class TestGet:
    def test_no_row_returns_none(self) -> None:
        conn, cur = _mock_connect(fetchone_value=None)
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            result = repo.get("CMIN3-VALE3")
        assert result == PairPosition.NONE

    def test_returns_long_spread(self) -> None:
        conn, cur = _mock_connect(fetchone_value=("LONG_SPREAD",))
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            result = repo.get("CMIN3-VALE3")
        assert result == PairPosition.LONG_SPREAD

    def test_returns_short_spread(self) -> None:
        conn, cur = _mock_connect(fetchone_value=("SHORT_SPREAD",))
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            result = repo.get("PETR3-PETR4")
        assert result == PairPosition.SHORT_SPREAD


# ── all ──────────────────────────────────────────────────────────────────────


class TestAll:
    def test_empty(self) -> None:
        conn, cur = _mock_connect(fetchall_value=[])
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            assert repo.all() == {}

    def test_multiple_pairs(self) -> None:
        conn, cur = _mock_connect(
            fetchall_value=[
                ("CMIN3-VALE3", "LONG_SPREAD"),
                ("PETR3-PETR4", "SHORT_SPREAD"),
            ]
        )
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            result = repo.all()
        assert result == {
            "CMIN3-VALE3": PairPosition.LONG_SPREAD,
            "PETR3-PETR4": PairPosition.SHORT_SPREAD,
        }


# ── upsert ───────────────────────────────────────────────────────────────────


class TestUpsert:
    def test_none_raises(self) -> None:
        repo = PsycopgPairPositionsRepository("postgres://stub")
        with pytest.raises(ValueError, match="NONE invalido"):
            repo.upsert("CMIN3-VALE3", PairPosition.NONE)

    def test_long_spread_with_cl_ord_id(self) -> None:
        conn, cur = _mock_connect()
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            repo.upsert(
                "CMIN3-VALE3",
                PairPosition.LONG_SPREAD,
                last_cl_ord_id="pairs:CMIN3-VALE3:a:OPEN_LONG_SPREAD:2026-05-04T11:00",
            )
        # Verifica params da query
        call = cur.execute.call_args
        sql, params = call.args
        assert "INSERT INTO robot_pair_positions" in sql
        assert "ON CONFLICT" in sql
        assert params == (
            "CMIN3-VALE3",
            "LONG_SPREAD",
            "pairs:CMIN3-VALE3:a:OPEN_LONG_SPREAD:2026-05-04T11:00",
        )
        conn.commit.assert_called_once()

    def test_short_spread_no_cl_ord_id(self) -> None:
        """COALESCE preserva valor existente quando None passado."""
        conn, cur = _mock_connect()
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            repo.upsert("PETR3-PETR4", PairPosition.SHORT_SPREAD)
        call = cur.execute.call_args
        params = call.args[1]
        assert params == ("PETR3-PETR4", "SHORT_SPREAD", None)


# ── delete ───────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_executes_and_commits(self) -> None:
        conn, cur = _mock_connect()
        psycopg2_mock = MagicMock(connect=MagicMock(return_value=conn))
        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock}):
            repo = PsycopgPairPositionsRepository("postgres://stub")
            repo.delete("CMIN3-VALE3")
        call = cur.execute.call_args
        sql, params = call.args
        assert "DELETE FROM robot_pair_positions" in sql
        assert params == ("CMIN3-VALE3",)
        conn.commit.assert_called_once()

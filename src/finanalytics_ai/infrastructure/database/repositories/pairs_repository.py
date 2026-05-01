"""
Repository de pares cointegrados (R3.2.B).

Le da tabela cointegrated_pairs (Postgres principal — Alembic 0023).
Sync via psycopg2 — consistente com o pattern de auto_trader_worker
(o consumer principal vai ser PairsTradingStrategy rodando dentro do
worker).

Caso queira usar via API/UI futuramente, reuso async/SQLAlchemy seria
appropriate (R3.3 UI). Por ora, sync pra worker.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from finanalytics_ai.domain.pairs.entities import ActivePair


class PairsRepository(Protocol):
    """Port — implementacao real le DB; tests usam in-memory stub."""

    def get_active_pairs(self, *, min_test_date: date | None = None) -> list[ActivePair]:
        """
        Retorna pares com cointegrated=TRUE e last_test_date >= min_test_date.

        min_test_date default = today - 7d (pares sem re-test recente NAO
        sao tradeable — cointegracao quebra em regime change e exige
        validacao continua).
        """
        ...


class PsycopgPairsRepository:
    """Implementacao concreta lendo cointegrated_pairs via psycopg2 sync."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def get_active_pairs(
        self, *, min_test_date: date | None = None
    ) -> list[ActivePair]:
        if min_test_date is None:
            min_test_date = date.today() - timedelta(days=7)

        sql = """
            SELECT ticker_a, ticker_b, beta, rho, p_value_adf,
                   half_life, lookback_days, last_test_date
              FROM cointegrated_pairs
             WHERE cointegrated = TRUE
               AND last_test_date >= %s
             ORDER BY p_value_adf ASC
        """
        # Import diferido p/ permitir tests sem psycopg2 instalado
        import psycopg2

        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (min_test_date,))
            rows = cur.fetchall()

        return [_row_to_active_pair(r) for r in rows]


def _row_to_active_pair(row: tuple[Any, ...]) -> ActivePair:
    ticker_a, ticker_b, beta, rho, p_value_adf, half_life, lookback_days, last_test_date = row
    return ActivePair(
        ticker_a=str(ticker_a),
        ticker_b=str(ticker_b),
        beta=float(beta),
        rho=float(rho),
        p_value_adf=float(p_value_adf),
        half_life=float(half_life) if half_life is not None else None,
        lookback_days=int(lookback_days),
        last_test_date=last_test_date,
    )

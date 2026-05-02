"""
Repositório de posições persistentes do PairsTradingStrategy (R3.2.B.3).

Substitui o dict in-memory do auto_trader_worker. Tabela
`robot_pair_positions` em Postgres principal (Alembic 0024). Convenção:
row presente = posição aberta; CLOSE/STOP = DELETE da row.

Sync via psycopg2 — consistente com `auto_trader_worker._db_*` e
PsycopgPairsRepository (R3.2.B.1).
"""

from __future__ import annotations

from typing import Any, Protocol

from finanalytics_ai.domain.pairs.strategy_logic import PairPosition


class PairPositionsRepository(Protocol):
    """Port — implementação real le DB; tests usam in-memory stub."""

    def get(self, pair_key: str) -> PairPosition: ...

    def all(self) -> dict[str, PairPosition]: ...

    def upsert(
        self, pair_key: str, position: PairPosition, last_cl_ord_id: str | None = None
    ) -> None: ...

    def delete(self, pair_key: str) -> None: ...


class PsycopgPairPositionsRepository:
    """
    Implementação concreta lendo/escrevendo robot_pair_positions via
    psycopg2 sync.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def get(self, pair_key: str) -> PairPosition:
        sql = "SELECT position FROM robot_pair_positions WHERE pair_key = %s"
        import psycopg2

        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (pair_key,))
            row = cur.fetchone()
        if row is None:
            return PairPosition.NONE
        return PairPosition(row[0])

    def all(self) -> dict[str, PairPosition]:
        """Snapshot completo p/ load no boot do worker."""
        sql = "SELECT pair_key, position FROM robot_pair_positions"
        import psycopg2

        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return {pk: PairPosition(p) for pk, p in rows}

    def upsert(
        self,
        pair_key: str,
        position: PairPosition,
        last_cl_ord_id: str | None = None,
    ) -> None:
        """
        Cria ou atualiza posição. Requer position != NONE (CLOSE = delete()).
        last_cl_ord_id atualiza apenas quando OPEN (worker passa o cl_ord_id
        do leg_a). Em UPDATE incremental sem novo cl_ord_id, preserva valor
        existente via COALESCE.
        """
        if position == PairPosition.NONE:
            raise ValueError("upsert com position=NONE invalido — use delete() pra remover")

        sql = """
            INSERT INTO robot_pair_positions
                (pair_key, position, opened_at, last_dispatch_cl_ord_id, updated_at)
            VALUES (%s, %s, NOW(), %s, NOW())
            ON CONFLICT (pair_key) DO UPDATE
            SET position = EXCLUDED.position,
                last_dispatch_cl_ord_id = COALESCE(
                    EXCLUDED.last_dispatch_cl_ord_id,
                    robot_pair_positions.last_dispatch_cl_ord_id
                ),
                updated_at = NOW()
        """
        import psycopg2

        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (pair_key, position.value, last_cl_ord_id))
            conn.commit()

    def delete(self, pair_key: str) -> None:
        sql = "DELETE FROM robot_pair_positions WHERE pair_key = %s"
        import psycopg2

        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (pair_key,))
            conn.commit()

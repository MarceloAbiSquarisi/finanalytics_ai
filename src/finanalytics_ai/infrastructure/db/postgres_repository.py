"""
Implementação PostgreSQL do AccountRepository.

Usa asyncpg diretamente (sem ORM) para manter controle total das queries
e compatibilidade com TimescaleDB já no projeto.

Decisão de design: `set_active` usa transação explícita com UPDATE em dois
passos (deactivate all → activate one) para garantir atomicidade sem UPSERT
condicional — mais legível e auditável em logs de banco.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import asyncpg

from finanalytics_ai.domain.accounts import (
    AccountNotFoundError,
    AccountRepository,
    AccountStatus,
    AccountType,
    DuplicateAccountError,
    TradingAccount,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS trading_accounts (
    uuid              TEXT        PRIMARY KEY,
    broker_id         TEXT        NOT NULL,
    account_id        TEXT        NOT NULL,
    account_type      TEXT        NOT NULL,   -- 'real' | 'simulator'
    label             TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'inactive',
    routing_password  TEXT,                   -- armazenado em plaintext por ora; candidato a vault
    broker_name       TEXT,
    sub_account_id    TEXT,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    UNIQUE (broker_id, account_id, account_type)
);
"""

_INSERT = """
INSERT INTO trading_accounts
    (uuid, broker_id, account_id, account_type, label, status,
     routing_password, broker_name, sub_account_id, created_at, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
"""

_SELECT_ALL = """
SELECT uuid, broker_id, account_id, account_type, label, status,
       routing_password, broker_name, sub_account_id, created_at, updated_at
FROM trading_accounts
ORDER BY created_at
"""

_SELECT_BY_TYPE = _SELECT_ALL.replace(
    "ORDER BY", "WHERE account_type = $1\nORDER BY"
)

_SELECT_BY_UUID = """
SELECT uuid, broker_id, account_id, account_type, label, status,
       routing_password, broker_name, sub_account_id, created_at, updated_at
FROM trading_accounts
WHERE uuid = $1
"""

_SELECT_BY_BROKER_ACCOUNT = """
SELECT uuid, broker_id, account_id, account_type, label, status,
       routing_password, broker_name, sub_account_id, created_at, updated_at
FROM trading_accounts
WHERE broker_id = $1 AND account_id = $2 AND account_type = $3
"""

_SELECT_ACTIVE = """
SELECT uuid, broker_id, account_id, account_type, label, status,
       routing_password, broker_name, sub_account_id, created_at, updated_at
FROM trading_accounts
WHERE status = 'active'
LIMIT 1
"""

_DEACTIVATE_ALL = "UPDATE trading_accounts SET status = 'inactive', updated_at = NOW()"
_ACTIVATE_ONE   = "UPDATE trading_accounts SET status = 'active',   updated_at = NOW() WHERE uuid = $1"

_UPDATE = """
UPDATE trading_accounts
SET label = $2, routing_password = $3, broker_name = $4,
    sub_account_id = $5, updated_at = $6
WHERE uuid = $1
"""

_DELETE = "DELETE FROM trading_accounts WHERE uuid = $1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_account(row: asyncpg.Record) -> TradingAccount:
    return TradingAccount(
        uuid=row["uuid"],
        broker_id=row["broker_id"],
        account_id=row["account_id"],
        account_type=AccountType(row["account_type"]),
        label=row["label"],
        status=AccountStatus(row["status"]),
        routing_password=row["routing_password"],
        broker_name=row["broker_name"],
        sub_account_id=row["sub_account_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Implementação
# ---------------------------------------------------------------------------

class PostgresAccountRepository:
    """
    AccountRepository sobre asyncpg.

    Recebe um `asyncpg.Pool` — criado uma única vez no startup da aplicação
    e injetado via DI manual.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def migrate(self) -> None:
        """Cria a tabela se não existir. Chame no startup."""
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)

    async def save(self, account: TradingAccount) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _INSERT,
                    account.uuid,
                    account.broker_id,
                    account.account_id,
                    account.account_type.value,
                    account.label,
                    account.status.value,
                    account.routing_password,
                    account.broker_name,
                    account.sub_account_id,
                    account.created_at,
                    account.updated_at,
                )
        except asyncpg.UniqueViolationError:
            raise DuplicateAccountError(
                account.broker_id, account.account_id, account.account_type
            )

    async def get_by_uuid(self, account_uuid: str) -> TradingAccount:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_UUID, account_uuid)
        if not row:
            raise AccountNotFoundError(account_uuid)
        return _row_to_account(row)

    async def get_by_broker_account(
        self,
        broker_id: str,
        account_id: str,
        account_type: AccountType,
    ) -> Optional[TradingAccount]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_BROKER_ACCOUNT, broker_id, account_id, account_type.value
            )
        return _row_to_account(row) if row else None

    async def list_all(self) -> Sequence[TradingAccount]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ALL)
        return [_row_to_account(r) for r in rows]

    async def list_by_type(self, account_type: AccountType) -> Sequence[TradingAccount]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_TYPE, account_type.value)
        return [_row_to_account(r) for r in rows]

    async def get_active(self) -> Optional[TradingAccount]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_ACTIVE)
        return _row_to_account(row) if row else None

    async def set_active(self, account_uuid: str) -> TradingAccount:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Garante que o uuid existe antes de mexer em outras linhas
                row = await conn.fetchrow(_SELECT_BY_UUID, account_uuid)
                if not row:
                    raise AccountNotFoundError(account_uuid)

                await conn.execute(_DEACTIVATE_ALL)
                await conn.execute(_ACTIVATE_ONE, account_uuid)

                # Relê pós-update para retornar estado consistente
                row = await conn.fetchrow(_SELECT_BY_UUID, account_uuid)

        return _row_to_account(row)

    async def update(self, account: TradingAccount) -> None:
        from datetime import datetime, timezone
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPDATE,
                account.uuid,
                account.label,
                account.routing_password,
                account.broker_name,
                account.sub_account_id,
                datetime.now(timezone.utc),
            )

    async def delete(self, account_uuid: str) -> None:
        async with self._pool.acquire() as conn:
            result = await conn.execute(_DELETE, account_uuid)
        if result == "DELETE 0":
            raise AccountNotFoundError(account_uuid)

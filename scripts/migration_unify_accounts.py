"""
Migration: unifica trading_accounts em investment_accounts.

Motivação: uma conta em corretora E a credencial Profit DLL são
o mesmo conceito no mundo real (uma conta pode fazer day trade,
swing, RF, cripto, etc). Ter 2 tabelas separadas cria duplicação
mental e no banco.

Passos (idempotentes):
  1. ALTER investment_accounts ADD COLUMN dll_* + is_dll_active
  2. Unique partial indexes:
     a. ux_inv_accounts_one_dll_sim  : so 1 conta com dll_account_type='simulator'
     b. ux_inv_accounts_one_dll_active_per_user : so 1 is_dll_active=true por user
  3. Migrar dados de trading_accounts → investment_accounts:
     - Para cada trading_account, tenta match com investment_account
       existente (mesmo user; dificil sem user_id em trading_accounts,
       entao criamos stubs por broker_id+account_id se nao houver match)
     - Copia broker_id → dll_broker_id, account_id → dll_account_id,
       routing_password → dll_routing_password,
       account_type → dll_account_type
     - A conta com status='active' vira is_dll_active=true
  4. DROP trading_accounts (confirmacao via flag --drop)

Uso:
  .venv\\Scripts\\python.exe scripts\\migration_unify_accounts.py
  .venv\\Scripts\\python.exe scripts\\migration_unify_accounts.py --drop
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
).replace("postgresql+asyncpg://", "postgresql://")


async def main(drop: bool) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration: unificar trading_accounts → investment_accounts ===\n")

        # 1. ADD COLUMNs (idempotente)
        print("[1/4] ALTER TABLE investment_accounts ADD COLUMN dll_*...")
        for stmt in [
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS dll_broker_id VARCHAR(20)",
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS dll_account_id VARCHAR(50)",
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS dll_sub_account_id VARCHAR(50)",
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS dll_routing_password TEXT",
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS dll_account_type VARCHAR(20)",
            "ALTER TABLE investment_accounts ADD COLUMN IF NOT EXISTS is_dll_active BOOLEAN NOT NULL DEFAULT FALSE",
        ]:
            await conn.execute(stmt)
        print("  ✓ Colunas DLL adicionadas")

        # 2. Unique partial indexes
        print("\n[2/4] Criando unique partial indexes...")
        # Só 1 conta simulator permitida no sistema
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inv_accounts_one_dll_sim
            ON investment_accounts (dll_account_type)
            WHERE dll_account_type = 'simulator'
        """)
        # Só 1 conta dll_active por user
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inv_accounts_one_dll_active_per_user
            ON investment_accounts (user_id)
            WHERE is_dll_active = TRUE
        """)
        # Constraint de consistência: dll_account_type só com is_dll_active=true precisa de broker_id/account_id
        # (fica a nivel aplicacao, nao no DB)
        print("  ✓ Indexes garantidos")

        # 3. Migrar dados
        print("\n[3/4] Migrando trading_accounts → investment_accounts...")
        # Check se trading_accounts existe
        ta_exists = await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='trading_accounts')
        """)
        if not ta_exists:
            print("  - trading_accounts nao existe (ja foi dropada)")
        else:
            tas = await conn.fetch("""
                SELECT uuid, broker_id, account_id, account_type, label, status,
                       routing_password, sub_account_id
                FROM trading_accounts
            """)
            print(f"  - {len(tas)} registros em trading_accounts")

            for ta in tas:
                # Cada user tem investment_accounts distintas. Como trading_accounts
                # nao tem user_id, precisamos de heuristica: pegar todos os users
                # que tem pelo menos 1 investment_account e aplicar a conta DLL a TODOS
                # (nao ideal mas conservador). Na pratica, como tipicamente temos 1
                # usuario em dev, isso vai fazer a coisa certa.
                users_with_accounts = await conn.fetch("""
                    SELECT DISTINCT user_id FROM investment_accounts
                """)
                if not users_with_accounts:
                    print(
                        f"    ! Nenhum user com investment_account — pulando trading_account {ta['uuid']}"
                    )
                    continue

                for u in users_with_accounts:
                    user_id = u["user_id"]
                    # Cria stub se nao houver investment_account com label similar
                    label_hint = ta["label"] or f"Conta DLL {ta['broker_id']}/{ta['account_id']}"
                    # Procura conta que ja tenha as creds DLL dessa trading_account
                    existing = await conn.fetchrow(
                        """
                        SELECT id FROM investment_accounts
                        WHERE user_id = $1
                          AND dll_broker_id = $2
                          AND dll_account_id = $3
                    """,
                        user_id,
                        ta["broker_id"],
                        ta["account_id"],
                    )

                    if existing:
                        print(
                            f"    - user={user_id[:8]} ja tem conta DLL {ta['broker_id']}/{ta['account_id']} — skip"
                        )
                        continue

                    # Se o user tem uma unica investment_account e ela nao tem DLL ainda, anexa
                    user_accounts = await conn.fetch(
                        """
                        SELECT id, apelido FROM investment_accounts
                        WHERE user_id = $1 AND dll_broker_id IS NULL
                    """,
                        user_id,
                    )

                    if ta["account_type"] == "simulator":
                        # Conta simulador: cria uma stub dedicada (ou anexa se for unica)
                        if len(user_accounts) == 1:
                            target = user_accounts[0]
                            print(
                                f"    → anexando creds SIM em conta existente user={user_id[:8]} "
                                f"conta={target['apelido'] or target['id'][:8]}"
                            )
                            await conn.execute(
                                """
                                UPDATE investment_accounts
                                SET dll_broker_id = $1,
                                    dll_account_id = $2,
                                    dll_sub_account_id = $3,
                                    dll_routing_password = $4,
                                    dll_account_type = 'simulator',
                                    is_dll_active = $5,
                                    updated_at = NOW()
                                WHERE id = $6
                            """,
                                ta["broker_id"],
                                ta["account_id"],
                                ta["sub_account_id"],
                                ta["routing_password"],
                                ta["status"] == "active",
                                target["id"],
                            )
                        else:
                            # Stub dedicada de simulador
                            import uuid

                            stub_id = str(uuid.uuid4())
                            print(f"    → criando stub SIM user={user_id[:8]} id={stub_id[:8]}")
                            await conn.execute(
                                """
                                INSERT INTO investment_accounts (
                                    id, user_id, institution_name, institution_code,
                                    agency, account_number, account_type,
                                    apelido, is_active,
                                    dll_broker_id, dll_account_id, dll_sub_account_id,
                                    dll_routing_password, dll_account_type, is_dll_active,
                                    created_at, updated_at
                                ) VALUES (
                                    $1, $2, 'Simulador Nelogica', 'SIM',
                                    '—', '—', 'corretora',
                                    $3, TRUE,
                                    $4, $5, $6,
                                    $7, 'simulator', $8,
                                    NOW(), NOW()
                                )
                            """,
                                stub_id,
                                user_id,
                                ta["label"] or "Simulador",
                                ta["broker_id"],
                                ta["account_id"],
                                ta["sub_account_id"],
                                ta["routing_password"],
                                ta["status"] == "active",
                            )
                    else:  # real
                        # Conta real: se houver unica investment_account sem DLL, anexa
                        if len(user_accounts) == 1:
                            target = user_accounts[0]
                            print(
                                f"    → anexando creds REAL em conta existente user={user_id[:8]} "
                                f"conta={target['apelido'] or target['id'][:8]}"
                            )
                            await conn.execute(
                                """
                                UPDATE investment_accounts
                                SET dll_broker_id = $1,
                                    dll_account_id = $2,
                                    dll_sub_account_id = $3,
                                    dll_routing_password = $4,
                                    dll_account_type = 'real',
                                    is_dll_active = $5,
                                    updated_at = NOW()
                                WHERE id = $6
                            """,
                                ta["broker_id"],
                                ta["account_id"],
                                ta["sub_account_id"],
                                ta["routing_password"],
                                ta["status"] == "active",
                                target["id"],
                            )
                        else:
                            # Stub dedicada
                            import uuid

                            stub_id = str(uuid.uuid4())
                            print(f"    → criando stub REAL user={user_id[:8]} id={stub_id[:8]}")
                            await conn.execute(
                                """
                                INSERT INTO investment_accounts (
                                    id, user_id, institution_name, institution_code,
                                    agency, account_number, account_type,
                                    apelido, is_active,
                                    dll_broker_id, dll_account_id, dll_sub_account_id,
                                    dll_routing_password, dll_account_type, is_dll_active,
                                    created_at, updated_at
                                ) VALUES (
                                    $1, $2, $3, $4,
                                    '—', '—', 'corretora',
                                    $5, TRUE,
                                    $6, $7, $8,
                                    $9, 'real', $10,
                                    NOW(), NOW()
                                )
                            """,
                                stub_id,
                                user_id,
                                f"Corretora {ta['broker_id']}",
                                ta["broker_id"],
                                ta["label"] or f"Conta {ta['account_id']}",
                                ta["broker_id"],
                                ta["account_id"],
                                ta["sub_account_id"],
                                ta["routing_password"],
                                ta["status"] == "active",
                            )

        # 4. DROP trading_accounts (se --drop)
        if drop:
            print("\n[4/4] DROP TABLE trading_accounts...")
            if ta_exists:
                await conn.execute("DROP TABLE IF EXISTS trading_accounts CASCADE")
                print("  ✓ Tabela trading_accounts removida")
            else:
                print("  - ja dropada")
        else:
            print("\n[4/4] DROP trading_accounts: SKIPPED (use --drop para remover)")

        # Relatório final
        print("\n=== Estado final ===")
        rows = await conn.fetch("""
            SELECT id, user_id, apelido, institution_name,
                   dll_broker_id, dll_account_id, dll_account_type, is_dll_active
            FROM investment_accounts
            ORDER BY user_id, created_at
        """)
        print(f"investment_accounts: {len(rows)} registros")
        for r in rows:
            dll_info = ""
            if r["dll_broker_id"]:
                dll_info = (
                    f" DLL={r['dll_broker_id']}/{r['dll_account_id']} ({r['dll_account_type']})"
                )
                if r["is_dll_active"]:
                    dll_info += " ★ ACTIVE"
            print(
                f"  {r['id'][:8]} user={r['user_id'][:8]} {r['apelido'] or '(sem apelido)':<25} "
                f"{r['institution_name']:<20}{dll_info}"
            )

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="DROP trading_accounts after migration")
    args = parser.parse_args()
    asyncio.run(main(args.drop))

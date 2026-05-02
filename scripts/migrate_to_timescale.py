"""
Sprint C v3 — Migração com COPY protocol (10-100x mais rápido que executemany).

Mudança principal: asyncpg.copy_records_to_table() usa o protocolo COPY binário
do PostgreSQL — sem round-trips por linha, sem parsing de SQL por batch.
Benchmark esperado: 50.000-200.000 linhas/s vs 300/s do executemany.

Uso:
    python scripts/migrate_to_timescale.py --table itens --parallel 6
    python scripts/migrate_to_timescale.py --table indicadores --parallel 6
    python scripts/migrate_to_timescale.py --verify
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import time

import asyncpg

BATCH_SIZE = 100_000  # maior batch — COPY é eficiente com lotes grandes
POSTGRES_URL = os.getenv(
    "DATABASE_URL", "postgresql://finanalytics:secret@localhost:5432/finanalytics"
)
TIMESCALE_URL = os.getenv(
    "TIMESCALE_URL", "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)


@dataclass
class TableMigration:
    name: str
    source_table: str
    dest_table: str
    time_col: str
    columns: list[str]  # colunas no destino (time primeiro)
    select_exprs: list[str]  # expressões SELECT (mesma ordem)
    year_range: tuple[int, int]


MIGRATIONS: list[TableMigration] = [
    TableMigration(
        name="itens",
        source_table="fintz_itens_contabeis",
        dest_table="fintz_itens_contabeis_ts",
        time_col="data_publicacao",
        columns=["time", "ticker", "item", "tipo_periodo", "valor"],
        select_exprs=[
            "data_publicacao AT TIME ZONE 'UTC'",
            "ticker",
            "item",
            "tipo_periodo",
            "valor",
        ],
        year_range=(2011, 2025),
    ),
    TableMigration(
        name="indicadores",
        source_table="fintz_indicadores",
        dest_table="fintz_indicadores_ts",
        time_col="data_publicacao",
        columns=["time", "ticker", "indicador", "valor"],
        select_exprs=[
            "data_publicacao AT TIME ZONE 'UTC'",
            "ticker",
            "indicador",
            "valor",
        ],
        year_range=(2010, 2025),
    ),
    TableMigration(
        name="cotacoes",
        source_table="fintz_cotacoes",
        dest_table="fintz_cotacoes_ts",
        time_col="data",
        columns=[
            "time",
            "ticker",
            "preco_fechamento",
            "preco_fechamento_ajustado",
            "preco_abertura",
            "preco_minimo",
            "preco_maximo",
            "volume_negociado",
            "fator_ajuste",
            "preco_medio",
            "quantidade_negociada",
            "quantidade_negocios",
            "fator_ajuste_desdobramentos",
            "preco_fechamento_ajustado_desdobramentos",
        ],
        select_exprs=[
            "data AT TIME ZONE 'UTC'",
            "ticker",
            "preco_fechamento",
            "preco_fechamento_ajustado",
            "preco_abertura",
            "preco_minimo",
            "preco_maximo",
            "volume_negociado",
            "fator_ajuste",
            "preco_medio",
            "quantidade_negociada",
            "quantidade_negocios",
            "fator_ajuste_desdobramentos",
            "preco_fechamento_ajustado_desdobramentos",
        ],
        year_range=(2010, 2025),
    ),
]


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s"


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
    c = {"INFO": "\033[36m", "OK": "\033[32m", "WARN": "\033[33m", "ERR": "\033[31m"}.get(level, "")
    print(f"  {c}[{ts}] {msg}\033[0m", flush=True)


async def migrate_year(
    pg_pool: asyncpg.Pool,
    ts_pool: asyncpg.Pool,
    m: TableMigration,
    year: int,
) -> tuple[int, int]:
    y0, y1 = f"{year}-01-01", f"{year + 1}-01-01"

    total: int = await pg_pool.fetchval(
        f"SELECT COUNT(*) FROM {m.source_table} "
        f"WHERE {m.time_col} >= '{y0}' AND {m.time_col} < '{y1}'"
    )
    if total == 0:
        return 0, 0

    # Encontra a última data já migrada para este ano (retomada granular)
    last_ts = await ts_pool.fetchval(
        f"SELECT MAX(time) FROM {m.dest_table} WHERE time >= '{y0}' AND time < '{y1}'"
    )

    already: int = await ts_pool.fetchval(
        f"SELECT COUNT(*) FROM {m.dest_table} WHERE time >= '{y0}' AND time < '{y1}'"
    )

    if already >= total:
        log(f"  {year}: {fmt_num(total)} já migradas — pulando", "WARN")
        return 0, total

    resume_clause = f"AND {m.time_col} > '{last_ts.date()}'" if last_ts else ""
    log(f"  {year}: {fmt_num(total)} linhas ({fmt_num(already)} já existentes)...")

    t0 = time.perf_counter()
    migrated = 0
    offset = 0  # COPY com WHERE de data — não usa offset

    select_sql = ", ".join(m.select_exprs)
    query = (
        f"SELECT {select_sql} FROM {m.source_table} "
        f"WHERE {m.time_col} >= '{y0}' AND {m.time_col} < '{y1}' "
        f"{resume_clause} "
        f"ORDER BY {m.time_col} "
        f"LIMIT $1 OFFSET $2"
    )

    pending = total - already
    while offset < pending:
        rows = await pg_pool.fetch(query, BATCH_SIZE, offset)
        if not rows:
            break

        data = [tuple(r) for r in rows]

        # COPY protocol — ordem de magnitude mais rápido que executemany
        async with ts_pool.acquire() as conn:
            await conn.copy_records_to_table(
                m.dest_table,
                records=data,
                columns=m.columns,
            )

        offset += len(rows)
        migrated += len(rows)

        elapsed = time.perf_counter() - t0
        rate = migrated / max(elapsed, 0.001)
        eta = (pending - migrated) / rate if rate > 0 else 0
        pct = migrated / pending * 100
        print(
            f"\r    {year}: {pct:5.1f}% | "
            f"{fmt_num(migrated)}/{fmt_num(pending)} | "
            f"{fmt_num(int(rate))}/s | ETA {fmt_dur(eta)}    ",
            end="",
            flush=True,
        )

    print()
    elapsed = time.perf_counter() - t0
    log(
        f"  {year}: ✓ {fmt_num(migrated)} em {fmt_dur(elapsed)} "
        f"({fmt_num(int(migrated / max(elapsed, 0.001)))}/s)",
        "OK",
    )
    return migrated, already


async def migrate_table(m: TableMigration, pg_dsn: str, ts_dsn: str, parallel: int) -> None:
    log(f"{'═' * 60}")
    log(f"TABELA: {m.source_table} → {m.dest_table}  (COPY protocol)")
    log(f"{'═' * 60}")

    t0 = time.perf_counter()
    pg_pool = await asyncpg.create_pool(pg_dsn, min_size=4, max_size=max(8, parallel * 2))
    ts_pool = await asyncpg.create_pool(ts_dsn, min_size=4, max_size=max(8, parallel * 2))

    years = list(range(m.year_range[0], m.year_range[1] + 1))
    total_migrated = total_skipped = 0

    for i in range(0, len(years), parallel):
        batch = years[i : i + parallel]
        results = await asyncio.gather(*[migrate_year(pg_pool, ts_pool, m, y) for y in batch])
        for migrated, skipped in results:
            total_migrated += migrated
            total_skipped += skipped

    await pg_pool.close()
    await ts_pool.close()

    elapsed = time.perf_counter() - t0
    log(
        f"TOTAL {m.name}: {fmt_num(total_migrated)} migradas + "
        f"{fmt_num(total_skipped)} existentes em {fmt_dur(elapsed)}",
        "OK",
    )


async def verify_counts(pg_dsn: str, ts_dsn: str) -> None:
    pg_pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    ts_pool = await asyncpg.create_pool(ts_dsn, min_size=1, max_size=2)

    checks = [
        ("fintz_itens_contabeis", "fintz_itens_contabeis_ts"),
        ("fintz_indicadores", "fintz_indicadores_ts"),
        ("fintz_cotacoes", "fintz_cotacoes_ts"),
    ]

    print(f"\n  {'Tabela origem':<30} {'Origem':>12} {'Destino':>12} {'Status':>8}")
    print(f"  {'─' * 30} {'─' * 12} {'─' * 12} {'─' * 8}")

    for src, dst in checks:
        src_n = await pg_pool.fetchval(f"SELECT COUNT(*) FROM {src}")
        try:
            dst_n = await ts_pool.fetchval(f"SELECT COUNT(*) FROM {dst}")
        except Exception:
            dst_n = 0
        pct = dst_n / src_n * 100 if src_n > 0 else 0
        status = "✓ OK" if pct >= 99.9 else f"{pct:.1f}%"
        print(f"  {src:<30} {fmt_num(src_n):>12} {fmt_num(dst_n):>12} {status:>8}")

    rows = await ts_pool.fetch("""
        SELECT hypertable_name,
               pg_size_pretty(hypertable_size(format('%I',hypertable_name)::regclass)) AS size,
               num_chunks
        FROM timescaledb_information.hypertables ORDER BY hypertable_name
    """)
    if rows:
        print(f"\n  {'Hypertable':<35} {'Tamanho':>10} {'Chunks':>8}")
        print(f"  {'─' * 35} {'─' * 10} {'─' * 8}")
        for r in rows:
            print(f"  {r['hypertable_name']:<35} {r['size']:>10} {r['num_chunks']:>8}")

    await pg_pool.close()
    await ts_pool.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table", choices=["itens", "indicadores", "cotacoes", "all"], default="all"
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()

    pg_dsn = POSTGRES_URL.replace("postgresql+asyncpg://", "postgresql://")
    ts_dsn = TIMESCALE_URL.replace("postgresql+asyncpg://", "postgresql://")

    print(f"\n{'═' * 62}")
    print("  Sprint C v3 — Migração com COPY protocol")
    print(f"  Postgres   : {pg_dsn.split('@')[1]}")
    print(f"  TimescaleDB: {ts_dsn.split('@')[1]}")
    print(f"  Batch: {fmt_num(BATCH_SIZE)} | Paralelo: {args.parallel} anos")
    print(f"{'═' * 62}\n")

    if args.verify:
        await verify_counts(pg_dsn, ts_dsn)
        return

    name_map = {"itens": "itens", "indicadores": "indicadores", "cotacoes": "cotacoes"}
    targets = (
        [mg for mg in MIGRATIONS if mg.name == name_map[args.table]]
        if args.table != "all"
        else MIGRATIONS
    )

    t0 = time.perf_counter()
    for mg in targets:
        await migrate_table(mg, pg_dsn, ts_dsn, args.parallel)

    await verify_counts(pg_dsn, ts_dsn)
    print(f"\n  ✓ Migração completa em {fmt_dur(time.perf_counter() - t0)}\n")


if __name__ == "__main__":
    asyncio.run(main())

"""
Screening de pares cointegrados (R3.1) — offline batch.

Carrega closes diarios de fintz_cotacoes_ts (TimescaleDB), roda Engle-Granger
2-step + half-life em todas as combinacoes da watchlist, e UPSERT em
cointegrated_pairs (Postgres principal).

Watchlist default — setores onde existe cointegracao documentada na B3:
  Bancos:   ITUB4, BBDC4, SANB11, BBAS3
  Petro:    PETR3, PETR4
  Mineracao: VALE3, CMIN3

Uso:
  python scripts/cointegration_screen.py                           # dry-run
  python scripts/cointegration_screen.py --persist                 # grava em DB
  python scripts/cointegration_screen.py --lookback 504 --persist  # 2 anos
  python scripts/cointegration_screen.py --watchlist PETR3 PETR4 ITUB4

DSNs (env):
  PROFIT_TIMESCALE_DSN  — fonte de closes (fintz_cotacoes_ts)
  DATABASE_URL          — destino do UPSERT (cointegrated_pairs)

Saida (stdout): tabela com beta, rho, p_value_adf, cointegrated, half_life
ordenada por p_value asc (mais cointegrados primeiro).

Recomendacao operacional: agendar diariamente as 06:30 BRT no scheduler
(antes do open) — pares quebram em regime change (2008/2020), re-test
e' obrigatorio. R3.2 le a tabela e SO trada pares com cointegrated=true
e last_test_date >= today.
"""

from __future__ import annotations

import argparse
from datetime import date
from itertools import combinations
import os
from pathlib import Path
import sys

# Garante import a partir da raiz do projeto
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg2

from finanalytics_ai.domain.pairs.cointegration import (
    CointegrationResult,
    engle_granger,
)

DEFAULT_WATCHLIST = [
    # Bancos (cointegracao classica B3)
    "ITUB4",
    "BBDC4",
    "SANB11",
    "BBAS3",
    # Petroleo (PETR3 vs PETR4 = mesmo ticker em classes diferentes — quase 1:1)
    "PETR3",
    "PETR4",
    # Mineracao
    "VALE3",
    "CMIN3",
]


def load_closes(dsn: str, ticker: str, lookback_days: int) -> list[float]:
    """
    Carrega ate `lookback_days` closes daily mais recentes via UNION
    cross-source (mesmo pattern do `/api/v1/marketdata/candles_daily`).

    Ordem de prioridade no dedup (igual ao endpoint daily): profit_daily_bars
    > ohlc_1m daily_agg > fintz_cotacoes_ts. Necessario porque Fintz freezou
    em 2025-11-03 — sem UNION, screening rodando 2026 testaria cointegracao
    sobre janela 2024-04 .. 2025-11 (5+ meses defasados, pares podem ter
    quebrado correlacao desde entao).

    Retorna list em ordem cronologica (antiga -> nova). Vazio se ticker
    nao existe em nenhuma fonte.
    """
    sql = """
        WITH daily_bars AS (
            SELECT time::date AS dt, close::float, 1 AS prio
            FROM profit_daily_bars
            WHERE ticker = %s AND time >= NOW() - INTERVAL '36 months'
        ),
        ohlc_1m_daily AS (
            SELECT time::date AS dt,
                   ((array_agg(close ORDER BY time DESC))[1])::float AS close,
                   2 AS prio
            FROM ohlc_1m
            WHERE ticker = %s AND time >= NOW() - INTERVAL '36 months'
            GROUP BY time::date
        ),
        fintz_daily AS (
            SELECT time::date AS dt,
                   preco_fechamento_ajustado::float AS close,
                   3 AS prio
            FROM fintz_cotacoes_ts
            WHERE ticker = %s
              AND time >= NOW() - INTERVAL '36 months'
              AND preco_fechamento_ajustado IS NOT NULL
        ),
        combined AS (
            SELECT * FROM daily_bars
            UNION ALL SELECT * FROM ohlc_1m_daily
            UNION ALL SELECT * FROM fintz_daily
        ),
        dedup AS (
            SELECT DISTINCT ON (dt) dt, close
            FROM combined
            ORDER BY dt ASC, prio ASC
        )
        SELECT close FROM dedup
        ORDER BY dt DESC
        LIMIT %s
    """
    t = ticker.upper()
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (t, t, t, lookback_days))
        rows = cur.fetchall()
    # Retorno ordem antiga -> nova
    return [float(r[0]) for r in reversed(rows)]


def upsert_pair(
    *,
    dsn: str,
    ticker_a: str,
    ticker_b: str,
    result: CointegrationResult,
    lookback_days: int,
    test_date: date,
) -> None:
    """UPSERT em cointegrated_pairs respeitando ordem canonica alfabetica."""
    # Garantir ordem alfabetica (ticker_a < ticker_b) — constraint do schema.
    if ticker_a > ticker_b:
        ticker_a, ticker_b = ticker_b, ticker_a
        # beta inverte: se A = beta * B, entao B = (1/beta) * A
        # rho e' simetrico. residuals/p_value validos sob nova ordem? — nao
        # exatamente, mas como o teste foi feito na ordem original, persistir
        # essa metadata ainda e' informativo. Documentado.

    sql = """
        INSERT INTO cointegrated_pairs
            (ticker_a, ticker_b, beta, rho, p_value_adf, cointegrated,
             half_life, lookback_days, last_test_date, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (ticker_a, ticker_b) DO UPDATE
        SET beta            = EXCLUDED.beta,
            rho             = EXCLUDED.rho,
            p_value_adf     = EXCLUDED.p_value_adf,
            cointegrated    = EXCLUDED.cointegrated,
            half_life       = EXCLUDED.half_life,
            lookback_days   = EXCLUDED.lookback_days,
            last_test_date  = EXCLUDED.last_test_date,
            updated_at      = NOW()
    """
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                ticker_a,
                ticker_b,
                result.beta,
                result.rho,
                result.p_value_adf,
                result.cointegrated,
                result.half_life,
                lookback_days,
                test_date,
            ),
        )
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--lookback",
        type=int,
        default=252,
        help="Janela em dias uteis (default 252 = 1 ano)",
    )
    ap.add_argument(
        "--watchlist",
        nargs="+",
        default=DEFAULT_WATCHLIST,
        help=f"Tickers (default: {' '.join(DEFAULT_WATCHLIST)})",
    )
    ap.add_argument(
        "--persist",
        action="store_true",
        help="UPSERT em cointegrated_pairs (default: dry-run, so imprime)",
    )
    ap.add_argument(
        "--p-threshold",
        type=float,
        default=0.05,
        help="ADF p-value threshold (default 0.05)",
    )
    args = ap.parse_args()

    timescale_dsn = os.environ.get(
        "PROFIT_TIMESCALE_DSN",
        "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
    )
    main_dsn = os.environ.get(
        "DATABASE_URL_SYNC",
        os.environ.get(
            "DATABASE_URL",
            "postgresql://finanalytics:postgres@localhost:5432/finanalytics",
        ),
    )
    # Se vier asyncpg URL, normaliza pra psycopg2 sync
    if "asyncpg" in main_dsn:
        main_dsn = main_dsn.replace("+asyncpg", "")

    today = date.today()
    watchlist = [t.upper() for t in args.watchlist]
    pairs = list(combinations(sorted(watchlist), 2))

    print(f"Screening {len(pairs)} pares de {len(watchlist)} tickers, lookback={args.lookback}")
    print(f"Persist: {args.persist} | p_threshold: {args.p_threshold}")
    print("=" * 90)
    print(
        f"{'PAIR':<20} {'BETA':>10} {'RHO':>8} {'P-ADF':>10} {'COINT':>8} {'HALF-LIFE':>12} {'N':>6}"
    )
    print("-" * 90)

    # Carrega closes uma vez por ticker (otimiza I/O)
    closes_by_ticker: dict[str, list[float]] = {}
    for t in watchlist:
        try:
            closes = load_closes(timescale_dsn, t, args.lookback)
        except Exception as exc:
            print(f"  warn: falha ao carregar {t}: {exc}")
            closes = []
        closes_by_ticker[t] = closes

    results: list[tuple[str, str, CointegrationResult]] = []

    for a, b in pairs:
        ca = closes_by_ticker.get(a, [])
        cb = closes_by_ticker.get(b, [])
        # Alinha pelos N mais recentes em comum
        n = min(len(ca), len(cb))
        if n < 30:
            print(f"{a:<8} {b:<8}  insufficient_data ({n} bars)")
            continue
        ca_n = ca[-n:]
        cb_n = cb[-n:]
        try:
            r = engle_granger(ca_n, cb_n, p_threshold=args.p_threshold)
        except Exception as exc:
            print(f"{a:<8} {b:<8}  engle_granger_failed: {exc}")
            continue

        results.append((a, b, r))
        hl_str = f"{r.half_life:.1f}d" if r.half_life is not None else "n/a"
        print(
            f"{a:<8} {b:<8}  {r.beta:>10.4f} {r.rho:>8.4f} "
            f"{r.p_value_adf:>10.4f} {str(r.cointegrated):>8} "
            f"{hl_str:>12} {r.sample_size:>6}"
        )

    # Resumo: cointegrados ordenados por p_value
    cointegrated_results = sorted(
        [(a, b, r) for a, b, r in results if r.cointegrated],
        key=lambda x: x[2].p_value_adf,
    )
    print("-" * 90)
    print(f"\nCointegrados (p < {args.p_threshold}): {len(cointegrated_results)} / {len(results)}")
    for a, b, r in cointegrated_results:
        hl_str = f"{r.half_life:.1f}d" if r.half_life is not None else "n/a"
        print(f"  {a}-{b}: p={r.p_value_adf:.4f}, beta={r.beta:.3f}, half-life={hl_str}")

    if args.persist:
        print(f"\nPersist em cointegrated_pairs ({main_dsn.split('@')[-1]})...")
        for a, b, r in results:
            try:
                upsert_pair(
                    dsn=main_dsn,
                    ticker_a=a,
                    ticker_b=b,
                    result=r,
                    lookback_days=args.lookback,
                    test_date=today,
                )
            except Exception as exc:
                print(f"  upsert failed {a}-{b}: {exc}")
        print(f"Done — {len(results)} rows upserted.")
    else:
        print("\n(dry-run — passe --persist para gravar em cointegrated_pairs)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

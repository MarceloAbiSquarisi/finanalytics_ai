"""
populate_daily_bars.py — Agrega market_history_trades → profit_daily_bars

Lê tickers ativos de profit_history_tickers e gera barras diárias OHLCV
a partir dos ticks históricos. Usa ON CONFLICT para idempotência.

Uso:
    python scripts/populate_daily_bars.py
    python scripts/populate_daily_bars.py --ticker PETR4
    python scripts/populate_daily_bars.py --dry-run
"""
from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import sys
import time

# Carrega .env
_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k not in os.environ:
                os.environ[_k] = _v

DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

_SQL_AGGREGATE_TICKS = """
SELECT
    trade_date::date AS date,
    (array_agg(price ORDER BY trade_date ASC))[1]  AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    (array_agg(price ORDER BY trade_date DESC))[1] AS close,
    SUM(volume)   AS volume,
    CAST(SUM(quantity) AS INTEGER) AS qty,
    COUNT(*)      AS trades
FROM market_history_trades
WHERE ticker = %s
GROUP BY trade_date::date
ORDER BY date
"""

# Fallback: agrega bars 1m -> diaria (quando nao ha ticks).
# open = 1o bar do dia, close = ultimo, high/low = extremos, volume = soma.
_SQL_AGGREGATE_1M = """
SELECT
    time::date AS date,
    (array_agg(open  ORDER BY time ASC))[1]  AS open,
    MAX(high) AS high,
    MIN(low)  AS low,
    (array_agg(close ORDER BY time DESC))[1] AS close,
    SUM(volume) AS volume,
    CAST(SUM(volume) AS INTEGER) AS qty,
    CAST(COALESCE(SUM(trades), 0) AS INTEGER) AS trades
FROM ohlc_1m
WHERE ticker = %s
GROUP BY time::date
ORDER BY date
"""

_SQL_UPSERT = """
INSERT INTO profit_daily_bars
    (time, ticker, exchange, open, high, low, close, volume, qty, trades)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (time, ticker, exchange) DO UPDATE SET
    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
    close=EXCLUDED.close, volume=EXCLUDED.volume,
    qty=EXCLUDED.qty, trades=EXCLUDED.trades
"""


def get_active_tickers(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, exchange
        FROM profit_history_tickers
        WHERE active = TRUE
        ORDER BY ticker
    """)
    rows = cur.fetchall()
    cur.close()
    return [{"ticker": r[0], "exchange": r[1]} for r in rows]


def populate_ticker(conn, ticker: str, exchange: str, dry_run: bool,
                    source_pref: str = "auto", min_price: float = 0.0) -> int:
    """Agrega para profit_daily_bars. source_pref:
        'auto'  -> tenta 1m primeiro (limpo), fallback para ticks
        'ticks' -> apenas market_history_trades (CUIDADO: bug escala /100, ver N1)
        '1m'    -> apenas ohlc_1m

    Nota N1 (27/abr/2026): 'auto' inverteu prioridade — antes tentava ticks
    primeiro mas market_history_trades chega com escala /100 intermitente
    para os tickers DLL Profit. ohlc_1m (source tick_agg_v1) e limpo.
    """
    cur = conn.cursor()
    rows: list = []
    source_used = None
    # N1 (27/abr): tenta 1m PRIMEIRO. Se vazio, fallback para ticks (futuros
    # como WDOFUT/WINFUT que so tem ticks).
    if source_pref in ("auto", "1m"):
        cur.execute(_SQL_AGGREGATE_1M, (ticker,))
        rows = cur.fetchall()
        if rows:
            source_used = "1m"
    if not rows and source_pref in ("auto", "ticks"):
        cur.execute(_SQL_AGGREGATE_TICKS, (ticker,))
        rows = cur.fetchall()
        if rows:
            source_used = "ticks"
    cur.close()

    if not rows:
        print(f"  [{ticker}] Sem dados (ticks ou 1m) -- pulando")
        return 0

    if min_price > 0:
        before = len(rows)
        rows = [r for r in rows if r[3] is not None and float(r[3]) >= min_price]
        if len(rows) < before:
            print(f"  [{ticker}] filtro min_price={min_price}: {before - len(rows)} dias rejeitados")
        if not rows:
            return 0

    if dry_run:
        print(f"  [{ticker}] {len(rows)} barras diarias (source={source_used}, dry-run)")
        return len(rows)

    cur = conn.cursor()
    for row in rows:
        d, open_, high, low, close, volume, qty, trades = row
        cur.execute(_SQL_UPSERT, (
            d, ticker, exchange,
            open_, high, low, close,
            volume, qty, trades,
        ))
    conn.commit()
    cur.close()

    print(f"  [{ticker}] {len(rows)} barras upserted (source={source_used})")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Popula profit_daily_bars a partir de ticks OU ohlc_1m (fallback)")
    parser.add_argument("--ticker", default=None, help="Ticker especifico (default: todos ativos)")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar")
    parser.add_argument("--source", choices=["auto", "ticks", "1m"], default="auto",
                        help="Fonte: 'auto' tenta ticks depois 1m; 'ticks'/'1m' forca")
    parser.add_argument("--min-price", type=float, default=0.0,
                        help="Filtra dias com low < min (defesa contra bug /100). 5.0 p/ stocks liquidas.")
    args = parser.parse_args()

    try:
        import psycopg2
    except ImportError:
        print("[ERRO] psycopg2 não instalado — pip install psycopg2-binary")
        sys.exit(1)

    conn = psycopg2.connect(DB_DSN)
    print(f"\n{'='*60}")
    print("POPULATE DAILY BARS (ticks/1m -> profit_daily_bars)")
    print(f"  DSN: ...@{DB_DSN.split('@')[-1]}")
    print(f"  Dry-run: {args.dry_run}")
    print(f"{'='*60}\n")

    if args.ticker:
        tickers = [{"ticker": args.ticker.upper(), "exchange": "B"}]
    else:
        tickers = get_active_tickers(conn)

    if not tickers:
        print("[ERRO] Nenhum ticker ativo em profit_history_tickers.")
        conn.close()
        sys.exit(1)

    print(f"[OK] {len(tickers)} ticker(s): {[t['ticker'] for t in tickers]}\n")

    t0 = time.perf_counter()
    total_bars = 0
    for t in tickers:
        total_bars += populate_ticker(conn, t["ticker"], t["exchange"],
                                      args.dry_run, source_pref=args.source,
                                      min_price=args.min_price)

    elapsed = time.perf_counter() - t0
    print(f"\n{'='*60}")
    print(f"RESUMO: {total_bars} barras diárias em {elapsed:.1f}s")
    print(f"{'='*60}\n")

    conn.close()


if __name__ == "__main__":
    main()

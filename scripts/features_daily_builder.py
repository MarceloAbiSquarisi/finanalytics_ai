"""
features_daily_builder.py — materializa `features_daily` a partir de OHLC diário.

Fontes (preferência decrescente):
  1. fintz_cotacoes_ts   (2010-01-04 -> 2025-12-30; 884 tickers) — fonte primária
  2. profit_daily_bars   (2026-01-02 -> hoje; 8 tickers DLL)   — ponte para 2026+

Pós-Sprint 1 completo, substituir/complementar com aggregação diária de
`ohlc_1m tick_agg_v1` para cobrir toda a watchlist em 2026+.

Uso:
    # Backfill completo para a watchlist (VERDE + AMARELO_*)
    python scripts/features_daily_builder.py --backfill --start 2020-01-02

    # Incremental (últimos 30 dias):
    python scripts/features_daily_builder.py --incremental

    # Ticker específico (debug):
    python scripts/features_daily_builder.py --only PETR4 --start 2024-01-01

    # Dry-run (não grava):
    python scripts/features_daily_builder.py --only PETR4 --dry-run
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import os
import sys
from typing import Iterable

import psycopg2
import psycopg2.extras

# ─── Config ────────────────────────────────────────────────────────────────────

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


@dataclass
class Bar:
    dia: date
    close: float
    high: float
    low: float
    volume: float
    source: str


# ─── Features (complementa feature_pipeline.py com ATR e SMAs) ─────────────────

def _sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i, _ in enumerate(values):
        if i + 1 < window:
            out.append(None)
        else:
            window_slice = values[i + 1 - window : i + 1]
            out.append(sum(window_slice) / window)
    return out


def _log_return(c_t: float, c_prev: float) -> float | None:
    if c_prev is None or c_prev <= 0 or c_t is None or c_t <= 0:
        return None
    return math.log(c_t / c_prev)


def _returns_log(closes: list[float], lag: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < lag:
            out.append(None)
        else:
            out.append(_log_return(closes[i], closes[i - lag]))
    return out


def _rolling_std(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        slc = values[i + 1 - window : i + 1]
        clean = [v for v in slc if v is not None]
        if len(clean) < window // 2:
            out.append(None)
            continue
        mean = sum(clean) / len(clean)
        var = sum((x - mean) ** 2 for x in clean) / (len(clean) - 1) if len(clean) > 1 else 0.0
        out.append(math.sqrt(var))
    return out


def _atr_14(highs: list[float], lows: list[float], closes: list[float]) -> list[float | None]:
    """Average True Range 14 (Wilder)."""
    tr: list[float] = []
    for i in range(len(highs)):
        if i == 0:
            tr.append(highs[i] - lows[i])
            continue
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr.append(max(hl, hc, lc))

    out: list[float | None] = []
    alpha = 1.0 / 14.0
    atr: float | None = None
    for i, tv in enumerate(tr):
        if i < 13:
            out.append(None)
            continue
        atr = sum(tr[:14]) / 14 if i == 13 else alpha * tv + (1 - alpha) * (atr or 0.0)
        out.append(atr)
    return out


def _rsi_14(closes: list[float]) -> list[float | None]:
    """RSI 14 Wilder."""
    if len(closes) < 15:
        return [None] * len(closes)
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    out: list[float | None] = [None] * 14
    avg_g = sum(gains[1:15]) / 14
    avg_l = sum(losses[1:15]) / 14
    rs = avg_g / avg_l if avg_l > 0 else float("inf")
    out.append(100 - 100 / (1 + rs) if avg_l > 0 else 100.0)
    for i in range(15, len(closes)):
        avg_g = (avg_g * 13 + gains[i]) / 14
        avg_l = (avg_l * 13 + losses[i]) / 14
        rs = avg_g / avg_l if avg_l > 0 else float("inf")
        out.append(100 - 100 / (1 + rs) if avg_l > 0 else 100.0)
    return out


def _volume_rel_20(volumes: list[float], window: int = 20) -> list[float | None]:
    """Razão volume / mediana(volume, window) pós-janela."""
    out: list[float | None] = []
    for i in range(len(volumes)):
        if i + 1 < window:
            out.append(None)
            continue
        slc = sorted(volumes[i + 1 - window : i + 1])
        mid = slc[window // 2] if window % 2 else (slc[window // 2 - 1] + slc[window // 2]) / 2
        if mid <= 0:
            out.append(None)
        else:
            out.append(volumes[i] / mid)
    return out


# ─── DB ────────────────────────────────────────────────────────────────────────

def watchlist_ativa(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM watchlist_tickers "
            "WHERE status = 'VERDE' OR status LIKE 'AMARELO_%' "
            "ORDER BY mediana_vol_brl DESC NULLS LAST"
        )
        return [r[0] for r in cur.fetchall()]


def load_bars(conn, ticker: str, d_start: date, d_end: date) -> list[Bar]:
    """
    Une fintz_cotacoes_ts (primária, 2010-2025-12-30) + profit_daily_bars (2026+).
    Ordena por dia ASC. Desambigua por source quando há overlap (preferência fintz).
    """
    bars: dict[date, Bar] = {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT time::date AS dia,
                   preco_fechamento::float AS close,
                   preco_maximo::float     AS high,
                   preco_minimo::float     AS low,
                   volume_negociado::float AS volume
              FROM fintz_cotacoes_ts
             WHERE ticker = %s AND time::date BETWEEN %s AND %s
             ORDER BY time ASC
            """,
            (ticker, d_start, d_end),
        )
        for dia, close, high, low, vol in cur.fetchall():
            if close is None:
                continue
            bars[dia] = Bar(dia, float(close), float(high or close), float(low or close), float(vol or 0), "fintz")

    # profit_daily_bars: DESABILITADO no MVP (19/abr/2026).
    # Observação: valores em profit_daily_bars para PETR4 oscilam entre ~0.49 e
    # ~49 entre dias consecutivos — quirk de escala da DLL ProfitDLL (possível
    # confusão close/close_ajustado ou split factor dinâmico). Causa
    # `r_1d > 300%` e RSI saturado. Investigar em Sprint 10.1 antes de religar.
    # Para 2026+, usar agregação diária de `ohlc_1m tick_agg_v1` OU esperar S1
    # completar e regenerar profit_daily_bars via populate_daily_bars.py.

    return sorted(bars.values(), key=lambda b: b.dia)


def upsert_features(conn, ticker: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO features_daily
            (ticker, dia, close, r_1d, r_5d, r_21d, atr_14, vol_21d, vol_rel_20,
             sma_50, sma_200, rsi_14, source, atualizado_em)
        VALUES %s
        ON CONFLICT (ticker, dia) DO UPDATE SET
            close        = EXCLUDED.close,
            r_1d         = EXCLUDED.r_1d,
            r_5d         = EXCLUDED.r_5d,
            r_21d        = EXCLUDED.r_21d,
            atr_14       = EXCLUDED.atr_14,
            vol_21d      = EXCLUDED.vol_21d,
            vol_rel_20   = EXCLUDED.vol_rel_20,
            sma_50       = EXCLUDED.sma_50,
            sma_200      = EXCLUDED.sma_200,
            rsi_14       = EXCLUDED.rsi_14,
            source       = EXCLUDED.source,
            atualizado_em= now()
    """
    values = [
        (
            ticker,
            r["dia"],
            r.get("close"),
            r.get("r_1d"),
            r.get("r_5d"),
            r.get("r_21d"),
            r.get("atr_14"),
            r.get("vol_21d"),
            r.get("vol_rel_20"),
            r.get("sma_50"),
            r.get("sma_200"),
            r.get("rsi_14"),
            r.get("source", "unknown"),
            datetime.utcnow(),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=500)
    return len(values)


# ─── Pipeline ──────────────────────────────────────────────────────────────────

def compute_features_for_ticker(bars: list[Bar]) -> list[dict]:
    if not bars:
        return []
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]

    r1 = _returns_log(closes, 1)
    r5 = _returns_log(closes, 5)
    r21 = _returns_log(closes, 21)
    atr = _atr_14(highs, lows, closes)
    vol21 = _rolling_std(r1, 21)  # usar retornos 1d
    volrel = _volume_rel_20(vols, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    rsi = _rsi_14(closes)

    out: list[dict] = []
    for i, b in enumerate(bars):
        out.append(
            {
                "dia": b.dia,
                "close": b.close,
                "r_1d": r1[i],
                "r_5d": r5[i],
                "r_21d": r21[i],
                "atr_14": atr[i],
                "vol_21d": vol21[i],
                "vol_rel_20": volrel[i],
                "sma_50": sma50[i],
                "sma_200": sma200[i],
                "rsi_14": rsi[i],
                "source": b.source,
            }
        )
    return out


# ─── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Materializa features_daily")
    p.add_argument("--start", type=str, default="2020-01-02")
    p.add_argument("--end", type=str, default=None, help="default=hoje")
    p.add_argument("--backfill", action="store_true", help="watchlist VERDE+AMARELO inteira")
    p.add_argument("--incremental", action="store_true", help="últimos 30 dias da watchlist")
    p.add_argument("--only", type=str, default=None, help='CSV de tickers, e.g. "PETR4,VALE3"')
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d_end = date.fromisoformat(args.end) if args.end else date.today()
    d_start = date.fromisoformat(args.start)
    if args.incremental:
        d_start = d_end - timedelta(days=30)

    conn = psycopg2.connect(DSN)
    try:
        if args.only:
            tickers = [t.strip().upper() for t in args.only.split(",") if t.strip()]
        elif args.backfill or args.incremental:
            tickers = watchlist_ativa(conn)
        else:
            print("Especifique --backfill, --incremental ou --only", file=sys.stderr)
            return 2

        print(
            f"features_daily_builder: {len(tickers)} tickers; {d_start} -> {d_end}; "
            f"dry_run={args.dry_run}"
        )

        total_rows = 0
        for i, tk in enumerate(tickers, 1):
            bars = load_bars(conn, tk, d_start, d_end)
            feats = compute_features_for_ticker(bars)
            if args.dry_run:
                print(f"[{i}/{len(tickers)}] {tk}: {len(feats)} feats (dry-run)")
                continue
            n = upsert_features(conn, tk, feats)
            conn.commit()
            total_rows += n
            if i % 10 == 0 or i == len(tickers):
                print(f"[{i}/{len(tickers)}] {tk}: +{n} rows (acumulado={total_rows})")
        print(f"done. total={total_rows}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

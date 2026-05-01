"""
rates_features_builder.py — popula rates_features_daily a partir de
yield_curves + breakeven_inflation. 1 linha por dia útil.

Usa rates_features (F2-F6) para:
  - Interpolar taxas nos vértices canônicos (3m/1y/2y/5y) pré e real
  - Slope, butterfly, Nelson-Siegel
  - Breakeven por vértice
  - TSMOM (3m, 12m) multi-vértice
  - Roll-down carry
  - Value z-score (5y histórico)
  - V+M combinado
  - FRAs

Uso:
    python scripts/rates_features_builder.py                    # todos os dias
    python scripts/rates_features_builder.py --start 2024-01-01 # range
    python scripts/rates_features_builder.py --dry-run
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime
import os
import sys
from typing import Any

import psycopg2
import psycopg2.extras

from finanalytics_ai.application.ml.rates_features import (
    butterfly_duration_neutral,
    carry_roll_down,
    fra_implied,
    nelson_siegel_fit,
    slope as calc_slope,
    taxa_em_vertice,
    tsmom_signal,
    value_momentum_combined,
    value_zscore,
)

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def load_curves(conn) -> tuple[dict, dict, dict]:
    """
    Retorna (curves_pre, curves_ipca, breakevens).
    Cada dict: { dia -> list[{dias_uteis, taxa_aa}] } (ordenado por vértice).
    """
    curves_pre: dict[date, list[dict]] = defaultdict(list)
    curves_ipca: dict[date, list[dict]] = defaultdict(list)
    breakevens: dict[date, list[dict]] = defaultdict(list)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT time::date, vertice_du, taxa_aa, taxa_real_aa, market
              FROM yield_curves
             WHERE source = 'anbima'
             ORDER BY time, vertice_du
            """
        )
        for d, du, tx_pre, tx_real, market in cur.fetchall():
            if market == "br_pre" and tx_pre is not None:
                curves_pre[d].append({"dias_uteis": int(du), "taxa_aa": float(tx_pre)})
            elif market == "br_ipca" and tx_real is not None:
                curves_ipca[d].append({"dias_uteis": int(du), "taxa_aa": float(tx_real)})

        cur.execute("SELECT time::date, vertice_du, breakeven_aa FROM breakeven_inflation ORDER BY time, vertice_du")
        for d, du, be in cur.fetchall():
            if be is not None:
                breakevens[d].append({"dias_uteis": int(du), "breakeven_aa": float(be)})

    return curves_pre, curves_ipca, breakevens


def build_history_series(curves_pre: dict, curves_ipca: dict,
                         dias_ord: list[date]) -> dict[str, list[float | None]]:
    """
    Para cada vértice canônico, constrói série ordenada por dia com a taxa
    interpolada (flat-forward). Retorna dict "serie_pre_1y", "serie_real_5y", etc.
    """
    vertices_pre  = {"3m": 63, "1y": 252, "2y": 504, "5y": 1260}
    vertices_real = {"1y": 252, "2y": 504, "5y": 1260}

    series: dict[str, list[float | None]] = {}
    for nome, du in vertices_pre.items():
        series[f"pre_{nome}"]  = [taxa_em_vertice(curves_pre.get(d, []), du) for d in dias_ord]
    for nome, du in vertices_real.items():
        series[f"real_{nome}"] = [taxa_em_vertice(curves_ipca.get(d, []), du) for d in dias_ord]
    return series


def compute_row(dia: date, idx: int, curves_pre: dict, curves_ipca: dict,
                breakevens: dict, series: dict) -> dict[str, Any]:
    """Computa todas as features RF para um único dia (dia = dias_ord[idx])."""
    curve_pre = curves_pre.get(dia, [])
    curve_ipca = curves_ipca.get(dia, [])
    be_curve = breakevens.get(dia, [])

    row: dict[str, Any] = {"dia": dia}

    # Taxas nos vértices canônicos
    row["taxa_pre_3m"]  = taxa_em_vertice(curve_pre, 63)
    row["taxa_pre_1y"]  = taxa_em_vertice(curve_pre, 252)
    row["taxa_pre_2y"]  = taxa_em_vertice(curve_pre, 504)
    row["taxa_pre_5y"]  = taxa_em_vertice(curve_pre, 1260)
    row["taxa_real_1y"] = taxa_em_vertice(curve_ipca, 252)
    row["taxa_real_2y"] = taxa_em_vertice(curve_ipca, 504)
    row["taxa_real_5y"] = taxa_em_vertice(curve_ipca, 1260)

    # Slope/butterfly/NS
    row["slope_1y_5y"]   = calc_slope(curve_pre, 252, 1260)
    row["slope_2y_10y"]  = calc_slope(curve_pre, 504, 2520)
    t1, t2, t5 = row["taxa_pre_1y"], row["taxa_pre_2y"], row["taxa_pre_5y"]
    row["curvatura_butterfly"] = (
        butterfly_duration_neutral(t1, t2, t5, 252, 504, 1260)
        if all(x is not None for x in (t1, t2, t5)) else None
    )
    ns = nelson_siegel_fit(
        [r["dias_uteis"] for r in curve_pre],
        [r["taxa_aa"] for r in curve_pre],
    )
    if ns:
        row["ns_level"]     = ns["beta0"]
        row["ns_slope"]     = ns["beta1"]
        row["ns_curvature"] = ns["beta2"]
        row["ns_lambda"]    = ns["lambda"]
    else:
        row["ns_level"] = row["ns_slope"] = row["ns_curvature"] = row["ns_lambda"] = None

    # Breakeven
    be_at = {}
    for r in be_curve:
        be_at[r["dias_uteis"]] = r["breakeven_aa"]
    def _be_lookup(target_du: int) -> float | None:
        if not be_curve:
            return None
        return taxa_em_vertice(be_curve, target_du, key_du="dias_uteis", key_tx="breakeven_aa")
    row["breakeven_1y"] = _be_lookup(252)
    row["breakeven_2y"] = _be_lookup(504)
    row["breakeven_5y"] = _be_lookup(1260)

    # TSMOM (3m e 12m) em séries históricas até dia idx
    def _tsmom(serie_key: str, lookback: int) -> float | None:
        serie = [x for x in series[serie_key][: idx + 1] if x is not None]
        if len(serie) < lookback + 20:
            return None
        return tsmom_signal(serie, lookback_dias=lookback, vol_target=0.10)

    for v in ("1y", "2y", "5y"):
        row[f"tsmom_di1_{v}_3m"]  = _tsmom(f"pre_{v}", 63)
        row[f"tsmom_di1_{v}_12m"] = _tsmom(f"pre_{v}", 252)

    # Roll-down carry
    if row["taxa_pre_1y"] is not None and row["taxa_pre_2y"] is not None:
        row["carry_roll_di1_2y"] = carry_roll_down(row["taxa_pre_2y"], row["taxa_pre_1y"], 504, 252)
    else:
        row["carry_roll_di1_2y"] = None
    if row["taxa_pre_2y"] is not None and row["taxa_pre_5y"] is not None:
        row["carry_roll_di1_5y"] = carry_roll_down(row["taxa_pre_5y"], row["taxa_pre_2y"], 1260, 504)
    else:
        row["carry_roll_di1_5y"] = None

    # Value z-score (janela 5y ≈ 1260 dias úteis)
    def _value_z(serie_key: str) -> float | None:
        serie_full = [x for x in series[serie_key][: idx + 1] if x is not None]
        if len(serie_full) < 50:
            return None
        hist = serie_full[-1260:-1] if len(serie_full) > 1 else serie_full[:-1]
        if len(hist) < 20:
            return None
        return value_zscore(serie_full[-1], hist)

    row["value_di1_1y_z"]  = _value_z("pre_1y")
    row["value_di1_2y_z"]  = _value_z("pre_2y")
    row["value_di1_5y_z"]  = _value_z("pre_5y")
    row["value_ntnb_2y_z"] = _value_z("real_2y")
    row["value_ntnb_5y_z"] = _value_z("real_5y")

    # V+M combinado
    row["vm_combo_1y"] = value_momentum_combined(row["value_di1_1y_z"], row["tsmom_di1_1y_3m"])
    row["vm_combo_2y"] = value_momentum_combined(row["value_di1_2y_z"], row["tsmom_di1_2y_3m"])
    row["vm_combo_5y"] = value_momentum_combined(row["value_di1_5y_z"], row["tsmom_di1_5y_3m"])

    # FRAs
    if row["taxa_pre_1y"] is not None and row["taxa_pre_2y"] is not None:
        row["fra_1y2y"] = fra_implied(row["taxa_pre_2y"], row["taxa_pre_1y"], 504, 252)
    else:
        row["fra_1y2y"] = None
    if row["taxa_pre_2y"] is not None and row["taxa_pre_5y"] is not None:
        row["fra_2y5y"] = fra_implied(row["taxa_pre_5y"], row["taxa_pre_2y"], 1260, 504)
    else:
        row["fra_2y5y"] = None

    return row


COLS_ORDER = [
    "dia",
    "taxa_pre_3m", "taxa_pre_1y", "taxa_pre_2y", "taxa_pre_5y",
    "taxa_real_1y", "taxa_real_2y", "taxa_real_5y",
    "slope_1y_5y", "slope_2y_10y", "curvatura_butterfly",
    "breakeven_1y", "breakeven_2y", "breakeven_5y",
    "ns_level", "ns_slope", "ns_curvature", "ns_lambda",
    "tsmom_di1_1y_3m", "tsmom_di1_2y_3m", "tsmom_di1_5y_3m",
    "tsmom_di1_1y_12m", "tsmom_di1_2y_12m", "tsmom_di1_5y_12m",
    "carry_roll_di1_2y", "carry_roll_di1_5y",
    "value_di1_1y_z", "value_di1_2y_z", "value_di1_5y_z",
    "value_ntnb_2y_z", "value_ntnb_5y_z",
    "vm_combo_1y", "vm_combo_2y", "vm_combo_5y",
    "fra_1y2y", "fra_2y5y",
]


def upsert_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols_sql = ", ".join(COLS_ORDER)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS_ORDER if c != "dia")
    updates += ", atualizado_em=now()"
    sql = f"""
        INSERT INTO rates_features_daily ({cols_sql}, atualizado_em)
        VALUES %s
        ON CONFLICT (dia) DO UPDATE SET {updates}
    """
    values = [tuple(r.get(c) for c in COLS_ORDER) + (datetime.utcnow(),) for r in rows]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=200)
    return len(values)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2020-01-02")
    p.add_argument("--end", default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d_start = date.fromisoformat(args.start)
    d_end = date.fromisoformat(args.end) if args.end else date.today()

    conn = psycopg2.connect(DSN)
    try:
        print("rates_features_builder: carregando yield_curves + breakevens...")
        curves_pre, curves_ipca, breakevens = load_curves(conn)
        print(f"  pre={len(curves_pre)} dias, ipca={len(curves_ipca)} dias, be={len(breakevens)} dias")

        # Ordem temporal: união de pre ∪ ipca, limitada pelo range
        dias_ord = sorted(set(curves_pre) | set(curves_ipca))
        dias_ord = [d for d in dias_ord if d_start <= d <= d_end]
        print(f"  processando {len(dias_ord)} dias: {dias_ord[0] if dias_ord else None} -> {dias_ord[-1] if dias_ord else None}")

        print("  construindo séries históricas por vértice...")
        series = build_history_series(curves_pre, curves_ipca, dias_ord)

        batch: list[dict] = []
        total = 0
        for i, d in enumerate(dias_ord):
            row = compute_row(d, i, curves_pre, curves_ipca, breakevens, series)
            batch.append(row)
            if len(batch) >= 200:
                if args.dry_run:
                    print(f"  [{i+1}/{len(dias_ord)}] batch de {len(batch)} (dry-run)")
                else:
                    n = upsert_rows(conn, batch)
                    conn.commit()
                    total += n
                    print(f"  [{i+1}/{len(dias_ord)}] batch +{n} (total={total})")
                batch.clear()
        if batch:
            if args.dry_run:
                print(f"  final batch {len(batch)} (dry-run)")
            else:
                n = upsert_rows(conn, batch)
                conn.commit()
                total += n
        print(f"done. total upserted: {total}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

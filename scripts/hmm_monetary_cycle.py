"""
hmm_monetary_cycle.py — HMM de 3 estados para ciclo monetário BR.

Estados: 0=easing / 1=neutro / 2=tightening (rotulados por média de delta_SELIC).

Features (§2.1 melhorias_renda_fixa.md):
  - SELIC over (% ao dia)
  - slope_2y_10y (de yield_curves br_pre)
  - IPCA 12m (acumulado dos 12 meses mensais mais recentes)
  - delta_SELIC_21d (variação em 21 dias úteis)

Persistência: tabela hmm_monetary_daily (dia, regime_id, regime_name,
proba_easing, proba_neutral, proba_tightening).

Uso:
    python scripts/hmm_monetary_cycle.py
    python scripts/hmm_monetary_cycle.py --dry-run
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
import sys
from typing import Any

import numpy as np
import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hmm_monetary_daily (
                dia                 date PRIMARY KEY,
                regime_id           int NOT NULL,
                regime_name         text NOT NULL,
                proba_easing        double precision,
                proba_neutral       double precision,
                proba_tightening    double precision,
                selic_over          double precision,
                slope_2y_10y        double precision,
                ipca_12m            double precision,
                delta_selic_21d     double precision,
                atualizado_em       timestamptz NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_hmm_mon_dia ON hmm_monetary_daily (dia DESC);
            COMMENT ON TABLE hmm_monetary_daily IS
                'HMM 3-estados de ciclo monetário BR (Sprint T2 §2.1).';
            """
        )
    conn.commit()


def load_features(conn) -> tuple[list[date], np.ndarray, dict]:
    """
    Retorna (dates, matrix [n x 4], raw).
    Features em matrix: SELIC_OVER, slope_2y_10y, IPCA_12m, delta_SELIC_21d.
    """
    # SELIC over
    with conn.cursor() as cur:
        cur.execute("SELECT time::date, value FROM br_macro_daily WHERE series='SELIC_OVER' AND value IS NOT NULL ORDER BY time")
        selic = dict(cur.fetchall())
    # IPCA mensal
    with conn.cursor() as cur:
        cur.execute("SELECT time::date, value FROM br_macro_daily WHERE series='IPCA' AND value IS NOT NULL ORDER BY time")
        ipca_m = dict(cur.fetchall())
    # slope BR pre 2y-10y (via yield_curves interpolado — aqui uso ~504 e ~2520 direto)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT time::date, vertice_du, taxa_aa
              FROM yield_curves
             WHERE source='anbima' AND market='br_pre' AND taxa_aa IS NOT NULL
             ORDER BY time, vertice_du
            """
        )
        curves: dict[date, list[tuple[int, float]]] = {}
        for d, du, v in cur.fetchall():
            curves.setdefault(d, []).append((int(du), float(v)))

    def interp_slope(d: date) -> float | None:
        pairs = curves.get(d)
        if not pairs or len(pairs) < 2:
            return None
        pairs = sorted(pairs)
        # taxa mais próxima de 504
        def nearest(target):
            return min(pairs, key=lambda p: abs(p[0] - target))
        _, tx_2y = nearest(504)
        _, tx_10y = nearest(2520)
        return tx_10y - tx_2y

    # IPCA 12m accumulado: para cada dia, pega últimos 12 valores mensais <= dia
    def ipca_12m(d: date) -> float | None:
        keys = sorted([k for k in ipca_m if k <= d])
        if len(keys) < 12:
            return None
        vals = [ipca_m[k] / 100.0 for k in keys[-12:]]  # % → decimal
        acc = 1.0
        for v in vals:
            acc *= (1 + v)
        return (acc - 1) * 100  # volta para %

    all_dates = sorted(set(selic) & set(curves))
    dates, rows, raw = [], [], []
    for i, d in enumerate(all_dates):
        if i < 21:
            continue
        s = selic[d]
        slope = interp_slope(d)
        ipca = ipca_12m(d)
        d_back = all_dates[i - 21]
        delta = selic[d] - selic.get(d_back, 0)
        if any(x is None for x in (slope, ipca)):
            continue
        rows.append([s, slope, ipca, delta])
        dates.append(d)
        raw.append({"selic": s, "slope": slope, "ipca": ipca, "delta": delta})

    if not rows:
        return [], np.empty((0, 4)), {}
    return dates, np.asarray(rows, dtype=float), {"raw": raw, "selic_dict": selic}


def fit_and_label(X: np.ndarray) -> tuple[Any, list[int], np.ndarray, dict[int, str]]:
    from hmmlearn import hmm
    model = hmm.GaussianHMM(n_components=3, covariance_type="full", n_iter=300, random_state=42)
    model.fit(X)
    states = model.predict(X)
    proba = model.predict_proba(X)

    # Rotular: estado com maior média de delta_SELIC_21d = tightening
    means_delta = [float(np.mean(X[states == i, 3])) if (states == i).any() else 0.0 for i in range(3)]
    order = sorted(range(3), key=lambda i: means_delta[i])
    label_map = {
        order[0]: "easing",
        order[1]: "neutral",
        order[2]: "tightening",
    }
    return model, states.tolist(), proba, label_map


def upsert(conn, dates: list[date], states: list[int], proba: np.ndarray,
           X: np.ndarray, label_map: dict[int, str]) -> int:
    if not dates:
        return 0
    sql = """
        INSERT INTO hmm_monetary_daily
            (dia, regime_id, regime_name, proba_easing, proba_neutral, proba_tightening,
             selic_over, slope_2y_10y, ipca_12m, delta_selic_21d, atualizado_em)
        VALUES %s
        ON CONFLICT (dia) DO UPDATE SET
            regime_id=EXCLUDED.regime_id, regime_name=EXCLUDED.regime_name,
            proba_easing=EXCLUDED.proba_easing, proba_neutral=EXCLUDED.proba_neutral,
            proba_tightening=EXCLUDED.proba_tightening,
            selic_over=EXCLUDED.selic_over, slope_2y_10y=EXCLUDED.slope_2y_10y,
            ipca_12m=EXCLUDED.ipca_12m, delta_selic_21d=EXCLUDED.delta_selic_21d,
            atualizado_em=now()
    """
    # label para índice reverso
    name_by_id = label_map
    rows = []
    for i, d in enumerate(dates):
        rid = states[i]
        rname = name_by_id[rid]
        proba_row = proba[i]
        # proba_easing/neutral/tightening = proba do rid-correspondente no label_map
        by_name = {"easing": 0.0, "neutral": 0.0, "tightening": 0.0}
        for state_id, name in name_by_id.items():
            by_name[name] = float(proba_row[state_id])
        rows.append((
            d, rid, rname,
            by_name["easing"], by_name["neutral"], by_name["tightening"],
            float(X[i, 0]), float(X[i, 1]), float(X[i, 2]), float(X[i, 3]),
            datetime.utcnow(),
        ))
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=200)
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    conn = psycopg2.connect(DSN)
    try:
        ensure_schema(conn)
        print("Carregando features...")
        dates, X, raw = load_features(conn)
        print(f"  {len(dates)} observações, features shape={X.shape}")
        if len(dates) < 100:
            print("Insuficiente para treinar HMM.")
            return 2

        print("Treinando GaussianHMM n_components=3...")
        model, states, proba, label_map = fit_and_label(X)
        print(f"  rótulos: {label_map}")

        # Distribuição de estados
        counts = {"easing": 0, "neutral": 0, "tightening": 0}
        for sid in states:
            counts[label_map[sid]] += 1
        print(f"  distribuição: {counts}")

        # Últimas 10 classificações
        print("\nÚltimos 10 regimes:")
        for i in range(max(0, len(dates) - 10), len(dates)):
            print(f"  {dates[i]}  {label_map[states[i]]:<12}  SELIC={X[i,0]:.3f}  slope={X[i,1]:.3f}  IPCA12m={X[i,2]:.2f}  ΔSELIC={X[i,3]:.3f}")

        if not args.dry_run:
            n = upsert(conn, dates, states, proba, X, label_map)
            conn.commit()
            print(f"\nUpserted: {n}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

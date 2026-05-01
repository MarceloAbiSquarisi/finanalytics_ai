"""
treasury_rf_mvp.py — MVP Random Forest direcional sobre US 10Y Treasury.

Target: sign(DGS10[t+21d] - DGS10[t]) — taxa sobe (+1) ou cai (0).

Features (§2.4 melhorias_renda_fixa.md):
  - DFF (Fed Funds)
  - CPI yoy (via CPIAUCSL)
  - slope_3m_10y (DGS10 - DGS3MO)
  - slope_2y_10y (DGS10 - DGS2)
  - VIX
  - HY spread (BAMLH0A0HYM2)
  - US 10Y retorno 1m
  - US 10Y vol 3m
  - breakeven 5y (T5YIE)
  - breakeven 10y (T10YIE)

Split: 2020-2023 treino / 2024+ teste.
Métricas: accuracy, AUC, hit rate por regime.

Uso:
    python scripts/treasury_rf_mvp.py
"""
from __future__ import annotations

from datetime import date, datetime
import os
import sys

import numpy as np
import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def load_series(conn) -> dict[str, dict[date, float]]:
    """Retorna { series_name: { date -> value } }."""
    out: dict[str, dict[date, float]] = {}

    # Treasuries (yield_curves)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT time::date, vertice_du, taxa_aa
              FROM yield_curves
             WHERE source='fred' AND market='us_treasury'
             ORDER BY time
            """
        )
        dgs3m, dgs2, dgs10 = {}, {}, {}
        for d, du, v in cur.fetchall():
            if v is None:
                continue
            if du == 63:
                dgs3m[d] = float(v)
            elif du == 504:
                dgs2[d] = float(v)
            elif du == 2520:
                dgs10[d] = float(v)
        out["DGS3MO"] = dgs3m
        out["DGS2"] = dgs2
        out["DGS10"] = dgs10

    # Macro
    with conn.cursor() as cur:
        cur.execute("SELECT series, time::date, value FROM us_macro_daily WHERE value IS NOT NULL")
        for s, d, v in cur.fetchall():
            out.setdefault(s, {})[d] = float(v)

    return out


def build_dataset(series: dict[str, dict[date, float]]) -> tuple[list[date], np.ndarray, np.ndarray]:
    """Constrói matriz de features + target direcional 21d."""
    dgs10 = series["DGS10"]
    dates = sorted(dgs10.keys())

    def g(s: str, d: date, default=None):
        return series.get(s, {}).get(d, default)

    # CPI yoy requer mensal: vou aproximar usando último CPI disponível vs 12 meses antes
    def cpi_yoy(d: date) -> float | None:
        # Procurar CPI mensal mais recente <= d e ~12 meses antes
        cpi = series.get("CPIAUCSL", {})
        keys = sorted([k for k in cpi if k <= d])
        if len(keys) < 12:
            return None
        recent = cpi[keys[-1]]
        year_ago = cpi[keys[-12]]
        if year_ago <= 0:
            return None
        return (recent / year_ago - 1) * 100

    X, y, dts = [], [], []
    for i, d in enumerate(dates):
        if i + 21 >= len(dates):
            break
        d_future = dates[i + 21]
        t10 = dgs10[d]
        t10_future = dgs10[d_future]

        # Features
        dff = g("DFF", d)
        cpi_y = cpi_yoy(d)
        s_3_10 = t10 - (series["DGS3MO"].get(d, np.nan))
        s_2_10 = t10 - (series["DGS2"].get(d, np.nan))
        vix = g("VIXCLS", d)
        hy = g("BAMLH0A0HYM2", d)
        be5 = g("T5YIE", d)
        be10 = g("T10YIE", d)

        # US 10Y retorno 1m e vol 3m
        d_1m = dates[i - 21] if i >= 21 else None
        ret_1m = (dgs10[d] / dgs10[d_1m] - 1) * 100 if d_1m else None

        vol_3m = None
        if i >= 63:
            window = [dgs10[dates[j]] for j in range(i - 63, i) if dates[j] in dgs10]
            if len(window) >= 20:
                rets = np.diff(np.array(window))
                vol_3m = float(np.std(rets) * np.sqrt(252))

        feats = [dff, cpi_y, s_3_10, s_2_10, vix, hy, be5, be10, ret_1m, vol_3m]
        if any(f is None or (isinstance(f, float) and np.isnan(f)) for f in feats):
            continue

        X.append(feats)
        y.append(1 if t10_future > t10 else 0)
        dts.append(d)

    return dts, np.array(X, dtype=float), np.array(y, dtype=int)


def main() -> int:
    import lightgbm as lgb  # reuso LightGBM (já no stack); RF também serviria
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, roc_auc_score

    conn = psycopg2.connect(DSN)
    try:
        print("Carregando séries FRED...")
        series = load_series(conn)
        for k, v in series.items():
            print(f"  {k}: {len(v)} pontos")

        dates, X, y = build_dataset(series)
        print(f"\nDataset: {len(X)} observações, {X.shape[1]} features")

        mask_train = np.array([d <= date(2023, 12, 31) for d in dates])
        mask_test  = np.array([d >  date(2023, 12, 31) for d in dates])
        print(f"  train={int(mask_train.sum())}  test={int(mask_test.sum())}")

        if mask_train.sum() < 50 or mask_test.sum() < 20:
            print("Dataset pequeno — abort")
            return 2

        model = RandomForestClassifier(
            n_estimators=500, max_features="sqrt",
            n_jobs=-1, random_state=42,
        )
        model.fit(X[mask_train], y[mask_train])

        p_train = model.predict_proba(X[mask_train])[:, 1]
        p_test  = model.predict_proba(X[mask_test])[:, 1]
        y_pred_train = (p_train > 0.5).astype(int)
        y_pred_test  = (p_test  > 0.5).astype(int)

        print("\n=== Treasury RF direcional 21d ===")
        print(f"  train accuracy: {accuracy_score(y[mask_train], y_pred_train):.4f}")
        print(f"  train AUC:      {roc_auc_score(y[mask_train], p_train):.4f}")
        print(f"  test  accuracy: {accuracy_score(y[mask_test], y_pred_test):.4f}")
        print(f"  test  AUC:      {roc_auc_score(y[mask_test], p_test):.4f}")

        feature_names = ["DFF", "CPI_yoy", "slope_3m_10y", "slope_2y_10y",
                         "VIX", "HY_spread", "breakeven_5y", "breakeven_10y",
                         "US10Y_ret_1m", "US10Y_vol_3m"]
        print("\nFeature importances:")
        for f, imp in sorted(zip(feature_names, model.feature_importances_), key=lambda x: -x[1]):
            print(f"  {f:<20} = {imp:.4f}")

    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

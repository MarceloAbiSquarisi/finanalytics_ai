"""r5_trade_level_dsr.py — recomputa DSR usando skew/kurt dos PNL_PCT
de trades do best_ticker, em vez de daily logrets do underlying.

Motivação: harness atual passa skew/kurt do underlying ao deflated_sharpe.
Pra horizons longos (h=42), o underlying acumula fat tails extremas
(PETR4 skew=11.85 kurt=218) que inflam DSR z artificialmente. O correto
é skew/kurt da curva de retornos do TRADER (per-trade pnl_pct), pois
é essa distribuição que gera o sharpe observado.

Para cada run no DB, re-roda WF do best_ticker (1 ticker, ~10s),
extrai trade-level pnl_pct, computa DSR completo trade-level vs
underlying-level, e exibe lado a lado.

Output: tabela comparativa.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, "/tmp")  # container

from finanalytics_ai.domain.backtesting.metrics import (
    deflated_sharpe,
    expected_max_sharpe,
    sample_skew_kurtosis,
)
from mlstrategy_backtest_wf import run_wf_for_ticker

DSN_PG = os.environ.get(
    "FINANALYTICS_DSN",
    "postgresql://finanalytics:secret@postgres:5432/finanalytics",
)


def trade_level_dsr(
    sharpe_obs: float,
    pnl_pcts: list[float],
    avg_duration_days: float,
    n_eff: int,
) -> dict:
    """DSR completo usando trade returns.

    annualization_factor = 252 / avg_duration_days  → SR per-period é per-trade
    sample_size = num_trades
    skew/kurt = momentos amostrais dos pnl_pcts (já em pontos pct, divide por 100
                pra ficar consistente com SR adimensional).
    """
    if len(pnl_pcts) < 3:
        return {"err": "trades<3"}
    rets = [p / 100.0 for p in pnl_pcts]
    skew, kurt = sample_skew_kurtosis(rets)
    ann = max(252.0 / max(avg_duration_days, 1.0), 1.0)
    res = deflated_sharpe(
        observed_sharpe=sharpe_obs,
        num_trials=n_eff,
        sample_size=len(rets),
        skew=skew,
        kurtosis=kurt,
        annualization_factor=ann,
    )
    return {
        "n_trades": len(rets),
        "avg_dur_days": avg_duration_days,
        "ann_factor": round(ann, 2),
        "skew_trade": round(skew, 3),
        "kurt_trade": round(kurt, 3),
        "dsr_z_trade": round(res.deflated_sharpe, 3),
        "prob_real_trade": round(res.prob_real, 4),
        "e_max_trade": round(res.e_max_sharpe, 3),
    }


def main() -> int:
    with psycopg2.connect(DSN_PG) as c:
        cur = c.cursor()
        cur.execute("""
            SELECT id, horizon, retrain_days, th_buy, th_sell, target_vol,
                   train_end, best_ticker, sharpe_max, n_eff_eig,
                   dsr_full_prob_real, dsr_full_z, dsr_full_skew, dsr_full_kurtosis
            FROM r5_runs ORDER BY id
        """)
        runs = cur.fetchall()

    print(f"{'id':<3} {'best':<8} {'h':<3} {'retr':<5} {'tvol':<6} {'sh_max':<7} "
          f"{'prob_under':<12} {'prob_trade':<12} {'z_under':<8} {'z_trade':<8} "
          f"{'sk_und':<8} {'sk_trd':<8} {'kt_und':<8} {'kt_trd':<8} {'n_tr':<5} {'dur':<5}")
    print("-" * 175)

    for r in runs:
        (rid, h, retr, thb, ths, tvol, train_end, best, sh_max, neff,
         pr_u, dz_u, sk_u, kt_u) = r
        if not best or sh_max is None:
            continue

        out = run_wf_for_ticker(
            ticker=best,
            horizon=int(h),
            retrain_days=int(retr),
            commission=0.001,
            th_buy=float(thb),
            th_sell=float(ths),
            train_end=str(train_end),
        )
        if not out["ok"]:
            print(f"{rid:<3} {best:<8} FAIL: {out.get('error')}")
            continue
        result = out["result"]
        if not result.trades:
            print(f"{rid:<3} {best:<8} no trades")
            continue

        pnl_pcts = [t.pnl_pct for t in result.trades]
        avg_dur = sum((t.exit_dt - t.entry_dt).days for t in result.trades) / len(result.trades) \
                  if hasattr(result.trades[0], "entry_dt") else result.metrics.avg_duration_days

        n_eff_int = int(round(float(neff))) if neff is not None else 11  # fallback Neff_eig do dataset
        td = trade_level_dsr(
            sharpe_obs=float(sh_max),
            pnl_pcts=pnl_pcts,
            avg_duration_days=avg_dur,
            n_eff=n_eff_int,
        )

        if "err" in td:
            print(f"{rid:<3} {best:<8} {td['err']}")
            continue

        print(f"{rid:<3} {best:<8} {h:<3} {retr:<5} {tvol or 'off':<6} "
              f"{float(sh_max):>6.3f} "
              f"{float(pr_u or 0):>10.4f}    "
              f"{td['prob_real_trade']:>10.4f}    "
              f"{float(dz_u or 0):>+6.2f}   "
              f"{td['dsr_z_trade']:>+6.2f}   "
              f"{float(sk_u or 0):>+6.2f}  "
              f"{td['skew_trade']:>+6.2f}  "
              f"{float(kt_u or 0):>6.2f}  "
              f"{td['kurt_trade']:>6.2f}  "
              f"{td['n_trades']:<5} {td['avg_dur_days']:>4.0f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

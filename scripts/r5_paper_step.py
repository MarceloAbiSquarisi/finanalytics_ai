"""r5_paper_step.py — daily step do forward-test paper R5.

Para cada paper_run ativo:
  1. Para cada ticker no universe, computa signal hoje:
     - load_rows até as_of_date
     - train models em rows[:as_of_idx]
     - score features de today, classifica BUY/SELL/HOLD
  2. Persiste paper_signals(run_id, today, ticker, ...)
  3. Atualiza state_json:
     - SELL signal em ticker com position aberta → fecha (realiza pnl)
     - BUY signal em ticker SEM position + cash >= slot_size → abre
     - HOLD ou já-aberto → noop
     - Mark-to-market: equity = cash + sum(qty*current_close)
     - Append snapshot a equity_curve

Idempotente: se signals já existem pra (run_id, today), pula geração e
só recomputa MTM (útil pra preço de fechamento atualizado).

Uso (manual, ou via cron diário 18:30 BRT após pregão):
  docker exec -e ... finanalytics_api python /tmp/paper_step.py
  docker exec -e ... finanalytics_api python /tmp/paper_step.py --as-of 2026-05-07
  docker exec -e ... finanalytics_api python /tmp/paper_step.py --create-run

Setup inicial (criar a paper_run named 'r5-id11-top18'):
  python /tmp/paper_step.py --create-run --name r5-id11-top18 \\
      --tickers BMEB4,FRAS3,...,CEAB3 --capital 100000
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, "/tmp")

import numpy as np

from mlstrategy_backtest_wf import (
    FEATURES_EQUITY,
    FEATURES_RF,
    classify,
    load_rows,
    predict_q,
    prob_positive,
    score_sig,
    train_models,
)

DSN = os.environ.get(
    "FINANALYTICS_DSN",
    "postgresql://finanalytics:secret@postgres:5432/finanalytics",
)

# Top-18 do rolling 5-fold (sharpe > 0.5 em todos folds)
DEFAULT_UNIVERSE = [
    "BMEB4", "FRAS3", "MDNE3", "DIRR3", "NEOE3", "CPLE3",
    "BPAC11", "ENEV3", "CMIG3", "ENGI11", "CYRE3",
    "GFSA3", "EQTL3", "ITSA3", "ITSA4", "B3SA3", "HAPV3", "CEAB3",
]


def create_paper_run(name: str, tickers: list[str], capital: float, config: dict, notes: str = "") -> int:
    """Cria nova paper_run. Falha se name já existe."""
    initial_state = {
        "positions": {},  # ticker -> {open_date, open_price, qty, slot_value}
        "cash": capital,
        "equity_curve": [],
        "trades_history": [],
    }
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO paper_runs (name, config_json, universe, initial_capital,
                                       n_slots, state_json, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (name, json.dumps(config), tickers, capital, len(tickers),
             json.dumps(initial_state), notes),
        )
        run_id = cur.fetchone()[0]
        conn.commit()
    print(f"[create] paper_run id={run_id} name={name} N_tickers={len(tickers)} capital=R${capital:.0f}")
    return run_id


def signal_for_ticker(
    ticker: str, as_of: date, config: dict, include_rf: bool = True
) -> dict | None:
    """Computa signal pra (ticker, as_of_date). Retorna None se data inválida."""
    rows = load_rows(ticker, include_rf=include_rf, horizon=config.get("horizon", 10))
    if not rows:
        return {"err": "no rows"}
    as_of_idx = next((i for i, r in enumerate(rows) if r.dia == as_of), -1)
    if as_of_idx < 0:
        # Permite as_of futuro do último row → usa o último disponível
        last_row_idx = len(rows) - 1
        if rows[last_row_idx].dia <= as_of:
            as_of_idx = last_row_idx
        else:
            return {"err": f"no row for {as_of}"}

    if as_of_idx < 100:
        return {"err": f"insufficient training data ({as_of_idx} rows)"}

    features = FEATURES_EQUITY + (FEATURES_RF if include_rf else [])
    # Train usando rows[:as_of_idx] (inclui ontem, exclui hoje)
    mask = np.zeros(len(rows), dtype=bool)
    mask[:as_of_idx] = True
    models = train_models(rows, features, mask)
    if not models:
        return {"err": "training failed (insufficient targets)"}

    r = rows[as_of_idx]
    feats = [r.features.get(f) for f in features]
    if any(v is None for v in feats) or r.vol_21d is None:
        return {"err": "missing features for as_of"}

    p10, p50, p90 = predict_q(models, feats)
    pp = prob_positive(p10, p50, p90)
    sc = score_sig(pp, p50, r.vol_21d)
    sig = classify(sc, config.get("th_buy", 0.10), config.get("th_sell", -0.10))

    return {
        "ok": True,
        "as_of": str(r.dia),
        "signal": sig.value if hasattr(sig, "value") else str(sig).split(".")[-1],
        "score": round(sc, 4),
        "prob_pos": round(pp, 4),
        "p10": round(p10, 4),
        "p50": round(p50, 4),
        "p90": round(p90, 4),
        "current_close": float(r.close),
        "vol_21d": float(r.vol_21d),
    }


def step_paper_run(run_id: int, as_of: date) -> dict:
    """Executa 1 step diário."""
    with psycopg2.connect(DSN) as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM paper_runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        if not run:
            return {"err": f"run {run_id} not found"}
        if not run["is_active"]:
            return {"err": "run inactive"}

        config = run["config_json"]
        universe = run["universe"]
        n_slots = run["n_slots"]
        slot_size = float(run["initial_capital"]) / max(n_slots, 1)
        state = run["state_json"]
        positions = state.get("positions", {})
        cash = float(state.get("cash", run["initial_capital"]))
        equity_curve = state.get("equity_curve", [])
        trades_history = state.get("trades_history", [])

        # Idempotência: se já tem signals pra (run, as_of), pula geração
        cur.execute(
            "SELECT count(*) AS n FROM paper_signals WHERE paper_run_id=%s AND signal_date=%s",
            (run_id, as_of),
        )
        existing = cur.fetchone()["n"]
        if existing > 0:
            print(f"  signals já existem pra {as_of} ({existing}/{len(universe)}), pulando geração")
            signals = {}
            cur.execute(
                "SELECT ticker, signal, current_close FROM paper_signals "
                "WHERE paper_run_id=%s AND signal_date=%s",
                (run_id, as_of),
            )
            for r in cur.fetchall():
                signals[r["ticker"]] = {"signal": r["signal"], "current_close": float(r["current_close"])}
        else:
            print(f"  Computando signals pra {len(universe)} tickers as-of {as_of}...")
            signals = {}
            for i, t in enumerate(universe, 1):
                res = signal_for_ticker(t, as_of, config)
                if not res or "err" in res:
                    print(f"  [{i:>2}/{len(universe)}] {t} skip: {res.get('err') if res else 'no result'}")
                    continue
                signals[t] = res
                cur.execute(
                    """INSERT INTO paper_signals (paper_run_id, signal_date, ticker, signal,
                                                  score, prob_pos, p10, p50, p90,
                                                  current_close, vol_21d)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (paper_run_id, signal_date, ticker) DO UPDATE SET
                         signal=EXCLUDED.signal, score=EXCLUDED.score,
                         prob_pos=EXCLUDED.prob_pos, p10=EXCLUDED.p10, p50=EXCLUDED.p50,
                         p90=EXCLUDED.p90, current_close=EXCLUDED.current_close,
                         vol_21d=EXCLUDED.vol_21d""",
                    (run_id, as_of, t, res["signal"], res["score"], res["prob_pos"],
                     res["p10"], res["p50"], res["p90"], res["current_close"], res["vol_21d"]),
                )
                print(f"  [{i:>2}/{len(universe)}] {t:<8} {res['signal']:<5} score={res['score']:>+6.3f} close=R${res['current_close']:.2f}")
            conn.commit()

        # 2. Aplica signals em positions (signal values são lowercase: buy/sell/hold)
        new_trades = []
        # CLOSE: SELL signal em ticker aberto
        for t, sig in list(signals.items()):
            if sig["signal"].lower() == "sell" and t in positions:
                pos = positions.pop(t)
                close_price = sig["current_close"]
                pnl = pos["qty"] * (close_price - pos["open_price"]) - pos["slot_value"] * config.get("commission", 0.001)
                pnl_pct = ((close_price / pos["open_price"]) - 1.0) * 100
                cash += pos["slot_value"] + pnl
                trade = {
                    "ticker": t,
                    "open_date": pos["open_date"],
                    "close_date": str(as_of),
                    "open_price": pos["open_price"],
                    "close_price": close_price,
                    "qty": pos["qty"],
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                }
                trades_history.append(trade)
                new_trades.append(("CLOSE", t, trade))
                print(f"  CLOSE {t} @ R${close_price:.2f} pnl={pnl:+.2f} ({pnl_pct:+.2f}%)")

        # OPEN: BUY signal em ticker sem position + cash disponível
        for t, sig in signals.items():
            if sig["signal"].lower() == "buy" and t not in positions:
                if cash < slot_size:
                    print(f"  SKIP open {t}: cash insuficiente ({cash:.0f} < {slot_size:.0f})")
                    continue
                open_price = sig["current_close"]
                # Lot size B3 = 100 (decisão #1)
                qty_unrounded = slot_size / open_price
                qty = int(qty_unrounded // 100) * 100  # arredonda pra baixo lote 100
                if qty <= 0:
                    print(f"  SKIP open {t}: qty=0 (preço R${open_price:.2f} > slot R${slot_size:.0f}/100)")
                    continue
                actual_value = qty * open_price
                positions[t] = {
                    "open_date": str(as_of),
                    "open_price": open_price,
                    "qty": qty,
                    "slot_value": round(actual_value, 2),
                }
                cash -= actual_value
                new_trades.append(("OPEN", t, positions[t]))
                print(f"  OPEN  {t} qty={qty} @ R${open_price:.2f} = R${actual_value:.2f}")

        # 3. Mark-to-market
        positions_value = sum(p["qty"] * signals.get(t, {}).get("current_close", p["open_price"])
                              for t, p in positions.items())
        total_equity = cash + positions_value
        equity_snap = {
            "date": str(as_of),
            "cash": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "total": round(total_equity, 2),
            "n_open_positions": len(positions),
        }
        # Se já tem snapshot pra hoje, substitui; senão append
        if equity_curve and equity_curve[-1].get("date") == str(as_of):
            equity_curve[-1] = equity_snap
        else:
            equity_curve.append(equity_snap)

        new_state = {
            "positions": positions,
            "cash": round(cash, 2),
            "equity_curve": equity_curve,
            "trades_history": trades_history,
        }

        cur.execute(
            "UPDATE paper_runs SET state_json=%s, last_step_date=%s, updated_at=NOW() WHERE id=%s",
            (json.dumps(new_state), as_of, run_id),
        )
        conn.commit()

    return {
        "run_id": run_id,
        "as_of": str(as_of),
        "n_signals": len(signals),
        "n_open_positions": len(positions),
        "cash": round(cash, 2),
        "total_equity": round(total_equity, 2),
        "n_trades_today": len(new_trades),
        "trades": new_trades,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="r5-id11-top18", help="paper_run name")
    ap.add_argument("--as-of", default=None, help="signal date (default: hoje, YYYY-MM-DD)")
    ap.add_argument("--create-run", action="store_true",
                    help="cria paper_run nova (precisa --tickers/--capital)")
    ap.add_argument("--tickers", default=None, help="CSV (default: top-18)")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--retrain-days", type=int, default=63)
    ap.add_argument("--th-buy", type=float, default=0.10)
    ap.add_argument("--th-sell", type=float, default=-0.10)
    ap.add_argument("--commission", type=float, default=0.001)
    ap.add_argument("--target-vol", type=float, default=0.015)
    args = ap.parse_args()

    if args.create_run:
        tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else DEFAULT_UNIVERSE
        config = {
            "horizon": args.horizon,
            "retrain_days": args.retrain_days,
            "th_buy": args.th_buy,
            "th_sell": args.th_sell,
            "commission": args.commission,
            "target_vol": args.target_vol,
            "source": "r5_harness_id11_deployment_candidate",
        }
        create_paper_run(args.name, tickers, args.capital, config,
                         notes="forward-test do best config rolling-origin (BMEB4 winner)")
        return 0

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with psycopg2.connect(DSN) as c, c.cursor() as cur:
        cur.execute("SELECT id FROM paper_runs WHERE name=%s", (args.name,))
        row = cur.fetchone()
        if not row:
            print(f"paper_run name='{args.name}' não existe. Use --create-run.")
            return 2
        run_id = row[0]

    print(f"=== Paper step run='{args.name}' (id={run_id}) as_of={as_of} ===")
    res = step_paper_run(run_id, as_of)
    print(f"\n=== STEP DONE ===")
    for k, v in res.items():
        if k == "trades":
            continue
        print(f"  {k:>22} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

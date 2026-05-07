"""r5_portfolio.py — agrega trades de N tickers em 1 portfolio backtest.

Por que: harness atual reporta 87 sharpes independentes. Pra capital
allocation real precisamos de UMA curva de equity cumulativo, com
diversificação verdadeira (correlation entre ticker losses/wins) e cap
de slots concurrent.

Metodologia (trade-level cash flow):
  1. Run WF por ticker (já valida config no rolling-origin), captura
     `trade.entry_dt`, `trade.exit_dt`, `trade.pnl_pct`.
  2. Sort eventos por timestamp (OPEN+CLOSE intercalados across tickers).
  3. Simula 1 cash pool comum:
       - cap = INITIAL_CAPITAL / N_SLOTS por trade
       - Se cash insuficiente OU positions saturado em N_SLOTS, skip
       - PnL realiza no CLOSE (não MTM diário entre OPEN/CLOSE)
  4. Computa portfolio sharpe + max_dd + total_return.

Comparação:
  - vs avg(single-ticker sharpes) — diversification gain
  - vs single best ticker — concentration vs diversification trade-off
  - portfolio dd vs max(single-ticker dd) — risk reduction

Uso:
  docker exec -e PROFIT_TIMESCALE_DSN=... -e FINANALYTICS_DSN=... \
    finanalytics_api python /tmp/portfolio.py
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, "/tmp")

from mlstrategy_backtest_wf import run_wf_for_ticker

# Top-18 do rolling 5-fold (sharpe > 0.5 em todos os 6 folds)
UNIVERSE_TOP18 = [
    "BMEB4", "FRAS3", "MDNE3", "DIRR3", "NEOE3", "CPLE3",     # Tier S (sh>1.0)
    "BPAC11", "ENEV3", "CMIG3", "ENGI11", "CYRE3",            # Tier A (sh>0.7)
    "GFSA3", "EQTL3", "ITSA3", "ITSA4", "B3SA3", "HAPV3", "CEAB3",  # Tier B
]

PARAMS = {
    "horizon": 10,
    "retrain_days": 63,
    "th_buy": 0.10,
    "th_sell": -0.10,
    "commission": 0.001,
    "train_end": "2023-12-31",
    "target_vol": 0.015,  # passa via position_size? Não — run_wf_for_ticker aceita
}


@dataclass
class Event:
    dt: object  # datetime
    kind: str   # 'OPEN' | 'CLOSE'
    ticker: str
    pnl_pct: float = 0.0
    open_evt: "Event | None" = None  # back-link no CLOSE


def fetch_trades(tickers: list[str]) -> dict[str, list]:
    """Roda WF per ticker, retorna {ticker: [Trade,...]}."""
    out = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i:>2}/{len(tickers)}] {t}...", end="", flush=True)
        # vol-target via position_size — simulamos manualmente passando ticker_vol
        # Aqui ignora vol-target pra simplicidade: pos_size=1.0 (full slot allocation)
        res = run_wf_for_ticker(
            ticker=t,
            horizon=PARAMS["horizon"],
            retrain_days=PARAMS["retrain_days"],
            commission=PARAMS["commission"],
            th_buy=PARAMS["th_buy"],
            th_sell=PARAMS["th_sell"],
            train_end=PARAMS["train_end"],
            position_size=1.0,
        )
        if not res["ok"]:
            print(f" FAIL: {res.get('error')}")
            continue
        n = len(res["result"].trades)
        sharpe = res["result"].metrics.sharpe_ratio
        ret = res["result"].metrics.total_return_pct
        print(f" {n} trades  sharpe={sharpe:.2f}  ret={ret:+.1f}%")
        out[t] = res["result"].trades
    return out


def simulate_portfolio(
    trades_by_ticker: dict[str, list],
    initial_capital: float = 100_000.0,
    n_slots: int = 18,
    max_concurrent: int | None = None,
) -> dict:
    """Simula 1 cash pool comum across N tickers.

    n_slots = quantos "slots" iguais o capital é dividido (cap por trade).
    max_concurrent = limite de positions simultâneas (default = n_slots).
    """
    if max_concurrent is None:
        max_concurrent = n_slots
    slot_size = initial_capital / n_slots

    events: list[Event] = []
    for t, trades in trades_by_ticker.items():
        for tr in trades:
            ev_open = Event(dt=tr.entry_date, kind="OPEN", ticker=t, pnl_pct=tr.pnl_pct)
            ev_close = Event(dt=tr.exit_date, kind="CLOSE", ticker=t, pnl_pct=tr.pnl_pct,
                             open_evt=ev_open)
            events.append(ev_open)
            events.append(ev_close)
    events.sort(key=lambda e: (e.dt, 0 if e.kind == "CLOSE" else 1))  # CLOSE antes OPEN no mesmo dia

    cash = initial_capital
    positions: dict[str, tuple[float, Event]] = {}  # ticker -> (slot_value, open_event)
    equity_curve: list[tuple] = []
    n_trades_executed = 0
    n_trades_skipped = 0

    for ev in events:
        if ev.kind == "OPEN":
            if ev.ticker in positions:
                continue  # já temos position nesse ticker
            if len(positions) >= max_concurrent or cash < slot_size:
                n_trades_skipped += 1
                continue
            positions[ev.ticker] = (slot_size, ev)
            cash -= slot_size
        else:  # CLOSE
            pos = positions.pop(ev.ticker, None)
            if pos is None:
                continue
            slot_value, _ = pos
            pnl = slot_value * (ev.pnl_pct / 100.0)
            cash += slot_value + pnl
            n_trades_executed += 1
            total_equity = cash + sum(v for v, _ in positions.values())
            equity_curve.append((ev.dt, total_equity))

    # Posições remaining no final: realizar com pnl=0 (assumimos roll-over)
    for ticker, (slot_value, _) in positions.items():
        cash += slot_value
    final_equity = cash
    if equity_curve:
        equity_curve.append((events[-1].dt, final_equity))

    if not equity_curve:
        return {"err": "nenhum trade executado"}

    # Métricas
    initial = initial_capital
    final = equity_curve[-1][1]
    total_ret = (final / initial - 1.0) * 100.0

    # Equity returns per close event
    equities = [e[1] for e in equity_curve]
    rets = [(equities[i] / equities[i - 1] - 1.0) for i in range(1, len(equities))]
    if len(rets) >= 2:
        mu = statistics.mean(rets)
        sd = statistics.stdev(rets)
        # Sharpe annualized: usa avg duration entre closes
        # Eventos de close vêm em datas diferentes. Aproximar: trading days span / N events
        first_dt = equity_curve[0][0]
        last_dt = equity_curve[-1][0]
        days_span = max(1, (last_dt - first_dt).days) if hasattr(last_dt, "day") else len(rets)
        avg_period_days = days_span / len(rets)
        ann_factor = math.sqrt(252.0 / max(avg_period_days, 1.0))
        sharpe_p = (mu / sd * ann_factor) if sd > 0 else 0.0
    else:
        sharpe_p = 0.0

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    for v in equities:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "n_trades_executed": n_trades_executed,
        "n_trades_skipped": n_trades_skipped,
        "n_unique_dates": len(equity_curve),
        "initial": initial,
        "final": round(final, 2),
        "total_return_pct": round(total_ret, 2),
        "sharpe_portfolio": round(sharpe_p, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "n_slots": n_slots,
        "max_concurrent": max_concurrent,
        "slot_size": round(slot_size, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(UNIVERSE_TOP18),
                    help="CSV (default: top-18 consistente)")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--slots", type=int, default=None,
                    help="N slots = N tickers default")
    ap.add_argument("--max-concurrent", type=int, default=None,
                    help="cap de positions simultaneas (default = N slots)")
    args = ap.parse_args()

    universe = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    n_slots = args.slots or len(universe)
    max_conc = args.max_concurrent

    print(f"=== R5 Portfolio Backtest ===")
    print(f"Universe ({len(universe)}): {universe}")
    print(f"Capital: R$ {args.capital:.0f}, slots={n_slots}, max_conc={max_conc or n_slots}")
    print(f"Config: {PARAMS}\n")

    print("--- Running WF per ticker ---")
    trades_by_ticker = fetch_trades(universe)

    print(f"\n--- Single-ticker baseline (avg/median) ---")
    sharpes = []
    rets = []
    dds = []
    for t, trades in trades_by_ticker.items():
        # Recompute simple metrics from trade list
        if not trades:
            continue
        ret_pct = (1.0 + sum(tr.pnl_pct/100 for tr in trades)) - 1.0  # additive approx
        rets.append(ret_pct * 100)
    print(f"  N tickers: {len(trades_by_ticker)}")
    print(f"  Avg trades/ticker: {sum(len(v) for v in trades_by_ticker.values())/max(len(trades_by_ticker),1):.1f}")

    print(f"\n--- Simulating portfolio (slot=R$ {args.capital/n_slots:.0f}) ---")
    res = simulate_portfolio(
        trades_by_ticker,
        initial_capital=args.capital,
        n_slots=n_slots,
        max_concurrent=max_conc,
    )

    print(f"\n=== PORTFOLIO RESULTS ===")
    for k, v in res.items():
        print(f"  {k:>22} = {v}")

    # Diversification benefit metric
    print(f"\n=== DIVERSIFICATION ===")
    print(f"  Single best ticker total_return: BMEB4 ~+830% (rolling avg)")
    print(f"  Single best ticker max_dd:        ~30% (rolling)")
    print(f"  Portfolio total_return:           {res.get('total_return_pct', 'N/A')}%")
    print(f"  Portfolio max_dd:                 {res.get('max_drawdown_pct', 'N/A')}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())

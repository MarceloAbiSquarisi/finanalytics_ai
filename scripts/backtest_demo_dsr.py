"""
Demo end-to-end do harness de backtest com slippage + Deflated Sharpe (R5).

Carrega bars diarias de fintz_cotacoes_ts (10+ anos B3), roda grid_search em
uma estrategia tecnica conhecida, e produz JSON com:
  - melhor combinacao de parametros
  - metricas de performance (SR, retorno, drawdown, win rate)
  - **Deflated Sharpe Ratio** (LdP 2014) — prob_real e o numero "honesto"
    apos correcao de multiple testing bias do grid

Saida: backtest_runs/<ticker>_<strategy>_<ts>.json

Uso:
  python scripts/backtest_demo_dsr.py
  python scripts/backtest_demo_dsr.py --ticker VALE3 --strategy macd --start 2018-01-01

Notas:
  - Slippage default-on (0.05% acoes / 2 ticks futuros) — ja embutido em run_backtest.
  - Top 10 candidatos retornados; DSR aplicado ao melhor (rank 1).
  - Util como referencia comparativa quando R2/R3/R4 forem implementados.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any

# Garante import a partir da raiz do projeto
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg2

from finanalytics_ai.domain.backtesting.optimizer import (
    OptimizationObjective,
    grid_search,
)


def load_bars(dsn: str, ticker: str, start_date: str, end_date: str) -> list[dict]:
    """
    Carrega OHLCV diario de fintz_cotacoes_ts no formato esperado pelo engine.

    Retorna lista de dicts com keys: time (epoch int), open, high, low, close, volume.
    """
    sql = """
        SELECT
          EXTRACT(EPOCH FROM time)::bigint AS ts,
          preco_abertura::float            AS open,
          preco_maximo::float              AS high,
          preco_minimo::float              AS low,
          preco_fechamento_ajustado::float AS close,
          volume_negociado::float          AS volume
        FROM fintz_cotacoes_ts
        WHERE ticker = %s
          AND time >= %s::date
          AND time <= %s::date
          AND preco_fechamento_ajustado IS NOT NULL
        ORDER BY time
    """
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker.upper(), start_date, end_date))
        rows = cur.fetchall()
    return [
        {
            "time": int(r[0]),
            "open": float(r[1] or r[4]),
            "high": float(r[2] or r[4]),
            "low": float(r[3] or r[4]),
            "close": float(r[4]),
            "volume": float(r[5] or 0.0),
        }
        for r in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="PETR4")
    ap.add_argument(
        "--strategy",
        default="rsi",
        choices=["rsi", "macd", "combined", "ema_cross", "momentum", "bollinger"],
    )
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument(
        "--objective",
        default="sharpe",
        choices=["sharpe", "return", "calmar", "win_rate", "profit_factor"],
    )
    ap.add_argument("--initial-capital", type=float, default=10_000.0)
    ap.add_argument(
        "--no-slippage",
        action="store_true",
        help="Desabilita slippage (default: ativo). Util para sanity check.",
    )
    ap.add_argument(
        "--persist",
        action="store_true",
        help="Persiste resultado em backtest_results (UPSERT por config_hash). "
        "Requer DATABASE_URL no .env e migration 0021 aplicada.",
    )
    ap.add_argument(
        "--respect-delisting",
        action="store_true",
        help="R5 step 2: consulta b3_delisted_tickers e passa delisting_date + "
        "last_known_price ao engine p/ force-close (anti survivorship bias).",
    )
    args = ap.parse_args()

    dsn = os.environ.get(
        "PROFIT_TIMESCALE_DSN",
        "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
    )

    print(f"== Backtest demo: {args.ticker} / {args.strategy} / {args.start}..{args.end} ==")
    bars = load_bars(dsn, args.ticker, args.start, args.end)
    if len(bars) < 100:
        print(f"!! Dados insuficientes: {len(bars)} bars. Aborte.", file=sys.stderr)
        return 1
    print(f"   bars carregados: {len(bars)}")
    print(f"   slippage:        {'desativado (--no-slippage)' if args.no_slippage else 'ATIVO'}")

    # R5 step 2 — survivorship bias
    delisting_date = None
    last_known_price = None
    if args.respect_delisting:
        delisting_date, last_known_price = _lookup_delisting(args.ticker)
        if delisting_date:
            print(
                f"   delisting:       {args.ticker.upper()} delistou em "
                f"{delisting_date} (last_close={last_known_price})"
            )
        else:
            print(f"   delisting:       {args.ticker.upper()} nao consta como delisted")

    # Grid search com objetivo configuravel. valid_runs viraum N pra DSR.
    print("   rodando grid search...")
    result = grid_search(
        bars=bars,
        strategy_name=args.strategy,
        ticker=args.ticker.upper(),
        range_period=f"{args.start}..{args.end}",
        initial_capital=args.initial_capital,
        objective=OptimizationObjective(args.objective),
        top_n=10,
        delisting_date=delisting_date,
        last_known_price=last_known_price,
    )

    # Sumario humano
    print("\n=== Top 5 candidatos ===")
    for run in result.top[:5]:
        m = run.metrics
        print(
            f"   #{run.rank}: {run.params!s:50} "
            f"score={run.score:.3f} ret={m.total_return_pct:.1f}% "
            f"SR={m.sharpe_ratio:.2f} DD={m.max_drawdown_pct:.1f}% "
            f"trades={m.total_trades} wr={m.win_rate_pct:.1f}%"
        )

    print("\n=== Deflated Sharpe (LdP 2014) ===")
    if result.deflated_sharpe:
        d = result.deflated_sharpe
        verdict = (
            "[OK] PROVAVELMENTE REAL"
            if d["prob_real"] >= 0.95
            else ("[!!] SINAL FRACO" if d["prob_real"] >= 0.5 else "[XX] PROVAVEL OVERFITTING")
        )
        print(f"   observed_sharpe : {d['observed_sharpe']:.3f}")
        print(f"   E[max SR | H0]  : {d['e_max_sharpe']:.3f} (sob {d['num_trials']} trials)")
        print(f"   deflated_sharpe : {d['deflated_sharpe']:.3f} (z-score)")
        print(f"   prob_real       : {d['prob_real']:.4f}  -> {verdict}")
        print(f"   sample_size T   : {d['sample_size']}")
    else:
        print("   (DSR nao calculado - poucos runs validos ou bars insuficientes)")

    # Persiste JSON
    Path(ROOT / "backtest_runs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_path = (
        ROOT / "backtest_runs" / f"{args.ticker.upper()}_{args.strategy}_{args.objective}_{ts}.json"
    )
    payload = {
        "config": {
            "ticker": args.ticker.upper(),
            "strategy": args.strategy,
            "objective": args.objective,
            "start": args.start,
            "end": args.end,
            "initial_capital": args.initial_capital,
            "slippage_applied": not args.no_slippage,
            "bars": len(bars),
        },
        "result": result.to_dict(),
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n-> Resultado completo salvo em: {out_path.relative_to(ROOT)}")

    if args.persist:
        _persist_to_db(args, result)

    return 0


def _persist_to_db(args: argparse.Namespace, result: Any) -> None:
    """
    UPSERT em backtest_results via psycopg2 sync (mais simples que stand up
    de async session_factory dentro de um script CLI). Re-runs do mesmo
    config sao idempotentes via UNIQUE em config_hash.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from finanalytics_ai.infrastructure.database.connection import Base
    from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
        BacktestResultRepository,
        compute_config_hash,
    )

    db_url = os.environ.get("DATABASE_URL") or os.environ.get(
        "FINANALYTICS_DATABASE_URL",
        "postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics",
    )
    if "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    config_hash = compute_config_hash(
        ticker=args.ticker,
        strategy=args.strategy,
        range_period=f"{args.start}..{args.end}",
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.initial_capital,
        objective=args.objective,
        slippage_applied=not args.no_slippage,
        params=result.best_params,
    )

    async def _do_save() -> None:
        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            repo = BacktestResultRepository(factory)
            row, created = await repo.save_run(
                config_hash=config_hash,
                ticker=args.ticker,
                strategy=args.strategy,
                full_result=result.to_dict(),
                range_period=f"{args.start}..{args.end}",
                start_date=args.start,
                end_date=args.end,
                initial_capital=args.initial_capital,
                objective=args.objective,
                slippage_applied=not args.no_slippage,
                params=result.best_params,
            )
            verb = "criado" if created else "atualizado"
            print(
                f"-> backtest_results: {verb} (config_hash={config_hash[:12]}..., id={row['id'][:8]}...)"
            )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_do_save())
    except Exception as exc:
        print(f"!! Persistencia falhou: {exc}", file=sys.stderr)


def _lookup_delisting(ticker: str) -> tuple[Any | None, float | None]:
    """
    Consulta b3_delisted_tickers (Postgres) p/ delisting_date + last_known_price.

    Retorna (None, None) se ticker nao esta na tabela ou esta com delisting_date NULL.
    Skip explicito para placeholders UNK_<cnpj>.
    """
    if ticker.upper().startswith("UNK_"):
        return None, None
    pg_dsn = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql://finanalytics:secret@localhost:5432/finanalytics",
    )
    if "asyncpg" in pg_dsn:
        pg_dsn = pg_dsn.replace("+asyncpg", "")
    try:
        with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT delisting_date, last_known_price
                FROM b3_delisted_tickers
                WHERE ticker = %s AND delisting_date IS NOT NULL
                LIMIT 1
                """,
                (ticker.upper(),),
            )
            row = cur.fetchone()
        if not row:
            return None, None
        return row[0], float(row[1]) if row[1] is not None else None
    except Exception as exc:
        print(f"!! Lookup delisting falhou: {exc}", file=sys.stderr)
        return None, None


if __name__ == "__main__":
    sys.exit(main())

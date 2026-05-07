"""Relatorio de cobertura ohlc_1m ticker-a-ticker.

Para cada ticker em ohlc_1m calcula:
  - first_day, last_day  (dia mais antigo e mais recente com dado)
  - present_days         (dias com pelo menos 1 bar)
  - expected_days        (trading_days_in_range entre first_day e last_day,
                          considerando segmento do ticker — 'futures' p/ WIN/WDO/IND/DOL,
                          'stocks' pro restante)
  - coverage_pct         (present / expected × 100)
  - missing_count

Output: docs/relatorio_cobertura.md (markdown).

Uso:
  docker exec finanalytics_api python scripts/generate_coverage_report.py \
    > docs/relatorio_cobertura.md
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

import asyncpg

from finanalytics_ai.application.services.backfill_runner import (
    _exchange_for_ticker,
)
from finanalytics_ai.infrastructure.database.repositories import (
    backfill_repo as r,
)


_TS_DSN = (
    "postgres://finanalytics:timescale_secret@timescale:5432/market_data"
)


async def main() -> None:
    await r.load_b3_no_trading_days()
    conn = await asyncpg.connect(_TS_DSN)
    try:
        # 1 query: por ticker, conta dias distintos com dado + range
        rows = await conn.fetch(
            """
            SELECT
              ticker,
              count(DISTINCT time::date) AS present_days,
              min(time)::date AS first_day,
              max(time)::date AS last_day,
              count(*) AS total_bars
            FROM ohlc_1m
            GROUP BY ticker
            ORDER BY ticker
            """
        )
    finally:
        await conn.close()

    out: list[dict] = []
    for row in rows:
        ticker: str = row["ticker"]
        first_day: date = row["first_day"]
        last_day: date = row["last_day"]
        present_days = int(row["present_days"])
        bars = int(row["total_bars"])
        ex = _exchange_for_ticker(ticker)
        expected = r.trading_days_in_range(first_day, last_day, ex)
        expected_n = len(expected)
        missing = max(0, expected_n - present_days)
        coverage_pct = (
            round(100.0 * present_days / expected_n, 1) if expected_n else 0.0
        )
        out.append({
            "ticker": ticker,
            "exchange": ex,
            "first_day": first_day,
            "last_day": last_day,
            "present_days": present_days,
            "expected_days": expected_n,
            "missing_count": missing,
            "coverage_pct": coverage_pct,
            "total_bars": bars,
            "years_span": round((last_day - first_day).days / 365.25, 1),
        })

    # Agrupa em buckets
    buckets = {
        "perfect": [x for x in out if x["coverage_pct"] >= 99.5],
        "high":    [x for x in out if 95 <= x["coverage_pct"] < 99.5],
        "medium":  [x for x in out if 80 <= x["coverage_pct"] < 95],
        "low":     [x for x in out if 50 <= x["coverage_pct"] < 80],
        "broken":  [x for x in out if x["coverage_pct"] < 50],
    }
    by_ex = Counter(x["exchange"] for x in out)

    today = date.today().isoformat()

    print("# Relatório de cobertura ohlc_1m por ticker")
    print()
    print(f"> Gerado em **{today}** por `scripts/generate_coverage_report.py`. "
          "Cobertura calculada com segmentação (futuros vs ações) e respeitando "
          "feriados/atípicos B3 do calendário oficial.")
    print()
    print("## Sumário")
    print()
    print(f"- **{len(out)} tickers** com pelo menos 1 bar em `ohlc_1m`")
    print(f"  - Stocks (ações B3): **{by_ex.get('B', 0)}**")
    print(f"  - Futures (WIN/WDO/IND/DOL): **{by_ex.get('F', 0)}**")
    print()
    print("### Distribuição de cobertura")
    print()
    print("| Bucket | Coverage | Tickers |")
    print("|---|---|---|")
    print(f"| 🟢 Perfeito | ≥99.5% | {len(buckets['perfect'])} |")
    print(f"| 🟡 Alto | 95–99.5% | {len(buckets['high'])} |")
    print(f"| 🟠 Médio | 80–95% | {len(buckets['medium'])} |")
    print(f"| 🔴 Baixo | 50–80% | {len(buckets['low'])} |")
    print(f"| ⛔ Quebrado | <50% | {len(buckets['broken'])} |")
    print()
    print("### Tickers críticos (bucket 🔴/⛔ — atenção pra backtests)")
    print()
    if buckets["broken"] or buckets["low"]:
        print("| Ticker | Range | Anos | Presentes | Esperados | Missing | Cobertura |")
        print("|---|---|---|---|---|---|---|")
        for x in sorted(buckets["broken"] + buckets["low"],
                        key=lambda r: r["coverage_pct"]):
            badge = "⛔" if x["coverage_pct"] < 50 else "🔴"
            print(
                f"| {badge} `{x['ticker']}` "
                f"| {x['first_day']} → {x['last_day']} "
                f"| {x['years_span']} "
                f"| {x['present_days']} "
                f"| {x['expected_days']} "
                f"| {x['missing_count']} "
                f"| {x['coverage_pct']}% |"
            )
    else:
        print("Nenhum ticker em bucket crítico — cobertura geral excelente.")
    print()
    print("### Tickers em bucket Médio (🟠 80–95%)")
    print()
    if buckets["medium"]:
        print("| Ticker | Range | Presentes / Esperados | Missing | Cobertura |")
        print("|---|---|---|---|---|")
        for x in sorted(buckets["medium"], key=lambda r: r["coverage_pct"]):
            print(
                f"| `{x['ticker']}` "
                f"| {x['first_day']} → {x['last_day']} "
                f"| {x['present_days']} / {x['expected_days']} "
                f"| {x['missing_count']} "
                f"| {x['coverage_pct']}% |"
            )
    else:
        print("(vazio)")
    print()
    print("## Lista completa")
    print()
    print("Ordenada por cobertura crescente (problemáticos primeiro).")
    print()
    print("| Ticker | Ex | First → Last | Anos | Bars (1m) | Dias (P/E) | Cov |")
    print("|---|---|---|---|---|---|---|")
    for x in sorted(out, key=lambda r: (r["coverage_pct"], r["ticker"])):
        cov_color = (
            "🟢" if x["coverage_pct"] >= 99.5
            else "🟡" if x["coverage_pct"] >= 95
            else "🟠" if x["coverage_pct"] >= 80
            else "🔴" if x["coverage_pct"] >= 50
            else "⛔"
        )
        print(
            f"| `{x['ticker']}` "
            f"| {x['exchange']} "
            f"| {x['first_day']} → {x['last_day']} "
            f"| {x['years_span']} "
            f"| {x['total_bars']:,} "
            f"| {x['present_days']} / {x['expected_days']} "
            f"| {cov_color} {x['coverage_pct']}% |"
        )
    print()
    print("## Como agir")
    print()
    print("- **🟢 Perfeito** — pode usar direto em backtests.")
    print("- **🟡 Alto (95–99.5%)** — gap pequeno, geralmente listing recente "
          "ou suspensão isolada. OK pra backtest com ressalva.")
    print("- **🟠 Médio (80–95%)** — investigar via `/admin → Banco de Dados → "
          "Gaps de Market Data` e usar **▶ Preencher agora** pra completar.")
    print("- **🔴 Baixo (50–80%)** — provavelmente listing começou no meio do "
          "range; truncar `first_day` antes de backtest, ou rodar backfill "
          "completo via `/admin → Backfill`.")
    print("- **⛔ Quebrado (<50%)** — pode ser ticker delisted antes de cobrir "
          "todo o range solicitado, ou erro de coleta. Validar via "
          "`b3_delisted_tickers` antes de incluir em backtest (survivorship).")
    print()
    print("## Re-gerar")
    print()
    print("```bash")
    print("docker exec finanalytics_api python scripts/generate_coverage_report.py \\")
    print("  > docs/relatorio_cobertura.md")
    print("```")


if __name__ == "__main__":
    asyncio.run(main())

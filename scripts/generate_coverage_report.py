"""Relatorio de cobertura ohlc_1m ticker-a-ticker.

Para cada ticker em ohlc_1m calcula:
  - first_day, last_day        (extremos da série)
  - present_days               (dias com pelo menos 1 bar)
  - effective_first_day        (1° dia com >= LIQUID_THRESHOLD bars no dia
                                — proxy de liquidez real, ignora periodos
                                pre-merger/IPO/baixa atividade)
  - expected_days              (trading_days_in_range entre first_day e last_day)
  - effective_expected_days    (idem, mas a partir de effective_first_day)
  - coverage_pct               (present / expected) — series completa
  - effective_coverage_pct     (present_in_effective_window / effective_expected)
                                — coverage real para uso em backtest

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

# Threshold de barras/dia para considerar "dia liquido". 50 bars 1m = ~9% do
# pregao 9h-18h. Filtra papeis ilíquidos (ex: DASA3 em 2020 com 4 bars/dia).
LIQUID_THRESHOLD = 50

# effective_first_day robusto: 1° dia liquido cuja janela CONTINUITY_WINDOW_DAYS
# (calendar) seguinte tem pelo menos CONTINUITY_MIN_LIQUID outros dias liquidos.
# Filtra ilhas isoladas (ex: 2 dias orfaos de PETR4 em 2017/2018).
CONTINUITY_WINDOW_DAYS = 30
CONTINUITY_MIN_LIQUID = 5


def _robust_effective_first_day(liquid_days: list[date]) -> date | None:
    """Acha 1° dia liquido seguido de >= MIN outros dias liquidos em janela."""
    if not liquid_days:
        return None
    n = len(liquid_days)
    j = 0
    for i in range(n):
        # Janela: liquid_days[k] em (liquid_days[i], liquid_days[i] + window]
        window_end = liquid_days[i].toordinal() + CONTINUITY_WINDOW_DAYS
        if j < i + 1:
            j = i + 1
        while j < n and liquid_days[j].toordinal() <= window_end:
            j += 1
        liquid_in_window = j - i - 1
        if liquid_in_window >= CONTINUITY_MIN_LIQUID:
            return liquid_days[i]
    return None

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
        # 1 query agrega por ticker incluindo `effective_first_day`
        # (primeiro dia com >= LIQUID_THRESHOLD bars). Usa CTE per_day para
        # contar bars/dia e filtrar.
        # Por ticker: agg + array de liquid_days p/ Python computar robust
        # effective_first_day. Robust = 1° dia liquido cuja janela 30
        # calendar-days a frente tem >= 5 outros dias liquidos. Filtra
        # ilhas isoladas (ex: PETR4 tem 2 dias órfãos em 2017/2018).
        rows = await conn.fetch(
            f"""
            WITH per_day AS (
              SELECT ticker, time::date AS day, count(*) AS bars
              FROM ohlc_1m
              GROUP BY 1, 2
            )
            SELECT
              ticker,
              count(*) AS present_days,
              min(day) AS first_day,
              max(day) AS last_day,
              sum(bars) AS total_bars,
              count(*) FILTER (WHERE bars >= {LIQUID_THRESHOLD}) AS liquid_days,
              array_agg(day ORDER BY day) FILTER (WHERE bars >= {LIQUID_THRESHOLD})
                AS liquid_days_list
            FROM per_day
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
        liquid_count = int(row["liquid_days"] or 0)
        liquid_days_list: list[date] = list(row["liquid_days_list"] or [])
        eff_first = _robust_effective_first_day(liquid_days_list)
        present_in_eff = (
            sum(1 for d in liquid_days_list if eff_first is not None and d >= eff_first)
            if eff_first else 0
        )
        ex = _exchange_for_ticker(ticker)
        expected = r.trading_days_in_range(first_day, last_day, ex)
        expected_n = len(expected)
        missing = max(0, expected_n - present_days)
        coverage_pct = (
            round(100.0 * present_days / expected_n, 1) if expected_n else 0.0
        )
        # Effective: a partir do 1° dia com liquidez. Se ticker nunca teve
        # dia "liquido" (ex: TKNO4 com 3 bars/dia), eff_first=None e
        # effective_coverage_pct fica 0.
        if eff_first is not None:
            eff_expected = r.trading_days_in_range(eff_first, last_day, ex)
            eff_expected_n = len(eff_expected)
            eff_missing = max(0, eff_expected_n - present_in_eff)
            eff_cov_pct = (
                round(100.0 * present_in_eff / eff_expected_n, 1)
                if eff_expected_n else 0.0
            )
        else:
            eff_expected_n = 0
            eff_missing = 0
            eff_cov_pct = 0.0
        out.append({
            "ticker": ticker,
            "exchange": ex,
            "first_day": first_day,
            "last_day": last_day,
            "effective_first_day": eff_first,
            "present_days": present_days,
            "liquid_days": liquid_count,
            "expected_days": expected_n,
            "effective_expected_days": eff_expected_n,
            "missing_count": missing,
            "effective_missing": eff_missing,
            "coverage_pct": coverage_pct,
            "effective_coverage_pct": eff_cov_pct,
            "total_bars": bars,
            "years_span": round((last_day - first_day).days / 365.25, 1),
            "effective_years_span": (
                round((last_day - eff_first).days / 365.25, 1)
                if eff_first else 0.0
            ),
        })

    # Buckets baseados em EFFECTIVE coverage (após 1° dia liquido).
    def _bucket(cov: float) -> str:
        if cov >= 99.5: return "perfect"
        if cov >= 95: return "high"
        if cov >= 80: return "medium"
        if cov >= 50: return "low"
        return "broken"

    buckets: dict[str, list[dict]] = {
        "perfect": [], "high": [], "medium": [], "low": [], "broken": [],
    }
    for x in out:
        # Tickers sem nenhum dia "liquido" vão pra broken
        if x["effective_first_day"] is None:
            buckets["broken"].append(x)
        else:
            buckets[_bucket(x["effective_coverage_pct"])].append(x)
    by_ex = Counter(x["exchange"] for x in out)

    today = date.today().isoformat()

    def _badge(cov: float) -> str:
        if cov >= 99.5: return "🟢"
        if cov >= 95: return "🟡"
        if cov >= 80: return "🟠"
        if cov >= 50: return "🔴"
        return "⛔"

    print("# Relatório de cobertura ohlc_1m por ticker")
    print()
    print(f"> Gerado em **{today}** por `scripts/generate_coverage_report.py`. "
          "Cobertura calculada com segmentação (futuros vs ações) e respeitando "
          "feriados/atípicos B3 do calendário oficial.")
    print(f">")
    print(f"> **`effective_first_day`**: 1° dia em que o ticker teve "
          f"≥{LIQUID_THRESHOLD} bars de 1m E é seguido de ≥"
          f"{CONTINUITY_MIN_LIQUID} outros dias líquidos nos próximos "
          f"{CONTINUITY_WINDOW_DAYS} dias (calendário). "
          "Continuity check filtra ilhas isoladas (ex: 2 dias órfãos de "
          "PETR4 em 2017/2018 antes da série Nelogica). Tickers com alta "
          "**effective_coverage_pct** podem ser usados em backtests a partir "
          "desse `effective_first_day` com confiança.")
    print()
    print("## Sumário")
    print()
    print(f"- **{len(out)} tickers** com pelo menos 1 bar em `ohlc_1m`")
    print(f"  - Stocks (ações B3): **{by_ex.get('B', 0)}**")
    print(f"  - Futures (WIN/WDO/IND/DOL): **{by_ex.get('F', 0)}**")
    print()
    print("### Distribuição de cobertura (após `effective_first_day`)")
    print()
    print("| Bucket | Effective Coverage | Tickers |")
    print("|---|---|---|")
    print(f"| 🟢 Perfeito | ≥99.5% | {len(buckets['perfect'])} |")
    print(f"| 🟡 Alto | 95–99.5% | {len(buckets['high'])} |")
    print(f"| 🟠 Médio | 80–95% | {len(buckets['medium'])} |")
    print(f"| 🔴 Baixo | 50–80% | {len(buckets['low'])} |")
    print(f"| ⛔ Quebrado | <50% (ou nunca líquido) | {len(buckets['broken'])} |")
    print()
    print("### Tickers críticos (bucket 🔴/⛔ — investigar antes de backtest)")
    print()
    if buckets["broken"] or buckets["low"]:
        print("| Ticker | Range Total | Effective First | Anos Eff. | "
              "Presentes/Esp. (Eff.) | Eff. Cov |")
        print("|---|---|---|---|---|---|")
        for x in sorted(buckets["broken"] + buckets["low"],
                        key=lambda r: r["effective_coverage_pct"]):
            eff = x["effective_first_day"]
            eff_str = eff.isoformat() if eff else "—"
            badge = _badge(x["effective_coverage_pct"])
            present_eff = (
                x["present_days"]
                if eff is None
                else max(0, x["effective_expected_days"] - x["effective_missing"])
            )
            print(
                f"| {badge} `{x['ticker']}` "
                f"| {x['first_day']} → {x['last_day']} "
                f"| {eff_str} "
                f"| {x['effective_years_span']} "
                f"| {present_eff} / {x['effective_expected_days']} "
                f"| {x['effective_coverage_pct']}% |"
            )
    else:
        print("Nenhum ticker em bucket crítico — cobertura geral excelente.")
    print()
    print("### Tickers em bucket Médio (🟠 80–95%)")
    print()
    if buckets["medium"]:
        print("| Ticker | Effective First | Eff. P/E | Missing | Eff. Cov |")
        print("|---|---|---|---|---|")
        for x in sorted(buckets["medium"], key=lambda r: r["effective_coverage_pct"]):
            eff = x["effective_first_day"]
            present_eff = max(0, x["effective_expected_days"] - x["effective_missing"])
            print(
                f"| `{x['ticker']}` "
                f"| {eff.isoformat() if eff else '—'} "
                f"| {present_eff} / {x['effective_expected_days']} "
                f"| {x['effective_missing']} "
                f"| {x['effective_coverage_pct']}% |"
            )
    else:
        print("(vazio)")
    print()
    print("## Lista completa")
    print()
    print("Ordenada por effective_coverage crescente (problemáticos primeiro).")
    print()
    print("| Ticker | Ex | First → Last (Total) | Eff. First | Eff. Anos | "
          "Bars (1m) | P/E (Eff.) | Eff. Cov | Total Cov |")
    print("|---|---|---|---|---|---|---|---|---|")
    for x in sorted(out, key=lambda r: (r["effective_coverage_pct"], r["ticker"])):
        eff = x["effective_first_day"]
        eff_str = eff.isoformat() if eff else "—"
        present_eff = (
            x["present_days"]
            if eff is None
            else max(0, x["effective_expected_days"] - x["effective_missing"])
        )
        eff_badge = _badge(x["effective_coverage_pct"])
        tot_badge = _badge(x["coverage_pct"])
        print(
            f"| `{x['ticker']}` "
            f"| {x['exchange']} "
            f"| {x['first_day']} → {x['last_day']} "
            f"| {eff_str} "
            f"| {x['effective_years_span']} "
            f"| {x['total_bars']:,} "
            f"| {present_eff} / {x['effective_expected_days']} "
            f"| {eff_badge} {x['effective_coverage_pct']}% "
            f"| {tot_badge} {x['coverage_pct']}% |"
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

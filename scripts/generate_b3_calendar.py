"""Gera docs/calendario_b3.md com feriados e atípicos B3 de 2017 a 2035.

Combina:
  - lib `holidays` BR + subdiv=SP, categorias public+optional
  - tabela b3_no_trading_days (dias atípicos auto-detectados ou seedados)

Uso:
  docker exec finanalytics_api python scripts/generate_b3_calendar.py \
    > docs/calendario_b3.md
"""

from __future__ import annotations

import asyncio
from datetime import date

import holidays as hol_lib

from finanalytics_ai.infrastructure.database.repositories import backfill_repo as r


DOW = {0: "seg", 1: "ter", 2: "qua", 3: "qui", 4: "sex", 5: "sab", 6: "dom"}


def main() -> None:
    asyncio.run(r.load_b3_no_trading_days())

    print("# Calendário B3 — Dias não negociados (2017–2035)")
    print()
    print("> **Gerado automaticamente** por `scripts/generate_b3_calendar.py`. "
          "Não editar à mão.")
    print(">")
    print("> Fontes:")
    print(">  1. lib Python `holidays` (BR + subdiv=SP, categorias public+optional)")
    print(">  2. tabela `b3_no_trading_days` (auto-populada quando backfill retorna 0 ticks)")
    print()
    print("## Como B3 fecha")
    print()
    print("**Feriados nacionais fixos:** Confraternização (1/jan), Tiradentes (21/abr), "
          "Trabalhador (1/mai), Independência (7/set), Aparecida (12/out), "
          "Finados (2/nov), República (15/nov), Natal (25/dez).")
    print()
    print("**Lei 14.759/2023:** Consciência Negra (20/nov) virou feriado nacional "
          "a partir de 2024.")
    print()
    print("**Móveis (calculados via Páscoa):** Carnaval (segunda+terça), Quarta de "
          "Cinzas (meio-pregão tratado como holiday), Sexta-feira Santa, "
          "Corpus Christi.")
    print()
    print("**Vésperas:** Véspera de Natal (24/dez) e Véspera de Ano-Novo (31/dez) — "
          "meio-pregão B3, tratado como holiday completo (liquidez ruim para "
          "backtests).")
    print()
    print("**Atípicos** (`b3_no_trading_days`): dias sem pregão por motivos "
          "não-fixos — Aniversário Bovespa antecipado, decisões pontuais da B3, "
          "feriados estaduais SP que historicamente fecharam.")
    print()
    print("## Resumo por ano")
    print()
    print("| Ano | Feriados B3 | Atípicos | Dias úteis |")
    print("|---|---|---|---|")
    for y in range(2017, 2036):
        hols = r._b3_holidays_for_year(y)
        atypical = {d for d in r._B3_NO_TRADING_DAYS if d.year == y}
        tdays = r.trading_days_in_range(date(y, 1, 1), date(y, 12, 31))
        print(f"| {y} | {len(hols)} | {len(atypical)} | {len(tdays)} |")
    print()
    print("## Detalhe ano-a-ano")
    print()
    for y in range(2017, 2036):
        print(f"### {y}")
        print()
        print("| Data | DoW | Tipo | Motivo |")
        print("|---|---|---|---|")
        h = hol_lib.country_holidays(
            "BR", subdiv="SP", years=y, categories=("public", "optional"),
        )
        rows: list[tuple[date, str, str]] = []
        bset = r._b3_holidays_for_year(y)
        for d, name in sorted(h.items()):
            if d in bset:
                rows.append((d, "Feriado B3", name))
        for d in sorted(r._B3_NO_TRADING_DAYS):
            if d.year == y:
                rows.append((d, "Atípico", "B3 sem pregão"))
        rows.sort()
        for d, tipo, name in rows:
            marker = "🔴 " if tipo == "Atípico" else ""
            print(f"| {d.isoformat()} | {DOW[d.weekday()]} | {marker}{tipo} | {name} |")
        print()
    print("## Adicionar dia atípico manualmente")
    print()
    print("Quando descobrir um dia novo (B3 anuncia ou backfill detecta 0 ticks):")
    print()
    print("```sql")
    print("INSERT INTO b3_no_trading_days (target_date, notes)")
    print("VALUES ('YYYY-MM-DD', 'razão');")
    print("```")
    print()
    print("**Auto-populate:** `backfill_runner.run_one_item` chama "
          "`mark_b3_no_trading_day` quando `final='ok'` AND `ticks_returned=0`. "
          "Sem intervenção manual em 99% dos casos — basta tentar coletar pelo "
          "fluxo *Preencher agora* em /admin → Banco de Dados → Gaps.")
    print()
    print("## Re-gerar este calendário")
    print()
    print("```bash")
    print("docker exec finanalytics_api python scripts/generate_b3_calendar.py \\")
    print("  > docs/calendario_b3.md")
    print("```")


if __name__ == "__main__":
    main()

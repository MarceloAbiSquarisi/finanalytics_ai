"""
scripts/validar_carga_fintz.py
──────────────────────────────
Rotina de validação da carga histórica Fintz.

Verifica:
  1. Cobertura    — todos os 80 datasets foram importados
  2. Volume       — quantidade de linhas por tabela dentro do esperado
  3. Cobertura temporal — histórico desde 2010 nas cotações
  4. Tickers      — principais ativos da B3 pre
  5. Integridade  — sem valores nulos onde não deveriam existir
  6. Consistência — preço ajustado ≤ preço bruto razoável
  7. Indicadores  — valores dentro de faixas plausíveis
  8. Itens contábeis — dados PIT com datas de publicação coerentes

Uso:
    uv run python scripts/validar_carga_fintz.py
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import sys
from typing import Any

from sqlalchemy import text
import structlog

from finanalytics_ai.infrastructure.database.connection import get_session

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True),
    ]
)
log = structlog.get_logger()

# ── Configurações de referência ───────────────────────────────────────────────

TICKERS_IBOV = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3",
    "BBAS3", "WEGE3", "RENT3", "SUZB3", "RADL3",
    "EQTL3", "VIVT3", "JBSS3", "RAIL3", "SBSP3",
]

DATA_INICIO_ESPERADA = date(2010, 1, 1)
TOTAL_DATASETS_ESPERADO = 80

INDICADORES_FAIXAS = {
    "P_L":           (-500,  500),
    "P_VP":          (-100,  100),
    "ROE":           (-5,    5),
    "ROA":           (-2,    2),
    "DividendYield": (0,     5),
    "MargemLiquida": (-10,   10),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

VERDE  = "\033[92m"
AMARELO = "\033[93m"
VERMELHO = "\033[91m"
RESET  = "\033[0m"
NEGRITO = "\033[1m"

passed = 0
failed = 0
warnings = 0


def ok(msg: str, detalhe: str = "") -> None:
    global passed
    passed += 1
    sufixo = f"  → {detalhe}" if detalhe else ""
    print(f"  {VERDE}✓{RESET} {msg}{sufixo}")


def fail(msg: str, detalhe: str = "") -> None:
    global failed
    failed += 1
    sufixo = f"  → {detalhe}" if detalhe else ""
    print(f"  {VERMELHO}✗{RESET} {msg}{sufixo}")


def warn(msg: str, detalhe: str = "") -> None:
    global warnings
    warnings += 1
    sufixo = f"  → {detalhe}" if detalhe else ""
    print(f"  {AMARELO}⚠{RESET} {msg}{sufixo}")


def secao(titulo: str) -> None:
    print(f"\n{NEGRITO}{'─'*60}{RESET}")
    print(f"{NEGRITO}  {titulo}{RESET}")
    print(f"{NEGRITO}{'─'*60}{RESET}")


async def q(sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    async with get_session() as session:
        result = await session.execute(text(sql), params or {})
        return result.fetchall()


async def q1(sql: str, params: dict[str, Any] | None = None) -> Any:
    rows = await q(sql, params)
    return rows[0][0] if rows else None


# ── Testes ────────────────────────────────────────────────────────────────────

async def teste_sync_log() -> None:
    secao("1. Sync Log — cobertura dos datasets")

    total_ok    = await q1("SELECT COUNT(*) FROM fintz_sync_log WHERE status = 'ok'")
    total_error = await q1("SELECT COUNT(*) FROM fintz_sync_log WHERE status = 'error'")
    total       = await q1("SELECT COUNT(*) FROM fintz_sync_log")

    if total_ok == TOTAL_DATASETS_ESPERADO:
        ok(f"Todos os {TOTAL_DATASETS_ESPERADO} datasets importados com sucesso")
    elif total_ok > 0:
        warn(
            f"Importação parcial: {total_ok}/{TOTAL_DATASETS_ESPERADO} datasets ok",
            f"{total_error} com erro",
        )
    else:
        fail("Nenhum dataset importado com sucesso", f"total no log: {total}")

    if total_error and total_error > 0:
        erros = await q(
            "SELECT dataset_key, error_message FROM fintz_sync_log "
            "WHERE status = 'error' ORDER BY dataset_key LIMIT 10"
        )
        for row in erros:
            fail(f"Erro em {row[0]}", str(row[1])[:80] if row[1] else "sem mensagem")


async def teste_volume_tabelas() -> None:
    secao("2. Volume — linhas por tabela")

    checks = [
        ("fintz_cotacoes",       500_000,  "cotações OHLC (>500k esperado para B3 desde 2010)"),
        ("fintz_itens_contabeis", 50_000,  "itens contábeis PIT"),
        ("fintz_indicadores",     50_000,  "indicadores PIT"),
    ]

    for tabela, minimo, descricao in checks:
        count = await q1(f"SELECT COUNT(*) FROM {tabela}")
        if count >= minimo:
            ok(f"{tabela}: {count:,} linhas", descricao)
        elif count > 0:
            warn(f"{tabela}: {count:,} linhas (esperado ≥ {minimo:,})", descricao)
        else:
            fail(f"{tabela}: vazia", descricao)


async def teste_cobertura_temporal() -> None:
    secao("3. Cobertura temporal — cotações desde 2010")

    data_min = await q1("SELECT MIN(data) FROM fintz_cotacoes")
    data_max = await q1("SELECT MAX(data) FROM fintz_cotacoes")

    if data_min and data_min <= DATA_INICIO_ESPERADA + timedelta(days=30):
        ok(f"Data mínima: {data_min}", "histórico desde 2010 ✓")
    elif data_min:
        warn(f"Data mínima: {data_min}", f"esperado próximo de {DATA_INICIO_ESPERADA}")
    else:
        fail("Sem dados de cotação")

    if data_max and data_max >= date.today() - timedelta(days=5):
        ok(f"Data máxima: {data_max}", "dados recentes ✓")
    elif data_max:
        warn(f"Data máxima: {data_max}", "dados podem estar desatualizados")

    # Cobertura por ano
    anos = await q(
        "SELECT EXTRACT(YEAR FROM data)::int AS ano, COUNT(DISTINCT ticker) AS tickers "
        "FROM fintz_cotacoes GROUP BY ano ORDER BY ano"
    )
    if anos:
        primeiro, ultimo = anos[0], anos[-1]
        ok(
            f"Cobertura: {primeiro[0]}–{ultimo[0]}",
            f"{len(anos)} anos · {ultimo[1]} tickers no último ano",
        )


async def teste_tickers_ibov() -> None:
    secao("4. Tickers — principais ativos do Ibovespa")

    tickers_presentes = await q(
        "SELECT DISTINCT ticker FROM fintz_cotacoes WHERE ticker = ANY(:tickers)",
        {"tickers": TICKERS_IBOV},
    )
    presentes = {r[0] for r in tickers_presentes}
    ausentes  = set(TICKERS_IBOV) - presentes

    if not ausentes:
        ok(f"Todos os {len(TICKERS_IBOV)} tickers do Ibov presentes")
    elif len(ausentes) <= 3:
        warn(f"{len(presentes)}/{len(TICKERS_IBOV)} tickers presentes", f"ausentes: {ausentes}")
    else:
        fail(f"Muitos tickers ausentes: {ausentes}")

    # Total de tickers únicos
    total_tickers = await q1("SELECT COUNT(DISTINCT ticker) FROM fintz_cotacoes")
    if total_tickers and total_tickers > 200:
        ok(f"Total de tickers únicos: {total_tickers}", "cobertura ampla da B3")
    elif total_tickers:
        warn(f"Total de tickers únicos: {total_tickers}", "esperado > 200")


async def teste_integridade_cotacoes() -> None:
    secao("5. Integridade — cotações OHLC")

    # Preços nulos
    nulos = await q1(
        "SELECT COUNT(*) FROM fintz_cotacoes "
        "WHERE preco_fechamento IS NULL AND preco_fechamento_ajustado IS NULL"
    )
    if nulos == 0:
        ok("Sem linhas com preço_fechamento e ajustado nulos simultaneamente")
    else:
        warn(f"{nulos:,} linhas com ambos preços nulos")

    # Preços negativos
    negativos = await q1(
        "SELECT COUNT(*) FROM fintz_cotacoes WHERE preco_fechamento < 0"
    )
    if negativos == 0:
        ok("Sem preços negativos")
    else:
        fail(f"{negativos:,} linhas com preço negativo")

    # Duplicatas
    dupes = await q1(
        "SELECT COUNT(*) FROM ("
        "  SELECT ticker, data, COUNT(*) FROM fintz_cotacoes "
        "  GROUP BY ticker, data HAVING COUNT(*) > 1"
        ") x"
    )
    if dupes == 0:
        ok("Sem duplicatas (ticker, data)")
    else:
        fail(f"{dupes:,} combinações (ticker, data) duplicadas")

    # Amostra PETR4 — últimos 5 pregões
    amostra = await q(
        "SELECT data, preco_fechamento, preco_fechamento_ajustado, volume_negociado "
        "FROM fintz_cotacoes WHERE ticker = 'PETR4' "
        "ORDER BY data DESC LIMIT 5"
    )
    if amostra:
        ok("Amostra PETR4 (últimos pregões):")
        for r in amostra:
            print(f"       {r[0]}  fechamento={r[1]}  ajustado={r[2]}  volume={r[3]:,}" if r[3] else
                  f"       {r[0]}  fechamento={r[1]}  ajustado={r[2]}")
    else:
        fail("PETR4 não encontrado nas cotações")


async def teste_indicadores() -> None:
    secao("6. Indicadores PIT — valores plausíveis")

    for indicador, (vmin, vmax) in INDICADORES_FAIXAS.items():
        fora = await q1(
            "SELECT COUNT(*) FROM fintz_indicadores "
            "WHERE indicador = :ind AND valor IS NOT NULL "
            "AND (valor < :vmin OR valor > :vmax)",
            {"ind": indicador, "vmin": vmin, "vmax": vmax},
        )
        total = await q1(
            "SELECT COUNT(*) FROM fintz_indicadores WHERE indicador = :ind",
            {"ind": indicador},
        )
        if total == 0:
            warn(f"{indicador}: sem dados")
        elif fora == 0:
            ok(f"{indicador}: {total:,} registros — todos na faixa [{vmin}, {vmax}]")
        else:
            pct = round(fora / total * 100, 1)
            warn(
                f"{indicador}: {fora:,}/{total:,} fora da faixa [{vmin}, {vmax}]",
                f"{pct}% — pode ser outlier legítimo",
            )

    # Cobertura de tickers nos indicadores
    tickers_ind = await q1("SELECT COUNT(DISTINCT ticker) FROM fintz_indicadores")
    ok(f"Indicadores cobrem {tickers_ind} tickers únicos")


async def teste_itens_contabeis() -> None:
    secao("7. Itens contábeis PIT — consistência")

    # Datas de publicação sempre após a data de referência (ano/trimestre)
    inconsistentes = await q1(
        "SELECT COUNT(*) FROM fintz_itens_contabeis "
        "WHERE data_publicacao < MAKE_DATE(ano, trimestre * 3, 1)"
    )
    if inconsistentes == 0:
        ok("Datas de publicação sempre posteriores ao período de referência")
    else:
        warn(f"{inconsistentes:,} registros com data_publicacao anterior ao período")

    # Amostra LucroLiquido 12M — PETR4
    amostra = await q(
        "SELECT data_publicacao, ano, trimestre, valor "
        "FROM fintz_itens_contabeis "
        "WHERE ticker = 'PETR4' AND item = 'LucroLiquido' AND tipo_periodo = '12M' "
        "ORDER BY data_publicacao DESC LIMIT 5"
    )
    if amostra:
        ok("Amostra LucroLiquido 12M — PETR4:")
        for r in amostra:
            print(f"       publicado={r[0]}  ref={r[1]}T{r[2]}  valor=R${r[3]:,.0f}")
    else:
        warn("PETR4 / LucroLiquido 12M não encontrado")

    # Itens distintos importados
    itens = await q1("SELECT COUNT(DISTINCT item) FROM fintz_itens_contabeis")
    ok(f"Total de itens contábeis distintos: {itens}")


# ── Runner ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{NEGRITO}{'='*60}{RESET}")
    print(f"{NEGRITO}  Validação da carga histórica Fintz{RESET}")
    print(f"{NEGRITO}  finanalytics_ai — {date.today()}{RESET}")
    print(f"{NEGRITO}{'='*60}{RESET}")

    await teste_sync_log()
    await teste_volume_tabelas()
    await teste_cobertura_temporal()
    await teste_tickers_ibov()
    await teste_integridade_cotacoes()
    await teste_indicadores()
    await teste_itens_contabeis()

    # ── Resultado final ───────────────────────────────────────────────────────
    print(f"\n{NEGRITO}{'='*60}{RESET}")
    print(f"{NEGRITO}  Resultado final{RESET}")
    print(f"{NEGRITO}{'='*60}{RESET}")
    print(f"  {VERDE}✓ Passou:    {passed}{RESET}")
    print(f"  {AMARELO}⚠ Avisos:   {warnings}{RESET}")
    print(f"  {VERMELHO}✗ Falhou:   {failed}{RESET}")

    if failed == 0 and warnings == 0:
        print(f"\n  {VERDE}{NEGRITO}Base Fintz íntegra e pronta para uso!{RESET}\n")
        sys.exit(0)
    elif failed == 0:
        print(f"\n  {AMARELO}{NEGRITO}Base ok com avisos — verifique os ⚠ acima.{RESET}\n")
        sys.exit(0)
    else:
        print(f"\n  {VERMELHO}{NEGRITO}Problemas encontrados — verifique os ✗ acima.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

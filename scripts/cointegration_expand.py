"""
Screening expandido de pares cointegrados — research / R3 expansion.

Diferente de `cointegration_screen.py` (job de producao 06:30 BRT), este
script roda offline com universo maior + multi-lookback + p_threshold
variavel pra responder: "B3 coopera mais com janelas/setores diferentes?"

Universo: ~30 tickers em 10 setores. Apenas pares **intra-setor** (mais
provavel ter cointegracao real, vs cross-setor que e' coincidencia).

Lookbacks: 252 (1y), 504 (2y), 756 (3y) — diferentes janelas economicas.

P-threshold: 0.05 (rigoroso) e 0.10 (permissivo) pra ver candidatos
borderline que podem virar tradeable com filtro half-life curto.

Uso:
  python scripts/cointegration_expand.py --dry                # so imprime
  python scripts/cointegration_expand.py --persist            # UPSERT cointegrated
  python scripts/cointegration_expand.py --sector bancos --dry  # filtrar 1 setor

Output: tabela markdown agrupada por setor, lookback, indicando p_value
e half_life pra cada par. Recomendacoes ao final pra R3.2.B engine.
"""

from __future__ import annotations

import argparse
from itertools import combinations
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg2

from finanalytics_ai.domain.pairs.cointegration import engle_granger

# Universo intra-setor (B3, todos com Fintz cobertura 2010+)
SECTORS: dict[str, list[str]] = {
    "bancos": ["ITUB4", "BBDC4", "SANB11", "BBAS3", "ABCB4", "BPAC11"],
    "petroleo": ["PETR3", "PETR4", "PRIO3", "RECV3", "RAPT4"],
    "mineracao_metalurgia": ["VALE3", "CMIN3", "GGBR4", "GOAU4", "USIM5", "CSNA3"],
    "papel_celulose": ["SUZB3", "KLBN11"],
    "varejo": ["MGLU3", "AMER3", "LREN3", "RENT3", "PETZ3"],
    "alimentos": ["BRFS3", "JBSS3", "MRFG3"],
    "telecom": ["VIVT3", "TIMS3"],
    "saneamento": ["SBSP3", "SAPR11"],
    "educacao": ["COGN3", "YDUQ3"],
    "energia": ["ENGI11", "EQTL3", "CMIG4", "TAEE11"],
}

LOOKBACKS = [252, 504, 756]
P_THRESHOLDS = [0.05, 0.10]


def load_closes(dsn: str, ticker: str, lookback_days: int) -> list[float]:
    sql = """
        SELECT preco_fechamento_ajustado::float
        FROM fintz_cotacoes_ts
        WHERE ticker = %s AND preco_fechamento_ajustado IS NOT NULL
        ORDER BY time DESC LIMIT %s
    """
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, lookback_days))
        rows = cur.fetchall()
    return [float(r[0]) for r in reversed(rows)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sector", default=None, help=f"Filtrar 1 setor (opcoes: {list(SECTORS)})")
    ap.add_argument("--dry", action="store_true", help="Nao persiste (default: imprime + UPSERT)")
    ap.add_argument("--lookbacks", nargs="+", type=int, default=LOOKBACKS)
    ap.add_argument("--p-thresholds", nargs="+", type=float, default=P_THRESHOLDS)
    args = ap.parse_args()

    dsn = os.environ.get(
        "PROFIT_TIMESCALE_DSN",
        "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
    )

    sectors = {args.sector: SECTORS[args.sector]} if args.sector else SECTORS

    # Pre-load closes em todos os tickers no maior lookback (slice depois)
    max_lookback = max(args.lookbacks)
    all_tickers = sorted({t for tickers in sectors.values() for t in tickers})
    print(f"Carregando closes ({max_lookback}d) de {len(all_tickers)} tickers...")
    closes_full: dict[str, list[float]] = {}
    for t in all_tickers:
        try:
            closes_full[t] = load_closes(dsn, t, max_lookback)
        except Exception as exc:
            print(f"  warn: {t}: {exc}")
            closes_full[t] = []

    # Loop intra-setor
    cointegrated_05: list[tuple[str, str, str, int, float, float]] = []  # sector,a,b,lb,p,hl
    cointegrated_10_only: list[tuple[str, str, str, int, float, float]] = []
    failed_pairs: list[tuple[str, str, str]] = []

    print()
    for sector_name, tickers in sectors.items():
        pairs = list(combinations(sorted(tickers), 2))
        print(f"\n## {sector_name.upper()} — {len(tickers)} tickers, {len(pairs)} pares")
        for lb in args.lookbacks:
            print(f"\n### lookback={lb}d")
            print(
                f"{'PAIR':<18} {'P-ADF':>8} {'BETA':>8} {'HL':>8} {'COINT@.05':>10} {'COINT@.10':>10}"
            )
            for a, b in pairs:
                ca = closes_full.get(a, [])
                cb = closes_full.get(b, [])
                n = min(len(ca), len(cb), lb)
                if n < 30:
                    failed_pairs.append((sector_name, f"{a}-{b}", f"insufficient_data n={n}"))
                    continue
                try:
                    r = engle_granger(ca[-n:], cb[-n:], p_threshold=0.05)
                except Exception as exc:
                    failed_pairs.append((sector_name, f"{a}-{b}", str(exc)))
                    continue
                hl = f"{r.half_life:.1f}" if r.half_life is not None else "n/a"
                c05 = "YES" if r.p_value_adf < 0.05 else "no"
                c10 = "YES" if r.p_value_adf < 0.10 else "no"
                print(
                    f"{a}-{b:<10} {r.p_value_adf:>8.4f} {r.beta:>8.3f} {hl:>8} {c05:>10} {c10:>10}"
                )
                if r.p_value_adf < 0.05 and r.half_life is not None:
                    cointegrated_05.append((sector_name, a, b, lb, r.p_value_adf, r.half_life))
                elif r.p_value_adf < 0.10 and r.half_life is not None:
                    cointegrated_10_only.append((sector_name, a, b, lb, r.p_value_adf, r.half_life))

    # Sumario tradeable (p<0.05 + half_life entre 5-50d = janela operacional)
    print("\n\n" + "=" * 90)
    print("SUMARIO — pares com p<0.05 + half_life em [5, 50] dias (faixa operacional R3.2.B)")
    print("=" * 90)
    tradeable = [x for x in cointegrated_05 if 5 <= x[5] <= 50]
    if tradeable:
        for sector_name, a, b, lb, p, hl in sorted(tradeable, key=lambda x: x[4]):
            print(f"  [{sector_name:>22}] {a}-{b} lb={lb}d p={p:.4f} hl={hl:.1f}d")
    else:
        print("  (nenhum)")

    print("\n--- pares borderline (p<0.10, p>=0.05) — re-test em janela maior ---")
    if cointegrated_10_only:
        for sector_name, a, b, lb, p, hl in sorted(cointegrated_10_only, key=lambda x: x[4]):
            hl_ok = "OK" if 5 <= hl <= 50 else "FORA"
            print(f"  [{sector_name:>22}] {a}-{b} lb={lb}d p={p:.4f} hl={hl:.1f}d ({hl_ok})")
    else:
        print("  (nenhum)")

    if failed_pairs:
        print(f"\n--- {len(failed_pairs)} pares falharam ---")
        for sector, pair, err in failed_pairs[:20]:
            print(f"  {sector}: {pair}: {err[:80]}")

    print(
        f"\n\nTotal: {len(cointegrated_05)} cointegrados @0.05, "
        f"{len(cointegrated_10_only)} adicionais @0.10, "
        f"{len(tradeable)} tradeable (hl em [5,50])."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

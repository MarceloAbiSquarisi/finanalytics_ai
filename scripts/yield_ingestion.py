"""
yield_ingestion.py — ingestão diária ANBIMA (curva pré + IPCA + breakeven).

Usa pyield (wrapper oficial ANBIMA/BCB/B3). Publica em:
  - yield_curves (hypertable; market='br_pre'|'br_ipca', source='anbima')
  - breakeven_inflation (hypertable; market='br')

Schedule sugerido: diário 20:30 BRT após publicação ANBIMA (seg-sex).

Uso:
    # data específica
    python scripts/yield_ingestion.py --date 2026-04-16

    # última data disponível (decrementa dias úteis até ANBIMA responder)
    python scripts/yield_ingestion.py

    # range (backfill)
    python scripts/yield_ingestion.py --start 2025-01-02 --end 2026-04-16

    # dry-run
    python scripts/yield_ingestion.py --date 2026-04-16 --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def _poll_str_cols(df) -> list[str]:
    """Retorna nomes de colunas (Polars) ou list(df.columns) (pandas)."""
    try:
        return list(df.columns)
    except Exception:
        return []


def _polars_to_records(df, mapping: dict[str, str]) -> list[dict]:
    """Extrai records (dict) de Polars DataFrame usando mapping src_col → dst_key."""
    out: list[dict] = []
    cols = _poll_str_cols(df)
    for src in mapping:
        if src not in cols:
            return []
    n = df.height if hasattr(df, "height") else len(df)
    for i in range(n):
        rec = {}
        for src, dst in mapping.items():
            v = df[src][i] if hasattr(df, "height") else df[src].iloc[i]
            rec[dst] = v
        out.append(rec)
    return out


def fetch_anbima_curves(data_ref: str) -> dict:
    """
    Retorna {ltn, ntnb, breakeven} com listas de dicts prontas para INSERT.
    Estrutura:
        ltn/ntnb: {data_ref, vencimento, dias_uteis, taxa_aa, pu}
        breakeven: {data_ref, vencimento, dias_uteis, breakeven_aa}
    """
    import pyield as yd

    result = {"ltn": [], "ntnb": [], "breakeven": []}

    # ── LTN (pré-fixada) ───────────────────────────────────────────────────
    try:
        df_ltn = yd.ltn.dados(data_ref)
        records = _polars_to_records(
            df_ltn,
            {
                "data_referencia": "data_ref",
                "data_vencimento":  "vencimento",
                "dias_uteis":       "dias_uteis",
                "taxa_indicativa":  "taxa_aa",
                "pu":               "pu",
            },
        )
        result["ltn"] = records
    except Exception as e:
        print(f"  LTN {data_ref} falhou: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)

    # ── NTN-B (IPCA-linked) — taxa indicativa é taxa real ─────────────────
    try:
        df_ntnb = yd.ntnb.dados(data_ref)
        records = _polars_to_records(
            df_ntnb,
            {
                "data_referencia": "data_ref",
                "data_vencimento":  "vencimento",
                "dias_uteis":       "dias_uteis",
                "taxa_indicativa":  "taxa_real_aa",
                "pu":               "pu",
            },
        )
        result["ntnb"] = records
    except Exception as e:
        print(f"  NTN-B {data_ref} falhou: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)

    # ── Breakeven: interpolar LTN em vértices NTN-B, calcular (1+pré)/(1+real)−1 ──
    try:
        if result["ltn"] and result["ntnb"]:
            pre_du = [r["dias_uteis"] for r in result["ltn"] if r.get("taxa_aa") is not None]
            pre_tx = [float(r["taxa_aa"]) / 100.0 for r in result["ltn"] if r.get("taxa_aa") is not None]
            ntnb_pairs = [
                (r["dias_uteis"], float(r["taxa_real_aa"]) / 100.0)
                for r in result["ntnb"]
                if r.get("taxa_real_aa") is not None
            ]
            if pre_du and ntnb_pairs:
                interp_pre = yd.Interpolador(pre_du, pre_tx, metodo="flat_forward")
                for du, real in ntnb_pairs:
                    pre_aqui = float(interp_pre(du))
                    if 1 + real > 0:
                        be = (1 + pre_aqui) / (1 + real) - 1
                        result["breakeven"].append({
                            "data_ref":     data_ref,
                            "dias_uteis":   int(du),
                            "breakeven_aa": be * 100.0,  # em %
                        })
    except Exception as e:
        print(f"  breakeven {data_ref} falhou: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)

    return result


def upsert_curves(conn, data_ref: date, recs_ltn: list[dict], recs_ntnb: list[dict]) -> tuple[int, int]:
    if not recs_ltn and not recs_ntnb:
        return 0, 0
    sql = """
        INSERT INTO yield_curves
            (time, market, vertice_du, taxa_aa, taxa_real_aa, preco_pu, source, atualizado_em)
        VALUES %s
        ON CONFLICT (time, market, vertice_du, source) DO UPDATE SET
            taxa_aa       = EXCLUDED.taxa_aa,
            taxa_real_aa  = EXCLUDED.taxa_real_aa,
            preco_pu      = EXCLUDED.preco_pu,
            atualizado_em = now()
    """
    rows = []
    for r in recs_ltn:
        rows.append((data_ref, "br_pre", int(r["dias_uteis"]),
                     float(r["taxa_aa"]) if r["taxa_aa"] is not None else None,
                     None,
                     float(r["pu"]) if r["pu"] is not None else None,
                     "anbima",
                     datetime.utcnow()))
    for r in recs_ntnb:
        rows.append((data_ref, "br_ipca", int(r["dias_uteis"]),
                     None,
                     float(r["taxa_real_aa"]) if r["taxa_real_aa"] is not None else None,
                     float(r["pu"]) if r["pu"] is not None else None,
                     "anbima",
                     datetime.utcnow()))
    if not rows:
        return 0, 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=200)
    return len(recs_ltn), len(recs_ntnb)


def upsert_breakeven(conn, data_ref: date, recs: list[dict]) -> int:
    if not recs:
        return 0
    sql = """
        INSERT INTO breakeven_inflation
            (time, market, vertice_du, breakeven_aa, atualizado_em)
        VALUES %s
        ON CONFLICT (time, market, vertice_du) DO UPDATE SET
            breakeven_aa  = EXCLUDED.breakeven_aa,
            atualizado_em = now()
    """
    rows = [
        (data_ref, "br", int(r["dias_uteis"]),
         float(r["breakeven_aa"]) if r["breakeven_aa"] is not None else None,
         datetime.utcnow())
        for r in recs if r.get("dias_uteis") is not None
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=200)
    return len(rows)


def ingest_one_day(conn, data_ref: date, dry_run: bool = False) -> dict:
    d_str = data_ref.strftime("%Y-%m-%d")
    print(f"\n[{d_str}] fetching ANBIMA...")
    data = fetch_anbima_curves(d_str)
    n_ltn = len(data["ltn"])
    n_ntnb = len(data["ntnb"])
    n_be = len(data["breakeven"])
    print(f"  LTN={n_ltn}, NTN-B={n_ntnb}, breakeven={n_be}")

    if dry_run:
        return {"ltn": n_ltn, "ntnb": n_ntnb, "breakeven": n_be, "inserted": False}

    if n_ltn + n_ntnb == 0 and n_be == 0:
        return {"ltn": 0, "ntnb": 0, "breakeven": 0, "inserted": False}

    ins_ltn, ins_ntnb = upsert_curves(conn, data_ref, data["ltn"], data["ntnb"])
    ins_be = upsert_breakeven(conn, data_ref, data["breakeven"])
    conn.commit()
    print(f"  upserted: LTN={ins_ltn}, NTN-B={ins_ntnb}, breakeven={ins_be}")
    return {"ltn": ins_ltn, "ntnb": ins_ntnb, "breakeven": ins_be, "inserted": True}


def daterange(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # seg-sex
            days.append(d)
        d += timedelta(days=1)
    return days


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingestão ANBIMA (curva pré + IPCA + breakeven)")
    p.add_argument("--date", type=str, help="data específica YYYY-MM-DD")
    p.add_argument("--start", type=str, help="range start")
    p.add_argument("--end", type=str, help="range end (default hoje)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.start:
        d_start = date.fromisoformat(args.start)
        d_end = date.fromisoformat(args.end) if args.end else date.today()
        dates = daterange(d_start, d_end)
    elif args.date:
        dates = [date.fromisoformat(args.date)]
    else:
        # "Última data ANBIMA": tenta hoje-1 útil e desce até encontrar (até 7 dias)
        dates = []
        d = date.today()
        for _ in range(7):
            d -= timedelta(days=1)
            if d.weekday() < 5:
                dates.append(d)
                break
        if not dates:
            dates = [date.today() - timedelta(days=1)]

    print(f"yield_ingestion: {len(dates)} data(s), dry_run={args.dry_run}")
    conn = psycopg2.connect(DSN) if not args.dry_run else None
    try:
        total = {"ltn": 0, "ntnb": 0, "breakeven": 0}
        for d in dates:
            r = ingest_one_day(conn, d, dry_run=args.dry_run)
            for k in total:
                total[k] += r.get(k, 0)
        print(f"\nTotal: LTN={total['ltn']}, NTN-B={total['ntnb']}, breakeven={total['breakeven']}")
    finally:
        if conn:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

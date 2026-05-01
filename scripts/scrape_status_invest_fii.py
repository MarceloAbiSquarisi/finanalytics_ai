"""
scrape_status_invest_fii.py — N5 (27/abr/2026)

Scraper de fundamentals para FIIs via Status Invest.
Coleta DY (TTM), P/VP, dividendos 12m e valor de mercado.

Persiste em `fii_fundamentals` (snapshot diário, idempotente por
(ticker, snapshot_date) — re-rodar no mesmo dia faz UPSERT).

Uso:
    python scripts/scrape_status_invest_fii.py                    # IFIX top 30
    python scripts/scrape_status_invest_fii.py --tickers KNRI11,MXRF11
    python scripts/scrape_status_invest_fii.py --dry-run

Rate limit: 1 req/s (cortesia ao Status Invest). 30 FIIs ~30s.
"""
from __future__ import annotations

import argparse
from datetime import date
import os
import re
import sys
import time

import httpx
import psycopg2

# Aceita TIMESCALE_URL (env do scheduler container, aponta pro hostname
# 'timescale:5432' interno do docker network) ou PROFIT_TIMESCALE_DSN
# (env legado do host Windows, aponta pro localhost:5433).
DSN = (
    os.environ.get("TIMESCALE_URL")
    or os.environ.get("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)

# Lista herdada do backfill_yahoo_fii.py para alinhamento. MALL11 e BCFF11
# estavam delistados no Yahoo, mas Status Invest pode ter — nao filtra aqui.
IFIX_TOP_30 = [
    "HGLG11", "BTLG11", "VILG11", "XPLG11", "BRCO11",
    "XPML11", "VISC11", "HGBS11", "MALL11", "VRTA11",
    "KNRI11", "HGRE11", "PVBI11", "RCRB11", "BRCR11",
    "HGRU11", "RECT11", "RBRR11",
    "MXRF11", "BCFF11", "RBRF11", "HCTR11", "VGIR11",
    "VGIP11", "RBRY11", "KNCR11", "KNHY11", "HFOF11",
]

USER_AGENT = "Mozilla/5.0 (compatible; FinAnalytics/1.0)"
URL_TEMPLATE = "https://statusinvest.com.br/fundos-imobiliarios/{ticker}"

# Padroes de extracao. Status Invest usa estrutura repetida:
#   <h3 class="title...">Indicador<...>...</h3>
#   <strong class="value">VALUE</strong>
# Para DY ha um span template intermediario (tooltip), entao tolerancia
# de DOTALL + .{0,N}? lazy ate o primeiro <strong class="value">.
_RE_DY = re.compile(
    r"Dividend Yield.{0,3000}?<strong\s+class=\"value\">\s*([0-9.,]+)\s*</strong>",
    re.DOTALL,
)
_RE_PVP = re.compile(
    r">P/VP</h3>\s*<strong\s+class=\"value\">\s*([0-9.,]+)\s*</strong>",
    re.DOTALL,
)
_RE_DIV12M = re.compile(
    r"Soma total de proventos distribu.+?nos.+?12 meses.+?<span\s+class=\"sub-value\">\s*R\$\s*([0-9.,]+)\s*</span>",
    re.DOTALL,
)
_RE_VALOR_MERCADO = re.compile(
    r"Valor de mercado</span>\s*<span\s+class=\"sub-value\">\s*R\$\s*([0-9.,]+)\s*</span>",
    re.DOTALL,
)


def _to_float(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_one(ticker: str, *, client: httpx.Client) -> dict | None:
    url = URL_TEMPLATE.format(ticker=ticker.lower())
    try:
        resp = client.get(url, timeout=20)
    except httpx.HTTPError as exc:
        print(f"  [{ticker}] http error: {exc}")
        return None

    if resp.status_code != 200:
        print(f"  [{ticker}] http {resp.status_code}")
        return None

    html = resp.text
    out = {
        "ticker": ticker,
        "dy_ttm": _to_float(_RE_DY.search(html).group(1)) if _RE_DY.search(html) else None,
        "p_vp": _to_float(_RE_PVP.search(html).group(1)) if _RE_PVP.search(html) else None,
        "div_12m": _to_float(_RE_DIV12M.search(html).group(1)) if _RE_DIV12M.search(html) else None,
        "valor_mercado": (
            _to_float(_RE_VALOR_MERCADO.search(html).group(1))
            if _RE_VALOR_MERCADO.search(html)
            else None
        ),
    }

    # heuristica: se DY+PVP None significa que pagina mudou de estrutura
    # ou ticker nao existe (FII delistado).
    if out["dy_ttm"] is None and out["p_vp"] is None:
        print(f"  [{ticker}] sem DY+PVP - pagina mudou ou ticker nao existe")
        return None

    return out


def upsert(conn, row: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO fii_fundamentals
            (ticker, snapshot_date, dy_ttm, p_vp, div_12m, valor_mercado, source, scraped_at)
        VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, 'status_invest', NOW())
        ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
            dy_ttm        = EXCLUDED.dy_ttm,
            p_vp          = EXCLUDED.p_vp,
            div_12m       = EXCLUDED.div_12m,
            valor_mercado = EXCLUDED.valor_mercado,
            scraped_at    = NOW()
        """,
        (
            row["ticker"],
            row["dy_ttm"],
            row["p_vp"],
            row["div_12m"],
            row["valor_mercado"],
        ),
    )
    conn.commit()
    cur.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="CSV (default = IFIX_TOP_30)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Segundos entre requests (default 1.0)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else IFIX_TOP_30
    )

    print(f"[scrape_status_invest_fii] {len(tickers)} FIIs · dry_run={args.dry_run}")
    print(f"[scrape_status_invest_fii] snapshot_date={date.today().isoformat()}")

    conn = None if args.dry_run else psycopg2.connect(DSN)

    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20) as client:
            ok = 0
            fail = 0
            for i, t in enumerate(tickers, 1):
                t0 = time.time()
                row = scrape_one(t, client=client)
                if row is None:
                    fail += 1
                else:
                    ok += 1
                    print(
                        f"  [{i:2d}/{len(tickers)}] {t}: "
                        f"DY={row['dy_ttm']!r}% PVP={row['p_vp']!r} "
                        f"div12m={row['div_12m']!r} mc={row['valor_mercado']!r} "
                        f"({time.time()-t0:.2f}s)"
                    )
                    if not args.dry_run:
                        upsert(conn, row)
                if i < len(tickers):
                    time.sleep(args.rate_limit)
    finally:
        if conn is not None:
            conn.close()

    print(f"[scrape_status_invest_fii] done · ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

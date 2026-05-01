"""copom_label_selic.py — gera labels fracas (hawkish/neutral/dovish) a partir
do SELIC_CHANGE implicado pela proxima reuniao.

Heuristica:
  - hawkish se selic_change > +0.125  (alta >= 12.5 bps)
  - dovish  se selic_change < -0.125
  - neutral caso contrario

O label e da DECISAO que o comunicado/ata discute. Como selic_change em
copom_documents e diff vs reuniao anterior (a meta VIGENTE pos reuniao),
isso e exatamente o que queremos.

Uso:
    python scripts/copom_label_selic.py --export data/copom/labeled.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

THRESHOLD = 0.125  # bps (12.5 bps)


def label_from_change(change: float | None) -> str | None:
    if change is None:
        return None
    if change >  THRESHOLD:
        return "hawkish"
    if change < -THRESHOLD:
        return "dovish"
    return "neutral"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="data/copom/labeled.csv")
    ap.add_argument("--only-with-text", action="store_true", default=True)
    args = ap.parse_args()

    out_path = Path(args.export)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(DSN)
    sql = """
      SELECT doc_date, doc_type, title, selic_change,
             COALESCE(text_pt, text_en) AS text
        FROM copom_documents
       WHERE selic_change IS NOT NULL
    """
    if args.only_with_text:
        sql += " AND (text_pt IS NOT NULL OR text_en IS NOT NULL)"
    sql += " ORDER BY doc_date"

    counts = {"hawkish": 0, "neutral": 0, "dovish": 0, "skip": 0}
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_date", "doc_type", "label", "selic_change", "title", "text"])
        for r in rows:
            dd, dt, ttl, chg, txt = r
            lbl = label_from_change(float(chg))
            if lbl is None:
                counts["skip"] += 1; continue
            counts[lbl] += 1
            w.writerow([dd, dt, lbl, chg, ttl, (txt or "")[:20000]])

    print(f"total rows: {sum(counts.values())}")
    print(f"  hawkish: {counts['hawkish']}")
    print(f"  neutral: {counts['neutral']}")
    print(f"  dovish:  {counts['dovish']}")
    print(f"  skip:    {counts['skip']}")
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

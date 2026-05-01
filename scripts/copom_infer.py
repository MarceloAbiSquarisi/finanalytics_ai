"""copom_infer.py — aplica modelo fine-tuned aos docs em copom_documents
e persiste scores em copom_sentiment.

Uso:
    python scripts/copom_infer.py --model-dir models/copom_bert_YYYYMMDD_HHMMSS
    python scripts/copom_infer.py --model-dir ... --only-date 2026-01-28
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def load_docs(conn, only_date: str | None) -> list[dict]:
    sql = ("SELECT doc_date, doc_type, COALESCE(text_pt, text_en) AS text "
           "FROM copom_documents WHERE (text_pt IS NOT NULL OR text_en IS NOT NULL)")
    params: tuple = ()
    if only_date:
        sql += " AND doc_date = %s"
        params = (only_date,)
    sql += " ORDER BY doc_date"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [{"doc_date": r[0], "doc_type": r[1], "text": r[2]} for r in rows]


def upsert_sentiment(conn, doc: dict, model_version: str,
                     probs: list[float], labels: list[str]) -> None:
    p = {l: float(probs[i]) for i, l in enumerate(labels)}
    hawkish_score = p.get("hawkish", 0.0) - p.get("dovish", 0.0)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO copom_sentiment
                (doc_date, doc_type, model_version,
                 dovish_prob, neutral_prob, hawkish_prob, hawkish_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (doc_date, doc_type, model_version) DO UPDATE SET
                dovish_prob=EXCLUDED.dovish_prob,
                neutral_prob=EXCLUDED.neutral_prob,
                hawkish_prob=EXCLUDED.hawkish_prob,
                hawkish_score=EXCLUDED.hawkish_score,
                inferred_at=now()
            """,
            (doc["doc_date"], doc["doc_type"], model_version,
             p.get("dovish", 0.0), p.get("neutral", 0.0), p.get("hawkish", 0.0),
             hawkish_score),
        )


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--only-date")
    ap.add_argument("--max-len", type=int, default=512)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    model_dir = Path(args.model_dir)
    if not model_dir.exists():
        print(f"model dir nao existe: {model_dir}", file=sys.stderr); return 2

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    meta_path = model_dir / "copom_meta.json"
    if not meta_path.exists():
        print("copom_meta.json ausente no model_dir", file=sys.stderr); return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    labels: list[str] = meta["labels"]
    model_version = f"{model_dir.name}"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device={device}  model={model_dir.name}")

    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    model = model.to(device); model.eval()

    conn = psycopg2.connect(DSN)
    try:
        docs = load_docs(conn, args.only_date)
        print(f"docs a inferir: {len(docs)}")

        with torch.no_grad():
            for i, d in enumerate(docs, 1):
                inp = tok(d["text"], return_tensors="pt", truncation=True,
                          max_length=args.max_len).to(device)
                logits = model(**inp).logits[0]
                probs = torch.softmax(logits, dim=-1).cpu().numpy().tolist()
                upsert_sentiment(conn, d, model_version, probs, labels)
                if i % 20 == 0 or i == len(docs):
                    print(f"  [{i}/{len(docs)}] {d['doc_date']} {d['doc_type']}")
                    conn.commit()
            conn.commit()
    finally:
        conn.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

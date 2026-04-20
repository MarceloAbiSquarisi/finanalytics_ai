"""copom_fetch.py — baixa atas e comunicados COPOM do BCB.

Fontes:
  - API BCB: /api/servico/sitebcb/copomminutes/ultimas (atas EN)
  - API BCB: /api/servico/sitebcb/copomcomunicados/ultimas (comunicados PT)
  - SGS 432: SELIC meta (para decisao do comite; join por data)
  - PDF/URL: fetch de cada Url listado e extracao do texto via pypdf

Armazena em copom_documents (ON CONFLICT UPDATE). Se a fonte primaria (API)
falhar, tenta cache local em data/copom_index.json de execucao anterior.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2
import requests


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

BCB_BASE = "https://www.bcb.gov.br"
MINUTES_EP      = f"{BCB_BASE}/api/servico/sitebcb/copomminutes/ultimas"
COMUNICADOS_EP  = f"{BCB_BASE}/api/servico/sitebcb/copomcomunicados/ultimas"
SGS_SELIC       = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json"

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "copom"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (FinAnalyticsAI/1.0)"}


def http_json_retry(url: str, params: dict | None = None, attempts: int = 5) -> Any | None:
    """GET JSON com retry exponencial (BCB costuma dar 500 transientes)."""
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, timeout=20, headers=HEADERS)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("application/json"):
                return r.json()
        except Exception as exc:
            print(f"  http {url} attempt {i+1}: {type(exc).__name__}: {str(exc)[:80]}")
        backoff = 2 ** i + (i * 2)
        print(f"  retry in {backoff}s (status {r.status_code if 'r' in dir() else '?'})")
        time.sleep(backoff)
    return None


def fetch_index(endpoint: str, max_items: int, cache_name: str) -> list[dict]:
    """Busca lista com quantidade decrescente; guarda cache em JSON."""
    cache_path = CACHE_DIR / cache_name
    for q in (max_items, 200, 100, 50, 20, 10, 5):
        print(f"fetch {endpoint} q={q} ...")
        data = http_json_retry(endpoint, params={"quantidade": q})
        if data is not None:
            itens = data.get("conteudo", data) if isinstance(data, dict) else data
            if isinstance(itens, list) and itens:
                cache_path.write_text(json.dumps(itens, indent=2, default=str), encoding="utf-8")
                print(f"  ok: {len(itens)} itens, cache gravado em {cache_path.name}")
                return itens
    if cache_path.exists():
        print(f"  WARN: API falhou; usando cache local {cache_path}")
        return json.loads(cache_path.read_text(encoding="utf-8"))
    print(f"  FAIL: sem dados de {endpoint} e sem cache")
    return []


def fetch_pdf_text(url: str) -> str | None:
    """Baixa PDF/HTML e extrai texto."""
    try:
        r = requests.get(url, timeout=30, headers=HEADERS)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "").lower()
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(r.content))
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
        # HTML fallback
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for s in soup(["script", "style", "nav", "footer", "header"]):
            s.decompose()
        return " ".join(soup.get_text(separator=" ").split())
    except Exception as exc:
        print(f"  pdf fetch fail {url}: {type(exc).__name__}")
        return None


def upsert_doc(conn, doc: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO copom_documents
                (doc_date, doc_type, title, url, text_pt, text_en,
                 selic_target, selic_change)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (doc_date, doc_type) DO UPDATE SET
                title=EXCLUDED.title,
                url=COALESCE(EXCLUDED.url, copom_documents.url),
                text_pt=COALESCE(EXCLUDED.text_pt, copom_documents.text_pt),
                text_en=COALESCE(EXCLUDED.text_en, copom_documents.text_en),
                selic_target=COALESCE(EXCLUDED.selic_target, copom_documents.selic_target),
                selic_change=COALESCE(EXCLUDED.selic_change, copom_documents.selic_change),
                fetched_at=now()
            """,
            (
                doc["doc_date"], doc["doc_type"], doc.get("title"),
                doc.get("url"), doc.get("text_pt"), doc.get("text_en"),
                doc.get("selic_target"), doc.get("selic_change"),
            ),
        )


def join_selic(docs: list[dict], selic_series: list[dict]) -> None:
    """Anexa selic_target (meta SELIC na data da reuniao) e selic_change
    (diff vs reuniao anterior) a cada doc in-place."""
    # SGS 432 retorna 'data' dd/mm/yyyy e 'valor' str decimal
    by_date: dict[date, float] = {}
    for s in selic_series:
        try:
            d = datetime.strptime(s["data"], "%d/%m/%Y").date()
            by_date[d] = float(s["valor"])
        except Exception:
            continue
    sorted_dates = sorted(by_date)
    docs_sorted = sorted(docs, key=lambda x: x["doc_date"])
    prev_target: float | None = None
    for d in docs_sorted:
        dd = d["doc_date"]
        # Busca a entrada SGS com data <= doc_date mais proxima
        nearest = None
        for sd in sorted_dates:
            if sd <= dd:
                nearest = sd
            else:
                break
        if nearest is None:
            continue
        target = by_date[nearest]
        d["selic_target"] = target
        d["selic_change"] = (target - prev_target) if prev_target is not None else None
        prev_target = target


def parse_api_item(item: dict, doc_type: str, lang: str) -> dict | None:
    """Normaliza item da API para dict homogeneo."""
    try:
        dref = item["DataReferencia"]
        doc_date = datetime.fromisoformat(dref.replace("Z", "+00:00")).date()
    except Exception:
        return None
    url = item.get("Url") or item.get("LinkPagina") or ""
    if url and not url.startswith("http"):
        url = BCB_BASE + url
    return {
        "doc_date": doc_date,
        "doc_type": doc_type,
        "title":    item.get("Titulo"),
        "url":      url,
        "text_pt":  None,
        "text_en":  None,
        "_lang":    lang,  # para saber onde gravar o texto extraido
    }


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=500)
    ap.add_argument("--fetch-texts", action="store_true",
                    help="Baixa PDFs/HTMLs (mais lento; idempotente)")
    ap.add_argument("--since", default="2010-01-01",
                    help="So processa docs com data >= esta")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    since = date.fromisoformat(args.since)

    # 1. Indices
    minutes   = fetch_index(MINUTES_EP,     args.max_items, "minutes_index.json")
    comunic   = fetch_index(COMUNICADOS_EP, args.max_items, "comunicados_index.json")
    print(f"\nbrutos: minutes={len(minutes)}, comunicados={len(comunic)}")

    docs: list[dict] = []
    for i in minutes:
        p = parse_api_item(i, "minute", "en")
        if p and p["doc_date"] >= since:
            docs.append(p)
    for i in comunic:
        p = parse_api_item(i, "communique", "pt")
        if p and p["doc_date"] >= since:
            docs.append(p)
    print(f"validos apos since={since}: {len(docs)}")

    # 2. SELIC meta (SGS 432)
    print("\nfetch SELIC 432 ...")
    selic = http_json_retry(SGS_SELIC)
    if selic:
        print(f"  selic pontos: {len(selic)}")
        join_selic(docs, selic)
    else:
        print("  WARN: SELIC indisponivel, selic_target/change ficam NULL")

    # 3. Textos (opcional)
    if args.fetch_texts:
        print("\nfetch textos (pode levar minutos)...")
        for i, d in enumerate(docs, 1):
            if not d.get("url") or not d["url"].endswith(".pdf"):
                continue
            print(f"  [{i}/{len(docs)}] {d['doc_date']} {d['doc_type']} {d['url'][-60:]}")
            txt = fetch_pdf_text(d["url"])
            if txt:
                key = "text_en" if d["_lang"] == "en" else "text_pt"
                d[key] = txt
            time.sleep(1)  # polido

    # 4. Upsert
    conn = psycopg2.connect(DSN)
    n = 0
    try:
        for d in docs:
            d.pop("_lang", None)
            upsert_doc(conn, d)
            n += 1
        conn.commit()
    finally:
        conn.close()
    print(f"\nupserted: {n}")

    # 5. Relatorio
    with psycopg2.connect(DSN) as cn, cn.cursor() as cur:
        cur.execute("SELECT doc_type, count(*) FROM copom_documents GROUP BY doc_type")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]}")
        cur.execute("SELECT count(*) FROM copom_documents WHERE text_pt IS NOT NULL OR text_en IS NOT NULL")
        print(f"  com texto: {cur.fetchone()[0]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

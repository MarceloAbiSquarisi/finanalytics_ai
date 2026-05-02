"""
Survivorship bias step 0 — coleta inicial via CVM cadastro de companhias abertas.

A CVM publica `cad_cia_aberta.csv` com TODAS as companhias que JA tiveram
registro aberto na CVM. A coluna SIT (situacao) marca CANCELADA pra empresas
que saíram da bolsa.

URL canonica: http://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv

Uso:
  python scripts/survivorship_collect_cvm.py --dry         # imprime + nao persiste
  python scripts/survivorship_collect_cvm.py --persist     # UPSERT b3_delisted_tickers
  python scripts/survivorship_collect_cvm.py --persist --source-csv ./cvm_cad.csv

Limitacao conhecida (step 1 pendente):
  CVM identifica companhias por CNPJ, NAO por ticker B3. Esta tabela popula
  cnpj + razao_social + delisting_date corretamente, mas o campo `ticker`
  fica NULL ate' termos a bridge `CNPJ -> tickers ativos historicamente`.

  Bridge possivel via:
    a) B3 RWS (Service Center) — XML com cadastro de instrumentos vinculados
       por CNPJ. Requer scraping autenticado.
    b) Wikipedia / fundamentus — tabela manual.
    c) Lista IBOV historica (mensal, mas so' cobre o que entrou no IBOV).

  Step 1 ideal: popular `b3_delisted_tickers.ticker` por enriquecimento
  manual a partir do CSV gerado aqui.

Output (dry):
  CNPJ                  RAZAO_SOCIAL                  SIT          DATA_REG_CANC
  XX.XXX.XXX/0001-XX    EMPRESA EXEMPLO S.A.          CANCELADA    2023-04-15
  ...

Saida:
  - dry: tabela stdout
  - persist: UPSERT em b3_delisted_tickers (com ticker=NULL ate' step 1)
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import io
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg2

CVM_CSV_URL = "http://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"


def fetch_cvm_csv(url: str = CVM_CSV_URL) -> str:
    """Baixa o CSV da CVM. Latin-1 (encoding canonico do governo brasileiro)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "finanalytics-ai/survivorship-collect"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("latin-1")


def parse_cvm_canceladas(csv_text: str) -> list[dict]:
    """
    Parse CSV CVM e' filtra linhas com SIT='CANCELADA'.

    Colunas relevantes do CVM (header comum):
      CNPJ_CIA, DENOM_SOCIAL, SIT, DT_REG, DT_CANCEL_REG, ...

    Returns: list de dicts c/ keys: cnpj, razao_social, sit, dt_cancel.
    """
    canceladas: list[dict] = []
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")
    for row in reader:
        sit = (row.get("SIT") or "").strip().upper()
        if sit != "CANCELADA":
            continue
        cnpj = (row.get("CNPJ_CIA") or "").strip()
        razao = (row.get("DENOM_SOCIAL") or "").strip()
        dt_cancel_str = (row.get("DT_CANCEL_REG") or "").strip()
        dt_cancel: date | None = None
        if dt_cancel_str:
            try:
                dt_cancel = datetime.strptime(dt_cancel_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        canceladas.append(
            {
                "cnpj": cnpj,
                "razao_social": razao,
                "sit": sit,
                "dt_cancel": dt_cancel,
            }
        )
    return canceladas


def upsert_delisted(dsn: str, rows: list[dict]) -> int:
    """
    UPSERT em b3_delisted_tickers. ticker fica NULL no step 0.

    Como ticker e' PK, usamos um placeholder unique-per-cnpj enquanto nao
    temos a bridge. Convenção: ticker = "UNK_<CNPJ_14digitos>" (18 chars
    total) — sera substituido no step 1 quando bridge estiver pronta.

    Usar 14 digitos completos (raiz+filial+verificador) garante unicidade
    entre filiais que compartilham raiz CNPJ.
    """
    sql = """
        INSERT INTO b3_delisted_tickers
            (ticker, cnpj, razao_social, delisting_date, delisting_reason,
             source, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker) DO UPDATE SET
            cnpj = EXCLUDED.cnpj,
            razao_social = EXCLUDED.razao_social,
            delisting_date = EXCLUDED.delisting_date,
            delisting_reason = EXCLUDED.delisting_reason,
            source = EXCLUDED.source,
            notes = EXCLUDED.notes,
            updated_at = NOW()
    """
    inserted = 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for r in rows:
            cnpj = r["cnpj"]
            # ticker placeholder ate' step 1 — usa 14 digitos completos do
            # cnpj (raiz+filial+verificador) p/ garantir unicidade
            cnpj_clean = cnpj.replace("/", "").replace("-", "").replace(".", "")
            ticker_placeholder = f"UNK_{cnpj_clean[:14]}"
            cur.execute(
                sql,
                (
                    ticker_placeholder,
                    cnpj,
                    r["razao_social"][:200],
                    r["dt_cancel"],
                    "CANCELAMENTO_REGISTRO",
                    "CVM",
                    "step 0 — placeholder ticker; popular ticker real no step 1",
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="Nao persiste — so' imprime")
    ap.add_argument("--persist", action="store_true", help="UPSERT em b3_delisted_tickers")
    ap.add_argument("--source-csv", default=None, help="Path local de CSV (em vez de URL)")
    ap.add_argument("--limit", type=int, default=None, help="Limitar p/ debug")
    args = ap.parse_args()

    if not args.dry and not args.persist:
        ap.error("Use --dry ou --persist")

    if args.source_csv:
        print(f"Lendo {args.source_csv}...")
        with open(args.source_csv, encoding="latin-1") as f:
            csv_text = f.read()
    else:
        print(f"Baixando {CVM_CSV_URL}...")
        try:
            csv_text = fetch_cvm_csv()
        except Exception as exc:
            print(f"ERRO ao baixar CVM CSV: {exc}")
            print("Tente baixar manualmente e usar --source-csv <path>")
            return 1
    print(f"  {len(csv_text)} bytes")

    canceladas = parse_cvm_canceladas(csv_text)
    print(f"\nEncontradas {len(canceladas)} companhias com SIT=CANCELADA\n")

    if args.limit:
        canceladas = canceladas[: args.limit]

    print(f"{'CNPJ':<22} {'RAZAO_SOCIAL':<60} {'DT_CANCEL':<12}")
    print("-" * 100)
    no_dt = 0
    for r in canceladas[:50]:
        dt = r["dt_cancel"].isoformat() if r["dt_cancel"] else "(sem data)"
        if not r["dt_cancel"]:
            no_dt += 1
        print(f"{r['cnpj']:<22} {r['razao_social'][:58]:<60} {dt:<12}")
    if len(canceladas) > 50:
        print(f"... ({len(canceladas) - 50} restantes ocultos)")

    print(f"\nResumo: {len(canceladas)} canceladas, {no_dt}/50 amostra sem dt_cancel")

    if args.persist:
        dsn = os.environ.get(
            "DATABASE_URL_SYNC",
            os.environ.get(
                "DATABASE_URL",
                "postgresql://finanalytics:postgres@localhost:5432/finanalytics",
            ),
        )
        if "asyncpg" in dsn:
            dsn = dsn.replace("+asyncpg", "")
        print(f"\nPersistindo em {dsn.split('@')[-1]}...")
        try:
            count = upsert_delisted(dsn, canceladas)
            print(f"OK — {count} rows UPSERT em b3_delisted_tickers")
        except psycopg2.errors.UndefinedTable:
            print("ERRO: tabela b3_delisted_tickers nao existe.")
            print("Rodar: alembic upgrade 0025_b3_delisted_tickers")
            return 2
        except Exception as exc:
            print(f"ERRO no UPSERT: {exc}")
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())

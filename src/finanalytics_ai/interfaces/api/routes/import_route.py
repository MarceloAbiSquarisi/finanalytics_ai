"""
finanalytics_ai.interfaces.api.routes.import_route
---------------------------------------------------
Rotas de importacao de extratos e notas de corretagem.

POST /api/v1/import/nota-xp        -- Nota de negociacao B3 (PDF)
POST /api/v1/import/posicao-xp     -- Posicao Detalhada XP (XLSX)
POST /api/v1/import/extrato-btg-br -- Extrato mensal BTG BR (PDF)
POST /api/v1/import/extrato-btg-us -- Account Statement BTG US (PDF)
POST /api/v1/import/extrato-mynt   -- Extrato Mynt cripto (PDF)
POST /api/v1/import/csv            -- CSV generico de posicoes
POST /api/v1/import/auto           -- Detecta o formato automaticamente

GET  /api/v1/import/history        -- Historico de importacoes
GET  /api/v1/import/positions      -- Todas as posicoes importadas
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
import io
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
import pdfplumber
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/import", tags=["Import"])

# Historico em memoria (substituir por DB quando migrations rodar)
_import_history: list[dict] = []
_positions: list[dict] = []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _dec(s: Any) -> Decimal:
    if not s:
        return Decimal(0)
    s = str(s).strip().replace("R$", "").replace("$", "").replace(" ", "")
    s = re.sub(r"[^\d,.]", "", s)
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s) if s else Decimal(0)
    except Exception:
        return Decimal(0)


def _date_br(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _pdf_lines_by_y(content: bytes, page_idx: int = 0) -> dict[int, list[tuple[float, str]]]:
    """Agrupa palavras do PDF por linha Y."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if page_idx >= len(pdf.pages):
            return {}
        words = pdf.pages[page_idx].extract_words()
    lines: dict[int, list] = defaultdict(list)
    for w in words:
        y = round(w["top"] / 4) * 4
        lines[y].append((w["x0"], w["text"]))
    return dict(lines)


def _pdf_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _pdf_tables(content: bytes, page_idx: int) -> list[list[list]]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if page_idx >= len(pdf.pages):
            return []
        return pdf.pages[page_idx].extract_tables() or []


# ── Parser: Nota de Negociacao B3 ───────────────────────────────────────────


def _parse_nota_xp(content: bytes, filename: str) -> dict[str, Any]:
    text = _pdf_text(content)
    lines_y = _pdf_lines_by_y(content, 0)

    # Metadados
    numero_nota = ""
    data_pregao = date.today()
    cpf = ""
    cnpj = ""

    m = re.search(
        r"Nr\. nota\s+Folha\s+Data preg[aã]o\s*\n([\d.]+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})", text, re.M
    )
    if m:
        numero_nota = m.group(1).replace(".", "")
        data_pregao = _date_br(m.group(3)) or date.today()

    m_cpf = re.search(r"(\d{3}\.\d{3}\.\d{3}-\d{2})", text)
    if m_cpf:
        cpf = m_cpf.group(1)

    m_cnpj = re.search(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", text)
    if m_cnpj:
        cnpj = m_cnpj.group(1)

    # Items via tabelas pdfplumber
    items = []
    seen = set()
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                for row in tbl:
                    if row and len(row) >= 7 and row[0] in ("C", "V"):
                        cv = row[0]
                        merc = str(row[1] or "").strip()
                        venc = _date_br(str(row[2] or ""))
                        qtd = _dec(str(row[3] or "").replace(".", ""))
                        preco = _dec(str(row[4] or ""))
                        tipo_neg = str(row[5] or "NORMAL").strip()
                        valor = _dec(str(row[6] or ""))
                        dc = str(row[7] or "D").strip() if len(row) > 7 else "D"
                        taxa_op = _dec(str(row[8] or "")) if len(row) > 8 else Decimal(0)
                        # Chave normalizada: (cv, mercadoria, qtd_arredondada, preco_arredondado)
                        key = (cv, merc, round(float(qtd), 4), round(float(preco), 2))
                        if key not in seen:
                            seen.add(key)
                            items.append(
                                {
                                    "cv": cv,
                                    "mercadoria": merc,
                                    "vencimento": str(venc),
                                    "quantidade": float(qtd),
                                    "preco": float(preco),
                                    "tipo_negocio": tipo_neg,
                                    "valor_operacao": float(valor),
                                    "dc": dc,
                                    "taxa_operacional": float(taxa_op),
                                }
                            )

    # Fallback regex para itens nao capturados pela tabela
    neg_pat = re.compile(
        r"^([CV])\s+([\w\s]+?)\s+(\d{2}/\d{2}/\d{4})\s+"
        r"([\d.]+)\s+([\d.,]+)\s+(DAY TRADE|NORMAL|FRACIONARIO)?\s*"
        r"([\d.,]+)\s+([DC])\s+([\d.,]+)\s*$",
        re.M,
    )
    for mm in neg_pat.finditer(text):
        qtd_r = round(float(_dec(mm.group(4).replace(".", ""))), 4)
        preco_r = round(float(_dec(mm.group(5))), 2)
        key = (mm.group(1), mm.group(2).strip(), qtd_r, preco_r)
        if key not in seen:
            seen.add(key)
            items.append(
                {
                    "cv": mm.group(1),
                    "mercadoria": mm.group(2).strip(),
                    "vencimento": str(_date_br(mm.group(3))),
                    "quantidade": float(_dec(mm.group(4).replace(".", ""))),
                    "preco": float(_dec(mm.group(5))),
                    "tipo_negocio": (mm.group(6) or "NORMAL").strip(),
                    "valor_operacao": float(_dec(mm.group(7))),
                    "dc": mm.group(8),
                    "taxa_operacional": float(_dec(mm.group(9))),
                }
            )

    # Financials por posicao Y
    fin: dict[str, Any] = {
        "valor_negocios": 0,
        "sinal_total": "D",
        "irrf": 0,
        "irrf_daytrade": 0,
        "corretagem": 0,
        "taxa_registro": 0,
        "emolumentos": 0,
        "outros_custos": 0,
        "impostos": 0,
        "ajuste_posicao": 0,
        "ajuste_daytrade": 0,
        "total_custos_operacionais": 0,
        "total_liquido": 0,
    }

    for y in sorted(lines_y.keys()):
        if y < 600:
            continue
        ws = sorted(lines_y[y])
        txt = " ".join(t for _, t in ws)
        nums_raw = re.findall(r"\d+,\d+", txt)
        dcs = re.findall(r"\|\s*([DC])", txt)
        nums = [_dec(n) for n in nums_raw]

        if len(nums) == 5 and len(dcs) == 1 and "0,00|" not in txt:
            # Linha: Venda_Disp Compra_Disp Venda_Op Compra_Op ValorNegocios|D
            fin["valor_negocios"] = float(nums[-1])
            fin["sinal_total"] = dcs[-1]

        elif len(nums) == 5 and len(dcs) == 1 and "0,00|" in txt:
            # Linha: IRRF IRRF_DT Taxa_Op Taxa_Reg Emol|D
            fin["irrf"] = float(nums[0])
            fin["irrf_daytrade"] = float(nums[1])
            fin["corretagem"] = float(nums[2])
            fin["taxa_registro"] = float(nums[3])
            fin["emolumentos"] = float(nums[4])

        elif len(nums) == 5 and len(dcs) == 2:
            # Linha: Outros Impostos Ajuste_Pos Ajuste_DT|D Total_Custos|D
            fin["outros_custos"] = float(nums[0])
            fin["impostos"] = float(nums[1])
            fin["ajuste_posicao"] = float(nums[2])
            fin["ajuste_daytrade"] = float(nums[3])
            fin["total_custos_operacionais"] = float(nums[4])

        elif len(nums) == 6 and len(dcs) == 2:
            # Linha: Outros IRRF_Op Total_CI|D Total_Liq|D Total_CN Total_Nota|D
            # Total liquido da nota = nums[3] com dcs[0]
            fin["total_liquido"] = float(nums[3])
            fin["sinal_total"] = dcs[0]

    return {
        "corretora": "XP",
        "numero_nota": numero_nota,
        "data_pregao": str(data_pregao),
        "cpf": cpf,
        "cnpj": cnpj,
        "items": items,
        "financials": fin,
        "resultado": -fin["total_liquido"] if fin["sinal_total"] == "D" else fin["total_liquido"],
        "arquivo": filename,
        "formato": "nota_xp",
    }


# ── Parser: XP Posicao Detalhada (XLSX) ──────────────────────────────────────


def _parse_posicao_xp(content: bytes, filename: str) -> dict[str, Any]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active
    rows = [[cell.value for cell in row] for row in ws.iter_rows()]

    ref_date = str(date.today())
    positions = []
    current_section = ""

    for row in rows:
        vals = [v for v in row if v is not None and str(v).strip() not in ("", " ")]
        if not vals:
            continue
        first = str(vals[0]).strip()

        # Data de referencia
        for v in row:
            if v and "Conta:" in str(v):
                m = re.search(r"(\d{2}/\d{2}/\d{4})", str(v))
                if m:
                    ref_date = str(_date_br(m.group(1)))

        # Secoes
        for sec, tipo in [
            ("COE", "coe"),
            ("Derivativos", "derivativo"),
            ("Ações", "acao"),
            ("Fundos", "fundo"),
            ("Renda Fixa", "rf"),
            ("Produtos Estruturados", "estruturado"),
            ("Carteira Administrada", "carteira_adm"),
            ("Custódia Remunerada", "custodia"),
        ]:
            if first.startswith(sec):
                current_section = tipo
                break

        skip = [
            "Posição",
            "Rentabilidade",
            "Alocação",
            "Valor aplicado",
            "Data ",
            "Tipo fixing",
            "Qtd. total",
            "Código",
            "Total",
            "Marcelo",
            "R$ ",
            "Proventos",
            "Ações e Fundos",
            "Dividendos",
            "Provisionado",
        ]
        if any(first.startswith(s) for s in skip):
            continue

        if current_section == "acao" and len(vals) >= 5:
            ticker = first.strip()
            if not re.match(r"^[A-Z]{4}\d{1,2}$", ticker):
                continue
            qtd = _dec(str(vals[6]).replace(".", "")) if len(vals) > 6 else Decimal(0)
            if qtd <= 0:
                continue
            pm_str = str(vals[4]) if len(vals) > 4 else ""
            pm = _dec(pm_str) if pm_str and pm_str != "Indefinido" else None
            preco = _dec(str(vals[5])) if len(vals) > 5 else None
            valor = _dec(str(vals[1])) if len(vals) > 1 else None
            tipo_map = {"LFTB11": "etf", "MELI34": "bdr"}
            tipo = tipo_map.get(ticker, "acao")
            positions.append(
                {
                    "ticker": ticker,
                    "tipo": tipo,
                    "corretora": "XP",
                    "moeda": "BRL",
                    "quantidade": float(qtd),
                    "preco_medio": float(pm) if pm else None,
                    "preco_atual": float(preco) if preco else None,
                    "valor_bruto": float(valor) if valor else None,
                    "data_referencia": ref_date,
                }
            )

        elif (
            current_section == "fundo"
            and len(vals) >= 5
            and not any(c in first for c in ["%", "R$"])
            and len(first) > 5
        ):
            nome = first
            posicao = _dec(str(vals[1])) if len(vals) > 1 else None
            aplic = _dec(str(vals[5])) if len(vals) > 5 else None
            liq = _dec(str(vals[6])) if len(vals) > 6 else None
            tipo_sub = (
                "fii" if "FII" in nome.upper() else "fip" if "FIP" in nome.upper() else "fundo"
            )
            positions.append(
                {
                    "ticker": nome[:80],
                    "tipo": tipo_sub,
                    "corretora": "XP",
                    "moeda": "BRL",
                    "quantidade": 1,
                    "preco_medio": float(aplic) if aplic else None,
                    "preco_atual": float(liq) if liq else None,
                    "valor_bruto": float(posicao) if posicao else None,
                    "valor_aplicado": float(aplic) if aplic else None,
                    "nome": nome,
                    "data_referencia": ref_date,
                }
            )

        elif current_section == "rf" and len(vals) >= 5 and len(first) > 5 and "%" not in first:
            nome = first
            posicao = _dec(str(vals[1])) if len(vals) > 1 else None
            aplic = _dec(str(vals[3])) if len(vals) > 3 else None
            taxa_str = str(vals[5]) if len(vals) > 5 else ""
            data_compra = str(_date_br(str(vals[6]))) if len(vals) > 6 else None
            vcto = str(_date_br(str(vals[7]))) if len(vals) > 7 else None
            tipo_rf = (
                "ntnb"
                if "NTN-B" in nome
                else "cra"
                if "CRA" in nome
                else "cri"
                if "CRI" in nome
                else "deb"
                if "DEB" in nome
                else "lci"
                if "LCI" in nome
                else "lca"
                if "LCA" in nome
                else "cdb"
                if "CDB" in nome
                else "rf"
            )
            positions.append(
                {
                    "ticker": nome[:80],
                    "tipo": tipo_rf,
                    "corretora": "XP",
                    "moeda": "BRL",
                    "quantidade": 1,
                    "preco_medio": float(aplic) if aplic else None,
                    "preco_atual": float(posicao) if posicao else None,
                    "valor_bruto": float(posicao) if posicao else None,
                    "valor_aplicado": float(aplic) if aplic else None,
                    "nome": nome,
                    "taxa": taxa_str,
                    "vencimento": vcto,
                    "data_compra": data_compra,
                    "data_referencia": ref_date,
                }
            )

        elif current_section == "coe" and len(vals) >= 5 and "%" not in first and len(first) > 5:
            nome = first
            posicao = _dec(str(vals[1])) if len(vals) > 1 else None
            aplic = _dec(str(vals[5])) if len(vals) > 5 else None
            vcto = str(_date_br(str(vals[6]))) if len(vals) > 6 else None
            positions.append(
                {
                    "ticker": nome[:80],
                    "tipo": "coe",
                    "corretora": "XP",
                    "moeda": "BRL",
                    "quantidade": 1,
                    "preco_medio": float(aplic) if aplic else None,
                    "preco_atual": float(posicao) if posicao else None,
                    "valor_bruto": float(posicao) if posicao else None,
                    "valor_aplicado": float(aplic) if aplic else None,
                    "nome": nome,
                    "vencimento": vcto,
                    "data_referencia": ref_date,
                }
            )

    return {
        "corretora": "XP",
        "formato": "posicao_xp",
        "arquivo": filename,
        "data_referencia": ref_date,
        "total_posicoes": len(positions),
        "positions": positions,
    }


# ── Parser: BTG US DriveWealth (PDF) ─────────────────────────────────────────


def _parse_extrato_btg_us(content: bytes, filename: str) -> dict[str, Any]:
    ref_date = str(date.today())
    text_all = _pdf_text(content)

    # Data de referencia
    m_period = re.search(r"(\w+ \d+, \d{4}) - (\w+ \d+, \d{4})", text_all)
    if m_period:
        try:
            ref_date = str(datetime.strptime(m_period.group(2), "%B %d, %Y").date())
        except ValueError:
            pass

    # Numero da conta
    m_acc = re.search(r"Account Number:([\w\-]+)", text_all)
    conta = m_acc.group(1) if m_acc else ""

    # Holdings via regex com conhecimento do formato de cada linha
    holding_pat = re.compile(
        r"^.+?\s+([A-Z0-9]{2,10})\s+"
        r"([\d.]+)\s+"
        r"([\d.,]+)\s+"
        r"([\d.,]+)\s+"
        r"([\d.,]+)\s+"
        r"([\d.,]+)\s+"
        r"(\([\d.,]+\)|[\d.,]+)\s+M\s*$",
        re.M,
    )

    positions = []
    skip_tickers = {"DWBDS", "M", "Equity", "Description", "Symbol", "931CVR013"}

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if "HOLDINGS" not in page_text and "AMAZON" not in page_text:
                continue
            for mm in holding_pat.finditer(page_text):
                ticker = mm.group(1)
                if ticker in skip_tickers:
                    continue
                qtd = _dec(mm.group(2))
                pm = _dec(mm.group(3))
                preco = _dec(mm.group(5))
                valor = _dec(mm.group(6))
                gain_raw = mm.group(7)
                gain = _dec(gain_raw)
                if "(" in gain_raw:
                    gain = -gain
                if qtd > 0:
                    positions.append(
                        {
                            "ticker": ticker,
                            "tipo": "stock",
                            "corretora": "BTG-US",
                            "moeda": "USD",
                            "quantidade": float(qtd),
                            "preco_medio": float(pm),
                            "preco_atual": float(preco),
                            "valor_bruto": float(valor),
                            "ganho_perda_usd": float(gain),
                            "data_referencia": ref_date,
                        }
                    )

    # Dividendos da secao ACTIVITY
    dividendos = []
    div_pat = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+USD\s+DIV\s+"
        r"(\w+)\s+.*?([\d.]+)\s*$",
        re.M,
    )
    for mm in div_pat.finditer(text_all):
        dividendos.append(
            {
                "data": str(_date_br(mm.group(1))),
                "ticker": mm.group(2),
                "valor": float(_dec(mm.group(3))),
                "tipo": "dividendo",
            }
        )

    return {
        "corretora": "BTG-US",
        "conta": conta,
        "formato": "extrato_btg_us",
        "arquivo": filename,
        "data_referencia": ref_date,
        "total_posicoes": len(positions),
        "positions": positions,
        "dividendos": dividendos,
    }


# ── Parser: CSV Generico ──────────────────────────────────────────────────────


def _parse_csv(content: bytes, filename: str, corretora: str = "CSV") -> dict[str, Any]:
    import csv as csv_mod

    text = content.decode("utf-8", errors="replace")
    reader = csv_mod.DictReader(io.StringIO(text))
    positions = []
    col_map = {
        "symbol": "ticker",
        "coin": "ticker",
        "ativo": "ticker",
        "amount": "quantidade",
        "qty": "quantidade",
        "qtd": "quantidade",
        "avg_price": "preco_medio",
        "buy_price": "preco_medio",
        "pm": "preco_medio",
        "current_price": "preco_atual",
        "currency": "moeda_col",
    }
    for row in reader:
        nr = {col_map.get(k.lower().strip(), k.lower().strip()): v for k, v in row.items()}
        ticker = str(nr.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        positions.append(
            {
                "ticker": ticker,
                "tipo": str(nr.get("tipo", "acao")).lower(),
                "corretora": corretora,
                "moeda": str(
                    nr.get("moeda_col", nr.get("moeda", nr.get("currency", "BRL")))
                ).upper()[:3],
                "quantidade": float(_dec(nr.get("quantidade", "0"))),
                "preco_medio": float(_dec(nr.get("preco_medio", ""))) or None,
                "preco_atual": float(_dec(nr.get("preco_atual", ""))) or None,
                "data_referencia": str(date.today()),
            }
        )
    return {
        "corretora": corretora,
        "formato": "csv",
        "arquivo": filename,
        "total_posicoes": len(positions),
        "positions": positions,
    }


# ── Rotas ─────────────────────────────────────────────────────────────────────


def _parse_extrato_btg_br(content: bytes, filename: str) -> dict[str, Any]:
    """Parser do Extrato Mensal BTG Pactual Brasil (PDF)."""
    import calendar as _cal
    from datetime import date as _date

    ref_date = str(_date.today())
    client = ""
    conta = ""
    positions: list[dict] = []
    movimentos: list[dict] = []

    text_all = _pdf_text(content)

    # Data de referencia
    meses_pt = {
        "janeiro": 1,
        "fevereiro": 2,
        "marco": 3,
        "marco": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    }
    m_mes = re.search(
        r"(janeiro|fevereiro|mar[co]o|abril|maio|junho|julho|agosto"
        r"|setembro|outubro|novembro|dezembro)[/\s]*(de\s*)?(\d{4})",
        text_all,
        re.I,
    )
    if m_mes:
        mes_str = m_mes.group(1).lower().replace("c", "c")
        mes = meses_pt.get(mes_str, 1)
        ano = int(m_mes.group(3))
        ultimo_dia = _cal.monthrange(ano, mes)[1]
        ref_date = f"{ano:04d}-{mes:02d}-{ultimo_dia:02d}"

    # Fallback DD/MM/YYYY
    if ref_date == str(_date.today()):
        m_dt = re.search(r"(\d{2})/(\d{2})/(\d{4})", text_all)
        if m_dt:
            ref_date = f"{m_dt.group(3)}-{m_dt.group(2)}-{m_dt.group(1)}"

    # Cliente e conta
    m_client = re.search(r"(?:cliente|nome)[:\s]+([A-Z][A-Za-z\s]{5,50})", text_all, re.I)
    if m_client:
        client = m_client.group(1).strip()

    m_conta = re.search(r"(?:conta|account)[:\s#]*(\d[\d.\-]{3,20})", text_all, re.I)
    if m_conta:
        conta = m_conta.group(1).strip()

    # Extrai posicoes por pagina
    raw_pages = 0
    full_text = ""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        raw_pages = len(pdf.pages)
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    # Ticker + qtd + preco + valor
    pat_ativo = re.compile(r"([A-Z]{3,6}\d{0,2}[BF]?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", re.M)
    SKIP = {"CPF", "CNPJ", "BTG", "PDF", "SAO", "RIO", "BRL", "USD", "EUR"}

    tipo_atual = "acao"
    for linha in full_text.splitlines():
        l_lower = linha.lower()
        if "etf" in l_lower or "fundo de indice" in l_lower:
            tipo_atual = "etf"
        elif "fundo" in l_lower and "investimento" in l_lower:
            tipo_atual = "fundo"
        elif any(k in l_lower for k in ["renda fixa", "cdb", "cri", "cra", "debenture"]):
            tipo_atual = "renda_fixa"
        elif any(k in l_lower for k in ["cripto", "bitcoin", "ethereum"]):
            tipo_atual = "cripto"
        elif any(k in l_lower for k in ["acoes", "acao"]):
            tipo_atual = "acao"

        m = pat_ativo.search(linha)
        if m:
            ticker = m.group(1)
            if ticker in SKIP:
                continue
            try:
                qtd = float(_dec(m.group(2)))
                preco = float(_dec(m.group(3)))
                valor = float(_dec(m.group(4)))
                if qtd > 0 and valor > 0:
                    positions.append(
                        {
                            "ticker": ticker,
                            "tipo": tipo_atual,
                            "qtd": qtd,
                            "preco": preco,
                            "valor": valor,
                            "moeda": "BRL",
                            "corretora": "BTG",
                            "ref_date": ref_date,
                        }
                    )
            except Exception:
                pass

    # Movimentos conta corrente
    pat_mov = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(.{5,60}?)\s+([DC])\s+([\d.,]+)", re.M)
    for m_mov in pat_mov.finditer(full_text):
        try:
            movimentos.append(
                {
                    "data": m_mov.group(1),
                    "descricao": m_mov.group(2).strip(),
                    "tipo": "debito" if m_mov.group(3) == "D" else "credito",
                    "valor": float(_dec(m_mov.group(4))),
                }
            )
        except Exception:
            pass

    # Remove duplicatas
    seen: set = set()
    unique: list[dict] = []
    for p in positions:
        key = (p["ticker"], p["qtd"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return {
        "source": "btg-br",
        "filename": filename,
        "ref_date": ref_date,
        "client": client,
        "conta": conta,
        "positions": unique,
        "movimentos": movimentos,
        "total": len(unique),
        "raw_pages": raw_pages,
    }


def _save_to_history(result: dict) -> None:
    _import_history.insert(
        0,
        {
            "id": len(_import_history) + 1,
            "arquivo": result.get("arquivo", ""),
            "corretora": result.get("corretora", ""),
            "formato": result.get("formato", ""),
            "total_posicoes": result.get("total_posicoes", result.get("total", 0)),
            "importado_em": datetime.now().isoformat(),
            "status": "ok" if not result.get("errors") else "erro",
        },
    )
    for pos in result.get("positions", []):
        _positions.append(pos)


@router.post("/nota-xp", summary="Importar nota de negociacao B3 (PDF)")
async def import_nota_xp(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Arquivo deve ser .pdf")
    content = await file.read()
    try:
        result = _parse_nota_xp(content, file.filename)
        result["total"] = len(result.get("items", []))
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.nota_xp.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/posicao-xp", summary="Importar posicao detalhada XP (XLSX)")
async def import_posicao_xp(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Arquivo deve ser .xlsx")
    content = await file.read()
    try:
        result = _parse_posicao_xp(content, file.filename)
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.posicao_xp.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/extrato-btg-us", summary="Importar Account Statement BTG US (PDF)")
async def import_btg_us(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Arquivo deve ser .pdf")
    content = await file.read()
    try:
        result = _parse_extrato_btg_us(content, file.filename)
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.btg_us.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/csv", summary="Importar CSV generico de posicoes")
async def import_csv(
    file: UploadFile = File(...),
    corretora: str = Query("CSV"),
) -> dict[str, Any]:
    content = await file.read()
    try:
        result = _parse_csv(content, file.filename or "arquivo.csv", corretora)
        _save_to_history(result)
        return result
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/auto", summary="Detecta formato e importa automaticamente")
async def import_auto(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    try:
        if fn.endswith(".xlsx"):
            result = _parse_posicao_xp(content, file.filename)
        elif fn.endswith(".csv"):
            result = _parse_csv(content, file.filename)
        elif fn.endswith(".pdf"):
            preview = _pdf_text(content).lower().replace("\x00", "a")
            if "nota de negociação" in preview or "xp investimentos" in preview:
                result = _parse_nota_xp(content, file.filename)
                result["total"] = len(result.get("items", []))
            elif "drivewealth" in preview or "account statement" in preview:
                result = _parse_extrato_btg_us(content, file.filename)
            elif "conta investimento" in preview and "btg" in preview:
                result = _parse_extrato_btg_br(content, file.filename)
            else:
                raise HTTPException(400, "Formato PDF nao reconhecido. Use o endpoint especifico.")
        else:
            raise HTTPException(400, "Formato nao suportado: " + fn.split(".")[-1])

        _save_to_history(result)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("import.auto.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/extrato-btg-br", summary="Importar Extrato Mensal BTG BR (PDF)")
async def import_extrato_btg_br(
    file: UploadFile = File(...),
) -> dict:
    """
    Importa extrato mensal do BTG Pactual Brasil (PDF).
    Extrai: posicoes em acoes, ETFs, fundos, renda fixa, cripto e movimentos.
    """
    content = await file.read()
    try:
        result = _parse_extrato_btg_br(content, file.filename)
        return {"ok": True, "source": "btg-br", "filename": file.filename, "data": result}
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao processar extrato BTG BR: {e}")


@router.get("/history", summary="Historico de importacoes")
async def get_history() -> dict[str, Any]:
    return {"total": len(_import_history), "items": _import_history}


@router.get("/positions", summary="Todas as posicoes importadas")
async def get_positions(
    corretora: str | None = Query(None),
    tipo: str | None = Query(None),
) -> dict[str, Any]:
    pos = _positions
    if corretora:
        pos = [p for p in pos if p.get("corretora", "").upper() == corretora.upper()]
    if tipo:
        pos = [p for p in pos if p.get("tipo", "").lower() == tipo.lower()]
    return {"total": len(pos), "positions": pos}


# ── Feature C6: Dividendos ───────────────────────────────────────────────────


@router.post("/dividends/preview", summary="Preview parser de dividendos (CSV/OFX)")
async def preview_dividends(
    account_id: str = Query(..., min_length=1),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse extrato (CSV/OFX) detectando dividendos/JCP/rendimentos.

    Faz match com positions do account_id; retorna preview SEM commit.
    """
    from finanalytics_ai.application.services.dividend_import_service import DividendImportService

    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    svc = DividendImportService()
    if fn.endswith(".csv"):
        parsed = svc.parse_csv(content)
    elif fn.endswith(".ofx") or fn.endswith(".qfx"):
        parsed = svc.parse_ofx(content)
    elif fn.endswith(".pdf"):
        try:
            parsed = svc.parse_pdf(content)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(
            400, f"Formato nao suportado: {fn.split('.')[-1]}. Use CSV, OFX ou PDF."
        )

    matched = await svc.match_to_positions(parsed, account_id)

    return {
        "filename": file.filename,
        "total_lines": len(parsed),
        "matched": [
            {
                "ticker": m.matched_ticker or m.parsed.ticker,
                "amount": m.parsed.amount,
                "date": m.parsed.date.isoformat(),
                "type": m.parsed.detected_type,
                "status": m.match_status,
                "position_id": m.matched_position_id,
                "candidates": m.candidates,
                "description": m.parsed.description[:120],
            }
            for m in matched
        ],
        "summary": {
            "matched": sum(1 for m in matched if m.match_status == "matched"),
            "unmatched": sum(1 for m in matched if m.match_status == "unmatched"),
            "ambiguous": sum(1 for m in matched if m.match_status == "ambiguous"),
        },
    }


@router.post("/dividends/commit", summary="Confirma e cria account_transactions de dividendos")
async def commit_dividends(
    account_id: str = Query(..., min_length=1),
    user_id: str = Query(
        ..., min_length=1, description="user_id (master pode importar para qualquer)"
    ),
    only_matched: bool = Query(False, description="Se true, ignora linhas unmatched/ambiguous"),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Re-parse + commit. Cria tx_type=dividend status=settled em account_transactions.

    Idempotente: linhas com mesma data+amount+ticker pulam (skip).
    Linhas unmatched ficam com related_id=None e podem ser reconciliadas manualmente depois.
    """
    from finanalytics_ai.application.services.dividend_import_service import DividendImportService

    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    svc = DividendImportService()
    if fn.endswith(".csv"):
        parsed = svc.parse_csv(content)
    elif fn.endswith(".ofx") or fn.endswith(".qfx"):
        parsed = svc.parse_ofx(content)
    elif fn.endswith(".pdf"):
        try:
            parsed = svc.parse_pdf(content)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, f"Formato nao suportado: {fn.split('.')[-1]}.")

    matched = await svc.match_to_positions(parsed, account_id)
    result = await svc.commit_dividends(
        matched, user_id=user_id, account_id=account_id, only_matched=only_matched
    )
    return result

"""
infrastructure/reports/fundamental_pdf.py
Gerador de relatórios fundamentalistas em PDF.

Dois modos:
  - Empresa única: análise profunda com histórico completo
  - Comparativo: side-by-side de 2-10 empresas

Usa ReportLab para layout e chart_builder para gráficos seaborn/matplotlib.
CPU-bound — deve ser chamado via asyncio.to_thread().
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

from finanalytics_ai.infrastructure.reports.chart_builder import (
    chart_barras_dre,
    chart_cotacoes,
    chart_linha_indicador,
    chart_linhas_multiplas,
    chart_radar_comparativo,
)

# ── Paleta ────────────────────────────────────────────────────────────────────
C_BG       = colors.HexColor("#0d1117")
C_PANEL    = colors.HexColor("#111822")
C_PANEL2   = colors.HexColor("#0e1420")
C_BORDER   = colors.HexColor("#1c2535")
C_TEXT     = colors.HexColor("#cdd6e0")
C_MUTED    = colors.HexColor("#4a5e72")
C_ACCENT   = colors.HexColor("#00d4ff")
C_GREEN    = colors.HexColor("#00e676")
C_RED      = colors.HexColor("#ff3d5a")
C_GOLD     = colors.HexColor("#F0B429")
C_WHITE    = colors.HexColor("#ffffff")

PAGE_W, PAGE_H = A4


# ── Estilos ───────────────────────────────────────────────────────────────────
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Normal"],
            fontSize=22, textColor=C_WHITE, fontName="Helvetica-Bold",
            spaceAfter=4, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"],
            fontSize=12, textColor=C_ACCENT, fontName="Helvetica",
            spaceAfter=2, alignment=TA_LEFT),
        "section": ParagraphStyle("section", parent=base["Normal"],
            fontSize=13, textColor=C_GOLD, fontName="Helvetica-Bold",
            spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["Normal"],
            fontSize=9, textColor=C_TEXT, fontName="Helvetica",
            leading=14, spaceAfter=4),
        "small": ParagraphStyle("small", parent=base["Normal"],
            fontSize=7, textColor=C_MUTED, fontName="Helvetica",
            leading=10),
        "kpi_label": ParagraphStyle("kpi_label", parent=base["Normal"],
            fontSize=7, textColor=C_MUTED, fontName="Helvetica",
            alignment=TA_CENTER),
        "kpi_value": ParagraphStyle("kpi_value", parent=base["Normal"],
            fontSize=14, textColor=C_WHITE, fontName="Helvetica-Bold",
            alignment=TA_CENTER),
        "table_header": ParagraphStyle("table_header", parent=base["Normal"],
            fontSize=8, textColor=C_GOLD, fontName="Helvetica-Bold",
            alignment=TA_CENTER),
        "table_cell": ParagraphStyle("table_cell", parent=base["Normal"],
            fontSize=8, textColor=C_TEXT, fontName="Helvetica",
            alignment=TA_CENTER),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def _png_to_image(png_bytes: bytes, width: float) -> Image:
    buf = io.BytesIO(png_bytes)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width
    img.drawHeight = width * ratio
    return img


def _fmt(value: Any, suffix: str = "", prefix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
        return f"{prefix}{v:,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_bilhoes(value: Any) -> str:
    if value is None:
        return "—"
    try:
        v = float(value) / 1e9
        return f"R$ {v:,.1f}B"
    except (TypeError, ValueError):
        return "—"


def _color_value(value: Any, higher_is_better: bool = True) -> colors.Color:
    if value is None:
        return C_MUTED
    try:
        v = float(value)
        if higher_is_better:
            return C_GREEN if v > 0 else C_RED
        else:
            return C_GREEN if v < 0 else C_RED
    except (TypeError, ValueError):
        return C_TEXT


def _table_style_base() -> list:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), C_PANEL),
        ("BACKGROUND", (0, 1), (-1, -1), C_BG),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_BG, C_PANEL2]),
        ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_GOLD),
        ("TEXTCOLOR", (0, 1), (-1, -1), C_TEXT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]


# ── Header/Footer ─────────────────────────────────────────────────────────────
def _on_first_page(canvas: Any, doc: Any, titulo: str, subtitulo: str) -> None:
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Barra superior
    canvas.setFillColor(C_PANEL)
    canvas.rect(0, PAGE_H - 60, PAGE_W, 60, fill=1, stroke=0)
    # Linha accent
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 62, PAGE_W, 2, fill=1, stroke=0)
    # Logo/Brand
    canvas.setFillColor(C_ACCENT)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(2 * cm, PAGE_H - 38, "FinAnalytics AI")
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(2 * cm, PAGE_H - 52,
                      f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    # Footer
    _draw_footer(canvas, doc)
    canvas.restoreState()


def _on_later_pages(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(C_PANEL)
    canvas.rect(0, PAGE_H - 30, PAGE_W, 30, fill=1, stroke=0)
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 32, PAGE_W, 2, fill=1, stroke=0)
    canvas.setFillColor(C_ACCENT)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(2 * cm, PAGE_H - 20, "FinAnalytics AI")
    _draw_footer(canvas, doc)
    canvas.restoreState()


def _draw_footer(canvas: Any, doc: Any) -> None:
    canvas.setFillColor(C_BORDER)
    canvas.rect(0, 0, PAGE_W, 20, fill=1, stroke=0)
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 6)
    canvas.drawString(2 * cm, 7, "Este relatório é gerado automaticamente. Não constitui recomendação de investimento.")
    canvas.drawRightString(PAGE_W - 2 * cm, 7,
                           f"Página {doc.page}")


# ── KPI Cards ────────────────────────────────────────────────────────────────
def _kpi_cards(indicadores: dict[str, Any], st: dict) -> Table:
    """Linha de cards KPI com os principais indicadores."""
    def _g(ind, key):
        v = ind.get(key, {})
        return v.get("valor") if isinstance(v, dict) else v
    # Indicadores percentuais: Fintz guarda em decimal (0.19 = 19%)
    def _pct(ind, key):
        v = _g(ind, key)
        return v * 100 if v is not None else None
    items = [
        ("P/L",        _g(indicadores, "P_L")),
        ("P/VP",       _g(indicadores, "P_VP")),
        ("ROE",        _pct(indicadores, "ROE"), "%"),
        ("DY",         _pct(indicadores, "DividendYield"), "%"),
        ("ROIC",       _pct(indicadores, "ROIC"), "%"),
        ("EV/EBITDA",  _g(indicadores, "EV_EBITDA")),
        ("Mg. EBITDA", _pct(indicadores, "MargemEBITDA"), "%"),
        ("D/EBITDA",   _g(indicadores, "DividaLiquida_EBITDA")),
    ]
    labels = [[Paragraph(label, st["kpi_label"])] for label, *_ in items]
    suffix_map = {i: s for i, (_, __, *s) in enumerate(items) if s}
    values = []
    for i, (label, val, *suf) in enumerate(items):
        s = suf[0] if suf else ""
        formatted = _fmt(val, suffix=s)
        values.append([Paragraph(formatted, st["kpi_value"])])

    col_w = (PAGE_W - 4 * cm) / len(items)
    label_row = [l[0] for l in labels]
    value_row = [v[0] for v in values]
    t = Table([label_row, value_row],
              colWidths=[col_w] * len(items),
              rowHeights=[14, 28])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PANEL),
        ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return t


# ── Tabela de indicadores históricos ─────────────────────────────────────────
def _tabela_indicadores(
    series: list[dict[str, Any]],
    indicador: str,
    st: dict,
    n_periodos: int = 8,
) -> Table | None:
    if not series:
        return None
    # Pivota por data
    dados = sorted(series, key=lambda r: str(r.get("data", "")), reverse=True)[:n_periodos]
    if not dados:
        return None
    header = ["Período"] + [str(r.get("data", ""))[:7] for r in reversed(dados)]
    row = [Paragraph(indicador, st["table_header"])] + [
        Paragraph(_fmt(r.get("valor")), st["table_cell"]) for r in reversed(dados)
    ]
    col_w_first = 3.5 * cm
    col_w_rest = (PAGE_W - 4 * cm - col_w_first) / max(len(dados), 1)
    t = Table([header, row],
              colWidths=[col_w_first] + [col_w_rest] * len(dados))
    t.setStyle(TableStyle(_table_style_base()))
    return t


# ── Geração do relatório EMPRESA ÚNICA ───────────────────────────────────────
def generate_fundamental_single(data: dict[str, Any]) -> bytes:
    """
    Gera relatório PDF de análise fundamentalista de uma empresa.

    data esperado:
    {
        "ticker": str,
        "nome": str,
        "setor": str,
        "preco": float,
        "market_cap": float,
        "indicadores_latest": dict,  # {indicador: {valor, data_ref}}
        "valuation_serie": list,     # [{data, indicador, valor}]
        "rentabilidade_serie": list,
        "dividendos_serie": list,
        "endividamento_serie": list,
        "dre": dict,                 # {item: [{data_publicacao, valor}]}
        "cotacoes": list,            # [{data, fechamento_ajustado}]
        "periodo_anos": int,
    }
    """
    buf = io.BytesIO()
    st = _styles()
    ticker = data.get("ticker", "—")
    nome = data.get("nome", ticker)
    setor = data.get("setor", "—")
    ind = data.get("indicadores_latest", {})
    periodo = data.get("periodo_anos", 5)

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=1.8 * cm,
    )
    first_frame = Frame(
        2 * cm, 1.8 * cm,
        PAGE_W - 4 * cm, PAGE_H - 4.5 * cm,
        id="first"
    )
    later_frame = Frame(
        2 * cm, 1.8 * cm,
        PAGE_W - 4 * cm, PAGE_H - 3.8 * cm,
        id="later"
    )
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first_frame],
                     onPage=lambda c, d: _on_first_page(c, d, ticker, nome)),
        PageTemplate(id="Later", frames=[later_frame],
                     onPage=_on_later_pages),
    ])

    content = []
    usable_w = PAGE_W - 4 * cm

    # ── Capa / Cabeçalho ──────────────────────────────────────────────────
    content.append(Spacer(1, 0.5 * cm))
    content.append(Paragraph(ticker, st["title"]))
    content.append(Paragraph(nome, st["subtitle"]))
    content.append(Paragraph(f"Setor: {setor}  ·  Análise de {periodo} anos", st["small"]))
    content.append(Spacer(1, 0.3 * cm))
    content.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))
    content.append(Spacer(1, 0.3 * cm))

    # ── KPIs ──────────────────────────────────────────────────────────────
    preco = data.get("preco")
    mcap = data.get("market_cap")
    info_data = [
        [Paragraph("Preço Atual", st["kpi_label"]),
         Paragraph("Market Cap", st["kpi_label"]),
         Paragraph("Setor", st["kpi_label"]),
         Paragraph("Data Ref.", st["kpi_label"])],
        [Paragraph(_fmt(preco, prefix="R$ "), st["kpi_value"]),
         Paragraph(_fmt_bilhoes(mcap), st["kpi_value"]),
         Paragraph(setor[:20], st["kpi_value"]),
         Paragraph(datetime.now().strftime("%d/%m/%Y"), st["kpi_value"])],
    ]
    cw = usable_w / 4
    t_info = Table(info_data, colWidths=[cw] * 4, rowHeights=[14, 28])
    t_info.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PANEL),
        ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    content.append(t_info)
    content.append(Spacer(1, 0.3 * cm))
    content.append(_kpi_cards(ind, st))
    content.append(Spacer(1, 0.5 * cm))

    # ── Cotação ───────────────────────────────────────────────────────────
    content.append(Paragraph("Cotação Histórica", st["section"]))
    cotacoes = data.get("cotacoes", [])
    if cotacoes:
        png = chart_cotacoes(cotacoes, ticker, figsize=(9, 2.8))
        content.append(_png_to_image(png, usable_w))
    content.append(Spacer(1, 0.4 * cm))

    # ── Valuation ─────────────────────────────────────────────────────────
    content.append(Paragraph("Valuation", st["section"]))
    val_serie = data.get("valuation_serie", [])
    val_indicadores = ["P/L", "P/VP", "EV/EBITDA"]
    for ind_nome in val_indicadores:
        serie = [r for r in val_serie if r.get("indicador") == ind_nome]
        if serie:
            png = chart_linha_indicador(serie, ind_nome, figsize=(9, 2.4))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    # Tabela snapshot valuation
    val_snap = {k: v for k, v in ind.items()
                if k in ["P_L", "P_VP", "EV_EBITDA", "P_EBITDA", "P_SR", "EV_EBIT"]}
    if val_snap:
        rows = [[Paragraph("Indicador", st["table_header"]),
                 Paragraph("Valor Atual", st["table_header"]),
                 Paragraph("Data Ref.", st["table_header"])]]
        for k, v in val_snap.items():
            rows.append([
                Paragraph(k, st["table_cell"]),
                Paragraph(_fmt(v.get("valor") if isinstance(v, dict) else v), st["table_cell"]),
                Paragraph(str(v.get("data_ref", "—") if isinstance(v, dict) else "—"), st["table_cell"]),
            ])
        t = Table(rows, colWidths=[usable_w * 0.5, usable_w * 0.25, usable_w * 0.25])
        t.setStyle(TableStyle(_table_style_base()))
        content.append(t)
    content.append(Spacer(1, 0.4 * cm))

    # ── Rentabilidade ─────────────────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Rentabilidade", st["section"]))
    rent_serie = data.get("rentabilidade_serie", [])
    rent_inds = ["ROE", "ROIC", "Margem Líquida", "Margem EBITDA"]
    for ind_nome in rent_inds:
        serie = [r for r in rent_serie if r.get("indicador") == ind_nome]
        if serie:
            color = "#00e676" if ind_nome in ["ROE", "ROIC"] else "#00d4ff"
            png = chart_linha_indicador(serie, f"{ind_nome} (%)", ylabel="%",
                                         color=color, figsize=(9, 2.4))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    # ── Dividendos ────────────────────────────────────────────────────────
    content.append(Paragraph("Dividendos", st["section"]))
    div_serie = data.get("dividendos_serie", [])
    for ind_nome in ["DY", "Payout"]:
        serie = [r for r in div_serie if r.get("indicador") == ind_nome]
        if serie:
            png = chart_linha_indicador(serie, f"{ind_nome} (%)", ylabel="%",
                                         color="#F0B429", figsize=(9, 2.4))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    # ── Endividamento ─────────────────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Solidez Financeira", st["section"]))
    end_serie = data.get("endividamento_serie", [])
    for ind_nome in ["Dívida Líquida/EBITDA", "Dívida Líquida/Patrimônio Líquido"]:
        serie = [r for r in end_serie if r.get("indicador") == ind_nome]
        if serie:
            png = chart_linha_indicador(serie, ind_nome,
                                         color="#ff3d5a", figsize=(9, 2.4))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    # ── DRE ───────────────────────────────────────────────────────────────
    dre = data.get("dre", {})
    if dre:
        content.append(Paragraph("Demonstração de Resultados (DRE)", st["section"]))
        png = chart_barras_dre(dre, figsize=(9, 3.2))
        content.append(_png_to_image(png, usable_w))
        content.append(Spacer(1, 0.4 * cm))
        # Tabela resumida
        itens_dre = list(dre.keys())[:5]
        if itens_dre:
            header_row = [Paragraph("Item", st["table_header"])]
            anos_set: set[str] = set()
            for serie in dre.values():
                for r in serie:
                    anos_set.add(str(r.get("data_publicacao", ""))[:4])
            anos = sorted(anos_set)[-6:]
            header_row += [Paragraph(a, st["table_header"]) for a in anos]
            rows = [header_row]
            for item in itens_dre:
                serie = dre[item]
                mapa = {str(r.get("data_publicacao", ""))[:4]: r.get("valor") for r in serie}
                row = [Paragraph(item[:25], st["table_cell"])]
                row += [Paragraph(_fmt_bilhoes(mapa.get(a)), st["table_cell"]) for a in anos]
                rows.append(row)
            cw_first = 4 * cm
            cw_rest = (usable_w - cw_first) / max(len(anos), 1)
            t = Table(rows, colWidths=[cw_first] + [cw_rest] * len(anos))
            t.setStyle(TableStyle(_table_style_base()))
            content.append(t)

    doc.build(content)
    return buf.getvalue()


# ── Geração do relatório COMPARATIVO ─────────────────────────────────────────
def generate_fundamental_comparative(data: dict[str, Any]) -> bytes:
    """
    Gera relatório PDF comparativo entre múltiplas empresas.

    data esperado:
    {
        "tickers": [str, ...],
        "empresas": {
            "TICKER": {
                "nome": str,
                "setor": str,
                "preco": float,
                "market_cap": float,
                "indicadores_latest": dict,
                "valuation_serie": list,
                "rentabilidade_serie": list,
                "cotacoes": list,
            }
        },
        "periodo_anos": int,
    }
    """
    buf = io.BytesIO()
    st = _styles()
    tickers = data.get("tickers", [])
    empresas = data.get("empresas", {})
    periodo = data.get("periodo_anos", 5)

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=1.8 * cm,
    )
    first_frame = Frame(2 * cm, 1.8 * cm, PAGE_W - 4 * cm, PAGE_H - 4.5 * cm, id="first")
    later_frame = Frame(2 * cm, 1.8 * cm, PAGE_W - 4 * cm, PAGE_H - 3.8 * cm, id="later")
    titulo_comp = " vs ".join(tickers[:5])
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first_frame],
                     onPage=lambda c, d: _on_first_page(c, d, titulo_comp, "Análise Comparativa")),
        PageTemplate(id="Later", frames=[later_frame],
                     onPage=_on_later_pages),
    ])

    content = []
    usable_w = PAGE_W - 4 * cm

    # ── Capa ──────────────────────────────────────────────────────────────
    content.append(Spacer(1, 0.5 * cm))
    content.append(Paragraph("Análise Comparativa", st["title"]))
    content.append(Paragraph(" · ".join(tickers), st["subtitle"]))
    content.append(Paragraph(f"Período: {periodo} anos", st["small"]))
    content.append(Spacer(1, 0.3 * cm))
    content.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))
    content.append(Spacer(1, 0.4 * cm))

    # ── Tabela resumo ─────────────────────────────────────────────────────
    content.append(Paragraph("Resumo", st["section"]))
    INDICADORES_TABELA = ["P/L", "P/VP", "EV/EBITDA", "ROE", "ROIC",
                          "Margem EBITDA", "DY", "Dívida Líquida/EBITDA"]
    header = [Paragraph("Indicador", st["table_header"])] + \
             [Paragraph(t, st["table_header"]) for t in tickers]
    rows = [header]
    for ind_nome in INDICADORES_TABELA:
        row = [Paragraph(ind_nome, st["table_cell"])]
        for t in tickers:
            emp = empresas.get(t, {})
            ind = emp.get("indicadores_latest", {})
            val = ind.get(ind_nome, {})
            if isinstance(val, dict):
                val = val.get("valor")
            row.append(Paragraph(_fmt(val), st["table_cell"]))
        rows.append(row)

    cw_first = 4.5 * cm
    cw_rest = (usable_w - cw_first) / max(len(tickers), 1)
    t = Table(rows, colWidths=[cw_first] + [cw_rest] * len(tickers))
    t.setStyle(TableStyle(_table_style_base()))
    content.append(t)
    content.append(Spacer(1, 0.5 * cm))

    # ── Radar comparativo ────────────────────────────────────────────────
    content.append(Paragraph("Score por Dimensão", st["section"]))
    # Calcula score normalizado 0-100 para cada dimensão
    scores: dict[str, dict[str, float]] = {}
    dimensoes = {
        "Valuation":      (["P/L", "P/VP"], False),     # menor = melhor
        "Rentabilidade":  (["ROE", "ROIC"], True),
        "Dividendos":     (["DY"], True),
        "Solidez":        (["Dívida Líquida/EBITDA"], False),
        "Margem":         (["Margem EBITDA", "Margem Líquida"], True),
    }
    for t_name in tickers:
        scores[t_name] = {}
        emp = empresas.get(t_name, {})
        ind = emp.get("indicadores_latest", {})
        for dim, (inds, higher_better) in dimensoes.items():
            vals = []
            for i in inds:
                v = ind.get(i, {})
                if isinstance(v, dict):
                    v = v.get("valor")
                if v is not None:
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        pass
            scores[t_name][dim] = sum(vals) / len(vals) if vals else 0

    # Normaliza 0-100 por dimensão
    for dim in dimensoes:
        dim_vals = [scores[t].get(dim, 0) for t in tickers]
        min_v, max_v = min(dim_vals), max(dim_vals)
        higher_better = dimensoes[dim][1]
        for t_name in tickers:
            raw = scores[t_name].get(dim, 0)
            if max_v != min_v:
                normalized = (raw - min_v) / (max_v - min_v) * 100
                scores[t_name][dim] = normalized if higher_better else 100 - normalized
            else:
                scores[t_name][dim] = 50.0

    png_radar = chart_radar_comparativo(tickers, scores, figsize=(7, 7))
    content.append(_png_to_image(png_radar, min(usable_w, 14 * cm)))
    content.append(Spacer(1, 0.5 * cm))

    # ── Gráficos comparativos por indicador ──────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Valuation Comparativo", st["section"]))
    for ind_nome in ["P/L", "P/VP", "EV/EBITDA"]:
        series_dict = {}
        for t_name in tickers:
            emp = empresas.get(t_name, {})
            serie = [r for r in emp.get("valuation_serie", [])
                     if r.get("indicador") == ind_nome]
            if serie:
                series_dict[t_name] = serie
        if series_dict:
            png = chart_linhas_multiplas(series_dict, ind_nome, figsize=(9, 2.8))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    content.append(Paragraph("Rentabilidade Comparativa", st["section"]))
    for ind_nome in ["ROE", "ROIC", "Margem EBITDA"]:
        series_dict = {}
        for t_name in tickers:
            emp = empresas.get(t_name, {})
            serie = [r for r in emp.get("rentabilidade_serie", [])
                     if r.get("indicador") == ind_nome]
            if serie:
                series_dict[t_name] = serie
        if series_dict:
            png = chart_linhas_multiplas(series_dict, f"{ind_nome} (%)", figsize=(9, 2.8))
            content.append(KeepTogether([
                Paragraph(f"<b>{ind_nome}</b>", st["body"]),
                _png_to_image(png, usable_w),
                Spacer(1, 0.3 * cm),
            ]))

    # ── Cotações comparativas ─────────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Performance — Cotações", st["section"]))
    series_cotacoes: dict[str, list] = {}
    for t_name in tickers:
        emp = empresas.get(t_name, {})
        cotacoes = emp.get("cotacoes", [])
        if cotacoes:
            # Normaliza base 100
            vals = [(str(r.get("data", ""))[:10],
                     r.get("fechamento_ajustado") or r.get("fechamento", 0))
                    for r in reversed(cotacoes) if r.get("fechamento_ajustado")]
            if vals:
                base = vals[0][1] or 1
                series_cotacoes[t_name] = [
                    {"data": d, "valor": v / base * 100} for d, v in vals
                ]
    if series_cotacoes:
        png = chart_linhas_multiplas(series_cotacoes,
                                      "Performance Relativa (base 100)",
                                      ylabel="Base 100",
                                      figsize=(9, 3.2))
        content.append(_png_to_image(png, usable_w))

    doc.build(content)
    return buf.getvalue()

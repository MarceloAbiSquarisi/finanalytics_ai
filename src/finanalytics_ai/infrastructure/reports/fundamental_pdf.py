"""
infrastructure/reports/fundamental_pdf.py — v2
Gerador de relatórios fundamentalistas em PDF.
Logo FinAnalytics AI desenhada com primitivas ReportLab.
Dados exclusivamente do Fintz.
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
from reportlab.graphics.shapes import Drawing, Rect, Line, Circle, String, PolyLine
from reportlab.graphics import renderPDF

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
C_NAVY     = colors.HexColor("#0A1628")
C_BLUE     = colors.HexColor("#3ABFF8")

PAGE_W, PAGE_H = A4


# ── Logo ──────────────────────────────────────────────────────────────────────
def _draw_logo(canvas: Any, x: float, y: float, w: float = 120, h: float = 36) -> None:
    """Desenha logo FinAnalytics AI usando primitivas ReportLab."""
    canvas.saveState()
    # Fundo pill
    canvas.setFillColor(C_NAVY)
    canvas.roundRect(x, y, w, h, 6, fill=1, stroke=0)
    # Linha accent
    canvas.setFillColor(C_ACCENT)
    canvas.rect(x, y + h - 2, w, 2, fill=1, stroke=0)
    # Candlestick mini (decorativo)
    cw = 3; gap = 5; bx = x + 8; by = y + 8
    bars = [(0, 12, colors.HexColor("#E05555")),
            (gap, 16, C_GREEN),
            (gap*2, 22, C_GOLD),
            (gap*3, 14, C_GREEN)]
    for dx, bh, bc in bars:
        canvas.setFillColor(bc)
        canvas.rect(bx + dx, by, cw, bh, fill=1, stroke=0)
    # Texto "Fin"
    canvas.setFont("Helvetica-Bold", 14)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(x + 30, y + h/2 - 4, "Fin")
    # Texto "Analytics"
    canvas.setFillColor(C_GOLD)
    canvas.drawString(x + 52, y + h/2 - 4, "Analytics")
    # Badge AI
    badge_x = x + 30; badge_y = y + 4
    canvas.setFillColor(C_GOLD)
    canvas.roundRect(badge_x, badge_y, 18, 9, 2, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 6)
    canvas.setFillColor(C_NAVY)
    canvas.drawCentredString(badge_x + 9, badge_y + 2, "AI")
    canvas.restoreState()


# ── Estilos ───────────────────────────────────────────────────────────────────
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Normal"],
            fontSize=28, textColor=C_WHITE, fontName="Helvetica-Bold",
            spaceAfter=2, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"],
            fontSize=11, textColor=C_ACCENT, fontName="Helvetica",
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


def _table_style_base() -> list:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), C_PANEL),
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
def _on_first_page(canvas: Any, doc: Any, ticker: str, nome: str) -> None:
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Barra superior
    canvas.setFillColor(C_PANEL)
    canvas.rect(0, PAGE_H - 52, PAGE_W, 52, fill=1, stroke=0)
    # Linha accent
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 54, PAGE_W, 2, fill=1, stroke=0)
    # Logo
    _draw_logo(canvas, 2 * cm, PAGE_H - 48, 130, 38)
    # Timestamp
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(PAGE_W - 2 * cm, PAGE_H - 24,
                           f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    _draw_footer(canvas, doc)
    canvas.restoreState()


def _on_later_pages(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(C_PANEL)
    canvas.rect(0, PAGE_H - 28, PAGE_W, 28, fill=1, stroke=0)
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 30, PAGE_W, 2, fill=1, stroke=0)
    _draw_logo(canvas, 2 * cm, PAGE_H - 26, 90, 22)
    _draw_footer(canvas, doc)
    canvas.restoreState()


def _draw_footer(canvas: Any, doc: Any) -> None:
    canvas.setFillColor(C_BORDER)
    canvas.rect(0, 0, PAGE_W, 20, fill=1, stroke=0)
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 6)
    canvas.drawString(2 * cm, 7,
        "Este relatório é gerado automaticamente. Não constitui recomendação de investimento.")
    canvas.drawRightString(PAGE_W - 2 * cm, 7, f"Página {doc.page}")


# ── KPI Cards ────────────────────────────────────────────────────────────────
def _kpi_cards(indicadores: dict[str, Any], st: dict) -> Table:
    def _g(ind, key):
        v = ind.get(key, {})
        return v.get("valor") if isinstance(v, dict) else v

    items = [
        ("P/L",        _g(indicadores, "P_L"),         "x"),
        ("P/VP",       _g(indicadores, "P_VP"),         "x"),
        ("ROE",        _g(indicadores, "ROE"),          "%"),
        ("DY",         _g(indicadores, "DividendYield"), "%"),
        ("ROIC",       _g(indicadores, "ROIC"),         "%"),
        ("EV/EBITDA",  _g(indicadores, "EV_EBITDA"),    "x"),
        ("Mg.EBITDA",  _g(indicadores, "MargemEBITDA"),  "%"),
        ("D/EBITDA",   _g(indicadores, "DividaLiquida_EBITDA"), "x"),
    ]
    label_row = [Paragraph(label, st["kpi_label"]) for label, _, __ in items]
    value_row = [
        Paragraph(_fmt(val, suffix=suf), st["kpi_value"])
        for _, val, suf in items
    ]
    col_w = (PAGE_W - 4 * cm) / len(items)
    t = Table([label_row, value_row],
              colWidths=[col_w] * len(items), rowHeights=[14, 28])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PANEL),
        ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


# ── Geração do relatório EMPRESA ÚNICA ───────────────────────────────────────
def generate_fundamental_single(data: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    st = _styles()
    ticker      = data.get("ticker", "—")
    nome        = data.get("nome", ticker)
    setor       = data.get("setor", "—")
    ind         = data.get("indicadores_latest", {})
    series_map  = data.get("series_map", {})
    periodo     = data.get("periodo_anos", 5)
    labels      = data.get("indicador_labels", {})
    pct_inds    = set(data.get("pct_indicators", []))

    def label(key: str) -> str:
        return labels.get(key, key)

    def serie_pct(ind_name: str) -> list[dict]:
        """Converte série decimal → % se necessário."""
        s = series_map.get(ind_name, [])
        if ind_name in pct_inds:
            return [{"data": r["data"],
                     "valor": r["valor"] * 100 if r.get("valor") is not None else None}
                    for r in s]
        return s

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=1.8*cm,
    )
    first_frame = Frame(2*cm, 1.8*cm, PAGE_W-4*cm, PAGE_H-4.2*cm, id="first")
    later_frame = Frame(2*cm, 1.8*cm, PAGE_W-4*cm, PAGE_H-3.5*cm, id="later")
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first_frame],
                     onPage=lambda c, d: _on_first_page(c, d, ticker, nome)),
        PageTemplate(id="Later", frames=[later_frame],
                     onPage=_on_later_pages),
    ])

    content = []
    usable_w = PAGE_W - 4*cm

    # ── Capa ──────────────────────────────────────────────────────────────
    content.append(Spacer(1, 0.4*cm))
    content.append(Paragraph(ticker, st["title"]))
    if nome and nome != ticker:
        content.append(Paragraph(nome, st["subtitle"]))
    content.append(Paragraph(
        f"Setor: {setor}  ·  Período: {periodo} anos  ·  Fonte: Fintz", st["small"]))
    content.append(Spacer(1, 0.3*cm))
    content.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))
    content.append(Spacer(1, 0.3*cm))

    # ── Info geral ────────────────────────────────────────────────────────
    preco = data.get("preco")
    mcap  = data.get("market_cap")

    def _g(key):
        v = ind.get(key, {})
        return v.get("valor") if isinstance(v, dict) else v

    lpa = _g("LPA"); vpa = _g("VPA")
    info_data = [
        [Paragraph("Preço Atual", st["kpi_label"]),
         Paragraph("Market Cap", st["kpi_label"]),
         Paragraph("LPA", st["kpi_label"]),
         Paragraph("VPA", st["kpi_label"]),
         Paragraph("Data Ref.", st["kpi_label"])],
        [Paragraph(_fmt(preco, prefix="R$ "), st["kpi_value"]),
         Paragraph(_fmt_bilhoes(mcap), st["kpi_value"]),
         Paragraph(_fmt(lpa, prefix="R$ "), st["kpi_value"]),
         Paragraph(_fmt(vpa, prefix="R$ "), st["kpi_value"]),
         Paragraph(datetime.now().strftime("%d/%m/%Y"), st["kpi_value"])],
    ]
    cw = usable_w / 5
    t_info = Table(info_data, colWidths=[cw]*5, rowHeights=[14, 28])
    t_info.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_PANEL),
        ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    content.append(t_info)
    content.append(Spacer(1, 0.3*cm))
    content.append(_kpi_cards(ind, st))
    content.append(Spacer(1, 0.5*cm))

    # ── Cotação ───────────────────────────────────────────────────────────
    content.append(Paragraph("Cotação Histórica", st["section"]))
    cotacoes = data.get("cotacoes", [])
    if cotacoes:
        png = chart_cotacoes(cotacoes, ticker, figsize=(9, 2.8))
        content.append(_png_to_image(png, usable_w))
    content.append(Spacer(1, 0.4*cm))

    # ── Valuation ─────────────────────────────────────────────────────────
    content.append(Paragraph("Valuation", st["section"]))
    for ind_key in ["P_L", "P_VP", "EV_EBITDA", "P_EBITDA"]:
        serie = series_map.get(ind_key, [])
        if serie:
            png = chart_linha_indicador(serie, label(ind_key),
                                         ylabel="x", figsize=(9, 2.4))
            content.append(KeepTogether([
                _png_to_image(png, usable_w),
                Spacer(1, 0.3*cm),
            ]))

    # Tabela snapshot valuation
    val_keys = ["P_L", "P_VP", "EV_EBITDA", "P_EBITDA", "P_SR", "EV_EBIT"]
    rows_val = [[Paragraph("Indicador", st["table_header"]),
                 Paragraph("Valor Atual", st["table_header"]),
                 Paragraph("Data Ref.", st["table_header"])]]
    for k in val_keys:
        v = ind.get(k, {})
        if isinstance(v, dict) and v.get("valor") is not None:
            rows_val.append([
                Paragraph(label(k), st["table_cell"]),
                Paragraph(_fmt(v["valor"], suffix="x"), st["table_cell"]),
                Paragraph(str(v.get("data_ref", "—")), st["table_cell"]),
            ])
    if len(rows_val) > 1:
        t = Table(rows_val, colWidths=[usable_w*0.5, usable_w*0.25, usable_w*0.25])
        t.setStyle(TableStyle(_table_style_base()))
        content.append(t)
    content.append(Spacer(1, 0.4*cm))

    # ── Rentabilidade ─────────────────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Rentabilidade", st["section"]))
    for ind_key in ["ROE", "ROIC", "ROA", "MargemLiquida", "MargemEBITDA"]:
        serie = serie_pct(ind_key)
        if serie:
            color = "#00e676" if ind_key in ["ROE", "ROIC", "ROA"] else "#00d4ff"
            png = chart_linha_indicador(serie, label(ind_key),
                                         ylabel="%", color=color, figsize=(9, 2.4))
            content.append(KeepTogether([
                _png_to_image(png, usable_w),
                Spacer(1, 0.3*cm),
            ]))

    # ── Dividendos ────────────────────────────────────────────────────────
    content.append(Paragraph("Dividendos", st["section"]))
    dy_serie = serie_pct("DividendYield")
    if dy_serie:
        png = chart_linha_indicador(dy_serie, label("DividendYield"),
                                     ylabel="%", color="#F0B429", figsize=(9, 2.8))
        content.append(_png_to_image(png, usable_w))
    else:
        content.append(Paragraph("Dados de dividendos não disponíveis para este ticker.", st["small"]))
    content.append(Spacer(1, 0.4*cm))

    # ── Solidez Financeira ────────────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Solidez Financeira", st["section"]))
    for ind_key in ["DividaLiquida_EBITDA", "DividaLiquida_PatrimonioLiquido",
                    "DividaBruta_PatrimonioLiquido"]:
        serie = series_map.get(ind_key, [])
        if serie:
            png = chart_linha_indicador(serie, label(ind_key),
                                         color="#ff3d5a", figsize=(9, 2.4))
            content.append(KeepTogether([
                _png_to_image(png, usable_w),
                Spacer(1, 0.3*cm),
            ]))

    # Tabela solidez
    solid_keys = ["DividaLiquida_EBITDA", "DividaLiquida_PatrimonioLiquido",
                  "DividaBruta_PatrimonioLiquido", "LiquidezCorrente"]
    rows_s = [[Paragraph("Indicador", st["table_header"]),
               Paragraph("Valor Atual", st["table_header"]),
               Paragraph("Data Ref.", st["table_header"])]]
    for k in solid_keys:
        v = ind.get(k, {})
        if isinstance(v, dict) and v.get("valor") is not None:
            rows_s.append([
                Paragraph(label(k), st["table_cell"]),
                Paragraph(_fmt(v["valor"], suffix="x"), st["table_cell"]),
                Paragraph(str(v.get("data_ref", "—")), st["table_cell"]),
            ])
    if len(rows_s) > 1:
        t = Table(rows_s, colWidths=[usable_w*0.5, usable_w*0.25, usable_w*0.25])
        t.setStyle(TableStyle(_table_style_base()))
        content.append(t)

    # ── DRE ───────────────────────────────────────────────────────────────
    dre = data.get("dre", {})
    if dre:
        content.append(PageBreak())
        content.append(Paragraph("Demonstração de Resultados (DRE)", st["section"]))
        png = chart_barras_dre(dre, figsize=(9, 3.2))
        content.append(_png_to_image(png, usable_w))
        content.append(Spacer(1, 0.4*cm))

    doc.build(content)
    return buf.getvalue()


# ── Geração do relatório COMPARATIVO ─────────────────────────────────────────
def generate_fundamental_comparative(data: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    st = _styles()
    tickers  = data.get("tickers", [])
    empresas = data.get("empresas", {})
    periodo  = data.get("periodo_anos", 5)

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=1.8*cm,
    )
    titulo = " vs ".join(tickers[:5])
    first_frame = Frame(2*cm, 1.8*cm, PAGE_W-4*cm, PAGE_H-4.2*cm, id="first")
    later_frame = Frame(2*cm, 1.8*cm, PAGE_W-4*cm, PAGE_H-3.5*cm, id="later")
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first_frame],
                     onPage=lambda c, d: _on_first_page(c, d, titulo, "Análise Comparativa")),
        PageTemplate(id="Later", frames=[later_frame],
                     onPage=_on_later_pages),
    ])

    content = []
    usable_w = PAGE_W - 4*cm
    labels_map = {}
    for t in tickers:
        emp = empresas.get(t, {})
        labels_map.update(emp.get("indicador_labels", {}))

    def label(key: str) -> str:
        return labels_map.get(key, key)

    # ── Capa ──────────────────────────────────────────────────────────────
    content.append(Spacer(1, 0.4*cm))
    content.append(Paragraph("Análise Comparativa", st["title"]))
    content.append(Paragraph(" · ".join(tickers), st["subtitle"]))
    content.append(Paragraph(f"Período: {periodo} anos  ·  Fonte: Fintz", st["small"]))
    content.append(Spacer(1, 0.3*cm))
    content.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))
    content.append(Spacer(1, 0.4*cm))

    # ── Tabela resumo ─────────────────────────────────────────────────────
    content.append(Paragraph("Resumo de Indicadores", st["section"]))
    IND_TABELA = ["P_L", "P_VP", "EV_EBITDA", "ROE", "ROIC",
                  "MargemEBITDA", "DividendYield", "DividaLiquida_EBITDA"]
    header = ([Paragraph("Indicador", st["table_header"])] +
              [Paragraph(t, st["table_header"]) for t in tickers])
    rows = [header]
    for ind_key in IND_TABELA:
        row = [Paragraph(label(ind_key), st["table_cell"])]
        for t in tickers:
            emp = empresas.get(t, {})
            ind = emp.get("indicadores_latest", {})
            val = ind.get(ind_key, {})
            if isinstance(val, dict):
                val = val.get("valor")
            row.append(Paragraph(_fmt(val), st["table_cell"]))
        rows.append(row)
    cw_first = 4.5*cm
    cw_rest = (usable_w - cw_first) / max(len(tickers), 1)
    t = Table(rows, colWidths=[cw_first] + [cw_rest]*len(tickers))
    t.setStyle(TableStyle(_table_style_base()))
    content.append(t)
    content.append(Spacer(1, 0.5*cm))

    # ── Radar ─────────────────────────────────────────────────────────────
    content.append(Paragraph("Score por Dimensão", st["section"]))
    scores: dict[str, dict[str, float]] = {}
    dimensoes = {
        "Valuation":     (["P_L", "P_VP"], False),
        "Rentabilidade": (["ROE", "ROIC"], True),
        "Dividendos":    (["DividendYield"], True),
        "Solidez":       (["DividaLiquida_EBITDA"], False),
        "Margem":        (["MargemEBITDA", "MargemLiquida"], True),
    }
    for tn in tickers:
        scores[tn] = {}
        emp = empresas.get(tn, {})
        ind = emp.get("indicadores_latest", {})
        for dim, (inds, higher) in dimensoes.items():
            vals = []
            for ik in inds:
                v = ind.get(ik, {})
                if isinstance(v, dict):
                    v = v.get("valor")
                if v is not None:
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        pass
            scores[tn][dim] = sum(vals)/len(vals) if vals else 0

    for dim in dimensoes:
        dv = [scores[t].get(dim, 0) for t in tickers]
        mn, mx = min(dv), max(dv)
        higher = dimensoes[dim][1]
        for tn in tickers:
            raw = scores[tn].get(dim, 0)
            if mx != mn:
                n = (raw - mn) / (mx - mn) * 100
                scores[tn][dim] = n if higher else 100 - n
            else:
                scores[tn][dim] = 50.0

    png_radar = chart_radar_comparativo(tickers, scores)
    content.append(_png_to_image(png_radar, min(usable_w, 14*cm)))
    content.append(Spacer(1, 0.5*cm))

    # ── Comparativo por indicador ─────────────────────────────────────────
    content.append(PageBreak())
    content.append(Paragraph("Valuation Comparativo", st["section"]))
    for ind_key in ["P_L", "P_VP", "EV_EBITDA"]:
        sd = {}
        for tn in tickers:
            emp = empresas.get(tn, {})
            s = emp.get("series_map", {}).get(ind_key, [])
            if s:
                sd[tn] = s
        if sd:
            png = chart_linhas_multiplas(sd, label(ind_key), figsize=(9, 2.8))
            content.append(KeepTogether([
                _png_to_image(png, usable_w), Spacer(1, 0.3*cm)
            ]))

    content.append(Paragraph("Rentabilidade Comparativa", st["section"]))
    for ind_key in ["ROE", "ROIC", "MargemEBITDA"]:
        sd = {}
        for tn in tickers:
            emp = empresas.get(tn, {})
            sm = emp.get("series_map", {})
            pct = set(emp.get("pct_indicators", []))
            s = sm.get(ind_key, [])
            if s and ind_key in pct:
                s = [{"data": r["data"],
                      "valor": r["valor"]*100 if r.get("valor") is not None else None}
                     for r in s]
            if s:
                sd[tn] = s
        if sd:
            png = chart_linhas_multiplas(sd, label(ind_key), ylabel="%", figsize=(9, 2.8))
            content.append(KeepTogether([
                _png_to_image(png, usable_w), Spacer(1, 0.3*cm)
            ]))

    doc.build(content)
    return buf.getvalue()

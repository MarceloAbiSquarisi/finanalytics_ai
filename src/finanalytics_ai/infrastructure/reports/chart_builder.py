"""
infrastructure/reports/chart_builder.py
Geração de gráficos com matplotlib/seaborn para relatórios PDF.
Retorna sempre bytes PNG prontos para embutir no ReportLab.
"""
from __future__ import annotations
import io
from typing import Any
import matplotlib
matplotlib.use("Agg")  # backend sem display
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
import numpy as np

# ── Paleta e tema ─────────────────────────────────────────────────────────────
DARK_BG    = "#0d1117"
PANEL_BG   = "#111822"
GRID_COLOR = "#1c2535"
TEXT_COLOR = "#cdd6e0"
ACCENT     = "#00d4ff"
GREEN      = "#00e676"
RED        = "#ff3d5a"
GOLD       = "#F0B429"
MUTED      = "#4a5e72"

PALETTE_MULTI = ["#00d4ff", "#00e676", "#F0B429", "#ff3d5a", "#a78bfa", "#fb923c", "#34d399"]

def _apply_dark_theme(fig: plt.Figure, axes: list[plt.Axes]) -> None:
    fig.patch.set_facecolor(DARK_BG)
    for ax in axes:
        ax.set_facecolor(PANEL_BG)
        ax.tick_params(colors=TEXT_COLOR, labelsize=8)
        ax.xaxis.label.set_color(TEXT_COLOR)
        ax.yaxis.label.set_color(TEXT_COLOR)
        ax.title.set_color(TEXT_COLOR)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
        ax.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle="--", alpha=0.7)
        ax.set_axisbelow(True)

def _to_png(fig: plt.Figure, dpi: int = 150) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# ── Gráficos de série temporal ────────────────────────────────────────────────
def chart_linha_indicador(
    series: list[dict[str, Any]],
    titulo: str,
    ylabel: str = "",
    color: str = ACCENT,
    figsize: tuple = (8, 2.8),
) -> bytes:
    """Linha simples de um indicador ao longo do tempo."""
    if not series:
        return _grafico_vazio(titulo, figsize)

    datas = [str(r.get("data", ""))[:7] for r in series]
    valores = [r.get("valor") for r in series]
    # Remove Nones e outliers extremos (IQR x 3)
    pairs = [(d, v) for d, v in zip(datas, valores) if v is not None]
    if len(pairs) > 4:
        vals_only = sorted(p[1] for p in pairs)
        q1 = vals_only[len(vals_only)//4]
        q3 = vals_only[3*len(vals_only)//4]
        iqr = q3 - q1
        fence = 3.0
        lo, hi = q1 - fence * iqr, q3 + fence * iqr
        pairs = [(d, v) for d, v in pairs if lo <= v <= hi]
    if not pairs:
        return _grafico_vazio(titulo, figsize)
    datas, valores = zip(*pairs)

    x = list(range(len(valores)))
    y_min = min((v for v in valores if v is not None), default=0)
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(x, valores, color=color, linewidth=2, marker="o", markersize=3)
    ax.fill_between(x, valores, y_min * 0.98 if y_min > 0 else y_min * 1.02,
                    alpha=0.1, color=color)
    # Ticks esparsos
    step = max(1, len(datas) // 8)
    tick_pos = x[::step]
    ax.set_xticks(tick_pos)
    ax.set_xticklabels([datas[i] for i in tick_pos],
                       rotation=30, ha="right", fontsize=7)
    ax.set_title(titulo, fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel(ylabel, fontsize=8)
    _apply_dark_theme(fig, [ax])
    fig.tight_layout()
    return _to_png(fig)


def chart_linhas_multiplas(
    series_dict: dict[str, list[dict[str, Any]]],
    titulo: str,
    ylabel: str = "",
    figsize: tuple = (8, 3.2),
) -> bytes:
    """Múltiplas séries sobrepostas (para comparativo)."""
    if not series_dict:
        return _grafico_vazio(titulo, figsize)

    fig, ax = plt.subplots(figsize=figsize)
    for i, (label, series) in enumerate(series_dict.items()):
        pairs = [(str(r.get("data", ""))[:7], r.get("valor"))
                 for r in reversed(series) if r.get("valor") is not None]
        if not pairs:
            continue
        datas, valores = zip(*pairs)
        color = PALETTE_MULTI[i % len(PALETTE_MULTI)]
        ax.plot(range(len(valores)), valores, color=color, linewidth=2,
                marker="o", markersize=3, label=label)

    ax.set_title(titulo, fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.legend(fontsize=7, facecolor=PANEL_BG, labelcolor=TEXT_COLOR,
              edgecolor=GRID_COLOR)
    _apply_dark_theme(fig, [ax])
    fig.tight_layout()
    return _to_png(fig)


def chart_barras_dre(
    itens: dict[str, list[dict[str, Any]]],
    titulo: str = "DRE — Evolução Anual",
    figsize: tuple = (8, 3.2),
) -> bytes:
    """Barras agrupadas para itens contábeis (Receita, EBITDA, Lucro)."""
    if not itens:
        return _grafico_vazio(titulo, figsize)

    # Pega anos comuns
    anos_set: set[str] = set()
    for series in itens.values():
        for r in series:
            anos_set.add(str(r.get("data_publicacao", ""))[:4])
    anos = sorted(anos_set)[-8:]  # últimos 8 anos

    fig, ax = plt.subplots(figsize=figsize)
    n_items = len(itens)
    bar_w = 0.8 / max(n_items, 1)
    x = np.arange(len(anos))

    for i, (label, series) in enumerate(itens.items()):
        mapa = {str(r.get("data_publicacao", ""))[:4]: r.get("valor", 0) or 0
                for r in series}
        valores = [mapa.get(a, 0) / 1e9 for a in anos]  # em R$ bilhões
        offset = (i - n_items / 2 + 0.5) * bar_w
        color = PALETTE_MULTI[i % len(PALETTE_MULTI)]
        ax.bar(x + offset, valores, bar_w * 0.9, label=label, color=color, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(anos, fontsize=8)
    ax.set_title(titulo, fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel("R$ Bilhões", fontsize=8)
    ax.legend(fontsize=7, facecolor=PANEL_BG, labelcolor=TEXT_COLOR,
              edgecolor=GRID_COLOR)
    _apply_dark_theme(fig, [ax])
    fig.tight_layout()
    return _to_png(fig)


def chart_cotacoes(
    cotacoes: list[dict[str, Any]],
    ticker: str,
    figsize: tuple = (8, 2.8),
) -> bytes:
    """Linha de preço de fechamento ajustado."""
    if not cotacoes:
        return _grafico_vazio(f"Cotação — {ticker}", figsize)

    dados = [(str(r.get("data", ""))[:10], r.get("fechamento_ajustado") or r.get("fechamento"))
             for r in reversed(cotacoes)]
    dados = [(d, v) for d, v in dados if v is not None]
    if not dados:
        return _grafico_vazio(f"Cotação — {ticker}", figsize)

    datas, valores = zip(*dados)
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(range(len(valores)), valores, color=GOLD, linewidth=1.5)
    ax.fill_between(range(len(valores)), valores,
                    min(valores) * 0.98, alpha=0.15, color=GOLD)

    step = max(1, len(datas) // 8)
    ax.set_xticks(range(0, len(datas), step))
    ax.set_xticklabels([datas[i][:7] for i in range(0, len(datas), step)],
                       rotation=30, ha="right", fontsize=7)
    ax.set_title(f"Cotação — {ticker}", fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel("R$", fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    _apply_dark_theme(fig, [ax])
    fig.tight_layout()
    return _to_png(fig)


def chart_radar_comparativo(
    tickers: list[str],
    scores: dict[str, dict[str, float]],
    titulo: str = "Comparativo — Score por Dimensão",
    figsize: tuple = (6, 6),
) -> bytes:
    """Radar chart comparando múltiplos tickers por dimensão."""
    if not scores or not tickers:
        return _grafico_vazio(titulo, figsize)

    categorias = list(next(iter(scores.values())).keys())
    N = len(categorias)
    if N < 3:
        return _grafico_vazio(titulo, figsize)

    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=figsize, subplot_kw=dict(polar=True))
    ax.set_facecolor(PANEL_BG)
    fig.patch.set_facecolor(DARK_BG)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categorias, color=TEXT_COLOR, size=8)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], color=MUTED, size=6)
    ax.grid(color=GRID_COLOR, linewidth=0.5)

    for i, ticker in enumerate(tickers):
        vals = [scores.get(ticker, {}).get(c, 0) for c in categorias]
        vals += vals[:1]
        color = PALETTE_MULTI[i % len(PALETTE_MULTI)]
        ax.plot(angles, vals, color=color, linewidth=2, label=ticker)
        ax.fill(angles, vals, color=color, alpha=0.1)

    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1),
              fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR,
              edgecolor=GRID_COLOR)
    ax.set_title(titulo, color=TEXT_COLOR, fontsize=11, fontweight="bold", pad=20)
    fig.tight_layout()
    return _to_png(fig)


def _grafico_vazio(titulo: str, figsize: tuple) -> bytes:
    fig, ax = plt.subplots(figsize=figsize)
    ax.text(0.5, 0.5, "Dados insuficientes", ha="center", va="center",
            color=MUTED, fontsize=10, transform=ax.transAxes)
    ax.set_title(titulo, fontsize=10, fontweight="bold", color=TEXT_COLOR)
    _apply_dark_theme(fig, [ax])
    return _to_png(fig)

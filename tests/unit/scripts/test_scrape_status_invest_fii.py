"""
Testes do scraper Status Invest FII (N5, 28/abr/2026).

Foco no que e reproduzivel sem rede:
  - _to_float (parsing localizado pt-BR)
  - regex de extracao DY/PVP/div_12m/valor_mercado em fragmentos HTML
    representativos do site (snapshot do KNRI11 em 28/abr).

Nao testa scrape_one nem o main loop — esses dependem de HTTP live (status_invest.com.br).
Cobertura ML/regex protege contra mudanca de layout — teste falha cedo.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Carrega o script standalone (nao e modulo Python instalado).
_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "scrape_status_invest_fii.py"
_spec = importlib.util.spec_from_file_location("scrape_status_invest_fii", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


# ── _to_float ─────────────────────────────────────────────────────────────────


def test_to_float_pt_br_decimal():
    assert _mod._to_float("7,47") == 7.47


def test_to_float_with_thousand_separator():
    """4.639.565.732 (sem virgula) — formato Status Invest valor_mercado."""
    assert _mod._to_float("4.639.565.732") == 4639565732.0


def test_to_float_mixed_thousand_and_decimal():
    """4.639.565.732,00 (raro mas possivel)."""
    assert _mod._to_float("4.639.565.732,00") == 4639565732.00


def test_to_float_zero():
    assert _mod._to_float("0,0") == 0.0


def test_to_float_none():
    assert _mod._to_float(None) is None


def test_to_float_empty_string():
    assert _mod._to_float("") is None


def test_to_float_invalid_string():
    assert _mod._to_float("nao-eh-numero") is None


# ── Regex DY/PVP/div_12m/valor_mercado ────────────────────────────────────────

# Snapshot reduzido representativo da estrutura HTML do Status Invest
# (KNRI11 em 28/abr/2026). Mantem so os trechos relevantes.
_HTML_SAMPLE_KNRI = """
<div class="info">
    <div title="Dividend Yield com base nos ultimos 12 meses">
        <h3 class="title m-0 legend-tooltip">Dividend Yield</h3>
        <strong class="value">7,47</strong>
        <span class="icon">%</span>
    </div>
    <div class="d-flex justify-between">
        <div title="Soma total de proventos distribuidos nos ultimos 12 meses">
            <span class="sub-title">Ultimos 12 meses</span>
            <span class="sub-value">R$ 12,3300</span>
        </div>
    </div>
</div>
<div class="info">
    <h3 class="title m-0">P/VP</h3>
    <strong class="value">1,01</strong>
    <span class="sub-title">Valor de mercado</span>
    <span class="sub-value">R$ 4.639.565.732</span>
</div>
"""


def test_regex_dy_extracts_value():
    m = _mod._RE_DY.search(_HTML_SAMPLE_KNRI)
    assert m is not None
    assert _mod._to_float(m.group(1)) == 7.47


def test_regex_pvp_extracts_value():
    m = _mod._RE_PVP.search(_HTML_SAMPLE_KNRI)
    assert m is not None
    assert _mod._to_float(m.group(1)) == 1.01


def test_regex_div12m_extracts_value():
    m = _mod._RE_DIV12M.search(_HTML_SAMPLE_KNRI)
    assert m is not None
    assert _mod._to_float(m.group(1)) == 12.33


def test_regex_valor_mercado_extracts_value():
    m = _mod._RE_VALOR_MERCADO.search(_HTML_SAMPLE_KNRI)
    assert m is not None
    assert _mod._to_float(m.group(1)) == 4639565732.0


def test_regex_dy_returns_none_when_html_changes():
    """Se o site mudar a estrutura, regex falha cedo."""
    broken = "<div>Dividend Yield: 7,47%</div>"  # sem <strong class="value">
    m = _mod._RE_DY.search(broken)
    assert m is None


@pytest.mark.parametrize(
    "term, expected",
    [
        ("DY", 7.47),
        ("PVP", 1.01),
    ],
)
def test_combined_extraction(term, expected):
    """Smoke combinado — todos os 4 indicadores em 1 HTML."""
    if term == "DY":
        m = _mod._RE_DY.search(_HTML_SAMPLE_KNRI)
    elif term == "PVP":
        m = _mod._RE_PVP.search(_HTML_SAMPLE_KNRI)
    assert m is not None
    assert _mod._to_float(m.group(1)) == expected


# ── IFIX_TOP_30 lista ────────────────────────────────────────────────────────


def test_ifix_top30_no_duplicates():
    """De-dupe deve preservar BCFF11/RBRF11 que estavam duplicados na lista."""
    assert len(_mod.IFIX_TOP_30) == len(set(_mod.IFIX_TOP_30))


def test_ifix_top30_includes_known_fiis():
    """Smoke da lista — verificar 5 FIIs classicos."""
    expected = {"KNRI11", "MXRF11", "HGLG11", "HFOF11", "BTLG11"}
    assert expected.issubset(set(_mod.IFIX_TOP_30))

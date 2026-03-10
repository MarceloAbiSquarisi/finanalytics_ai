"""Testes unitários — Cobertura FGC (Sprint 33)"""

from datetime import date, timedelta

from finanalytics_ai.domain.fixed_income.fgc import (
    analyze_fgc,
    fgc_coverage,
)
from finanalytics_ai.domain.fixed_income.portfolio import RFHolding


def _h(hid, btype, issuer, amount, matured=False):
    d = date.today()
    return RFHolding(
        holding_id=hid,
        portfolio_id="p1",
        bond_id="b1",
        bond_name=f"{btype} {issuer}",
        bond_type=btype,
        indexer="CDI",
        issuer=issuer,
        invested=amount,
        rate_annual=0.12,
        rate_pct_indexer=110.0,
        purchase_date=d - timedelta(days=100),
        maturity_date=d - timedelta(days=1) if matured else d + timedelta(days=200),
        ir_exempt=False,
    )


# ── fgc_coverage ──────────────────────────────────────────────────────────────
def test_cdb_covered():
    assert fgc_coverage("CDB") == "fgc"


def test_lci_covered():
    assert fgc_coverage("LCI") == "fgc"


def test_lca_covered():
    assert fgc_coverage("LCA") == "fgc"


def test_cri_not_covered():
    assert fgc_coverage("CRI") == "none"


def test_cra_not_covered():
    assert fgc_coverage("CRA") == "none"


def test_deb_not_covered():
    assert fgc_coverage("Debênture") == "none"


def test_tesouro_selic():
    assert fgc_coverage("Tesouro SELIC") == "sovereign"


def test_tesouro_ipca():
    assert fgc_coverage("Tesouro IPCA+") == "sovereign"


def test_tesouro_pre():
    assert fgc_coverage("Tesouro Prefixado") == "sovereign"


# ── analyze_fgc — caso normal (dentro do limite) ──────────────────────────────
_ok = analyze_fgc("p1", [_h("h1", "CDB", "Banco A", 100_000), _h("h2", "LCI", "Banco B", 80_000)])


def test_within_limit_no_excess():
    assert _ok.total_at_risk == 0.0


def test_within_limit_score_high():
    assert _ok.score == 100


def test_within_limit_no_critical():
    assert all(a["level"] != "critical" for a in _ok.alerts)


# ── analyze_fgc — excede limite por instituição ───────────────────────────────
_exc = analyze_fgc("p1", [_h("h1", "CDB", "Banco A", 200_000), _h("h2", "CDB", "Banco A", 100_000)])


def test_excess_detected():
    assert _exc.total_at_risk > 0


def test_excess_correct():
    assert abs(_exc.total_at_risk - 50_000) < 1


def test_excess_alert_present():
    assert any(a["type"] == "institution_limit_exceeded" for a in _exc.alerts)


def test_excess_score_reduced():
    assert _exc.score < 100


# ── analyze_fgc — sem cobertura (CRI/CRA/Debênture) ──────────────────────────
_unc = analyze_fgc("p1", [_h("h1", "CRI", "Fundo X", 50_000), _h("h2", "Debênture", "Empresa", 30_000)])


def test_uncovered_total():
    assert _unc.total_uncovered == 80_000


def test_uncovered_alert():
    assert any(a["type"] == "no_fgc_coverage" for a in _unc.alerts)


def test_uncovered_score_low():
    assert _unc.score <= 50


# ── analyze_fgc — Tesouro Direto (soberano) ───────────────────────────────────
_sov = analyze_fgc("p1", [_h("h1", "Tesouro IPCA+", "Tesouro", 200_000)])


def test_sovereign_not_uncovered():
    assert _sov.total_uncovered == 0


def test_sovereign_info_alert():
    assert any(a["type"] == "sovereign_info" for a in _sov.alerts)


def test_sovereign_at_risk_zero():
    assert _sov.total_at_risk == 0


# ── analyze_fgc — carteira vazia ─────────────────────────────────────────────
def test_empty_portfolio():
    a = analyze_fgc("p1", [])
    assert a.score == 100
    assert a.total_invested == 0


# ── analyze_fgc — holdings vencidos ignorados ────────────────────────────────
def test_matured_ignored():
    a = analyze_fgc("p1", [_h("h1", "CRI", "X", 999_000, matured=True)])
    assert a.total_uncovered == 0

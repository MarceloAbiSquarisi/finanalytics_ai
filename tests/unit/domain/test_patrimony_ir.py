"""Testes unitários — Patrimônio Consolidado + IR Calculator (Sprints 34/35)"""

from datetime import date, timedelta

from finanalytics_ai.domain.fixed_income.ir_calculator import (
    analyze_ir_timing,
    iof_rate_for_days,
    ir_rate_for_days,
)
from finanalytics_ai.domain.patrimony.consolidated import (
    AssetClass,
    build_snapshot,
)

T = date.today()


# ── IR rate table ──────────────────────────────────────────────────────────
def test_ir_180():
    assert ir_rate_for_days(180) == 0.225


def test_ir_181():
    assert ir_rate_for_days(181) == 0.200


def test_ir_360():
    assert ir_rate_for_days(360) == 0.200


def test_ir_361():
    assert ir_rate_for_days(361) == 0.175


def test_ir_720():
    assert ir_rate_for_days(720) == 0.175


def test_ir_721():
    assert ir_rate_for_days(721) == 0.150


# ── IOF table ──────────────────────────────────────────────────────────────
def test_iof_day1():
    assert iof_rate_for_days(1) == 0.96


def test_iof_day15():
    assert iof_rate_for_days(15) == 0.50


def test_iof_day29():
    assert iof_rate_for_days(29) == 0.03


def test_iof_day30():
    assert iof_rate_for_days(30) == 0.0


def test_iof_day90():
    assert iof_rate_for_days(90) == 0.0


# ── analyze_ir_timing — CDB 90 dias ───────────────────────────────────────
_pur = T - timedelta(days=90)
_adv = analyze_ir_timing(
    "h1",
    "CDB A",
    "CDB",
    "Banco",
    10000,
    _pur,
    T + timedelta(days=400),
    0.12,
    110.0,
    "CDI",
    0.1065,
    0.0483,
)


def test_cdb_ir_today():
    assert _adv.today_scenario.ir_rate == 0.225


def test_cdb_no_iof():
    assert _adv.today_scenario.iof_amount == 0.0


def test_cdb_has_saving():
    assert _adv.max_saving > 0


def test_cdb_best_lower():
    assert _adv.best_scenario.ir_rate < 0.225


def test_cdb_net_positive():
    assert _adv.today_scenario.net_value > 10000


def test_cdb_scenarios_gt1():
    assert len(_adv.scenarios) >= 2


def test_cdb_recommendation_not_empty():
    assert len(_adv.recommendation) > 10


# ── LCI isento ────────────────────────────────────────────────────────────
_lci = analyze_ir_timing(
    "h2",
    "LCI",
    "LCI",
    "Banco",
    5000,
    _pur,
    None,
    0.09,
    90.0,
    "CDI",
    0.1065,
    0.0483,
)


def test_lci_exempt():
    assert _lci.ir_exempt


def test_lci_ir_zero():
    assert _lci.today_scenario.ir_amount == 0.0


def test_lci_iof_zero():
    assert _lci.today_scenario.iof_amount == 0.0


# ── IOF ativo (dia 10) ────────────────────────────────────────────────────
_iof = analyze_ir_timing(
    "h3",
    "CDB IOF",
    "CDB",
    "Banco",
    20000,
    T - timedelta(days=10),
    T + timedelta(days=300),
    0.12,
    110.0,
    "CDI",
    0.1065,
    0.0483,
)


def test_iof_active():
    assert _iof.today_scenario.iof_amount > 0


def test_iof_scenario():
    assert any("IOF" in s.label or "30" in s.label for s in _iof.scenarios)


# ── IR incide sobre rendimento - IOF (não sobre total) ────────────────────
def test_ir_base_after_iof():
    s = _iof.today_scenario
    expected_base = s.gross_yield - s.iof_amount
    assert abs(s.ir_base - expected_base) < 0.01


# ── ConsolidatedSnapshot ──────────────────────────────────────────────────
_snap = build_snapshot(
    "u1",
    equities_value=50000,
    equities_invested=45000,
    equities_positions=4,
    etfs_value=25000,
    etfs_invested=22000,
    etfs_positions=3,
    rf_value=40000,
    rf_invested=40000,
    rf_positions=5,
    cash_value=5000,
)


def test_total_value():
    assert abs(_snap.total_value - 120000) < 1


def test_total_pl():
    assert abs(_snap.total_pl - 13000) < 1


def test_pl_pct():
    assert abs(_snap.total_pl_pct - (13000 / 112000 * 100)) < 0.1


def test_weights_sum():
    assert abs(sum(c.weight_pct for c in _snap.classes) - 100) < 0.1


def test_eq_overweight():
    eq = next(c for c in _snap.classes if c.asset_class == AssetClass.EQUITIES)
    # 50k/120k = 41.67%, meta=40% → desvio > 0
    assert eq.deviation_ppt > 0


def test_to_dict_keys():
    d = _snap.to_dict()
    for k in ["total_value", "total_pl", "total_pl_pct", "classes", "rebalance_needed"]:
        assert k in d

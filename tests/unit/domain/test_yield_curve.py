"""
Testes unitários — Yield Curve + Stress Test (Sprint 28b)
"""
from __future__ import annotations
import pytest
from datetime import date
from finanalytics_ai.domain.fixed_income.yield_curve import (
    YieldCurvePoint, YieldCurve, StressScenario, ScenarioComparison,
    StressResult, STANDARD_SCENARIOS,
)


# ── YieldCurvePoint ───────────────────────────────────────────────────────────

def test_yield_curve_point_rate_pct():
    p = YieldCurvePoint(maturity_days=252, rate_annual=0.1285)
    assert p.rate_pct == 12.85

def test_yield_curve_point_maturity_years():
    p = YieldCurvePoint(maturity_days=504, rate_annual=0.12)
    assert p.maturity_years == 2.0

def test_yield_curve_point_frozen():
    p = YieldCurvePoint(maturity_days=252, rate_annual=0.12)
    with pytest.raises(Exception):
        p.maturity_days = 500  # type: ignore


# ── YieldCurve ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_curve() -> YieldCurve:
    points = [
        YieldCurvePoint(21,   0.1065),
        YieldCurvePoint(126,  0.1085),
        YieldCurvePoint(252,  0.1120),
        YieldCurvePoint(756,  0.1180),
        YieldCurvePoint(1260, 0.1220),
    ]
    return YieldCurve(
        reference_date=date(2025, 3, 8),
        selic=0.1065, cdi=0.1065, ipca=0.0483,
        points=points, source="synthetic",
    )

def test_curve_short_rate(sample_curve):
    assert sample_curve.short_rate == 0.1065

def test_curve_long_rate(sample_curve):
    assert sample_curve.long_rate == 0.1220

def test_curve_not_inverted(sample_curve):
    assert not sample_curve.is_inverted

def test_curve_slope_positive(sample_curve):
    assert sample_curve.slope > 0

def test_curve_inverted():
    points = [
        YieldCurvePoint(252,  0.1300),
        YieldCurvePoint(1260, 0.1100),
    ]
    curve = YieldCurve(date.today(), 0.1300, 0.1300, 0.05, points)
    assert curve.is_inverted
    assert curve.slope < 0

def test_curve_empty_points():
    curve = YieldCurve(date.today(), 0.1065, 0.1065, 0.0483, [])
    assert curve.short_rate == 0.1065  # fallback para SELIC
    assert curve.long_rate  == 0.1065
    assert not curve.is_inverted


# ── StressScenario ────────────────────────────────────────────────────────────

def test_stress_scenario_base():
    s = StressScenario("Base")
    rates = s.apply_to_rates(0.1065, 0.1065, 0.0483, 0.062)
    assert rates["selic"] == pytest.approx(0.1065)
    assert rates["cdi"]   == pytest.approx(0.1065)
    assert rates["ipca"]  == pytest.approx(0.0483)

def test_stress_scenario_selic_up():
    s = StressScenario("SELIC +1 p.p.", delta_selic=0.01, delta_cdi=0.01)
    rates = s.apply_to_rates(0.1065, 0.1065, 0.0483, 0.062)
    assert rates["selic"] == pytest.approx(0.1165)
    assert rates["cdi"]   == pytest.approx(0.1165)
    assert rates["ipca"]  == pytest.approx(0.0483)  # inalterado

def test_stress_scenario_ipca_up():
    s = StressScenario("IPCA +2 p.p.", delta_ipca=0.02)
    rates = s.apply_to_rates(0.1065, 0.1065, 0.0483, 0.062)
    assert rates["ipca"] == pytest.approx(0.0683)

def test_stress_scenario_floor_at_zero():
    """Taxas não podem ficar negativas."""
    s = StressScenario("Queda extrema", delta_selic=-0.99)
    rates = s.apply_to_rates(0.1065, 0.1065, 0.0483, 0.062)
    assert rates["selic"] == 0.0

def test_standard_scenarios_count():
    assert len(STANDARD_SCENARIOS) == 9

def test_standard_scenarios_has_base():
    names = [s.name for s in STANDARD_SCENARIOS]
    assert "Base" in names

def test_standard_scenarios_colors_distinct():
    colors = [s.color for s in STANDARD_SCENARIOS]
    assert len(set(colors)) == len(colors), "cada cenário deve ter cor única"


# ── ScenarioComparison ────────────────────────────────────────────────────────

def _make_result(name: str, net_return: float, color: str = "#aaa") -> StressResult:
    return StressResult(
        scenario_name=name, bond_id="b1", bond_name="CDB Test",
        principal=10000, days=365,
        selic_applied=0.1065, cdi_applied=0.1065, ipca_applied=0.0483,
        gross_return=net_return + 0.02,
        net_return=net_return,
        net_value=10000 * (1 + net_return),
        ir_amount=10000 * 0.02 * 0.20,
        iof_amount=0.0,
        effective_rate=net_return,
        color=color,
    )

def test_scenario_comparison_base():
    comp = ScenarioComparison("b1", "CDB", 10000, 365, [
        _make_result("Base",          0.09),
        _make_result("SELIC +1 p.p.", 0.10),
        _make_result("SELIC -1 p.p.", 0.08),
    ])
    assert comp.base_result.net_return  == pytest.approx(0.09)
    assert comp.best_result.net_return  == pytest.approx(0.10)
    assert comp.worst_result.net_return == pytest.approx(0.08)

def test_scenario_comparison_max_drawdown():
    comp = ScenarioComparison("b1", "CDB", 10000, 365, [
        _make_result("Base",  0.09),
        _make_result("Crise", 0.05),
    ])
    assert comp.max_drawdown_pct == pytest.approx(4.0)

def test_scenario_comparison_empty():
    comp = ScenarioComparison("b1", "CDB", 10000, 365, [])
    assert comp.base_result  is None
    assert comp.worst_result is None
    assert comp.max_drawdown_pct == 0.0

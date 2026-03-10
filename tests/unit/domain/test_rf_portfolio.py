"""Testes unitários — Carteira RF + Diversificação (Sprint 30)"""

from __future__ import annotations

from datetime import date

from finanalytics_ai.domain.fixed_income.portfolio import (
    DiversificationReport,
    RFHolding,
    RFPortfolio,
)


def _holding(hid, indexer, issuer, invested, ir_exempt=False, maturity=None):
    return RFHolding(
        holding_id=hid,
        portfolio_id="p1",
        bond_id="b1",
        bond_name=f"Bond {hid}",
        bond_type="CDB",
        indexer=indexer,
        issuer=issuer,
        invested=invested,
        rate_annual=0.12,
        rate_pct_indexer=False,
        purchase_date=date(2024, 1, 1),
        maturity_date=maturity,
        ir_exempt=ir_exempt,
    )


def _portfolio(holdings):
    return RFPortfolio("p1", "user1", "Teste", holdings)


def test_total_invested():
    p = _portfolio([_holding("1", "CDI", "Itaú", 5000), _holding("2", "IPCA", "Governo", 3000)])
    assert p.total_invested == 8000


def test_allocation_by_indexer():
    p = _portfolio([_holding("1", "CDI", "Itaú", 6000), _holding("2", "IPCA", "Governo", 4000)])
    alloc = p.allocation_by_indexer()
    assert alloc["CDI"] == 60.0
    assert alloc["IPCA"] == 40.0


def test_allocation_by_issuer():
    p = _portfolio(
        [
            _holding("1", "CDI", "Itaú", 4000),
            _holding("2", "CDI", "Itaú", 2000),
            _holding("3", "IPCA", "Governo", 4000),
        ]
    )
    alloc = p.allocation_by_issuer()
    assert alloc["Itaú"] == 60.0
    assert alloc["Governo"] == 40.0


def test_ir_exempt_pct():
    p = _portfolio(
        [
            _holding("1", "CDI", "Itaú", 6000, ir_exempt=False),
            _holding("2", "CDI", "BTG", 4000, ir_exempt=True),
        ]
    )
    assert p.ir_exempt_pct() == 40.0


def test_avg_rate_weighted():
    p = _portfolio(
        [
            RFHolding("1", "p1", "b1", "CDB", "CDB", "CDI", "Itaú", 8000, 0.10, False, date(2024, 1, 1)),
            RFHolding("2", "p1", "b2", "LCI", "LCI", "CDI", "BTG", 2000, 0.20, False, date(2024, 1, 1)),
        ]
    )
    # (8000×0.10 + 2000×0.20) / 10000 = 1200/10000 = 0.12 → 12%
    assert abs(p.avg_rate() - 12.0) < 0.001


def test_matured_holdings_excluded():
    past = date(2020, 1, 1)
    p = _portfolio(
        [
            _holding("1", "CDI", "Itaú", 5000),
            _holding("2", "CDI", "BTG", 3000, maturity=past),
        ]
    )
    assert len(p.active_holdings) == 1
    assert len(p.matured_holdings) == 1


def test_diversification_score_excellent():
    p = _portfolio(
        [
            _holding("1", "CDI", "Itaú", 3000, ir_exempt=False),
            _holding("2", "IPCA", "Governo", 2000, ir_exempt=False),
            _holding("3", "SELIC", "BB", 2000, ir_exempt=True),
            _holding("4", "CDI", "BTG", 2000, ir_exempt=True),
            _holding("5", "PREFIXADO", "XP", 1000, ir_exempt=False),
        ]
    )
    report = DiversificationReport.build(p)
    assert report.n_indexers == 4
    assert report.n_issuers == 5
    assert report.score >= 60  # boa ou excelente


def test_diversification_alerts_concentration():
    p = _portfolio(
        [
            _holding("1", "CDI", "Itaú", 9000),
            _holding("2", "CDI", "BTG", 1000),
        ]
    )
    report = DiversificationReport.build(p)
    issuer_alerts = [a for a in report.alerts if a.alert_type == "issuer"]
    assert any(a.name == "Itaú" and a.pct == 90.0 for a in issuer_alerts)


def test_diversification_recommendations_not_empty():
    p = _portfolio([_holding("1", "CDI", "Itaú", 10000)])
    report = DiversificationReport.build(p)
    assert len(report.recommendations) > 0


def test_holding_days_held():
    h = _holding("1", "CDI", "Itaú", 5000)
    assert h.days_held >= 0


def test_holding_is_matured_false():
    future = date(2099, 12, 31)
    h = _holding("1", "CDI", "Itaú", 5000, maturity=future)
    assert not h.is_matured


def test_holding_to_dict_keys():
    h = _holding("1", "CDI", "Itaú", 5000)
    d = h.to_dict()
    for key in ["holding_id", "bond_name", "invested", "rate_annual", "indexer", "ir_exempt"]:
        assert key in d

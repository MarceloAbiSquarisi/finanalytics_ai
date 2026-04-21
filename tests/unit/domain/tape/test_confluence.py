"""
tests/unit/domain/tape/test_confluence.py
------------------------------------------
Testes unitários da ConflunceEngine.

Cobertura:
    - Sinal LONG claro (C/V alto, saldo positivo, velocidade institucional)
    - Sinal SHORT claro (C/V baixo, saldo negativo)
    - Neutro (equilíbrio)
    - Dados insuficientes (poucos trades, mercado lento)
    - Conflito de fatores (C/V comprador, saldo vendedor)
    - Thresholds customizados via ConflunceConfig
    - Score normalizado dentro do range [0, 100]
    - is_actionable apenas para FORTE/MODERADO/EXTREMO
"""

from __future__ import annotations

import pytest

from finanalytics_ai.domain.tape.confluence import (
    ConflunceConfig,
    ConflunceEngine,
    Direction,
    Strength,
)


@pytest.fixture
def engine() -> ConflunceEngine:
    return ConflunceEngine()


def _eval(
    engine: ConflunceEngine,
    *,
    ticker: str = "WINFUT",
    ratio_cv: float = 1.0,
    saldo_fluxo: float = 0.0,
    trades_por_min: float = 20.0,
    total_trades: int = 100,
    vol_compra: float = 5000.0,
    vol_venda: float = 5000.0,
):
    return engine.evaluate(
        ticker=ticker,
        ratio_cv=ratio_cv,
        saldo_fluxo=saldo_fluxo,
        trades_por_min=trades_por_min,
        total_trades=total_trades,
        vol_compra=vol_compra,
        vol_venda=vol_venda,
    )


class TestLongSignal:
    def test_strong_long(self, engine):
        """C/V=2.0 + saldo 30% vol + institucional -> score ~68, MODERADO/FORTE."""
        sig = _eval(
            engine,
            ratio_cv=2.0,
            saldo_fluxo=3000.0,
            vol_compra=8000.0,
            vol_venda=2000.0,
            trades_por_min=60.0,
            total_trades=200,
        )
        assert sig.is_valid
        assert sig.direction == Direction.LONG
        assert sig.strength in (Strength.FORTE, Strength.MODERADO, Strength.EXTREMO)
        assert sig.score >= 60.0
        assert sig.is_actionable

    def test_very_strong_long(self, engine):
        """C/V extremo + saldo dominante -> score >= 70, FORTE."""
        sig = _eval(
            engine,
            ratio_cv=3.0,
            saldo_fluxo=8000.0,
            vol_compra=12000.0,
            vol_venda=2000.0,
            trades_por_min=80.0,
            total_trades=400,
        )
        assert sig.direction == Direction.LONG
        assert sig.strength in (Strength.FORTE, Strength.EXTREMO)
        assert sig.score >= 70.0

    def test_moderate_long(self, engine):
        """C/V=1.4 + saldo 10% vol + normal -> score ~48, FRACO."""
        sig = _eval(
            engine,
            ratio_cv=1.4,
            saldo_fluxo=1000.0,
            vol_compra=6000.0,
            vol_venda=4000.0,
            trades_por_min=25.0,
            total_trades=80,
        )
        assert sig.is_valid
        assert sig.direction == Direction.LONG
        assert sig.score >= 40.0

    def test_cv_just_above_threshold(self, engine):
        """C/V=1.31 (limiar) + saldo 1% vol -> score fraco ~37."""
        sig = _eval(engine, ratio_cv=1.31, saldo_fluxo=100.0, vol_compra=5100.0, vol_venda=4900.0)
        assert sig.direction == Direction.LONG
        assert 30.0 <= sig.score <= 55.0


class TestShortSignal:
    def test_strong_short(self, engine):
        sig = _eval(
            engine,
            ratio_cv=0.4,
            saldo_fluxo=-4000.0,
            vol_compra=2000.0,
            vol_venda=8000.0,
            trades_por_min=70.0,
            total_trades=300,
        )
        assert sig.is_valid
        assert sig.direction == Direction.SHORT
        assert sig.strength in (Strength.FORTE, Strength.EXTREMO)
        assert sig.score >= 70.0

    def test_cv_just_below_threshold(self, engine):
        """C/V=0.76 + saldo -1% vol -> score ~37, FRACO."""
        sig = _eval(engine, ratio_cv=0.76, saldo_fluxo=-100.0, vol_compra=4900.0, vol_venda=5100.0)
        assert sig.direction == Direction.SHORT
        assert 30.0 <= sig.score <= 55.0


class TestNeutral:
    def test_balanced_cv(self, engine):
        sig = _eval(engine, ratio_cv=1.0, saldo_fluxo=0.0)
        assert sig.direction == Direction.NEUTRO
        assert not sig.is_actionable

    def test_conflicting_factors_low_score(self, engine):
        """C/V comprador mas saldo vendedor — conflito reduz score."""
        sig = _eval(
            engine,
            ratio_cv=1.5,  # comprador
            saldo_fluxo=-2000.0,  # mas saldo vendedor
            vol_compra=4000.0,
            vol_venda=6000.0,
        )
        # Fatores conflitantes → direção incerta e score baixo
        assert sig.score < 70.0


class TestInvalidSignals:
    def test_insufficient_trades(self, engine):
        sig = _eval(engine, total_trades=3)
        assert not sig.is_valid
        assert "insuficientes" in sig.reason
        assert sig.score == 0.0

    def test_market_too_slow(self, engine):
        sig = _eval(engine, trades_por_min=1.0, total_trades=50)
        assert not sig.is_valid
        assert "lento" in sig.reason

    def test_invalid_not_actionable(self, engine):
        sig = _eval(engine, total_trades=1)
        assert not sig.is_actionable


class TestScoreRange:
    def test_score_always_0_to_100(self, engine):
        """Score nunca sai do range 0-100 independente dos valores."""
        cases = [
            (99.0, 1_000_000.0, 500.0),  # C/V extremo
            (0.01, -1_000_000.0, 500.0),  # Venda extrema
            (1.0, 0.0, 0.0),  # Neutro
            (1.3, 0.0, 3.0),  # Limiar C/V
        ]
        for ratio_cv, saldo, tpm in cases:
            sig = _eval(
                engine,
                ratio_cv=ratio_cv,
                saldo_fluxo=saldo,
                trades_por_min=tpm,
                total_trades=100,
                vol_compra=max(saldo, 0) + 5000,
                vol_venda=max(-saldo, 0) + 5000,
            )
            assert 0.0 <= sig.score <= 100.0, f"score={sig.score} fora do range"


class TestCustomConfig:
    def test_stricter_cv_threshold(self):
        """Config mais rígida exige C/V > 1.5 para sinal comprador."""
        strict = ConflunceEngine(ConflunceConfig(cv_bullish_threshold=1.5))
        sig = _eval(
            strict,
            ratio_cv=1.35,  # abaixo do novo threshold
            saldo_fluxo=2000.0,
            vol_compra=7000.0,
            vol_venda=3000.0,
        )
        # C/V 1.35 não atinge 1.5 → região neutra no fator cv
        cv_factor = next(f for f in sig.factors if f.name == "cv_ratio")
        assert cv_factor.direction == Direction.NEUTRO

    def test_higher_min_trades(self):
        engine = ConflunceEngine(ConflunceConfig(min_trades=50))
        sig = _eval(engine, total_trades=20, ratio_cv=2.0)
        assert not sig.is_valid


class TestToDict:
    def test_valid_signal_dict_keys(self, engine):
        sig = _eval(
            engine,
            ratio_cv=1.8,
            saldo_fluxo=2000.0,
            vol_compra=7000.0,
            vol_venda=3000.0,
            trades_por_min=40.0,
        )
        d = sig.to_dict()
        assert {
            "ticker",
            "score",
            "direction",
            "strength",
            "actionable",
            "valid",
            "reason",
            "factors",
        } == set(d.keys())
        assert len(d["factors"]) == 3
        assert all("name" in f and "score" in f for f in d["factors"])

    def test_invalid_signal_dict(self, engine):
        sig = _eval(engine, total_trades=1)
        d = sig.to_dict()
        assert d["valid"] is False
        assert d["factors"] == []

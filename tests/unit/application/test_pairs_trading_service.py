"""
Testes do PairsTradingService (R3.2.B.1).

Cobertura:
  - Sem pares ativos -> []
  - Filtro Bonferroni rejeita -> blocked_by_filter=True, action=NONE
  - Filtro half-life rejeita -> blocked_by_filter=True
  - Candles ausentes -> NONE com reason="missing_candles"
  - Candles desalinhados (poucos) -> NONE com reason
  - Z-score nao calculavel (historico constante) -> NONE
  - Z baixo + posicao NONE -> NONE
  - Z alto positivo + posicao NONE -> OPEN_SHORT_SPREAD com legs corretos
  - Z baixo negativo + posicao NONE -> OPEN_LONG_SPREAD
  - Posicao SHORT_SPREAD + Z exit -> CLOSE
  - Posicao LONG_SPREAD + |Z|>stop -> STOP

Mocks: stub PairsRepository, CandleFetcher, PositionState. Synthetic
data construida p/ produzir Z-score conhecido.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from finanalytics_ai.application.services.pairs_trading_service import (
    PairsServiceConfig,
    evaluate_active_pairs,
)
from finanalytics_ai.domain.pairs import (
    PairAction,
    PairPosition,
    PairThresholds,
)
from finanalytics_ai.domain.pairs.entities import ActivePair

# ── Stubs ────────────────────────────────────────────────────────────────────


class _StubRepo:
    def __init__(self, pairs: list[ActivePair]) -> None:
        self._pairs = pairs

    def get_active_pairs(self, *, min_test_date: date | None = None) -> list[ActivePair]:
        return list(self._pairs)


class _StubCandles:
    def __init__(self, closes_by_ticker: dict[str, list[float]]) -> None:
        self._closes = closes_by_ticker

    def fetch_closes(self, ticker: str, n: int) -> list[float] | None:
        c = self._closes.get(ticker)
        if c is None:
            return None
        return c[-n:]


class _StubState:
    def __init__(self, positions: dict[str, PairPosition] | None = None) -> None:
        self._pos = positions or {}

    def get(self, pair_key: str) -> PairPosition:
        return self._pos.get(pair_key, PairPosition.NONE)


def _pair(
    ticker_a: str = "CMIN3",
    ticker_b: str = "VALE3",
    beta: float = 0.1,
    p_value: float = 0.001,
    half_life: float = 20.0,
) -> ActivePair:
    return ActivePair(
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        beta=beta,
        rho=0.5,
        p_value_adf=p_value,
        half_life=half_life,
        lookback_days=504,
        last_test_date=date.today(),
    )


# ── Empty repo ────────────────────────────────────────────────────────────────


def test_no_active_pairs_returns_empty() -> None:
    out = evaluate_active_pairs(
        repo=_StubRepo([]),
        candles=_StubCandles({}),
        position_state=_StubState(),
        n_pairs_tested=28,
    )
    assert out == []


# ── Filtros ──────────────────────────────────────────────────────────────────


class TestFilters:
    def test_bonferroni_blocks(self) -> None:
        # p=0.04 em 28 testes -> alpha_eff=0.0018 -> rejeita
        p = _pair(p_value=0.04)
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": [10.0] * 60, "VALE3": [100.0] * 60}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert len(out) == 1
        assert out[0].blocked_by_filter is True
        assert out[0].action == PairAction.NONE
        assert "bonferroni" in out[0].reason

    def test_half_life_too_short_blocks(self) -> None:
        p = _pair(half_life=2.0)  # < 5d default
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": [10.0] * 60, "VALE3": [100.0] * 60}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].blocked_by_filter is True
        assert "half_life_too_short" in out[0].reason

    def test_half_life_too_long_blocks(self) -> None:
        p = _pair(half_life=50.0)  # > 30d default
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": [10.0] * 60, "VALE3": [100.0] * 60}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].blocked_by_filter is True
        assert "half_life_too_long" in out[0].reason


# ── Candles missing / insufficient ──────────────────────────────────────────


class TestCandles:
    def test_missing_candles_a(self) -> None:
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"VALE3": [100.0] * 60}),  # falta CMIN3
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.NONE
        assert out[0].reason == "missing_candles"

    def test_insufficient_aligned_closes(self) -> None:
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": [10.0] * 10, "VALE3": [100.0] * 10}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.NONE
        assert "insufficient_aligned" in out[0].reason


# ── Z-score degenerado ──────────────────────────────────────────────────────


def test_constant_spread_returns_zscore_none() -> None:
    """Closes constantes -> std=0 -> zscore=None."""
    p = _pair()
    out = evaluate_active_pairs(
        repo=_StubRepo([p]),
        # Spread = A - 0.1 * B = const se A e B sao const proporcionais
        candles=_StubCandles({"CMIN3": [10.0] * 70, "VALE3": [100.0] * 70}),
        position_state=_StubState(),
        n_pairs_tested=28,
    )
    assert out[0].z is None
    assert out[0].action == PairAction.NONE
    assert "zscore_undefined" in out[0].reason


# ── Decisões ─────────────────────────────────────────────────────────────────


def _build_closes_for_z(
    target_z: float,
    n: int = 70,
    beta: float = 0.1,
    spread_mean: float = 0.0,
    spread_std: float = 1.0,
    seed: int = 42,
) -> tuple[list[float], list[float]]:
    """
    Constroi closes (A, B) tais que o ULTIMO spread = mean + target_z * std.
    Spread_history (todos menos o ultimo) tem mean=spread_mean, std=spread_std
    aproximadamente. Os primeiros (n-1) spreads sao ruido normal centrado em
    spread_mean.
    """
    rng = np.random.default_rng(seed)
    b = np.linspace(100.0, 110.0, n)
    history_spreads = rng.normal(spread_mean, spread_std, size=n - 1)
    # Forca ultimo spread = spread_mean + z * (sample_std calculado)
    # Aproximacao: depois de gerar history, calcular sample_std e ajustar
    last_spread_target = spread_mean + target_z * spread_std
    # Construir A: a_t = beta * b_t + spread_t
    spreads = list(history_spreads) + [last_spread_target]
    a = [beta * b[t] + spreads[t] for t in range(n)]
    return list(a), list(b)


class TestDecisions:
    def test_z_low_returns_none(self) -> None:
        a, b = _build_closes_for_z(target_z=0.5, n=70)
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": a, "VALE3": b}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].z is not None
        assert abs(out[0].z) < 2.0  # confirma z modesto
        assert out[0].action == PairAction.NONE

    def test_z_high_opens_short_spread(self) -> None:
        a, b = _build_closes_for_z(target_z=3.0, n=70)
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": a, "VALE3": b}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.OPEN_SHORT_SPREAD
        assert out[0].leg_a_side == "sell"  # vende A
        assert out[0].leg_b_side == "buy"  # compra B
        assert out[0].z > 2.0

    def test_z_low_negative_opens_long_spread(self) -> None:
        a, b = _build_closes_for_z(target_z=-3.0, n=70)
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": a, "VALE3": b}),
            position_state=_StubState(),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.OPEN_LONG_SPREAD
        assert out[0].leg_a_side == "buy"  # compra A
        assert out[0].leg_b_side == "sell"  # vende B

    def test_short_spread_position_z_exit_closes(self) -> None:
        a, b = _build_closes_for_z(target_z=0.3, n=70)  # < z_exit
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": a, "VALE3": b}),
            position_state=_StubState({"CMIN3-VALE3": PairPosition.SHORT_SPREAD}),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.CLOSE
        # Reverse legs nao sao preenchidos pelo service (caller decide)
        assert out[0].leg_a_side is None

    def test_long_spread_position_extreme_z_stops(self) -> None:
        a, b = _build_closes_for_z(target_z=4.5, n=70)  # > z_stop
        p = _pair()
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"CMIN3": a, "VALE3": b}),
            position_state=_StubState({"CMIN3-VALE3": PairPosition.LONG_SPREAD}),
            n_pairs_tested=28,
        )
        assert out[0].action == PairAction.STOP

    def test_pair_key_uses_canonical_order(self) -> None:
        a, b = _build_closes_for_z(target_z=0.0, n=70)
        p = _pair(ticker_a="ABEV3", ticker_b="VALE3")  # alfabetico
        out = evaluate_active_pairs(
            repo=_StubRepo([p]),
            candles=_StubCandles({"ABEV3": a, "VALE3": b}),
            position_state=_StubState({"ABEV3-VALE3": PairPosition.SHORT_SPREAD}),
            n_pairs_tested=28,
        )
        assert out[0].current_position == PairPosition.SHORT_SPREAD
        assert out[0].snapshot["pair_key"] == "ABEV3-VALE3"


# ── Multiple pairs ──────────────────────────────────────────────────────────


def test_multiple_pairs_independent_decisions() -> None:
    a1, b1 = _build_closes_for_z(target_z=3.0, n=70)
    a2, b2 = _build_closes_for_z(target_z=0.0, n=70)
    pair1 = _pair(ticker_a="AAAA3", ticker_b="BBBB3")  # alfabetico
    pair2 = _pair(ticker_a="CCCC3", ticker_b="DDDD3")
    out = evaluate_active_pairs(
        repo=_StubRepo([pair1, pair2]),
        candles=_StubCandles({"AAAA3": a1, "BBBB3": b1, "CCCC3": a2, "DDDD3": b2}),
        position_state=_StubState(),
        n_pairs_tested=28,
    )
    assert len(out) == 2
    assert out[0].action == PairAction.OPEN_SHORT_SPREAD
    assert out[1].action == PairAction.NONE


# ── Custom config ────────────────────────────────────────────────────────────


def test_custom_thresholds_respect() -> None:
    """z_entry custom deve mover boundary."""
    a, b = _build_closes_for_z(target_z=1.5, n=70)
    p = _pair()
    # Default z_entry=2.0 -> z=1.5 nao abre. Custom z_entry=1.0 -> abre.
    cfg = PairsServiceConfig(
        z_thresholds=PairThresholds(z_exit=0.3, z_entry=1.0, z_stop=3.0),
    )
    out = evaluate_active_pairs(
        repo=_StubRepo([p]),
        candles=_StubCandles({"CMIN3": a, "VALE3": b}),
        position_state=_StubState(),
        n_pairs_tested=28,
        config=cfg,
    )
    assert out[0].action == PairAction.OPEN_SHORT_SPREAD

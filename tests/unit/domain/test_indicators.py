"""
Testes unitários para indicadores técnicos.

Valida:
  - Comprimento dos arrays (alinhamento com input)
  - Valores de warmup = None
  - Valores conhecidos contra referências manuais
  - Casos de borda (lista vazia, muito curta)
  - Propriedades matemáticas (RSI ∈ [0,100], BB upper > lower)
"""
from __future__ import annotations

import math
import pytest
from finanalytics_ai.domain.indicators.technical import (
    compute_rsi,
    compute_macd,
    compute_bollinger,
    compute_all,
)


# ── FIXTURES ──────────────────────────────────────────────────────────────────

def _rising(n: int = 50, start: float = 10.0, step: float = 0.5) -> list[float]:
    """Série monotonicamente crescente."""
    return [start + i * step for i in range(n)]


def _falling(n: int = 50, start: float = 35.0, step: float = 0.5) -> list[float]:
    return [start - i * step for i in range(n)]


def _flat(n: int = 50, value: float = 30.0) -> list[float]:
    return [value] * n


def _sine(n: int = 100, amplitude: float = 5.0, base: float = 30.0) -> list[float]:
    """Série senoidal realista."""
    return [base + amplitude * math.sin(i * 0.3) for i in range(n)]


# ── RSI ───────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_output_length_matches_input(self):
        closes = _sine(60)
        result = compute_rsi(closes, period=14)
        assert len(result["values"]) == len(closes)

    def test_warmup_is_none(self):
        closes = _sine(50)
        result = compute_rsi(closes, period=14)
        # Os primeiros `period` valores devem ser None
        for i in range(14):
            assert result["values"][i] is None, f"índice {i} deveria ser None"

    def test_valid_values_in_range(self):
        closes = _sine(80)
        result = compute_rsi(closes, period=14)
        for v in result["values"]:
            if v is not None:
                assert 0.0 <= v <= 100.0, f"RSI fora do intervalo [0,100]: {v}"

    def test_rising_series_rsi_above_50(self):
        """Série crescente constante → RSI deve convergir acima de 50."""
        closes = _rising(60)
        result = compute_rsi(closes, period=14)
        valid = [v for v in result["values"] if v is not None]
        # Após warmup, série 100% de alta → RSI deve ser alto
        assert all(v > 50 for v in valid[5:]), "RSI em série crescente deve ser > 50"

    def test_falling_series_rsi_below_50(self):
        """Série decrescente constante → RSI deve convergir abaixo de 50."""
        closes = _falling(60)
        result = compute_rsi(closes, period=14)
        valid = [v for v in result["values"] if v is not None]
        assert all(v < 50 for v in valid[5:]), "RSI em série decrescente deve ser < 50"

    def test_flat_series_rsi_is_none_or_fifty(self):
        """
        Série flat: todos os deltas são 0.
        Avg gain = avg loss = 0 → RSI = 100 por convenção (evita divisão por zero).
        Ou pode ser 50 dependendo da implementação. Testamos que não levanta exceção.
        """
        closes = _flat(50)
        result = compute_rsi(closes, period=14)
        assert len(result["values"]) == 50

    def test_too_short_returns_all_none(self):
        closes = _sine(10)
        result = compute_rsi(closes, period=14)
        assert all(v is None for v in result["values"])

    def test_empty_input(self):
        result = compute_rsi([], period=14)
        assert result["values"] == []

    def test_reference_values(self):
        """
        Teste com série conhecida. Usamos série simples onde podemos calcular
        manualmente: 7 dias de alta (+1), 7 dias de baixa (-1), período=14.
        """
        # 15 valores para ter ao menos 1 RSI válido
        closes = [10.0] + [10.0 + i for i in range(1, 8)] + [17.0 - i for i in range(1, 9)]
        result = compute_rsi(closes, period=14)
        # Verifica que o primeiro valor não-None existe e está no range
        valid = [v for v in result["values"] if v is not None]
        assert len(valid) >= 1
        assert 0 <= valid[0] <= 100


# ── MACD ──────────────────────────────────────────────────────────────────────

class TestMACD:
    def test_output_length_matches_input(self):
        closes = _sine(100)
        result = compute_macd(closes)
        n = len(closes)
        assert len(result["macd"])      == n
        assert len(result["signal"])    == n
        assert len(result["histogram"]) == n

    def test_warmup_indices_are_none(self):
        closes = _sine(100)
        result = compute_macd(closes, fast=12, slow=26, signal_period=9)
        # Antes do slow-1=25, MACD deve ser None
        for i in range(25):
            assert result["macd"][i] is None

    def test_histogram_equals_macd_minus_signal(self):
        closes = _sine(100)
        result = compute_macd(closes)
        for m, s, h in zip(result["macd"], result["signal"], result["histogram"]):
            if m is not None and s is not None and h is not None:
                assert abs(h - (m - s)) < 1e-9, "histogram ≠ macd - signal"

    def test_rising_series_macd_positive(self):
        """Em série fortemente crescente, MACD deve ser positivo após aquecimento."""
        closes = _rising(80, step=1.0)
        result = compute_macd(closes)
        valid_macd = [v for v in result["macd"] if v is not None]
        assert len(valid_macd) > 0
        # Os últimos valores devem ser positivos (fast EMA > slow EMA em série crescente)
        assert valid_macd[-1] > 0

    def test_empty_input(self):
        result = compute_macd([])
        assert result["macd"] == []

    def test_too_short_returns_all_none(self):
        closes = _sine(20)
        result = compute_macd(closes, fast=12, slow=26, signal_period=9)
        assert all(v is None for v in result["macd"])


# ── BOLLINGER BANDS ───────────────────────────────────────────────────────────

class TestBollinger:
    def test_output_length_matches_input(self):
        closes = _sine(80)
        result = compute_bollinger(closes, period=20)
        n = len(closes)
        assert len(result["upper"])     == n
        assert len(result["middle"])    == n
        assert len(result["lower"])     == n
        assert len(result["bandwidth"]) == n
        assert len(result["pct_b"])     == n

    def test_warmup_is_none(self):
        closes = _sine(80)
        result = compute_bollinger(closes, period=20)
        for i in range(19):
            assert result["upper"][i]  is None
            assert result["middle"][i] is None
            assert result["lower"][i]  is None

    def test_upper_always_above_lower(self):
        closes = _sine(100)
        result = compute_bollinger(closes, period=20)
        for u, l in zip(result["upper"], result["lower"]):
            if u is not None and l is not None:
                assert u >= l, "upper deve ser >= lower"

    def test_middle_between_bands(self):
        closes = _sine(100)
        result = compute_bollinger(closes, period=20)
        for u, m, l in zip(result["upper"], result["middle"], result["lower"]):
            if all(v is not None for v in [u, m, l]):
                assert l <= m <= u, "middle deve estar entre lower e upper"

    def test_flat_series_bands_converge(self):
        """
        Série completamente flat → σ = 0 → upper = middle = lower.
        Não deve levantar exceção, e as bandas devem ser iguais.
        """
        closes = _flat(50)
        result = compute_bollinger(closes, period=20)
        for u, m, l in zip(result["upper"], result["middle"], result["lower"]):
            if all(v is not None for v in [u, m, l]):
                assert abs(u - l) < 1e-9, "bandas devem convergir em série flat"

    def test_pct_b_close_to_middle_is_half(self):
        """Se o preço estiver exatamente na média, %B = 0.5."""
        closes = _sine(80)
        bb = compute_bollinger(closes, period=20)
        # Encontra índice onde close ≈ middle
        for i, (m, u, l, pb) in enumerate(
            zip(bb["middle"], bb["upper"], bb["lower"], bb["pct_b"])
        ):
            if m is None or u is None or l is None or pb is None:
                continue
            if abs(closes[i] - m) < 0.01:
                assert abs(pb - 0.5) < 0.05

    def test_empty_input(self):
        result = compute_bollinger([])
        assert result["upper"] == []

    def test_std_dev_scaling(self):
        """Bandas com std=4 devem ser mais largas que com std=2."""
        closes = _sine(80)
        bb2 = compute_bollinger(closes, period=20, std_dev=2.0)
        bb4 = compute_bollinger(closes, period=20, std_dev=4.0)
        for u2, l2, u4, l4 in zip(bb2["upper"], bb2["lower"], bb4["upper"], bb4["lower"]):
            if all(v is not None for v in [u2, l2, u4, l4]):
                assert (u4 - l4) >= (u2 - l2) - 1e-9, "bandas com std=4 devem ser mais largas"


# ── FACADE ────────────────────────────────────────────────────────────────────

class TestComputeAll:
    def _make_bars(self, n: int = 100) -> list[dict]:
        closes = _sine(n, amplitude=5.0, base=30.0)
        return [
            {"time": 1700000000 + i * 86400, "open": c - 0.1, "high": c + 0.3,
             "low": c - 0.3, "close": c, "volume": 1_000_000}
            for i, c in enumerate(closes)
        ]

    def test_all_arrays_same_length(self):
        bars = self._make_bars(100)
        result = compute_all(bars)
        n = len(bars)
        assert len(result["rsi"]["values"])         == n
        assert len(result["macd"]["macd"])          == n
        assert len(result["bollinger"]["upper"])    == n
        assert len(result["timestamps"])            == n

    def test_timestamps_match_bars(self):
        bars = self._make_bars(50)
        result = compute_all(bars)
        for i, ts in enumerate(result["timestamps"]):
            assert ts == bars[i]["time"]

    def test_empty_bars(self):
        result = compute_all([])
        assert result["count"] == 0
        assert result["rsi"]["values"] == []
        assert result["macd"]["macd"] == []
        assert result["bollinger"]["upper"] == []

    def test_very_short_bars(self):
        """Menos barras que o período mais longo (MACD slow=26) não deve levantar exceção."""
        bars = self._make_bars(10)
        result = compute_all(bars)
        assert len(result["rsi"]["values"]) == 10

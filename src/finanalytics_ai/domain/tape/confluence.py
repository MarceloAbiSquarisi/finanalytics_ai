"""
finanalytics_ai.domain.tape.confluence
---------------------------------------
Engine de confluência de sinais de tape reading.

Score 0-100 composto por três fatores:
    1. Ratio C/V     — pressão direcional (peso 40%)
    2. Saldo de Fluxo — acumulação/distribuição (peso 40%)
    3. Velocidade    — intensidade institucional (peso 20%)

Cada fator é normalizado para 0-100 individualmente,
depois combinado com os pesos acima.

Interpretação:
    score >= 70  → sinal FORTE
    score >= 50  → sinal MODERADO
    score >= 30  → sinal FRACO
    score <  30  → NEUTRO (sem operação)

Direção:
    LONG   → compradores dominam (ratio_cv > 1.0, saldo > 0)
    SHORT  → vendedores dominam  (ratio_cv < 1.0, saldo < 0)
    NEUTRO → conflito ou dados insuficientes

Design decisions:
    - Lógica 100% pura — sem I/O, sem estado externo
    - Thresholds configuráveis via ConflunceConfig
    - Cada fator gera um FactorScore com razão legível (auditoria/debug)
    - Sem herança — composição via dataclasses
    - Testável com TapeMetrics sintético, sem dependências de infra
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple


# ── Enums ─────────────────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG   = "LONG"
    SHORT  = "SHORT"
    NEUTRO = "NEUTRO"


class Strength(str, Enum):
    EXTREMO  = "EXTREMO"   # score >= 85
    FORTE    = "FORTE"     # score >= 70
    MODERADO = "MODERADO"  # score >= 50
    FRACO    = "FRACO"     # score >= 30
    NEUTRO   = "NEUTRO"    # score <  30


# ── Configuração ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConflunceConfig:
    """Thresholds configuráveis da engine. Imutável por design."""

    # C/V: ratio acima deste valor = pressão compradora
    cv_bullish_threshold: float = 1.3
    # C/V: ratio abaixo deste valor = pressão vendedora
    cv_bearish_threshold: float = 0.77  # ~1/1.3

    # Saldo de fluxo: magnitude considerada "forte" (contratos/lotes)
    flow_strong_volume: float = 5_000.0

    # Velocidade: trades/min considerado "institucional"
    speed_institutional: float = 50.0
    # Velocidade mínima para o sinal ser relevante
    speed_min_relevant: float = 3.0

    # Pesos de cada fator (devem somar 1.0)
    weight_cv: float    = 0.40
    weight_flow: float  = 0.40
    weight_speed: float = 0.20

    # Mínimo de trades para considerar o sinal válido
    min_trades: int = 5


# ── Tipos internos ─────────────────────────────────────────────────────────────

class FactorScore(NamedTuple):
    """Score normalizado (0-100) de um fator individual."""
    name: str
    raw_value: float       # valor bruto (ratio, saldo, tpm)
    score: float           # 0-100
    direction: Direction   # contribuição direcional deste fator
    reason: str            # texto legível para debug/UI


# ── Sinal de saída ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConflunceSignal:
    """
    Sinal de confluência calculado para um ticker.
    Imutável — calculado uma vez por snapshot de TapeMetrics.
    """
    ticker: str
    score: float           # 0-100 ponderado
    direction: Direction
    strength: Strength
    factors: list[FactorScore]
    is_valid: bool         # False se dados insuficientes
    reason: str            # motivo de invalidação (se is_valid=False)

    @property
    def is_actionable(self) -> bool:
        """True se o sinal justifica uma entrada."""
        return self.is_valid and self.strength in (
            Strength.FORTE, Strength.MODERADO, Strength.EXTREMO
        )

    def to_dict(self) -> dict:
        return {
            "ticker":    self.ticker,
            "score":     round(self.score, 1),
            "direction": self.direction.value,
            "strength":  self.strength.value,
            "actionable": self.is_actionable,
            "valid":     self.is_valid,
            "reason":    self.reason,
            "factors": [
                {
                    "name":      f.name,
                    "raw":       round(f.raw_value, 3),
                    "score":     round(f.score, 1),
                    "direction": f.direction.value,
                    "reason":    f.reason,
                }
                for f in self.factors
            ],
        }


# ── Engine ─────────────────────────────────────────────────────────────────────

class ConflunceEngine:
    """
    Engine de cálculo de confluência.

    Uso:
        engine = ConflunceEngine()
        signal = engine.evaluate(metrics)

    Para thresholds customizados:
        engine = ConflunceEngine(ConflunceConfig(cv_bullish_threshold=1.5))
    """

    def __init__(self, config: ConflunceConfig | None = None) -> None:
        self.config = config or ConflunceConfig()

    def evaluate(
        self,
        ticker: str,
        ratio_cv: float,
        saldo_fluxo: float,
        trades_por_min: float,
        total_trades: int,
        vol_compra: float = 0.0,
        vol_venda: float = 0.0,
    ) -> ConflunceSignal:
        """
        Calcula o sinal de confluência a partir das métricas do tape.

        Aceita os campos individuais de TapeMetrics para manter a engine
        desacoplada do TapeService (sem import circular).
        """
        cfg = self.config

        # ── Validação de dados mínimos ────────────────────────────────────────
        if total_trades < cfg.min_trades:
            return self._invalid(
                ticker,
                f"dados insuficientes: {total_trades} trades (mínimo {cfg.min_trades})"
            )

        if trades_por_min < cfg.speed_min_relevant:
            return self._invalid(
                ticker,
                f"mercado muito lento: {trades_por_min:.1f} trades/min"
            )

        # ── Fator 1: C/V Ratio ────────────────────────────────────────────────
        cv_factor = self._score_cv(ratio_cv)

        # ── Fator 2: Saldo de Fluxo ───────────────────────────────────────────
        flow_factor = self._score_flow(saldo_fluxo, vol_compra, vol_venda)

        # ── Fator 3: Velocidade ───────────────────────────────────────────────
        speed_factor = self._score_speed(trades_por_min)

        factors = [cv_factor, flow_factor, speed_factor]

        # ── Score ponderado ───────────────────────────────────────────────────
        weighted = (
            cv_factor.score    * cfg.weight_cv    +
            flow_factor.score  * cfg.weight_flow  +
            speed_factor.score * cfg.weight_speed
        )

        # ── Direção por votação ponderada ─────────────────────────────────────
        direction = self._resolve_direction(factors)

        # ── Força ─────────────────────────────────────────────────────────────
        strength = self._resolve_strength(weighted, direction)

        return ConflunceSignal(
            ticker=ticker,
            score=round(weighted, 2),
            direction=direction,
            strength=strength,
            factors=factors,
            is_valid=True,
            reason="",
        )

    # ── Scoring individual ────────────────────────────────────────────────────

    def _score_cv(self, ratio_cv: float) -> FactorScore:
        """
        Normaliza o ratio C/V para 0-100.
        Região neutra: 0.77–1.30 → score baixo
        Extremos: ratio > 3 ou < 0.33 → score 100
        """
        cfg = self.config

        if ratio_cv >= cfg.cv_bullish_threshold:
            # Comprador: mapeia [1.3, 3.0] → [50, 100]
            raw_norm = (ratio_cv - cfg.cv_bullish_threshold) / (3.0 - cfg.cv_bullish_threshold)
            score = 50.0 + 50.0 * min(raw_norm, 1.0)
            direction = Direction.LONG
            reason = f"C/V={ratio_cv:.2f} — compradores pressionando"

        elif ratio_cv <= cfg.cv_bearish_threshold:
            # Vendedor: mapeia [0.77, 0.0] → [50, 100]
            raw_norm = (cfg.cv_bearish_threshold - ratio_cv) / cfg.cv_bearish_threshold
            score = 50.0 + 50.0 * min(raw_norm, 1.0)
            direction = Direction.SHORT
            reason = f"C/V={ratio_cv:.2f} — vendedores pressionando"

        else:
            # Neutro: [0.77, 1.30] → [0, 50] (zona de equilíbrio)
            distance = abs(ratio_cv - 1.0)
            max_distance = max(
                cfg.cv_bullish_threshold - 1.0,
                1.0 - cfg.cv_bearish_threshold,
            )
            score = 40.0 * (distance / max_distance)
            direction = Direction.NEUTRO
            reason = f"C/V={ratio_cv:.2f} — equilíbrio entre compradores e vendedores"

        return FactorScore("cv_ratio", ratio_cv, score, direction, reason)

    def _score_flow(
        self,
        saldo_fluxo: float,
        vol_compra: float,
        vol_venda: float,
    ) -> FactorScore:
        """
        Normaliza o saldo de fluxo para 0-100.
        Usa raiz quadrada para suavizar outliers de volume.
        """
        cfg = self.config
        total_vol = vol_compra + vol_venda

        if total_vol == 0:
            return FactorScore(
                "saldo_fluxo", 0.0, 0.0, Direction.NEUTRO,
                "sem volume negociado"
            )

        # Normaliza pelo volume total para ser relativo (não absoluto)
        pct_saldo = saldo_fluxo / total_vol  # -1.0 a +1.0

        # Suaviza com sqrt para não explodir em ticks de volume alto
        magnitude = math.sqrt(abs(pct_saldo)) * math.copysign(1, pct_saldo)
        score = min(abs(magnitude) * 100.0, 100.0)

        if saldo_fluxo > 0:
            direction = Direction.LONG
            reason = f"saldo={saldo_fluxo:+.0f} — acumulação ({pct_saldo*100:.1f}% do volume)"
        elif saldo_fluxo < 0:
            direction = Direction.SHORT
            reason = f"saldo={saldo_fluxo:+.0f} — distribuição ({pct_saldo*100:.1f}% do volume)"
        else:
            direction = Direction.NEUTRO
            reason = "saldo nulo — fluxo perfeitamente equilibrado"

        return FactorScore("saldo_fluxo", saldo_fluxo, score, direction, reason)

    def _score_speed(self, trades_por_min: float) -> FactorScore:
        """
        Normaliza velocidade para 0-100.
        Velocidade alta = institucional = sinal mais confiável.
        Não tem direção própria — apenas amplifica os outros fatores.
        """
        cfg = self.config

        # Mapeia [3, 150+] → [0, 100] com log para suavizar
        if trades_por_min <= 0:
            score = 0.0
        else:
            log_norm = math.log1p(trades_por_min) / math.log1p(cfg.speed_institutional * 2)
            score = min(log_norm * 100.0, 100.0)

        if trades_por_min >= cfg.speed_institutional:
            label = "institucional"
        elif trades_por_min >= 20:
            label = "normal"
        else:
            label = "varejista"

        return FactorScore(
            "velocidade",
            trades_por_min,
            score,
            Direction.NEUTRO,  # velocidade não tem direção
            f"{trades_por_min:.1f} trades/min ({label})",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_direction(self, factors: list[FactorScore]) -> Direction:
        """
        Votação ponderada dos fatores direcionais.
        Velocidade é neutra — não vota na direção.
        """
        cfg = self.config
        weights = {"cv_ratio": cfg.weight_cv, "saldo_fluxo": cfg.weight_flow}
        long_weight = sum(
            weights.get(f.name, 0) for f in factors if f.direction == Direction.LONG
        )
        short_weight = sum(
            weights.get(f.name, 0) for f in factors if f.direction == Direction.SHORT
        )
        if long_weight > short_weight and long_weight >= 0.3:
            return Direction.LONG
        if short_weight > long_weight and short_weight >= 0.3:
            return Direction.SHORT
        return Direction.NEUTRO

    def _resolve_strength(self, score: float, direction: Direction) -> Strength:
        if direction == Direction.NEUTRO:
            return Strength.NEUTRO
        if score >= 85:
            return Strength.EXTREMO
        if score >= 70:
            return Strength.FORTE
        if score >= 50:
            return Strength.MODERADO
        if score >= 30:
            return Strength.FRACO
        return Strength.NEUTRO

    def _invalid(self, ticker: str, reason: str) -> ConflunceSignal:
        return ConflunceSignal(
            ticker=ticker,
            score=0.0,
            direction=Direction.NEUTRO,
            strength=Strength.NEUTRO,
            factors=[],
            is_valid=False,
            reason=reason,
        )

"""
Entities do dominio pares (R3.2.B).

ActivePair representa um par cointegrado pronto p/ ser avaliado em runtime
pela PairsTradingStrategy. Diferente do CointegrationResult (em
cointegration.py) que e' o resultado bruto do screening — ActivePair tem
apenas o subset que a strategy precisa: identificacao + beta (p/ residuos)
+ half-life (filtro), sem residuals/p_value.

PairEvaluation e' a saida da strategy: decisao + dados de auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from finanalytics_ai.domain.pairs.strategy_logic import PairAction, PairPosition


@dataclass(frozen=True)
class ActivePair:
    """Par cointegrado ativo lido de cointegrated_pairs."""

    ticker_a: str
    ticker_b: str
    beta: float  # OLS A ~ B (sem intercept)
    rho: float  # correlacao Pearson (info-only)
    p_value_adf: float  # ADF p-value mais recente
    half_life: float | None  # dias (None se nao mean-reverte)
    lookback_days: int  # janela do screening
    last_test_date: date


@dataclass(frozen=True)
class PairEvaluation:
    """Saida da strategy para 1 par em 1 evaluation cycle."""

    pair: ActivePair
    z: float | None  # Z-score atual (None se historico insuficiente)
    action: PairAction
    current_position: PairPosition
    reason: str  # explicacao p/ log/audit
    blocked_by_filter: bool = False  # True se filtro estatistico/economico bloqueou
    # Detalhes da ordem (preenchido quando action e' OPEN_*).
    # leg_a/leg_b serao usados pelo dispatcher dual-leg em R3.2.B.2.
    leg_a_side: str | None = None  # 'buy'|'sell'
    leg_b_side: str | None = None
    snapshot: dict = field(default_factory=dict)  # contexto livre p/ audit

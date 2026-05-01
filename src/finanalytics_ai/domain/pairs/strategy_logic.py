"""
Pairs trading — lógica pura de decisão (R3.2.A).

Funções puras (sem DB, sem I/O) para o pipeline de pairs trading. Composição:

  1. compute_zscore: spread atual normalizado por (mean, std) do historico
     do spread. Z-score mede "quão longe do equilibrio" estamos hoje.

  2. PairPosition: estado da posicao (NONE, LONG_A_SHORT_B, SHORT_A_LONG_B).
     NONE = sem posicao aberta.

  3. PairAction: decisao a tomar (NONE, OPEN_LONG_SPREAD, OPEN_SHORT_SPREAD,
     CLOSE, STOP). LONG_SPREAD = comprar A, vender B (aposta que spread sobe);
     SHORT_SPREAD = vender A, comprar B (aposta que spread cai).

  4. decide_pair_action: state machine. Entrada quando |Z| > z_entry e
     posicao = NONE. Saida quando |Z| < z_exit e posicao != NONE. Stop
     forcado quando |Z| > z_stop.

  5. apply_bonferroni: ajusta p_threshold p/ multiple testing (28 pares ->
     0.05/28 = 0.0018). Critico p/ evitar spurious cointegration tradeable.

  6. validate_pair_filters: aceita um par cointegrado se passa Bonferroni
     e half_life esta na janela [min, max] (default 5-30 dias).

Convencoes:
  - Z-score positivo = A > beta*B (A relativamente caro vs B)
    -> SHORT_SPREAD aposta na convergencia (vende A, compra B)
  - Z-score negativo = A < beta*B (A relativamente barato vs B)
    -> LONG_SPREAD aposta na convergencia (compra A, vende B)
  - "spread" aqui = residual Engle-Granger = A_t - beta*B_t
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


# ── Constantes default (sobreescritas por config_json da strategy) ───────────

DEFAULT_Z_ENTRY: float = 2.0  # |Z| > 2 abre posicao
DEFAULT_Z_EXIT: float = 0.5  # |Z| < 0.5 fecha (mean reversion)
DEFAULT_Z_STOP: float = 4.0  # |Z| > 4 stop loss forcado
DEFAULT_MIN_HALF_LIFE: float = 5.0  # dias — abaixo: ruido
DEFAULT_MAX_HALF_LIFE: float = 30.0  # dias — acima: capital preso por muito tempo
DEFAULT_BONFERRONI_ALPHA: float = 0.05


# ── Enums ────────────────────────────────────────────────────────────────────


class PairPosition(str, Enum):
    """Estado atual da posicao no par."""

    NONE = "NONE"
    LONG_SPREAD = "LONG_SPREAD"  # long A, short B (apostando spread sobe)
    SHORT_SPREAD = "SHORT_SPREAD"  # short A, long B (apostando spread cai)


class PairAction(str, Enum):
    """Decisao a tomar a partir do estado + Z-score atual."""

    NONE = "NONE"  # nada a fazer (HOLD)
    OPEN_LONG_SPREAD = "OPEN_LONG_SPREAD"
    OPEN_SHORT_SPREAD = "OPEN_SHORT_SPREAD"
    CLOSE = "CLOSE"  # mean reversion atingida
    STOP = "STOP"  # |Z| explodiu - stop loss


# ── Z-score ──────────────────────────────────────────────────────────────────


def compute_zscore(spread_history: list[float], current_spread: float) -> float | None:
    """
    Z-score = (current_spread - mean_history) / std_history.

    Usa estimadores classicos:
      - mean = media simples
      - std = sample std (ddof=1)

    Retorna None se:
      - historico vazio ou tamanho < 2
      - std == 0 (todos os spreads iguais — degenerado)

    Nota: caller deve usar janela razoavel (e.g. ultimos 60-252 dias).
    Janela curta demais -> Z muito volatil; longa demais -> demora p/ adaptar.
    """
    if len(spread_history) < 2:
        return None
    n = len(spread_history)
    mean = sum(spread_history) / n
    var = sum((s - mean) ** 2 for s in spread_history) / (n - 1)
    if var <= 0:
        return None
    std = math.sqrt(var)
    return (current_spread - mean) / std


# ── State machine ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairThresholds:
    """Parametros de decisao p/ um par."""

    z_entry: float = DEFAULT_Z_ENTRY
    z_exit: float = DEFAULT_Z_EXIT
    z_stop: float = DEFAULT_Z_STOP

    def __post_init__(self) -> None:
        if not (0 < self.z_exit < self.z_entry < self.z_stop):
            raise ValueError(
                f"thresholds devem satisfazer 0 < z_exit < z_entry < z_stop. "
                f"recebido: exit={self.z_exit}, entry={self.z_entry}, stop={self.z_stop}"
            )


def decide_pair_action(
    z: float,
    current_position: PairPosition,
    thresholds: PairThresholds | None = None,
) -> PairAction:
    """
    State machine: dado Z-score atual + posicao, decide acao.

    Regras:
      Sem posicao (NONE):
        - Z > z_entry  -> OPEN_SHORT_SPREAD (spread alto, aposta convergencia)
        - Z < -z_entry -> OPEN_LONG_SPREAD  (spread baixo, aposta convergencia)
        - senao        -> NONE

      Posicao SHORT_SPREAD (apostando Z cai):
        - |Z| > z_stop -> STOP (Z continuou subindo demais)
        - Z < z_exit   -> CLOSE (mean reversion atingida)
        - senao        -> NONE (aguarda)

      Posicao LONG_SPREAD (apostando Z sobe):
        - |Z| > z_stop -> STOP
        - Z > -z_exit  -> CLOSE
        - senao        -> NONE
    """
    th = thresholds or PairThresholds()

    if current_position == PairPosition.NONE:
        if z > th.z_entry:
            return PairAction.OPEN_SHORT_SPREAD
        if z < -th.z_entry:
            return PairAction.OPEN_LONG_SPREAD
        return PairAction.NONE

    # Stop tem prioridade — checar primeiro independente da direcao
    if abs(z) > th.z_stop:
        return PairAction.STOP

    if current_position == PairPosition.SHORT_SPREAD:
        # Apostamos que Z volta pra zero. Saimos quando cruza z_exit pra baixo.
        if z < th.z_exit:
            return PairAction.CLOSE
        return PairAction.NONE

    if current_position == PairPosition.LONG_SPREAD:
        # Apostamos que Z volta pra zero. Saimos quando cruza -z_exit pra cima.
        if z > -th.z_exit:
            return PairAction.CLOSE
        return PairAction.NONE

    return PairAction.NONE


# ── Filtros estatisticos / economicos ────────────────────────────────────────


def apply_bonferroni(alpha: float, n_pairs: int) -> float:
    """
    Ajuste Bonferroni p/ multiple testing.

    Sem ajuste: testar 28 pares com p=0.05 cada -> esperamos ~1.4 falsos
    positivos por pura sorte. Bonferroni divide alpha por N -> alpha_eff
    apertado o suficiente p/ controlar familywise error rate.

    Critico em pairs trading porque p_value baixo isolado nao garante
    cointegracao real — pode ser ruido em regime estavel.

    Trade-off: muito conservador (rejeita cointegracoes verdadeiras de
    pairs com edge real). FDR (Benjamini-Hochberg) seria menos conservador
    mas mais complexo. Para R3.2 MVP, Bonferroni e' bom o suficiente.
    """
    if n_pairs <= 0:
        return alpha
    return alpha / n_pairs


def validate_pair_filters(
    *,
    p_value: float,
    half_life: float | None,
    n_pairs_tested: int,
    alpha: float = DEFAULT_BONFERRONI_ALPHA,
    min_half_life: float = DEFAULT_MIN_HALF_LIFE,
    max_half_life: float = DEFAULT_MAX_HALF_LIFE,
) -> tuple[bool, str | None]:
    """
    Valida se um par e' tradeable. Retorna (ok, reason_if_blocked).

    Filtros aplicados em sequencia:
      1. Bonferroni p_value: p_value < (alpha / n_pairs_tested)
      2. Half-life: min <= half_life <= max
         - half_life None  -> reject (sem mean-reversion estimavel)
         - half_life curto -> ruido (intraday flip-flops)
         - half_life longo -> capital preso, drawdown longo

    Retorna (False, "reason_blocked") na primeira falha.
    """
    alpha_bonf = apply_bonferroni(alpha, n_pairs_tested)
    if p_value >= alpha_bonf:
        return (
            False,
            f"bonferroni_failed: p={p_value:.4f} >= alpha_eff={alpha_bonf:.6f} "
            f"({n_pairs_tested} pairs tested)",
        )

    if half_life is None:
        return (False, "half_life_undefined: spread sem mean-reversion")

    if half_life < min_half_life:
        return (
            False,
            f"half_life_too_short: {half_life:.1f}d < {min_half_life:.1f}d "
            f"(noise/intraday flip-flops)",
        )

    if half_life > max_half_life:
        return (
            False,
            f"half_life_too_long: {half_life:.1f}d > {max_half_life:.1f}d "
            f"(capital preso, drawdown estendido)",
        )

    return (True, None)

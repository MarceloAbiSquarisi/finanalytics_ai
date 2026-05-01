"""
Pairs Trading Service (R3.2.B.1) — orchestra a logica pura.

Pipeline:
  1. Repository.get_active_pairs() -> lista de ActivePair
  2. Para cada par, validate_pair_filters (Bonferroni + half-life range)
  3. Se passou filtros, fetch closes recentes p/ A e B
  4. compute_residuals + compute_zscore
  5. decide_pair_action(z, current_position) -> PairAction
  6. Empacota tudo em PairEvaluation com leg_a_side/leg_b_side preenchidos
     se OPEN

Ainda NAO faz dispatch — isso fica em R3.2.B.2 (worker integration). Esta
camada e' apenas decisao + auditoria; quem chama decide o que fazer com
as evaluations (log, alert, dispatch).

Deps abstratas (Protocol) p/ ser totalmente testavel em isolamento:
  - PairsRepository (DB read)
  - CandleFetcher (HTTP fetch ou DB read direto)
  - PositionState (lookup de posicao atual por par_key)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog

from finanalytics_ai.domain.pairs import (
    DEFAULT_BONFERRONI_ALPHA,
    DEFAULT_MAX_HALF_LIFE,
    DEFAULT_MIN_HALF_LIFE,
    PairAction,
    PairPosition,
    PairThresholds,
    compute_residuals,
    compute_zscore,
    decide_pair_action,
    validate_pair_filters,
)
from finanalytics_ai.domain.pairs.entities import ActivePair, PairEvaluation
from finanalytics_ai.infrastructure.database.repositories.pairs_repository import (
    PairsRepository,
)

logger = structlog.get_logger(__name__)


class CandleFetcher(Protocol):
    """Port — retorna closes diarios alinhados (mais recente por ultimo)."""

    def fetch_closes(self, ticker: str, n: int) -> list[float] | None:
        """Retorna ate `n` closes mais recentes de `ticker`. None se falhar."""
        ...


class PositionState(Protocol):
    """Port — lookup de posicao corrente por chave do par."""

    def get(self, pair_key: str) -> PairPosition:
        """Retorna posicao atual ou NONE se nao tem registro."""
        ...


@dataclass(frozen=True)
class PairsServiceConfig:
    """Config single-source para o service."""

    z_thresholds: PairThresholds = PairThresholds()
    alpha: float = DEFAULT_BONFERRONI_ALPHA
    min_half_life: float = DEFAULT_MIN_HALF_LIFE
    max_half_life: float = DEFAULT_MAX_HALF_LIFE
    # Janela do historico do spread p/ Z-score. 60d e' standard:
    # - longo o suficiente p/ media estavel
    # - curto o suficiente p/ adaptar a regime change rapido
    zscore_lookback_days: int = 60
    # Numero minimo de closes alinhados p/ confiar no Z. Abaixo disso, SKIP.
    min_aligned_closes: int = 30


def _pair_key(pair: ActivePair) -> str:
    """Identificador canonico do par (ticker_a < ticker_b por construcao)."""
    return f"{pair.ticker_a}-{pair.ticker_b}"


def evaluate_active_pairs(
    *,
    repo: PairsRepository,
    candles: CandleFetcher,
    position_state: PositionState,
    n_pairs_tested: int,
    config: PairsServiceConfig | None = None,
) -> list[PairEvaluation]:
    """
    Pipeline completo. Retorna 1 PairEvaluation por par ativo no DB.

    n_pairs_tested e' usado pelo Bonferroni — caller informa quantos pares
    foram testados na ultima rodada de screening (e.g. 28). Em pratica
    sempre vem do mesmo job que populou cointegrated_pairs.

    Convencao de leg sides:
      OPEN_SHORT_SPREAD = vende A, compra B  (Z alto -> aposta queda)
        -> leg_a_side='sell', leg_b_side='buy'
      OPEN_LONG_SPREAD  = compra A, vende B  (Z baixo -> aposta alta)
        -> leg_a_side='buy', leg_b_side='sell'
      CLOSE: sentido reverso da posicao aberta — caller decide via
        current_position.
    """
    cfg = config or PairsServiceConfig()
    pairs = repo.get_active_pairs()
    if not pairs:
        logger.info("pairs_service.no_active_pairs")
        return []

    evaluations: list[PairEvaluation] = []

    for p in pairs:
        # 1. Filtros estatisticos/economicos
        ok, blocked_reason = validate_pair_filters(
            p_value=p.p_value_adf,
            half_life=p.half_life,
            n_pairs_tested=n_pairs_tested,
            alpha=cfg.alpha,
            min_half_life=cfg.min_half_life,
            max_half_life=cfg.max_half_life,
        )
        current_pos = position_state.get(_pair_key(p))
        if not ok:
            evaluations.append(
                PairEvaluation(
                    pair=p,
                    z=None,
                    action=PairAction.NONE,
                    current_position=current_pos,
                    reason=blocked_reason or "filter_failed",
                    blocked_by_filter=True,
                    snapshot={"pair_key": _pair_key(p)},
                )
            )
            continue

        # 2. Fetch closes A + B
        n_needed = cfg.zscore_lookback_days + 5
        closes_a = candles.fetch_closes(p.ticker_a, n_needed)
        closes_b = candles.fetch_closes(p.ticker_b, n_needed)
        if not closes_a or not closes_b:
            evaluations.append(
                PairEvaluation(
                    pair=p,
                    z=None,
                    action=PairAction.NONE,
                    current_position=current_pos,
                    reason="missing_candles",
                    snapshot={"pair_key": _pair_key(p)},
                )
            )
            continue

        # 3. Alinha pelo N mais recente em comum
        n = min(len(closes_a), len(closes_b))
        if n < cfg.min_aligned_closes:
            evaluations.append(
                PairEvaluation(
                    pair=p,
                    z=None,
                    action=PairAction.NONE,
                    current_position=current_pos,
                    reason=f"insufficient_aligned_closes ({n} < {cfg.min_aligned_closes})",
                    snapshot={"pair_key": _pair_key(p)},
                )
            )
            continue

        ca = closes_a[-n:]
        cb = closes_b[-n:]

        # 4. Spread + Z-score (excluindo ultimo ponto p/ historico,
        # ultimo ponto e' o "current spread")
        residuals = compute_residuals(ca, cb, p.beta)
        spread_history = list(residuals[:-1])
        current_spread = float(residuals[-1])
        z = compute_zscore(spread_history, current_spread)
        if z is None:
            evaluations.append(
                PairEvaluation(
                    pair=p,
                    z=None,
                    action=PairAction.NONE,
                    current_position=current_pos,
                    reason="zscore_undefined (historico ou std degenerado)",
                    snapshot={
                        "pair_key": _pair_key(p),
                        "current_spread": current_spread,
                    },
                )
            )
            continue

        # 5. Decide
        action = decide_pair_action(z, current_pos, cfg.z_thresholds)

        # 6. Determinar leg sides p/ OPEN
        leg_a, leg_b = _legs_for_action(action)

        evaluations.append(
            PairEvaluation(
                pair=p,
                z=z,
                action=action,
                current_position=current_pos,
                reason=_action_reason(action, z, current_pos, cfg.z_thresholds),
                leg_a_side=leg_a,
                leg_b_side=leg_b,
                snapshot={
                    "pair_key": _pair_key(p),
                    "z": z,
                    "current_spread": current_spread,
                    "spread_mean": sum(spread_history) / len(spread_history),
                    "history_size": len(spread_history),
                },
            )
        )

    return evaluations


def _legs_for_action(action: PairAction) -> tuple[str | None, str | None]:
    """Mapeia PairAction -> (leg_a_side, leg_b_side) ou (None, None)."""
    if action == PairAction.OPEN_SHORT_SPREAD:
        return ("sell", "buy")  # vende A, compra B
    if action == PairAction.OPEN_LONG_SPREAD:
        return ("buy", "sell")  # compra A, vende B
    # CLOSE/STOP precisam saber a posicao corrente — caller (worker em R3.2.B.2)
    # determina os sides reverters via current_position.
    return (None, None)


def _action_reason(
    action: PairAction,
    z: float,
    pos: PairPosition,
    thresholds: PairThresholds,
) -> str:
    if action == PairAction.NONE:
        return f"z={z:.2f} dentro da banda (entry={thresholds.z_entry})"
    if action == PairAction.OPEN_SHORT_SPREAD:
        return f"z={z:.2f} > {thresholds.z_entry} -> SHORT spread (vende A, compra B)"
    if action == PairAction.OPEN_LONG_SPREAD:
        return f"z={z:.2f} < -{thresholds.z_entry} -> LONG spread (compra A, vende B)"
    if action == PairAction.CLOSE:
        return f"z={z:.2f} cruzou exit ({thresholds.z_exit}) com pos={pos.value} -> mean reversion"
    if action == PairAction.STOP:
        return f"|z|={abs(z):.2f} > stop ({thresholds.z_stop}) com pos={pos.value} -> stop loss"
    return "unknown"

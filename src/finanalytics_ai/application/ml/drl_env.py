"""
drl_env.py — SCAFFOLD de ambiente DRL multi-asset (Sprint F8).

§6 melhorias_renda_fixa_2.md — DRL-PPO multi-asset (equities + DI1 +
Treasuries). Observation space expandido com features RF.

TODO para implementação completa:
  1. Gym/Gymnasium Env com:
     - reset(): snapshot inicial de features_daily_full + rates_features_daily.
     - step(action): aplica posições, computa P&L, avança 1 dia.
     - observation_space: dict/Box com features de 100 ativos + 30 features RF.
     - action_space: Box(-1, +1, shape=(103,)) = pesos em 100 stocks +
       DI1 1Y/2Y/5Y (3) + TLT/SHY (2) = 105. Ajustar conforme universo.
  2. Reward: Sharpe incremental - comissão - penalidade drawdown.
  3. Training: stable-baselines3 PPO, 10M steps, GPU.
  4. Evaluation: walk-forward, benchmark vs buy-and-hold + factor model.

Dependências adicionais:
    uv add gymnasium stable-baselines3 torch

Ver também:
  - src/finanalytics_ai/domain/backtesting/engine.py (engine determinístico)
  - scripts/mlstrategy_backtest.py (baseline ML sem RL)

Este arquivo é placeholder. Não importar em produção.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Observation features dict (§6 melhorias_renda_fixa_2.md)
RATE_OBSERVATIONS = [
    # DI1 por vértice
    "di1_1y_taxa",
    "di1_2y_taxa",
    "di1_5y_taxa",
    "di1_10y_taxa",
    "di1_1y_tsmom_3m",
    "di1_2y_tsmom_3m",
    "di1_5y_tsmom_3m",
    "di1_1y_tsmom_12m",
    "di1_2y_tsmom_12m",
    "di1_5y_tsmom_12m",
    # Fatores de curva
    "slope_1y_5y",
    "slope_2y_10y",
    "butterfly_1y_2y_5y",
    "butterfly_2y_3y_7y",
    "pc1_level",
    "pc2_slope",
    "pc3_curvature",
    # Carry e value
    "carry_roll_2y",
    "carry_roll_5y",
    "value_di1_2y_zscore",
    "value_ntnb_5y_zscore",
    # Macro monetário
    "monetary_regime",  # 0=easing / 1=neutro / 2=tightening
    "copom_dias_restantes",
    "copom_hawkish_proba",
    # US Treasury
    "us_2y_tsmom_3m",
    "us_10y_tsmom_3m",
    "us_slope_2y_10y",
    "carry_treasury_10y",
    "breakeven_5y_vs_focus",
]


@dataclass
class DRLConfig:
    n_stocks: int = 100
    n_rates_actions: int = 3  # DI1 1Y, 2Y, 5Y
    n_us_actions: int = 2  # TLT, SHY
    reward_alpha_rf: float = 0.3  # peso RF no reward
    drawdown_threshold: float = 0.10
    commission_pct: float = 0.001


class DRLMultiAssetEnv:
    """SCAFFOLD — ver TODO no docstring do módulo."""

    def __init__(self, config: DRLConfig | None = None) -> None:
        self.config = config or DRLConfig()
        self._step = 0

    def reset(self, seed: int | None = None) -> tuple[dict, dict]:
        self._step = 0
        # TODO: carregar snapshot de features_daily_full +
        # rates_features_daily + us_macro_daily para o primeiro dia de treino.
        return {}, {}

    def step(self, action: Any) -> tuple[dict, float, bool, bool, dict]:
        self._step += 1
        # TODO: aplicar action (vetor de pesos), avançar 1 dia, computar
        # reward, detectar episódio encerrado.
        return {}, 0.0, False, False, {}

    def close(self) -> None:
        pass


def reward_fn(
    pnl_equity: float, pnl_rates: float, drawdown: float, custo_tx: float, alpha: float = 0.3
) -> float:
    """Reward scaffold conforme §6."""
    pnl_total = (1 - alpha) * pnl_equity + alpha * pnl_rates

    denom = abs(pnl_total) + 1e-8  # vol local proxy
    sharpe_incr = pnl_total / denom
    custo = custo_tx * abs(pnl_total)
    penalidade_dd = max(0.0, drawdown - 0.10) * 10
    return float(sharpe_incr - custo - penalidade_dd)

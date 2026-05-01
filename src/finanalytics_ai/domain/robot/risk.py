"""
Risk Engine (R1 Phase 2).

Funcoes puras (sem DB, sem I/O) para ser testavel em isolamento e reusavel
em backtest. O worker chama estas funcoes; persistencia e responsabilidade
do auto_trader_worker.

Componentes:
  position_size_vol_target — Kelly fracionario sobre vol target anual.
  compute_atr               — ATR Wilder sobre N bars (default 14).
  compute_atr_levels        — TP/SL como N x ATR a partir do entry price.
  check_max_positions       — gate por classe de strategy.
  check_circuit_breaker     — gate por DD intra-day em %.

Convencoes:
  Vol annual = sigma_diario * sqrt(252).
  Vol target default 15% anual (10% conservador, 20% agressivo).
  Kelly fracionario default 0.25x — 1/4 do Kelly otimo, padrao da literatura
  (Thorp 2006, Lopez de Prado 2018) p/ reduzir ruido de estimacao.
  ATR Wilder smoothing convencional, mesmo do `domain/indicators/atr.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

# ── Constantes ────────────────────────────────────────────────────────────────

ANNUALIZATION_FACTOR: float = 252.0  # dias uteis B3
DEFAULT_TARGET_VOL: float = 0.15  # 15% ao ano
DEFAULT_KELLY_FRACTION: float = 0.25  # 1/4 do Kelly otimo
DEFAULT_MAX_POSITION_PCT: float = 0.10  # 10% do equity por posicao (cap)
DEFAULT_ATR_PERIOD: int = 14
DEFAULT_ATR_SL_MULT: float = 2.0  # SL = 2x ATR abaixo do entry (BUY)
DEFAULT_ATR_TP_MULT: float = 3.0  # TP = 3x ATR acima do entry — risk/reward 1.5
DEFAULT_DD_CIRCUIT_PCT: float = -2.0  # halt em DD <= -2% intra-day


@dataclass(frozen=True)
class SizingResult:
    """Resultado de position_size_vol_target."""

    qty: int  # quantidade arredondada para baixo (lots inteiros)
    notional: float  # qty * price
    capital_at_risk: float  # qty * sl_distance (se SL conhecido); 0 sem SL
    blocked: bool  # True se gates de risk impediram
    reason: str | None  # quando blocked, motivo


# ── Position sizing ──────────────────────────────────────────────────────────


def realized_vol_daily(returns: list[float]) -> float:
    """Std dev simples dos retornos diarios. Vazio -> 0."""
    if not returns or len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var)


def annualize_vol(daily_vol: float, factor: float = ANNUALIZATION_FACTOR) -> float:
    """sigma_diaria -> sigma_anual via sqrt(factor)."""
    return daily_vol * math.sqrt(factor)


def position_size_vol_target(
    *,
    capital: float,
    price: float,
    realized_vol_annual: float,
    target_vol: float = DEFAULT_TARGET_VOL,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    sl_distance: float | None = None,
    lot_size: int = 1,
) -> SizingResult:
    """
    Position size baseado em vol target anualizada + Kelly fracionario.

    Formula:
      raw_qty = (target_vol * capital * kelly_fraction) / (realized_vol * price)
      capped_qty = min(raw_qty, max_position_pct * capital / price)
      qty = floor(capped_qty / lot_size) * lot_size

    Vol baixa -> qty grande (oportunidade). Vol alta -> qty pequena (cuidado).
    Kelly fracionario reduz size em 75% — protege contra erro de estimacao
    de mu/sigma.

    Parametros:
      capital              — equity disponivel para alocar
      price                — preco do ticker (R$/contrato)
      realized_vol_annual  — sigma anualizada do ticker
      target_vol           — alvo de vol da carteira (default 15%)
      kelly_fraction       — fracao do Kelly otimo (default 0.25)
      max_position_pct     — cap absoluto por posicao (default 10% do equity)
      sl_distance          — opcional: distancia ate SL em valor absoluto.
                             Se fornecido, calcula capital_at_risk = qty*sl_distance.
      lot_size             — tamanho minimo de lote (1 acao, 5 mini, etc.)

    Retorna SizingResult com qty arredondado para baixo + diagnosticos.
    """
    if capital <= 0 or price <= 0:
        return SizingResult(
            qty=0, notional=0.0, capital_at_risk=0.0, blocked=True, reason="zero_inputs"
        )

    if realized_vol_annual <= 0:
        return SizingResult(
            qty=0,
            notional=0.0,
            capital_at_risk=0.0,
            blocked=True,
            reason="missing_realized_vol",
        )

    # Raw qty: vol target adjusted by Kelly fraction
    raw_qty = (target_vol * capital * kelly_fraction) / (realized_vol_annual * price)

    # Cap absoluto: nunca mais do que max_position_pct do equity
    max_qty_by_cap = (max_position_pct * capital) / price

    capped = min(raw_qty, max_qty_by_cap)
    qty_int = int(capped // lot_size) * lot_size

    if qty_int < lot_size:
        return SizingResult(
            qty=0,
            notional=0.0,
            capital_at_risk=0.0,
            blocked=True,
            reason=f"qty_below_lot_size (raw={raw_qty:.2f}, lot={lot_size})",
        )

    notional = qty_int * price
    risk = qty_int * abs(sl_distance) if sl_distance else 0.0

    return SizingResult(
        qty=qty_int,
        notional=notional,
        capital_at_risk=risk,
        blocked=False,
        reason=None,
    )


# ── ATR ──────────────────────────────────────────────────────────────────────


def compute_atr(bars: list[dict[str, Any]], period: int = DEFAULT_ATR_PERIOD) -> float:
    """
    Average True Range (Wilder smoothing) sobre os ultimos `period+1` bars.

    TR_i = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR  = SMA(TR, period) -> depois Wilder smoothing exponencial

    Retorna 0 se dados insuficientes — caller decide fallback.
    """
    if len(bars) < period + 1:
        return 0.0

    trs: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i].get("high", 0) or 0)
        l = float(bars[i].get("low", 0) or 0)
        prev_c = float(bars[i - 1].get("close", 0) or 0)
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    if len(trs) < period:
        return 0.0

    # Inicial: SMA dos primeiros `period` TRs
    atr = sum(trs[:period]) / period
    # Wilder smoothing nas barras subsequentes
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def compute_atr_levels(
    *,
    entry: float,
    side: str,
    atr: float,
    sl_mult: float = DEFAULT_ATR_SL_MULT,
    tp_mult: float = DEFAULT_ATR_TP_MULT,
) -> tuple[float | None, float | None]:
    """
    Calcula TP e SL como N x ATR a partir do entry price.

    BUY:  SL = entry - sl_mult*ATR ; TP = entry + tp_mult*ATR
    SELL: SL = entry + sl_mult*ATR ; TP = entry - tp_mult*ATR

    Sem ATR (<=0): retorna (None, None) — caller decide se trada sem niveis.
    """
    if atr <= 0 or entry <= 0:
        return (None, None)
    s = side.lower()
    if s.startswith("b"):  # BUY
        sl = max(entry - sl_mult * atr, 0.01)
        tp = entry + tp_mult * atr
    else:  # SELL
        sl = entry + sl_mult * atr
        tp = max(entry - tp_mult * atr, 0.01)
    return (tp, sl)


# ── Gates ────────────────────────────────────────────────────────────────────


def check_max_positions(current: int, limit: int) -> tuple[bool, str | None]:
    """True se OK para abrir nova posicao; (False, motivo) se atinge limite."""
    if current >= limit:
        return (False, f"max_positions_reached ({current}/{limit})")
    return (True, None)


def check_circuit_breaker(
    pnl_pct_today: float, threshold: float = DEFAULT_DD_CIRCUIT_PCT
) -> tuple[bool, str | None]:
    """True se OK; (False, motivo) se DD <= threshold (e.g. -2.0 = -2%)."""
    if pnl_pct_today <= threshold:
        return (
            False,
            f"circuit_breaker_dd ({pnl_pct_today:.2f}% <= {threshold:.2f}%)",
        )
    return (True, None)

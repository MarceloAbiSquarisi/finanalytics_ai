"""
Slippage model por classe de ativo.

Spec R5 (Melhorias.md): "Slippage realista: 0.05% round-trip ações líquidas,
2 ticks WDOFUT/WINFUT". Antes desta peça, o engine cobrava apenas
`commission_pct` flat — fantasia para futuros e otimista para small caps.

Modelo aplicado:
  - Futuros B3 (WDO/WIN/IND/DOL/DI/CCM/BGI/OZM): N_TICKS_FUTURE ticks por lado
    em valor absoluto (slippage cresce em barras de baixa liquidez ou rompimento
    rápido — modelo simplificado usa fixo).
  - Ações: SLIPPAGE_PCT_STOCK por lado (relativo ao preço).

Modelo ADV-aware (R5 follow-up, ativado por flag em run_backtest):
  - participation = trade_notional / ADV (Average Daily Volume notional)
  - multiplier = 1 + IMPACT_COEF * sqrt(participation), capado em MAX_ADV_MULT
  - Slippage final = base_slippage * multiplier
  - Funcao de raiz quadrada: convencao de market impact (Almgren-Chriss),
    impacto cresce sublinearmente com tamanho do trade.
  - Sem ADV (None) ou ADV<=0: fallback para modelo fixo.

Por que aplicar no preço (não na comissão):
  - `commission_pct` fica reservado para taxas reais (B3 + corretora + emolumentos).
  - Slippage é fricção de execução (gap entre o preço marcado e o preço efetivo).
  - Separar permite calibrar cada um independente.

Como o engine usa:
  - BUY:  effective_entry = close + slippage
  - SELL: effective_exit  = close - slippage
  - Trade.entry_price/exit_price registram o `effective_*`, então P&L já
    reflete o custo de execução em todas as métricas downstream.
"""

from __future__ import annotations

import math
from typing import Any

# Tick sizes oficiais B3 (referência: especificações de contratos B3)
# Futuros mini (WIN/WDO) e cheios (IND/DOL): tick é o menor incremento de preço.
TICK_SIZES: dict[str, float] = {
    "WDO": 0.5,  # mini-dolar
    "WIN": 5.0,  # mini-indice (5 pontos)
    "IND": 5.0,  # indice cheio
    "DOL": 0.5,  # dolar cheio
    "DI1": 0.005,  # taxa juros DI1 (0.005 = 0.5 ponto-base)
    "DI": 0.005,
    "CCM": 0.10,  # milho
    "BGI": 0.05,  # boi gordo (R$ 0,05/arroba)
    "OZM": 0.005,  # ouro mini
}

# Quantidade de ticks de slippage por lado (ida ou volta) para futuros.
# 2 ticks é o consenso para WDO/WIN em horario regular; pico de news/rompimento
# pode ser 5+. Para backtest "honesto" usamos 2 — pessimista o suficiente para
# nao gerar strategy fantasia, mas nao tao pessimista que mate edge real.
N_TICKS_FUTURE: int = 2

# Slippage percentual por lado para acoes liquidas. 0.05% (5 bps) e o tipico
# de liquido B3 (PETR4/VALE3/ITUB4 em horario regular). Acoes ilíquidas
# precisariam mais — modelo simples nao distingue por liquidez (futura
# extensao: lookup por ticker em watchlist por liquidez).
SLIPPAGE_PCT_STOCK: float = 0.0005


# Prefixos que identificam contratos de futuros B3. Comparacao por prefixo
# (ex: "WINM26" -> "WIN") cobre alias (WINFUT) e contratos mensais (WINK26).
_FUTURE_PREFIXES = tuple(TICK_SIZES.keys())


def _is_future(ticker: str) -> bool:
    """True se o ticker e contrato de futuros B3."""
    if not ticker:
        return False
    upper = ticker.upper()
    return any(upper.startswith(p) for p in _FUTURE_PREFIXES)


def _future_tick(ticker: str) -> float:
    """Tick size para o ticker (futuro). Default 0.01 se nao reconhecido."""
    upper = (ticker or "").upper()
    for prefix, tick in TICK_SIZES.items():
        if upper.startswith(prefix):
            return tick
    return 0.01


# ── ADV-aware (R5 follow-up) ──────────────────────────────────────────────────

# Coeficiente de impacto sqrt: slippage = base * (1 + IMPACT_COEF * sqrt(part)).
# 0.5 calibra para que participacao=4% (~tipica de instituicional) cause ~10% de
# acrescimo, e 25% (extremo) bata o cap MAX_ADV_MULT. Valor literatura academica
# (Almgren et al.) varia 0.5-1.5; escolhemos lower bound conservador.
IMPACT_COEF: float = 1.0

# Cap multiplicador. 5x significa que o pior caso considera 5*base — alem disso
# o modelo simples deixa de fazer sentido (precisaria simular ordem fragmentada).
MAX_ADV_MULT: float = 5.0


def compute_adv(bars: list[dict[str, Any]], idx: int, lookback: int = 20) -> float:
    """
    Average Daily Volume *notional* (em R$/USD) ate a barra idx (exclusivo).

    Notional = volume_shares * close_price. Usa janela [idx-lookback, idx).
    Sem look-ahead bias — a barra atual nao entra na media.

    Retorna 0.0 se janela insuficiente. Caller decide fallback (e.g. modelo fixo).
    """
    if idx < 1 or not bars:
        return 0.0
    start = max(0, idx - lookback)
    window = bars[start:idx]
    if not window:
        return 0.0
    notional_sum = 0.0
    n = 0
    for b in window:
        vol = float(b.get("volume", 0.0) or 0.0)
        close = float(b.get("close", 0.0) or 0.0)
        if vol > 0 and close > 0:
            notional_sum += vol * close
            n += 1
    return notional_sum / n if n > 0 else 0.0


def adv_multiplier(notional_trade: float, adv_notional: float) -> float:
    """
    Multiplicador de slippage por ADV-participation. Sqrt-impact capado em MAX_ADV_MULT.

    Sem ADV (<=0) ou trade sem notional -> 1.0 (modelo fixo).
    """
    if adv_notional <= 0 or notional_trade <= 0:
        return 1.0
    participation = notional_trade / adv_notional
    mult = 1.0 + IMPACT_COEF * math.sqrt(participation)
    return min(mult, MAX_ADV_MULT)


def slippage_amount(
    price: float,
    ticker: str,
    *,
    notional_trade: float | None = None,
    adv_notional: float | None = None,
) -> float:
    """
    Slippage absoluto (em R$) por lado, dado o preco e o ticker.

    Modelo base:
      - Futuros: N_TICKS_FUTURE * tick_size do contrato.
      - Acoes:   SLIPPAGE_PCT_STOCK * price.

    Modelo ADV-aware (opcional, ativo se notional_trade e adv_notional > 0):
      base * adv_multiplier(notional_trade, adv_notional).
      Aplica para futuros e acoes — para futuros, notional eh contracts*price.

    Sempre positivo. Aplicar com sinal correto (somar em BUY, subtrair em SELL).
    """
    if price <= 0:
        return 0.0
    if _is_future(ticker):
        base = N_TICKS_FUTURE * _future_tick(ticker)
    else:
        base = SLIPPAGE_PCT_STOCK * price

    if notional_trade is not None and adv_notional is not None:
        base *= adv_multiplier(notional_trade, adv_notional)

    return base


def apply_slippage(
    price: float,
    side: str,
    ticker: str,
    *,
    notional_trade: float | None = None,
    adv_notional: float | None = None,
) -> float:
    """
    Retorna o preco efetivo de execucao apos slippage.

    side='buy'  -> price + slippage (paga acima do close)
    side='sell' -> price - slippage (recebe abaixo do close)

    Para usar modelo ADV-aware, passe notional_trade e adv_notional. Sem eles,
    cai no modelo fixo (compat com chamadas legacy).
    """
    if price <= 0:
        return price
    s = slippage_amount(price, ticker, notional_trade=notional_trade, adv_notional=adv_notional)
    if side.lower().startswith("b"):
        return price + s
    return max(price - s, 0.0)

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


def slippage_amount(price: float, ticker: str) -> float:
    """
    Slippage absoluto (em R$) por lado, dado o preco e o ticker.

    Para futuros: N_TICKS_FUTURE * tick_size do contrato.
    Para acoes:   SLIPPAGE_PCT_STOCK * price.

    Sempre positivo. Aplicar com sinal correto (somar em BUY, subtrair em SELL).
    """
    if price <= 0:
        return 0.0
    if _is_future(ticker):
        return N_TICKS_FUTURE * _future_tick(ticker)
    return SLIPPAGE_PCT_STOCK * price


def apply_slippage(price: float, side: str, ticker: str) -> float:
    """
    Retorna o preco efetivo de execucao apos slippage.

    side='buy'  -> price + slippage (paga acima do close)
    side='sell' -> price - slippage (recebe abaixo do close)
    """
    if price <= 0:
        return price
    s = slippage_amount(price, ticker)
    if side.lower().startswith("b"):
        return price + s
    return max(price - s, 0.0)

"""
Screener de acoes — dominio puro, sem I/O.

Responsabilidades:
  1. Modelar dados fundamentalistas de um ativo (FundamentalData)
  2. Modelar criterios de filtro (FilterCriteria)
  3. Aplicar filtros e retornar ScreenerResult

Design decisions:

  FundamentalData com Optional[float]:
    Nem todos os ativos tem todos os indicadores (FIIs nao tem P/E,
    empresas sem lucro tem P/E negativo ou None, etc.).
    Filtros sobre campos None sao IGNORADOS — o ativo passa o filtro.
    Isso evita falsos negativos por dados faltantes, colocando o onus
    de interpretar sobre o usuario (que ve o campo como "-").

  FilterCriteria como dataclass com Optional[float] min/max:
    Cada indicador tem um intervalo [min, max]. None = sem limite.
    Ex: pe_max=15 significa P/E <= 15, pe_min=None sem minimo.
    Interface minimalista e extensivel sem condicional explosion.

  Score de ranking:
    Cada ativo recebe um score composito baseado nos filtros ativos.
    Indicadores onde "menor e melhor" (P/E, P/VP, divida) contribuem
    inversamente; onde "maior e melhor" (dividendo, ROE) contribuem
    diretamente. Score normalizado 0-100 para facilitar comparacao.

  Universo padrao (IBOV_UNIVERSE):
    Lista estatica dos ~60 principais ativos do Ibovespa.
    Evita dependencia de API para listar tickers — mais rapido e
    previsivel. Usuario pode complementar com tickers customizados.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any, Optional


# ── Universo padrao ───────────────────────────────────────────────────────────

IBOV_UNIVERSE: list[str] = [
    # Petroleo & Gas
    "PETR4", "PETR3", "PRIO3", "RECV3", "CSAN3",
    # Mineracao & Siderurgia
    "VALE3", "CSNA3", "GGBR4", "USIM5", "BRAP4",
    # Bancos
    "ITUB4", "BBDC4", "BBAS3", "SANB11", "BPAC11",
    # Seguros & Financeiro
    "BBSE3", "IRBR3", "PSSA3", "WIZC3",
    # Energia Eletrica
    "ELET3", "ELET6", "ENEV3", "ENGI11", "CPFE3", "NEOE3", "TAEE11",
    # Utilidades
    "SBSP3", "SAPR11", "CSMG3",
    # Varejo & Consumo
    "MGLU3", "VIIA3", "NTCO3", "SOMA3", "PETZ3", "ARZZ3", "SBFG3",
    # Alimentos & Bebidas
    "ABEV3", "BRFS3", "JBSS3", "MRFG3", "BEEF3",
    # Saude
    "RDOR3", "HAPV3", "GNDI3", "QUAL3", "FLRY3",
    # Tecnologia & Telecom
    "TOTS3", "LWSA3", "POSI3", "TIMS3", "VIVT3",
    # Logistica & Transporte
    "RAIL3", "CCRO3", "ECOR3", "GOLL4", "AZUL4",
    # Construcao & Real Estate
    "EZTC3", "MRVE3", "CYRE3", "EVEN3", "DIRR3",
    # Educacao
    "YDUQ3", "COGN3", "ANIM3",
    # Papel & Celulose
    "SUZB3", "KLBN11",
    # Industrial
    "WEGE3", "EMBR3", "ROMI3",
]


# ── FundamentalData ────────────────────────────────────────────────────────────

@dataclass
class FundamentalData:
    """
    Dados fundamentalistas de um ativo retornados pela BRAPI.

    Todos os indicadores sao Optional[float] — campos ausentes ficam None.

    Convencao de nomenclatura:
      pe    = Price/Earnings (P/L)
      pvp   = Price/Book Value (P/VP)
      dy    = Dividend Yield (%)
      roe   = Return on Equity (%)
      roic  = Return on Invested Capital (%)
      ebitda_margin = EBITDA / Receita (%)
      net_margin    = Lucro Liquido / Receita (%)
      debt_equity   = Divida Liquida / Patrimonio
      revenue_growth  = Crescimento de Receita YoY (%)
    """
    ticker:           str
    name:             str             = ""
    sector:           str             = ""
    price:            Optional[float] = None
    market_cap:       Optional[float] = None   # R$ (absoluto)
    pe:               Optional[float] = None   # P/L
    pvp:              Optional[float] = None   # P/VP
    dy:               Optional[float] = None   # Dividend Yield %
    roe:              Optional[float] = None   # ROE %
    roic:             Optional[float] = None   # ROIC %
    ebitda_margin:    Optional[float] = None   # %
    net_margin:       Optional[float] = None   # %
    debt_equity:      Optional[float] = None   # D/E ratio
    revenue_growth:   Optional[float] = None   # % YoY
    eps:              Optional[float] = None   # Earnings per Share
    high_52w:         Optional[float] = None
    low_52w:          Optional[float] = None
    volume:           Optional[float] = None

    def pct_from_low(self) -> Optional[float]:
        """Distancia percentual do preco atual em relacao a minima de 52 semanas."""
        if self.price and self.low_52w and self.low_52w > 0:
            return (self.price - self.low_52w) / self.low_52w * 100
        return None

    def pct_from_high(self) -> Optional[float]:
        """Distancia percentual do preco atual em relacao a maxima de 52 semanas."""
        if self.price and self.high_52w and self.high_52w > 0:
            return (self.price - self.high_52w) / self.high_52w * 100
        return None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            d[f.name] = round(v, 4) if isinstance(v, float) else v
        d["pct_from_low"]  = self.pct_from_low()
        d["pct_from_high"] = self.pct_from_high()
        if d["pct_from_low"]  is not None: d["pct_from_low"]  = round(d["pct_from_low"],  1)
        if d["pct_from_high"] is not None: d["pct_from_high"] = round(d["pct_from_high"], 1)
        return d


# ── FilterCriteria ────────────────────────────────────────────────────────────

@dataclass
class FilterCriteria:
    """
    Criterios de filtro para o screener.

    Cada campo representa um intervalo [min, max].
    None em qualquer extremo significa "sem limite nesse lado".

    Convencao: nome_min / nome_max para cada indicador.
    """
    pe_min:             Optional[float] = None
    pe_max:             Optional[float] = None
    pvp_min:            Optional[float] = None
    pvp_max:            Optional[float] = None
    dy_min:             Optional[float] = None
    dy_max:             Optional[float] = None
    roe_min:            Optional[float] = None
    roe_max:            Optional[float] = None
    roic_min:           Optional[float] = None
    roic_max:           Optional[float] = None
    ebitda_margin_min:  Optional[float] = None
    ebitda_margin_max:  Optional[float] = None
    net_margin_min:     Optional[float] = None
    net_margin_max:     Optional[float] = None
    debt_equity_max:    Optional[float] = None
    revenue_growth_min: Optional[float] = None
    market_cap_min:     Optional[float] = None   # R$ bilhoes
    market_cap_max:     Optional[float] = None
    sector:             Optional[str]   = None   # filtro por setor (substring)

    def is_empty(self) -> bool:
        """Retorna True se nenhum criterio foi definido."""
        for f in fields(self):
            if getattr(self, f.name) is not None:
                return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FilterCriteria":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


# ── apply_filters ─────────────────────────────────────────────────────────────

def _passes_range(
    value:    Optional[float],
    min_val:  Optional[float],
    max_val:  Optional[float],
) -> bool:
    """
    Verifica se value esta no intervalo [min_val, max_val].

    Se value e None, retorna True (dados ausentes nao desqualificam).
    Se min_val e max_val sao None, retorna True (sem restricao).
    """
    if value is None:
        return True
    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def apply_filters(
    stocks:   list[FundamentalData],
    criteria: FilterCriteria,
) -> list[FundamentalData]:
    """
    Filtra lista de FundamentalData segundo criterios.

    Retorna lista ordenada por score composito (maior primeiro).
    """
    result: list[FundamentalData] = []

    for stock in stocks:
        # Filtro por setor (case-insensitive substring)
        if criteria.sector:
            if criteria.sector.lower() not in stock.sector.lower():
                continue

        # Filtros de intervalo
        checks = [
            _passes_range(stock.pe,             criteria.pe_min,            criteria.pe_max),
            _passes_range(stock.pvp,            criteria.pvp_min,           criteria.pvp_max),
            _passes_range(stock.dy,             criteria.dy_min,            criteria.dy_max),
            _passes_range(stock.roe,            criteria.roe_min,           criteria.roe_max),
            _passes_range(stock.roic,           criteria.roic_min,          criteria.roic_max),
            _passes_range(stock.ebitda_margin,  criteria.ebitda_margin_min, criteria.ebitda_margin_max),
            _passes_range(stock.net_margin,     criteria.net_margin_min,    criteria.net_margin_max),
            _passes_range(stock.debt_equity,    None,                       criteria.debt_equity_max),
            _passes_range(stock.revenue_growth, criteria.revenue_growth_min, None),
            _passes_range(
                stock.market_cap / 1e9 if stock.market_cap else None,
                criteria.market_cap_min,
                criteria.market_cap_max,
            ),
        ]

        if all(checks):
            result.append(stock)

    # Ordena por score composito
    result.sort(key=lambda s: _composite_score(s), reverse=True)
    return result


def _composite_score(stock: FundamentalData) -> float:
    """
    Score composito para ranking de acoes.

    Logica:
      - Indicadores de rentabilidade (ROE, ROIC, margens) contribuem positivamente
      - Indicadores de valuation (P/E, P/VP) contribuem negativamente (menor = melhor)
      - Dividend Yield contribui positivamente
      - Divida contribui negativamente

    Score nao normalizado — usado apenas para ordenacao relativa.
    Campos None sao ignorados (tratados como neutros = 0).
    """
    score = 0.0

    if stock.roe  is not None and stock.roe  > 0: score += stock.roe  * 0.3
    if stock.roic is not None and stock.roic > 0: score += stock.roic * 0.3
    if stock.dy   is not None and stock.dy   > 0: score += stock.dy   * 0.5

    if stock.net_margin    is not None and stock.net_margin    > 0:
        score += stock.net_margin * 0.2
    if stock.ebitda_margin is not None and stock.ebitda_margin > 0:
        score += stock.ebitda_margin * 0.1
    if stock.revenue_growth is not None and stock.revenue_growth > 0:
        score += stock.revenue_growth * 0.1

    # Penalidades por valuation caro ou divida alta
    if stock.pe  is not None and stock.pe  > 0: score -= math.log(stock.pe)  * 0.5
    if stock.pvp is not None and stock.pvp > 0: score -= math.log(stock.pvp) * 0.5
    if stock.debt_equity is not None and stock.debt_equity > 0:
        score -= stock.debt_equity * 0.2

    return score


# ── ScreenerResult ────────────────────────────────────────────────────────────

@dataclass
class ScreenerResult:
    """Resultado do screener."""
    total_universe:  int
    total_passed:    int
    criteria:        dict[str, Any]
    stocks:          list[FundamentalData]
    errors:          list[dict[str, str]] = field(default_factory=list)
    sectors:         list[str]            = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_universe": self.total_universe,
            "total_passed":   self.total_passed,
            "criteria":       self.criteria,
            "stocks":         [s.to_dict() for s in self.stocks],
            "errors":         self.errors,
            "sectors":        sorted(self.sectors),
        }

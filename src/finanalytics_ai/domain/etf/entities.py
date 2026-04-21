"""
finanalytics_ai.domain.etf.entities
─────────────────────────────────────
Entidades de domínio para análise de ETFs.

Catálogo de ETFs:
  Mantemos um catálogo estático dos principais ETFs listados na B3.
  Cada ETF tem: ticker, nome, benchmark de referência, categoria,
  taxa de administração e moeda de exposição.

  Fonte: B3 + CVM (dados públicos).
  Atualização: manual por sprint (os dados mudam raramente).

ETFMetrics:
  Value object calculado a partir de série histórica de preços.
  Nunca persistido — sempre calculado on demand.
  Inclui retorno, volatilidade, Sharpe, drawdown máximo e VaR.

TrackingErrorResult:
  Mede o desvio do ETF em relação ao seu benchmark.
  Tracking error = std(retorno_etf - retorno_benchmark) × √252.
  Tracking difference = retorno_benchmark - retorno_etf (custo implícito).

RebalanceRecommendation:
  Dado um portfólio alvo (pesos desejados) e portfólio atual (valores),
  calcula quanto comprar/vender de cada ETF para rebalancear.
  Aceita tanto rebalanceamento puro quanto rebalanceamento com aporte.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Catálogo de ETFs ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ETFInfo:
    ticker: str
    name: str
    benchmark: str  # ticker do benchmark (ex: "^BVSP", "SPY")
    category: str  # "Ações BR", "Ações EUA", "Cripto", "Renda Fixa", etc.
    ter: float  # Taxa de administração anual (%, ex: 0.10)
    currency: str  # "BRL", "USD"
    description: str = ""

    @property
    def ter_pct(self) -> str:
        return f"{self.ter:.2f}%"


# Catálogo principal — ETFs listados na B3
ETF_CATALOG: list[ETFInfo] = [
    # ── Ações Brasil ──────────────────────────────────────────────────────────
    ETFInfo("BOVA11", "iShares Ibovespa", "^BVSP", "Ações BR", 0.10, "BRL", "Replica o Ibovespa"),
    ETFInfo(
        "SMAL11", "iShares Small Cap", "SMLL", "Ações BR", 0.50, "BRL", "Small caps brasileiras"
    ),
    ETFInfo(
        "DIVO11",
        "iShares Dividendos",
        "IDIV",
        "Ações BR",
        0.20,
        "BRL",
        "Ações com maiores dividendos",
    ),
    ETFInfo("FIND11", "iShares Financeiro", "IFNC", "Ações BR", 0.50, "BRL", "Setor financeiro B3"),
    ETFInfo(
        "MATB11",
        "iShares Materiais Básicos",
        "IMAT",
        "Ações BR",
        0.50,
        "BRL",
        "Materiais básicos B3",
    ),
    ETFInfo(
        "UTIP11", "iShares Utilidade Pública", "UTIL", "Ações BR", 0.50, "BRL", "Utilidades B3"
    ),
    ETFInfo(
        "ECOO11",
        "iShares Carbono Eficiente",
        "ICO2",
        "Ações BR",
        0.40,
        "BRL",
        "Empresas de baixo carbono",
    ),
    ETFInfo("BOVV11", "IT Now Ibovespa", "^BVSP", "Ações BR", 0.05, "BRL", "Ibovespa - menor TER"),
    ETFInfo(
        "XBOV11", "Xtrackers Ibovespa", "^BVSP", "Ações BR", 0.07, "BRL", "Ibovespa - Deutsche Bank"
    ),
    # ── Ações EUA ─────────────────────────────────────────────────────────────
    ETFInfo("IVVB11", "iShares S&P 500", "^GSPC", "Ações EUA", 0.23, "BRL", "S&P 500 em BRL"),
    ETFInfo("SPXI11", "IT Now S&P 500", "^GSPC", "Ações EUA", 0.15, "BRL", "S&P 500 em BRL"),
    ETFInfo("NASD11", "Invesco Nasdaq 100", "QQQ", "Ações EUA", 0.35, "BRL", "Nasdaq 100 em BRL"),
    ETFInfo(
        "ACWI11", "iShares MSCI ACWI", "ACWI", "Ações Global", 0.30, "BRL", "Mercado global ex-BR"
    ),
    ETFInfo("EURP11", "iShares Europa", "EZU", "Ações Global", 0.40, "BRL", "Mercado europeu"),
    # ── Cripto ────────────────────────────────────────────────────────────────
    ETFInfo(
        "HASH11",
        "Hashdex Cripto",
        "BTC-USD",
        "Cripto",
        0.95,
        "BRL",
        "Cesta cripto (BTC+ETH+outros)",
    ),
    ETFInfo("QBTC11", "QR Bitcoin", "BTC-USD", "Cripto", 1.30, "BRL", "Bitcoin puro"),
    ETFInfo("BITH11", "Hashdex Bitcoin", "BTC-USD", "Cripto", 0.75, "BRL", "Bitcoin — menor TER"),
    # ── Renda Fixa ────────────────────────────────────────────────────────────
    ETFInfo("NTNB11", "iShares NTN-B", "IMA-B", "Renda Fixa", 0.25, "BRL", "Tesouro IPCA+ (IMA-B)"),
    ETFInfo("IMAB11", "iShares IMA-B", "IMA-B", "Renda Fixa", 0.20, "BRL", "IMA-B completo"),
    ETFInfo(
        "B5P211", "iShares IMA-B 5+", "IMA-B5+", "Renda Fixa", 0.20, "BRL", "NTN-B prazo longo"
    ),
    ETFInfo("IRFM11", "iShares IRF-M", "IRF-M", "Renda Fixa", 0.20, "BRL", "Prefixados (IRF-M)"),
    # ── Commodities/Ouro ──────────────────────────────────────────────────────
    ETFInfo("GOLD11", "iShares Ouro", "GLD", "Commodities", 0.20, "BRL", "Ouro em BRL"),
    ETFInfo("OGLD11", "Trend ETF Ouro", "GLD", "Commodities", 0.30, "BRL", "Ouro — Trend"),
    # ── FIIs ──────────────────────────────────────────────────────────────────
    ETFInfo("XFIX11", "iShares FIIs", "IFIX", "FIIs", 0.30, "BRL", "Fundo de FIIs (IFIX)"),
    ETFInfo(
        "VISC11", "Vinci Shopping Centers", "IFIX", "FIIs", 0.00, "BRL", "FII tijolo — shoppings"
    ),
]

ETF_BY_TICKER: dict[str, ETFInfo] = {e.ticker: e for e in ETF_CATALOG}

ETF_CATEGORIES: list[str] = sorted({e.category for e in ETF_CATALOG})


def get_etf(ticker: str) -> ETFInfo | None:
    return ETF_BY_TICKER.get(ticker.upper())


def etfs_by_category(category: str) -> list[ETFInfo]:
    return [e for e in ETF_CATALOG if e.category == category]


# ── ETFMetrics ────────────────────────────────────────────────────────────────


@dataclass
class ETFMetrics:
    """
    Métricas calculadas de um ETF para um período.

    total_return:     retorno total no período (decimal)
    annual_return:    retorno anualizado (CAGR)
    volatility:       volatilidade anualizada (std dos retornos diários × √252)
    sharpe:           Sharpe ratio (usando CDI como risk-free)
    max_drawdown:     maior queda do pico ao vale (decimal negativo)
    var_95:           Value at Risk 95% (perda máxima diária esperada)
    calmar:           annual_return / |max_drawdown| (ajuste por drawdown)
    n_days:           número de dias de dados
    start_price:      preço no início do período
    end_price:        preço no final do período
    """

    ticker: str
    name: str
    period: str
    total_return: float
    annual_return: float
    volatility: float
    sharpe: float
    max_drawdown: float
    var_95: float
    calmar: float
    n_days: int
    start_price: float
    end_price: float
    category: str = ""
    ter: float = 0.0

    @property
    def total_return_pct(self) -> float:
        return round(self.total_return * 100, 2)

    @property
    def annual_return_pct(self) -> float:
        return round(self.annual_return * 100, 2)

    @property
    def volatility_pct(self) -> float:
        return round(self.volatility * 100, 2)

    @property
    def max_drawdown_pct(self) -> float:
        return round(self.max_drawdown * 100, 2)

    @property
    def var_95_pct(self) -> float:
        return round(self.var_95 * 100, 2)

    @property
    def sharpe_label(self) -> str:
        if self.sharpe >= 2.0:
            return "Excelente"
        if self.sharpe >= 1.0:
            return "Bom"
        if self.sharpe >= 0.5:
            return "Razoável"
        if self.sharpe >= 0.0:
            return "Fraco"
        return "Negativo"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "period": self.period,
            "category": self.category,
            "ter": self.ter,
            "total_return_pct": self.total_return_pct,
            "annual_return_pct": self.annual_return_pct,
            "volatility_pct": self.volatility_pct,
            "sharpe": round(self.sharpe, 3),
            "sharpe_label": self.sharpe_label,
            "max_drawdown_pct": self.max_drawdown_pct,
            "var_95_pct": self.var_95_pct,
            "calmar": round(self.calmar, 3),
            "n_days": self.n_days,
            "start_price": round(self.start_price, 2),
            "end_price": round(self.end_price, 2),
        }


# ── ETFComparison ─────────────────────────────────────────────────────────────


@dataclass
class ETFComparison:
    """Comparação lado a lado de múltiplos ETFs."""

    period: str
    risk_free: float
    metrics: list[ETFMetrics]
    price_series: dict[str, list[dict]]  # ticker → [{date, close, normalized}]

    @property
    def best_return(self) -> ETFMetrics | None:
        return max(self.metrics, key=lambda m: m.total_return) if self.metrics else None

    @property
    def best_sharpe(self) -> ETFMetrics | None:
        return max(self.metrics, key=lambda m: m.sharpe) if self.metrics else None

    @property
    def lowest_volatility(self) -> ETFMetrics | None:
        return min(self.metrics, key=lambda m: m.volatility) if self.metrics else None


# ── TrackingErrorResult ───────────────────────────────────────────────────────


@dataclass
class TrackingErrorResult:
    """
    Análise de tracking error de um ETF vs seu benchmark.

    tracking_error:      std anualizado da diferença de retornos diários
    tracking_difference: retorno_benchmark - retorno_etf (custo implícito total)
    correlation:         correlação dos retornos com o benchmark
    beta:                sensibilidade ao benchmark
    r_squared:           % da variância explicada pelo benchmark
    information_ratio:   excess_return / tracking_error (qualidade do gestor)
    """

    ticker: str
    benchmark: str
    period: str
    tracking_error_pct: float
    tracking_diff_pct: float  # positivo = ETF ficou abaixo do benchmark
    correlation: float
    beta: float
    r_squared: float
    information_ratio: float
    etf_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    n_days: int
    daily_diffs: list[dict]  # [{date, diff}] para gráfico

    @property
    def quality_label(self) -> str:
        if self.tracking_error_pct < 0.5:
            return "Excelente replicação"
        if self.tracking_error_pct < 1.5:
            return "Boa replicação"
        if self.tracking_error_pct < 3.0:
            return "Replicação razoável"
        return "Desvio elevado"


# ── RebalanceRecommendation ───────────────────────────────────────────────────


@dataclass
class RebalancePosition:
    """Posição atual vs target para um ETF."""

    ticker: str
    name: str
    current_value: float
    current_weight: float  # % atual
    target_weight: float  # % desejada
    deviation: float  # current_weight - target_weight (p.p.)
    action: str  # "COMPRAR" | "VENDER" | "MANTER"
    amount: float  # R$ a comprar (>0) ou vender (<0)
    units_approx: float  # unidades aproximadas (se preço fornecido)
    current_price: float = 0.0


@dataclass
class RebalanceRecommendation:
    """
    Recomendação de rebalanceamento de uma carteira de ETFs.

    Aceita rebalanceamento puro (vender/comprar dentro do patrimônio)
    ou rebalanceamento com aporte (capital novo direciona os ajustes).
    """

    total_current: float
    total_after: float
    new_contribution: float
    positions: list[RebalancePosition]
    rebalance_cost: float  # R$ total a movimentar
    n_buys: int
    n_sells: int
    note: str = ""

    @property
    def turnover_pct(self) -> float:
        """% do portfólio que será movimentado."""
        if self.total_current == 0:
            return 0.0
        return round(self.rebalance_cost / self.total_current * 100, 2)

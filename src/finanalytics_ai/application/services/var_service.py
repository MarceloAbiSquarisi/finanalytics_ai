"""
finanalytics_ai.application.services.var_service
-------------------------------------------------
Value at Risk (VaR) para carteiras de acoes.

Metodos implementados (stdlib puro, sem numpy):

  1. VaR Historico (Historical Simulation)
     Usa os retornos diarios reais dos ultimos N dias.
     Ordena os retornos e pega o percentil (ex: 5% piores dias).
     Mais robusto para distribuicoes com caudas gordas.
     Referencia: Jorion (2006), Value at Risk, 3rd ed.

  2. VaR Parametrico (Variancia-Covariancia)
     Assume distribuicao normal dos retornos.
     VaR = media - z * desvio_padrao
     z = 1.645 (95%), 2.326 (99%)
     Mais rapido mas subestima risco em caudas gordas.

  3. CVaR (Conditional VaR / Expected Shortfall)
     Media dos retornos piores que o VaR.
     Mais conservador e coerente que o VaR.
     Recomendado por Basel III para gestao de risco.

Niveis de confianca suportados: 90%, 95%, 99%

Design:
  - Usa retornos de fintz_cotacoes (banco local, zero latencia)
  - Calcula VaR individual por ativo e consolidado da carteira
  - Correlacao simples entre ativos (diversificacao)
  - Resultado em R$ e percentual da carteira
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Z-scores para niveis de confianca comuns
_Z_SCORES = {
    0.90: 1.2816,
    0.95: 1.6449,
    0.99: 2.3263,
}


@dataclass
class AssetVaR:
    """VaR de um ativo individual."""
    ticker: str
    quantity: float
    avg_price: float
    current_price: float
    position_value: float
    weight: float           # % da carteira
    daily_returns: list[float]
    var_hist_pct: float     # VaR historico em %
    var_hist_brl: float     # VaR historico em R$
    var_param_pct: float    # VaR parametrico em %
    var_param_brl: float    # VaR parametrico em R$
    cvar_pct: float         # CVaR em %
    cvar_brl: float         # CVaR em R$
    volatility_daily: float # Desvio padrao diario
    volatility_annual: float# Volatilidade anualizada
    num_observations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":           self.ticker,
            "quantity":         round(self.quantity, 2),
            "avg_price":        round(self.avg_price, 2),
            "current_price":    round(self.current_price, 2),
            "position_value":   round(self.position_value, 2),
            "weight_pct":       round(self.weight * 100, 2),
            "var_hist_pct":     round(self.var_hist_pct * 100, 2),
            "var_hist_brl":     round(self.var_hist_brl, 2),
            "var_param_pct":    round(self.var_param_pct * 100, 2),
            "var_param_brl":    round(self.var_param_brl, 2),
            "cvar_pct":         round(self.cvar_pct * 100, 2),
            "cvar_brl":         round(self.cvar_brl, 2),
            "volatility_daily": round(self.volatility_daily * 100, 2),
            "volatility_annual":round(self.volatility_annual * 100, 2),
            "num_observations": self.num_observations,
        }


@dataclass
class PortfolioVaR:
    """VaR consolidado da carteira."""
    portfolio_value: float
    confidence_level: float         # ex: 0.95
    lookback_days: int
    assets: list[AssetVaR]
    # VaR da carteira (considera correlacao via retornos agregados)
    var_hist_pct: float
    var_hist_brl: float
    var_param_pct: float
    var_param_brl: float
    cvar_pct: float
    cvar_brl: float
    # Diversificacao: diferenca entre soma dos VaRs individuais e VaR da carteira
    diversification_benefit_brl: float
    portfolio_volatility_daily: float
    portfolio_volatility_annual: float
    calculated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_value":              round(self.portfolio_value, 2),
            "confidence_level":             round(self.confidence_level * 100, 1),
            "lookback_days":                self.lookback_days,
            "var_hist_pct":                 round(self.var_hist_pct * 100, 2),
            "var_hist_brl":                 round(self.var_hist_brl, 2),
            "var_param_pct":                round(self.var_param_pct * 100, 2),
            "var_param_brl":                round(self.var_param_brl, 2),
            "cvar_pct":                     round(self.cvar_pct * 100, 2),
            "cvar_brl":                     round(self.cvar_brl, 2),
            "diversification_benefit_brl":  round(self.diversification_benefit_brl, 2),
            "portfolio_volatility_daily":   round(self.portfolio_volatility_daily * 100, 2),
            "portfolio_volatility_annual":  round(self.portfolio_volatility_annual * 100, 2),
            "calculated_at":                self.calculated_at,
            "assets":                       [a.to_dict() for a in self.assets],
            "interpretation": _interpret(self.var_hist_brl, self.portfolio_value, self.confidence_level),
        }


def _interpret(var_brl: float, portfolio_value: float, confidence: float) -> str:
    """Interpretacao em linguagem natural do VaR."""
    pct = var_brl / portfolio_value * 100 if portfolio_value > 0 else 0
    conf_pct = int(confidence * 100)
    return (
        f"Com {conf_pct}% de confianca, a perda maxima esperada em 1 dia "
        f"e de R$ {var_brl:,.2f} ({pct:.1f}% da carteira). "
        f"Em media, 1 dia em cada {100//(100-conf_pct)} dias pode exceder esse valor."
    )


class VaRService:
    """
    Calcula VaR historico, parametrico e CVaR para carteiras.

    market_data: MarketDataProvider (CompositeMarketDataClient)
    """

    def __init__(self, market_data: Any) -> None:
        self._market = market_data

    async def calculate(
        self,
        positions: list[dict],
        confidence_level: float = 0.95,
        lookback_days: int = 252,
    ) -> PortfolioVaR:
        """
        Calcula VaR para uma lista de posicoes.

        positions: [{"ticker": "PETR4", "quantity": 100, "average_price": 29.50}]
        confidence_level: 0.90, 0.95 ou 0.99
        lookback_days: dias de historico (252 = 1 ano, 504 = 2 anos)
        """
        import asyncio
        from finanalytics_ai.domain.value_objects.money import Ticker

        if confidence_level not in _Z_SCORES:
            raise ValueError(f"confidence_level deve ser 0.90, 0.95 ou 0.99")

        # Busca retornos historicos em paralelo
        sem = asyncio.Semaphore(5)
        ticker_returns: dict[str, list[float]] = {}
        ticker_prices: dict[str, float] = {}

        async def _fetch(pos: dict) -> None:
            ticker = pos["ticker"].upper()
            async with sem:
                try:
                    range_p = "1y" if lookback_days <= 252 else "2y"
                    bars = await self._market.get_ohlc_bars(
                        Ticker(ticker), range_period=range_p
                    )
                    if bars and len(bars) >= 10:
                        closes = [float(b.get("close", 0) or 0) for b in bars if b.get("close")]
                        returns = [
                            (closes[i] - closes[i-1]) / closes[i-1]
                            for i in range(1, len(closes))
                            if closes[i-1] > 0
                        ]
                        ticker_returns[ticker] = returns[-lookback_days:]
                        ticker_prices[ticker] = closes[-1]
                except Exception:
                    pass

        import asyncio
        await asyncio.gather(*[_fetch(p) for p in positions])

        # Calcula VaR por ativo
        total_value = sum(
            float(p["quantity"]) * ticker_prices.get(p["ticker"].upper(), float(p.get("average_price", 0)))
            for p in positions
        )

        asset_vars: list[AssetVaR] = []
        portfolio_weighted_returns: list[float] = []

        for pos in positions:
            ticker = pos["ticker"].upper()
            qty = float(pos["quantity"])
            avg_price = float(pos.get("average_price", 0))
            current_price = ticker_prices.get(ticker, avg_price)
            pos_value = qty * current_price
            weight = pos_value / total_value if total_value > 0 else 0
            returns = ticker_returns.get(ticker, [])

            if not returns:
                continue

            av = _calc_asset_var(
                ticker=ticker,
                quantity=qty,
                avg_price=avg_price,
                current_price=current_price,
                position_value=pos_value,
                weight=weight,
                returns=returns,
                confidence_level=confidence_level,
            )
            asset_vars.append(av)

            # Acumula retornos ponderados para VaR da carteira
            min_len = len(portfolio_weighted_returns) or len(returns)
            if not portfolio_weighted_returns:
                portfolio_weighted_returns = [r * weight for r in returns[-min_len:]]
            else:
                min_len = min(len(portfolio_weighted_returns), len(returns))
                portfolio_weighted_returns = [
                    portfolio_weighted_returns[i] + returns[-min_len:][i] * weight
                    for i in range(min_len)
                ]

        if not portfolio_weighted_returns:
            raise ValueError("Nenhum dado historico encontrado para os ativos")

        # VaR consolidado da carteira
        port_var = _calc_var_from_returns(portfolio_weighted_returns, confidence_level)
        port_var_hist_brl  = port_var["var_hist_pct"] * total_value
        port_var_param_brl = port_var["var_param_pct"] * total_value
        port_cvar_brl      = port_var["cvar_pct"] * total_value

        # Beneficio de diversificacao
        sum_individual_var = sum(a.var_hist_brl for a in asset_vars)
        diversification = sum_individual_var - port_var_hist_brl

        return PortfolioVaR(
            portfolio_value=total_value,
            confidence_level=confidence_level,
            lookback_days=lookback_days,
            assets=asset_vars,
            var_hist_pct=port_var["var_hist_pct"],
            var_hist_brl=port_var_hist_brl,
            var_param_pct=port_var["var_param_pct"],
            var_param_brl=port_var_param_brl,
            cvar_pct=port_var["cvar_pct"],
            cvar_brl=port_cvar_brl,
            diversification_benefit_brl=max(diversification, 0),
            portfolio_volatility_daily=port_var["vol_daily"],
            portfolio_volatility_annual=port_var["vol_annual"],
        )


def _calc_asset_var(
    ticker: str,
    quantity: float,
    avg_price: float,
    current_price: float,
    position_value: float,
    weight: float,
    returns: list[float],
    confidence_level: float,
) -> AssetVaR:
    var_data = _calc_var_from_returns(returns, confidence_level)
    return AssetVaR(
        ticker=ticker,
        quantity=quantity,
        avg_price=avg_price,
        current_price=current_price,
        position_value=position_value,
        weight=weight,
        daily_returns=returns,
        var_hist_pct=var_data["var_hist_pct"],
        var_hist_brl=var_data["var_hist_pct"] * position_value,
        var_param_pct=var_data["var_param_pct"],
        var_param_brl=var_data["var_param_pct"] * position_value,
        cvar_pct=var_data["cvar_pct"],
        cvar_brl=var_data["cvar_pct"] * position_value,
        volatility_daily=var_data["vol_daily"],
        volatility_annual=var_data["vol_annual"],
        num_observations=len(returns),
    )


def _calc_var_from_returns(
    returns: list[float],
    confidence_level: float,
) -> dict[str, float]:
    """Calcula VaR historico, parametrico e CVaR de uma serie de retornos."""
    if not returns:
        return {"var_hist_pct": 0, "var_param_pct": 0, "cvar_pct": 0,
                "vol_daily": 0, "vol_annual": 0}

    sorted_returns = sorted(returns)
    n = len(sorted_returns)

    # VaR Historico: percentil (1 - confidence_level)
    idx = max(0, int(n * (1 - confidence_level)) - 1)
    var_hist = abs(sorted_returns[idx])

    # CVaR: media dos retornos piores que o VaR
    tail = sorted_returns[:idx+1]
    cvar = abs(statistics.mean(tail)) if tail else var_hist

    # VaR Parametrico
    mean = statistics.mean(returns)
    try:
        std = statistics.stdev(returns)
    except statistics.StatisticsError:
        std = 0.0

    z = _Z_SCORES.get(confidence_level, 1.6449)
    var_param = abs(mean - z * std)

    # Volatilidade
    vol_daily  = std
    vol_annual = std * math.sqrt(252)

    return {
        "var_hist_pct":  var_hist,
        "var_param_pct": var_param,
        "cvar_pct":      cvar,
        "vol_daily":     vol_daily,
        "vol_annual":    vol_annual,
    }

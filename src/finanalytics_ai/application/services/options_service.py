"""
finanalytics_ai.application.services.options_service
-----------------------------------------------------
Calculadora de opcoes: Black-Scholes, Greeks e Volatilidade Implicita.

Implementacao pura em Python stdlib (sem numpy/scipy no caminho critico).
Usa approximacao de Abramowitz & Stegun para a CDF normal (erro < 7.5e-8).

Funcionalidades:
  1. Precificacao Black-Scholes (call/put europeia)
  2. Greeks: Delta, Gamma, Theta, Vega, Rho
  3. Volatilidade Implicita (Newton-Raphson + bisseccao como fallback)
  4. Estrategias compostas: Straddle, Strangle, Bull/Bear Spread,
     Iron Condor, Butterfly, Covered Call

Referencias:
  - Black, F. & Scholes, M. (1973). The Pricing of Options and Corporate Liabilities.
  - Abramowitz, M. & Stegun, I. (1964). Handbook of Mathematical Functions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

# ─── Constantes ────────────────────────────────────────────────────────────────
SQRT_2PI = math.sqrt(2 * math.pi)
SQRT_2 = math.sqrt(2)


# ─── Distribuicao Normal ────────────────────────────────────────────────────────


def _norm_pdf(x: float) -> float:
    """Densidade da normal padrao."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _norm_cdf(x: float) -> float:
    """
    CDF da normal padrao via approximacao de Abramowitz & Stegun (7.1.26).
    Erro maximo: |eps| < 7.5e-8
    """
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    p = t * (
        0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    cdf = 1.0 - _norm_pdf(x) * p
    return 0.5 + sign * (cdf - 0.5)


# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class GreeksResult:
    """Resultado do calculo de greeks para uma opcao."""

    option_type: str  # "call" ou "put"
    spot: float  # preco atual do ativo
    strike: float  # preco de exercicio
    expiry_days: int  # dias ate vencimento
    volatility: float  # volatilidade anualizada (ex: 0.35 = 35%)
    rate: float  # taxa livre de risco anualizada (ex: 0.1375)
    dividend: float  # dividend yield anualizado

    price: float  # preco teorico Black-Scholes
    delta: float  # sensibilidade ao preco do ativo
    gamma: float  # variacao do delta
    theta: float  # decaimento temporal (por dia)
    vega: float  # sensibilidade a volatilidade (por 1%)
    rho: float  # sensibilidade a taxa (por 1%)

    d1: float  # parametro interno d1
    d2: float  # parametro interno d2
    intrinsic_value: float  # valor intrinseco
    time_value: float  # valor temporal
    moneyness: str  # ITM, ATM, OTM

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_type": self.option_type,
            "spot": round(self.spot, 4),
            "strike": round(self.strike, 4),
            "expiry_days": self.expiry_days,
            "volatility": round(self.volatility * 100, 2),  # em %
            "rate": round(self.rate * 100, 2),  # em %
            "dividend": round(self.dividend * 100, 2),  # em %
            "price": round(self.price, 4),
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 4),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "rho": round(self.rho, 4),
            "d1": round(self.d1, 4),
            "d2": round(self.d2, 4),
            "intrinsic_value": round(self.intrinsic_value, 4),
            "time_value": round(self.time_value, 4),
            "moneyness": self.moneyness,
        }


@dataclass
class ImpliedVolResult:
    """Resultado do calculo de volatilidade implicita."""

    option_type: str
    market_price: float
    spot: float
    strike: float
    expiry_days: int
    rate: float
    implied_vol: float | None  # None se nao convergiu
    iterations: int
    converged: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_type": self.option_type,
            "market_price": round(self.market_price, 4),
            "spot": round(self.spot, 4),
            "strike": round(self.strike, 4),
            "expiry_days": self.expiry_days,
            "rate": round(self.rate * 100, 2),
            "implied_vol": round(self.implied_vol * 100, 2) if self.implied_vol else None,
            "converged": self.converged,
            "iterations": self.iterations,
            "error": self.error,
        }


@dataclass
class StrategyLeg:
    """Uma perna de uma estrategia de opcoes."""

    option_type: str  # "call" ou "put"
    strike: float
    quantity: int  # positivo = comprado, negativo = vendido
    greeks: GreeksResult | None = None


@dataclass
class StrategyResult:
    """Resultado de uma estrategia composta."""

    name: str
    description: str
    legs: list[StrategyLeg]
    total_premium: float  # positivo = debito, negativo = credito
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    delta: float
    gamma: float
    theta: float
    vega: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "total_premium": round(self.total_premium, 4),
            "max_profit": round(self.max_profit, 4) if self.max_profit is not None else None,
            "max_loss": round(self.max_loss, 4) if self.max_loss is not None else None,
            "breakevens": [round(b, 4) for b in self.breakevens],
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 4),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "legs": [
                {
                    "option_type": leg.option_type,
                    "strike": round(leg.strike, 4),
                    "quantity": leg.quantity,
                    "price": round(leg.greeks.price, 4) if leg.greeks else None,
                    "delta": round(leg.greeks.delta, 4) if leg.greeks else None,
                }
                for leg in self.legs
            ],
        }


# ─── Black-Scholes ──────────────────────────────────────────────────────────────


class OptionsService:
    """
    Servico de precificacao de opcoes.

    Stateless — todos os metodos sao puros (sem I/O).
    Instanciado como singleton no startup da API.
    """

    # Taxa livre de risco padrao (SELIC aproximada)
    DEFAULT_RATE = 0.1375
    DEFAULT_DIVIDEND = 0.0

    def calculate_greeks(
        self,
        option_type: str,
        spot: float,
        strike: float,
        expiry_days: int,
        volatility: float,
        rate: float | None = None,
        dividend: float | None = None,
    ) -> GreeksResult:
        """
        Calcula preco e greeks via Black-Scholes.

        option_type: "call" ou "put"
        spot:        preco atual do ativo subjacente (R$)
        strike:      preco de exercicio (R$)
        expiry_days: dias corridos ate o vencimento
        volatility:  volatilidade anualizada (ex: 0.35 para 35%)
        rate:        taxa livre de risco anualizada (None = SELIC padrao)
        dividend:    dividend yield anualizado (None = 0)
        """
        option_type = option_type.lower()
        if option_type not in ("call", "put"):
            raise ValueError("option_type deve ser 'call' ou 'put'")
        if spot <= 0 or strike <= 0 or volatility <= 0:
            raise ValueError("spot, strike e volatility devem ser positivos")
        if expiry_days <= 0:
            raise ValueError("expiry_days deve ser > 0")

        r = rate if rate is not None else self.DEFAULT_RATE
        q = dividend if dividend is not None else self.DEFAULT_DIVIDEND
        T = expiry_days / 365.0
        sqrtT = math.sqrt(T)

        # d1, d2
        d1 = (math.log(spot / strike) + (r - q + 0.5 * volatility**2) * T) / (volatility * sqrtT)
        d2 = d1 - volatility * sqrtT

        # CDF e PDF
        Nd1 = _norm_cdf(d1)
        Nd2 = _norm_cdf(d2)
        Nmd1 = _norm_cdf(-d1)
        Nmd2 = _norm_cdf(-d2)
        nd1 = _norm_pdf(d1)

        discount = math.exp(-r * T)
        div_disc = math.exp(-q * T)

        if option_type == "call":
            price = spot * div_disc * Nd1 - strike * discount * Nd2
            delta = div_disc * Nd1
            rho = strike * T * discount * Nd2 / 100
            intrinsic = max(spot - strike, 0.0)
        else:
            price = strike * discount * Nmd2 - spot * div_disc * Nmd1
            delta = -div_disc * Nmd1
            rho = -strike * T * discount * Nmd2 / 100
            intrinsic = max(strike - spot, 0.0)

        gamma = div_disc * nd1 / (spot * volatility * sqrtT)
        vega = spot * div_disc * nd1 * sqrtT / 100  # por 1% de vol
        theta = (
            -(spot * div_disc * nd1 * volatility) / (2 * sqrtT)
            - r * strike * discount * (Nd2 if option_type == "call" else -Nmd2)
            + q * spot * div_disc * (Nd1 if option_type == "call" else -Nmd1)
        ) / 365.0  # por dia

        time_value = max(price - intrinsic, 0.0)

        # Moneyness
        ratio = spot / strike
        if ratio > 1.02:
            moneyness = "ITM" if option_type == "call" else "OTM"
        elif ratio < 0.98:
            moneyness = "OTM" if option_type == "call" else "ITM"
        else:
            moneyness = "ATM"

        return GreeksResult(
            option_type=option_type,
            spot=spot,
            strike=strike,
            expiry_days=expiry_days,
            volatility=volatility,
            rate=r,
            dividend=q,
            price=price,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            rho=rho,
            d1=d1,
            d2=d2,
            intrinsic_value=intrinsic,
            time_value=time_value,
            moneyness=moneyness,
        )

    def implied_volatility(
        self,
        option_type: str,
        market_price: float,
        spot: float,
        strike: float,
        expiry_days: int,
        rate: float | None = None,
        dividend: float | None = None,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> ImpliedVolResult:
        """
        Calcula volatilidade implicita via Newton-Raphson.
        Fallback para bisseccao se Newton nao convergir.

        market_price: preco de mercado da opcao (premio)
        """
        r = rate if rate is not None else self.DEFAULT_RATE
        q = dividend if dividend is not None else self.DEFAULT_DIVIDEND

        def price_fn(vol: float) -> float:
            try:
                return self.calculate_greeks(
                    option_type, spot, strike, expiry_days, vol, r, q
                ).price
            except Exception:
                return 0.0

        def vega_fn(vol: float) -> float:
            try:
                return (
                    self.calculate_greeks(option_type, spot, strike, expiry_days, vol, r, q).vega
                    * 100
                )  # converte de por-1% para por-unidade
            except Exception:
                return 0.0

        # Chute inicial: Brenner & Subrahmanyam (1988)
        T = expiry_days / 365.0
        vol = math.sqrt(2 * math.pi / T) * market_price / spot

        # Limita chute inicial
        vol = max(0.01, min(vol, 5.0))

        # Newton-Raphson
        iterations = 0
        for i in range(max_iter):
            iterations += 1
            p = price_fn(vol)
            diff = p - market_price
            if abs(diff) < tol:
                return ImpliedVolResult(
                    option_type=option_type,
                    market_price=market_price,
                    spot=spot,
                    strike=strike,
                    expiry_days=expiry_days,
                    rate=r,
                    implied_vol=vol,
                    iterations=iterations,
                    converged=True,
                )
            v = vega_fn(vol)
            if abs(v) < 1e-10:
                break
            vol = vol - diff / v
            vol = max(0.001, min(vol, 10.0))

        # Fallback: bisseccao
        lo, hi = 0.001, 10.0
        for i in range(200):
            iterations += 1
            mid = (lo + hi) / 2
            p = price_fn(mid)
            diff = p - market_price
            if abs(diff) < tol:
                return ImpliedVolResult(
                    option_type=option_type,
                    market_price=market_price,
                    spot=spot,
                    strike=strike,
                    expiry_days=expiry_days,
                    rate=r,
                    implied_vol=mid,
                    iterations=iterations,
                    converged=True,
                )
            if diff > 0:
                hi = mid
            else:
                lo = mid

        return ImpliedVolResult(
            option_type=option_type,
            market_price=market_price,
            spot=spot,
            strike=strike,
            expiry_days=expiry_days,
            rate=r,
            implied_vol=None,
            iterations=iterations,
            converged=False,
            error="Nao convergiu apos biseccao",
        )

    # ─── Estrategias ─────────────────────────────────────────────────────────────

    def _leg(
        self,
        option_type: str,
        spot: float,
        strike: float,
        expiry_days: int,
        vol: float,
        rate: float,
        qty: int,
    ) -> StrategyLeg:
        g = self.calculate_greeks(option_type, spot, strike, expiry_days, vol, rate)
        return StrategyLeg(option_type=option_type, strike=strike, quantity=qty, greeks=g)

    def _build_strategy(
        self,
        name: str,
        description: str,
        legs: list[StrategyLeg],
    ) -> StrategyResult:
        """Agrega greeks e calcula P&L de uma estrategia."""
        premium = sum(l.quantity * l.greeks.price for l in legs if l.greeks)
        delta = sum(l.quantity * l.greeks.delta for l in legs if l.greeks)
        gamma = sum(l.quantity * l.greeks.gamma for l in legs if l.greeks)
        theta = sum(l.quantity * l.greeks.theta for l in legs if l.greeks)
        vega = sum(l.quantity * l.greeks.vega for l in legs if l.greeks)

        return StrategyResult(
            name=name,
            description=description,
            legs=legs,
            total_premium=premium,
            max_profit=None,
            max_loss=None,
            breakevens=[],
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
        )

    def straddle(
        self,
        spot: float,
        strike: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Straddle: compra call + put no mesmo strike."""
        r = rate or self.DEFAULT_RATE
        call = self._leg("call", spot, strike, expiry_days, vol, r, 1)
        put = self._leg("put", spot, strike, expiry_days, vol, r, 1)
        prem = call.greeks.price + put.greeks.price
        s = self._build_strategy(
            "Straddle",
            "Compra call + put no mesmo strike. Lucra com grande movimento em qualquer direcao.",
            [call, put],
        )
        s.max_loss = prem
        s.max_profit = None  # teoricamente ilimitado
        s.breakevens = [strike - prem, strike + prem]
        return s

    def strangle(
        self,
        spot: float,
        strike_put: float,
        strike_call: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Strangle: compra put OTM + call OTM em strikes diferentes."""
        r = rate or self.DEFAULT_RATE
        call = self._leg("call", spot, strike_call, expiry_days, vol, r, 1)
        put = self._leg("put", spot, strike_put, expiry_days, vol, r, 1)
        prem = call.greeks.price + put.greeks.price
        s = self._build_strategy(
            "Strangle",
            "Compra put OTM + call OTM. Mais barato que straddle, requer movimento maior.",
            [put, call],
        )
        s.max_loss = prem
        s.max_profit = None
        s.breakevens = [strike_put - prem, strike_call + prem]
        return s

    def bull_call_spread(
        self,
        spot: float,
        strike_low: float,
        strike_high: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Bull Call Spread: compra call ATM + vende call OTM."""
        r = rate or self.DEFAULT_RATE
        long_call = self._leg("call", spot, strike_low, expiry_days, vol, r, 1)
        short_call = self._leg("call", spot, strike_high, expiry_days, vol, r, -1)
        prem = long_call.greeks.price - short_call.greeks.price
        s = self._build_strategy(
            "Bull Call Spread",
            "Compra call + vende call OTM. Custo menor, lucro limitado. Bom para alta moderada.",
            [long_call, short_call],
        )
        s.max_loss = prem
        s.max_profit = (strike_high - strike_low) - prem
        s.breakevens = [strike_low + prem]
        return s

    def bear_put_spread(
        self,
        spot: float,
        strike_high: float,
        strike_low: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Bear Put Spread: compra put ATM + vende put OTM."""
        r = rate or self.DEFAULT_RATE
        long_put = self._leg("put", spot, strike_high, expiry_days, vol, r, 1)
        short_put = self._leg("put", spot, strike_low, expiry_days, vol, r, -1)
        prem = long_put.greeks.price - short_put.greeks.price
        s = self._build_strategy(
            "Bear Put Spread",
            "Compra put + vende put OTM. Custo menor, lucro limitado. Bom para queda moderada.",
            [long_put, short_put],
        )
        s.max_loss = prem
        s.max_profit = (strike_high - strike_low) - prem
        s.breakevens = [strike_high - prem]
        return s

    def iron_condor(
        self,
        spot: float,
        strike_put_low: float,
        strike_put_high: float,
        strike_call_low: float,
        strike_call_high: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Iron Condor: vende put spread + vende call spread. Ideal para baixa volatilidade."""
        r = rate or self.DEFAULT_RATE
        lp = self._leg("put", spot, strike_put_low, expiry_days, vol, r, 1)
        sp = self._leg("put", spot, strike_put_high, expiry_days, vol, r, -1)
        sc = self._leg("call", spot, strike_call_low, expiry_days, vol, r, -1)
        lc = self._leg("call", spot, strike_call_high, expiry_days, vol, r, 1)
        credit = (sp.greeks.price - lp.greeks.price) + (sc.greeks.price - lc.greeks.price)
        wing_put = strike_put_high - strike_put_low
        wing_call = strike_call_high - strike_call_low
        max_loss = max(wing_put, wing_call) - credit
        s = self._build_strategy(
            "Iron Condor",
            "4 pernas: ideal para ativo em range. Recebe credito, perde se ativo sair do corredor.",
            [lp, sp, sc, lc],
        )
        s.max_profit = credit
        s.max_loss = max_loss
        s.breakevens = [strike_put_high - credit, strike_call_low + credit]
        s.total_premium = -credit  # credito = negativo por convencao
        return s

    def butterfly(
        self,
        spot: float,
        strike_low: float,
        strike_mid: float,
        strike_high: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
        option_type: str = "call",
    ) -> StrategyResult:
        """Butterfly: compra 2 extremos + vende 2 do meio."""
        r = rate or self.DEFAULT_RATE
        l1 = self._leg(option_type, spot, strike_low, expiry_days, vol, r, 1)
        s1 = self._leg(option_type, spot, strike_mid, expiry_days, vol, r, -2)
        l2 = self._leg(option_type, spot, strike_high, expiry_days, vol, r, 1)
        prem = l1.greeks.price - 2 * s1.greeks.price + l2.greeks.price
        wing = strike_mid - strike_low
        s = self._build_strategy(
            "Butterfly",
            "Lucro maximo se ativo fechar no strike do meio no vencimento.",
            [l1, s1, l2],
        )
        s.max_profit = wing - prem
        s.max_loss = prem
        s.breakevens = [strike_low + prem, strike_high - prem]
        return s

    def covered_call(
        self,
        spot: float,
        strike: float,
        expiry_days: int,
        vol: float,
        rate: float | None = None,
    ) -> StrategyResult:
        """Covered Call: posicao comprada + venda de call."""
        r = rate or self.DEFAULT_RATE
        # Stock position (delta = 1.0 por acao)
        short_call = self._leg("call", spot, strike, expiry_days, vol, r, -1)
        premium_received = short_call.greeks.price
        s = self._build_strategy(
            "Covered Call",
            "Venda de call sobre acao ja comprada. Receita de premio, cap no upside.",
            [short_call],
        )
        s.max_profit = (strike - spot) + premium_received
        s.max_loss = spot - premium_received  # perda maxima teorica
        s.breakevens = [spot - premium_received]
        s.delta = 1.0 + short_call.greeks.delta  # inclui a acao
        s.total_premium = -premium_received  # credito
        return s

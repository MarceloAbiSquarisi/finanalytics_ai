"""
finanalytics_ai.interfaces.api.routes.opcoes
--------------------------------------------
Rotas da calculadora de opcoes.

GET  /api/v1/opcoes/greeks          -- calcula greeks Black-Scholes
GET  /api/v1/opcoes/iv              -- volatilidade implicita
POST /api/v1/opcoes/estrategia      -- precifica estrategia composta
GET  /api/v1/opcoes/estrategias     -- lista estrategias disponiveis
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/opcoes", tags=["Opcoes"])


def _get_service(request: Request) -> Any:
    svc = getattr(request.app.state, "options_service", None)
    if svc is None:
        raise HTTPException(503, "OptionsService nao inicializado")
    return svc


# ─── Greeks ──────────────────────────────────────────────────────────────────


@router.get("/greeks", summary="Greeks Black-Scholes (call ou put)")
async def get_greeks(
    request: Request,
    option_type: str = Query(..., description="call ou put"),
    spot: float = Query(..., description="Preco atual do ativo (R$)"),
    strike: float = Query(..., description="Preco de exercicio (R$)"),
    expiry_days: int = Query(..., description="Dias ate o vencimento"),
    volatility: float = Query(..., description="Volatilidade anualizada (ex: 0.35 = 35%)"),
    rate: float = Query(None, description="Taxa livre de risco anualizada (padrao: SELIC)"),
    dividend: float = Query(0.0, description="Dividend yield anualizado"),
) -> dict[str, Any]:
    """
    Calcula preco teorico e greeks de uma opcao europeia via Black-Scholes.

    Retorna: price, delta, gamma, theta (por dia), vega (por 1% vol), rho (por 1% taxa)
    """
    svc = _get_service(request)
    try:
        result = svc.calculate_greeks(
            option_type=option_type,
            spot=spot,
            strike=strike,
            expiry_days=expiry_days,
            volatility=volatility,
            rate=rate,
            dividend=dividend,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("opcoes.greeks.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


# ─── Volatilidade Implicita ───────────────────────────────────────────────────


@router.get("/iv", summary="Volatilidade implicita")
async def get_implied_vol(
    request: Request,
    option_type: str = Query(..., description="call ou put"),
    market_price: float = Query(..., description="Premio de mercado da opcao (R$)"),
    spot: float = Query(..., description="Preco atual do ativo (R$)"),
    strike: float = Query(..., description="Preco de exercicio (R$)"),
    expiry_days: int = Query(..., description="Dias ate o vencimento"),
    rate: float = Query(None, description="Taxa livre de risco anualizada"),
) -> dict[str, Any]:
    """
    Calcula a volatilidade implicita dado o preco de mercado da opcao.
    Usa Newton-Raphson com fallback para bisseccao.
    """
    svc = _get_service(request)
    try:
        result = svc.implied_volatility(
            option_type=option_type,
            market_price=market_price,
            spot=spot,
            strike=strike,
            expiry_days=expiry_days,
            rate=rate,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("opcoes.iv.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


# ─── Estrategias ─────────────────────────────────────────────────────────────


class EstrategyRequest(BaseModel):
    strategy: str = Field(
        ...,
        description="straddle|strangle|bull_call_spread|bear_put_spread|iron_condor|butterfly|covered_call",
    )
    spot: float = Field(..., description="Preco atual do ativo (R$)")
    expiry_days: int = Field(..., description="Dias ate o vencimento")
    volatility: float = Field(..., description="Volatilidade anualizada (ex: 0.35)")
    rate: float = Field(None, description="Taxa livre de risco")
    # Parametros especificos por estrategia
    strike: float | None = None  # straddle, covered_call
    strike_low: float | None = None  # spread, butterfly, iron_condor
    strike_mid: float | None = None  # butterfly
    strike_high: float | None = None  # spread, butterfly, iron_condor
    strike_put: float | None = None  # strangle
    strike_call: float | None = None  # strangle
    strike_put_low: float | None = None  # iron_condor
    strike_put_high: float | None = None  # iron_condor
    strike_call_low: float | None = None  # iron_condor
    strike_call_high: float | None = None  # iron_condor
    option_type: str = "call"  # butterfly


@router.post("/estrategia", summary="Precifica estrategia composta")
async def get_strategy(
    body: EstrategyRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Precifica uma estrategia composta de opcoes.

    Estrategias suportadas:
    - straddle: compra call + put (mesmo strike)
    - strangle: compra call OTM + put OTM (strikes diferentes)
    - bull_call_spread: compra call ATM + vende call OTM
    - bear_put_spread: compra put ATM + vende put OTM
    - iron_condor: 4 pernas, vende call spread + put spread
    - butterfly: 3 strikes, lucro no meio
    - covered_call: acao comprada + venda de call
    """
    svc = _get_service(request)
    s = body.strategy.lower()
    vol = body.volatility
    spot = body.spot
    exp = body.expiry_days
    r = body.rate

    try:
        if s == "straddle":
            if not body.strike:
                raise ValueError("straddle requer: strike")
            result = svc.straddle(spot, body.strike, exp, vol, r)

        elif s == "strangle":
            if not body.strike_put or not body.strike_call:
                raise ValueError("strangle requer: strike_put, strike_call")
            result = svc.strangle(spot, body.strike_put, body.strike_call, exp, vol, r)

        elif s == "bull_call_spread":
            if not body.strike_low or not body.strike_high:
                raise ValueError("bull_call_spread requer: strike_low, strike_high")
            result = svc.bull_call_spread(spot, body.strike_low, body.strike_high, exp, vol, r)

        elif s == "bear_put_spread":
            if not body.strike_low or not body.strike_high:
                raise ValueError("bear_put_spread requer: strike_low, strike_high")
            result = svc.bear_put_spread(spot, body.strike_high, body.strike_low, exp, vol, r)

        elif s == "iron_condor":
            if not all(
                [
                    body.strike_put_low,
                    body.strike_put_high,
                    body.strike_call_low,
                    body.strike_call_high,
                ]
            ):
                raise ValueError(
                    "iron_condor requer: strike_put_low, strike_put_high, strike_call_low, strike_call_high"
                )
            result = svc.iron_condor(
                spot,
                body.strike_put_low,
                body.strike_put_high,
                body.strike_call_low,
                body.strike_call_high,
                exp,
                vol,
                r,
            )

        elif s == "butterfly":
            if not all([body.strike_low, body.strike_mid, body.strike_high]):
                raise ValueError("butterfly requer: strike_low, strike_mid, strike_high")
            result = svc.butterfly(
                spot,
                body.strike_low,
                body.strike_mid,
                body.strike_high,
                exp,
                vol,
                r,
                body.option_type,
            )

        elif s == "covered_call":
            if not body.strike:
                raise ValueError("covered_call requer: strike")
            result = svc.covered_call(spot, body.strike, exp, vol, r)

        else:
            raise ValueError(
                f"Estrategia '{s}' nao suportada. Use: straddle, strangle, bull_call_spread, bear_put_spread, iron_condor, butterfly, covered_call"
            )

        return result.to_dict()

    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("opcoes.estrategia.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/estrategias", summary="Lista estrategias disponiveis")
async def list_strategies() -> dict[str, Any]:
    return {
        "estrategias": [
            {
                "key": "straddle",
                "nome": "Straddle",
                "descricao": "Compra call + put no mesmo strike. Lucra com grande movimento em qualquer direcao.",
                "parametros": ["strike"],
                "mercado": "Alta volatilidade esperada",
            },
            {
                "key": "strangle",
                "nome": "Strangle",
                "descricao": "Compra call OTM + put OTM. Mais barato que straddle, requer movimento maior.",
                "parametros": ["strike_put", "strike_call"],
                "mercado": "Alta volatilidade, movimento grande",
            },
            {
                "key": "bull_call_spread",
                "nome": "Bull Call Spread",
                "descricao": "Compra call ATM + vende call OTM. Custo menor, lucro limitado.",
                "parametros": ["strike_low", "strike_high"],
                "mercado": "Alta moderada",
            },
            {
                "key": "bear_put_spread",
                "nome": "Bear Put Spread",
                "descricao": "Compra put ATM + vende put OTM. Custo menor, lucro limitado.",
                "parametros": ["strike_low", "strike_high"],
                "mercado": "Queda moderada",
            },
            {
                "key": "iron_condor",
                "nome": "Iron Condor",
                "descricao": "4 pernas: vende call spread + put spread. Recebe credito, ativo em range.",
                "parametros": [
                    "strike_put_low",
                    "strike_put_high",
                    "strike_call_low",
                    "strike_call_high",
                ],
                "mercado": "Baixa volatilidade, ativo em range",
            },
            {
                "key": "butterfly",
                "nome": "Butterfly",
                "descricao": "3 strikes: lucro maximo se ativo fechar no strike do meio.",
                "parametros": ["strike_low", "strike_mid", "strike_high"],
                "mercado": "Preve fechamento proximo ao strike do meio",
            },
            {
                "key": "covered_call",
                "nome": "Covered Call",
                "descricao": "Acao comprada + venda de call. Receita de premio, cap no upside.",
                "parametros": ["strike"],
                "mercado": "Neutro a levemente altista",
            },
        ]
    }

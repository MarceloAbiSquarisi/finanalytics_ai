"""
Rotas do Screener de Acoes.

POST /api/v1/screener/run    — executa screener com criterios e universo
GET  /api/v1/screener/run    — mesmo, via query string (criterios simples)
GET  /api/v1/screener/fields — descreve os campos disponiveis e seus intervalos
"""
from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.screener_service import ScreenerService
from finanalytics_ai.domain.screener.engine import FilterCriteria

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/screener", tags=["Screener"])


class ScreenerRequest(BaseModel):
    # Filtros de valuation
    pe_min:             Optional[float] = None
    pe_max:             Optional[float] = None
    pvp_min:            Optional[float] = None
    pvp_max:            Optional[float] = None
    # Proventos
    dy_min:             Optional[float] = None
    dy_max:             Optional[float] = None
    # Rentabilidade
    roe_min:            Optional[float] = None
    roe_max:            Optional[float] = None
    roic_min:           Optional[float] = None
    roic_max:           Optional[float] = None
    # Margens
    ebitda_margin_min:  Optional[float] = None
    ebitda_margin_max:  Optional[float] = None
    net_margin_min:     Optional[float] = None
    net_margin_max:     Optional[float] = None
    # Solidez financeira
    debt_equity_max:    Optional[float] = None
    revenue_growth_min: Optional[float] = None
    # Tamanho
    market_cap_min:     Optional[float] = None  # R$ bilhoes
    market_cap_max:     Optional[float] = None
    # Setor
    sector:             Optional[str]   = None
    # Universo
    extra_tickers:      list[str]       = Field(default_factory=list)
    use_universe:       bool            = True


def _get_service(request: Request) -> ScreenerService:
    svc = getattr(request.app.state, "screener_service", None)
    if svc is None:
        raise HTTPException(503, "ScreenerService nao inicializado")
    return svc


@router.post("/run")
async def run_screener(
    body:    ScreenerRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Executa o screener com criterios fundamentalistas.

    Busca os ~75 ativos do universo Ibovespa + tickers extras opcionais,
    aplica os filtros e retorna lista ordenada por score composito.

    Score composito: pondera ROE, DY, margens positivamente;
    penaliza P/E alto, P/VP alto e divida elevada.
    """
    svc = _get_service(request)
    criteria = FilterCriteria(
        pe_min             = body.pe_min,
        pe_max             = body.pe_max,
        pvp_min            = body.pvp_min,
        pvp_max            = body.pvp_max,
        dy_min             = body.dy_min,
        dy_max             = body.dy_max,
        roe_min            = body.roe_min,
        roe_max            = body.roe_max,
        roic_min           = body.roic_min,
        roic_max           = body.roic_max,
        ebitda_margin_min  = body.ebitda_margin_min,
        ebitda_margin_max  = body.ebitda_margin_max,
        net_margin_min     = body.net_margin_min,
        net_margin_max     = body.net_margin_max,
        debt_equity_max    = body.debt_equity_max,
        revenue_growth_min = body.revenue_growth_min,
        market_cap_min     = body.market_cap_min,
        market_cap_max     = body.market_cap_max,
        sector             = body.sector,
    )
    try:
        result = await svc.screen(
            criteria      = criteria,
            extra_tickers = body.extra_tickers,
            use_universe  = body.use_universe,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("screener.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no screener")


@router.get("/run")
async def run_screener_get(
    request:          Request,
    pe_max:           Optional[float] = Query(None),
    pvp_max:          Optional[float] = Query(None),
    dy_min:           Optional[float] = Query(None),
    roe_min:          Optional[float] = Query(None),
    debt_equity_max:  Optional[float] = Query(None),
    sector:           Optional[str]   = Query(None),
    extra_tickers:    Optional[str]   = Query(None),
) -> dict[str, Any]:
    """Screener via GET — subset de filtros para uso rapido."""
    extras = [t.strip() for t in extra_tickers.split(",")] if extra_tickers else []
    svc = _get_service(request)
    criteria = FilterCriteria(
        pe_max          = pe_max,
        pvp_max         = pvp_max,
        dy_min          = dy_min,
        roe_min         = roe_min,
        debt_equity_max = debt_equity_max,
        sector          = sector,
    )
    try:
        result = await svc.screen(criteria=criteria, extra_tickers=extras)
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("screener.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno no screener")


@router.get("/fields")
async def screener_fields() -> dict[str, Any]:
    """
    Descreve os campos de filtro disponiveis, seus tipos e intervalos sugeridos.
    Usado pelo frontend para montar o formulario dinamicamente.
    """
    return {
        "filters": [
            # Valuation
            {"key": "pe_min",            "label": "P/L Min",           "group": "Valuation",      "unit": "x",  "hint": "Tipico: 5-50"},
            {"key": "pe_max",            "label": "P/L Max",           "group": "Valuation",      "unit": "x",  "hint": "Barganhas < 10"},
            {"key": "pvp_min",           "label": "P/VP Min",          "group": "Valuation",      "unit": "x",  "hint": "< 1 = abaixo do patrimonio"},
            {"key": "pvp_max",           "label": "P/VP Max",          "group": "Valuation",      "unit": "x",  "hint": "Tipico: 0.5-5"},
            # Proventos
            {"key": "dy_min",            "label": "D.Y. Min",          "group": "Proventos",      "unit": "%",  "hint": "Ex: 5 = acima de 5%"},
            {"key": "dy_max",            "label": "D.Y. Max",          "group": "Proventos",      "unit": "%",  "hint": "Ex: 20"},
            # Rentabilidade
            {"key": "roe_min",           "label": "ROE Min",           "group": "Rentabilidade",  "unit": "%",  "hint": "Bom ROE > 15%"},
            {"key": "roe_max",           "label": "ROE Max",           "group": "Rentabilidade",  "unit": "%"},
            {"key": "roic_min",          "label": "ROIC Min",          "group": "Rentabilidade",  "unit": "%",  "hint": "Bom ROIC > 10%"},
            {"key": "roic_max",          "label": "ROIC Max",          "group": "Rentabilidade",  "unit": "%"},
            # Margens
            {"key": "ebitda_margin_min", "label": "Margem EBITDA Min", "group": "Margens",        "unit": "%"},
            {"key": "ebitda_margin_max", "label": "Margem EBITDA Max", "group": "Margens",        "unit": "%"},
            {"key": "net_margin_min",    "label": "Margem Liq. Min",   "group": "Margens",        "unit": "%"},
            {"key": "net_margin_max",    "label": "Margem Liq. Max",   "group": "Margens",        "unit": "%"},
            # Solidez
            {"key": "debt_equity_max",   "label": "D/PL Max",          "group": "Solidez",        "unit": "x",  "hint": "< 2 = conservador"},
            {"key": "revenue_growth_min","label": "Cresc. Receita Min","group": "Solidez",        "unit": "%",  "hint": "YoY"},
            # Tamanho
            {"key": "market_cap_min",    "label": "Market Cap Min",    "group": "Tamanho",        "unit": "R$B"},
            {"key": "market_cap_max",    "label": "Market Cap Max",    "group": "Tamanho",        "unit": "R$B"},
        ],
        "presets": [
            {"name": "Barganhas",     "description": "P/L baixo, P/VP < 1, ROE solido",
             "criteria": {"pe_max": 12, "pvp_max": 1.0, "roe_min": 10}},
            {"name": "Dividendos",    "description": "Dividend Yield alto, divida controlada",
             "criteria": {"dy_min": 6, "debt_equity_max": 2.0}},
            {"name": "Crescimento",   "description": "Receita crescendo, margens saudaveis",
             "criteria": {"revenue_growth_min": 10, "net_margin_min": 8}},
            {"name": "Qualidade",     "description": "ROE e ROIC elevados, margem EBITDA forte",
             "criteria": {"roe_min": 15, "roic_min": 10, "ebitda_margin_min": 15}},
            {"name": "Defensivo",     "description": "DY decente, divida baixa, sem crescimento req.",
             "criteria": {"dy_min": 4, "debt_equity_max": 1.5, "pvp_max": 2.0}},
        ],
    }

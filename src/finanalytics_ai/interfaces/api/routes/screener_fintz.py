"""
Rotas do Screener Fintz -- dados locais, zero rate limit.

POST /api/v1/screener/fintz/run      -- screener com dados Fintz
GET  /api/v1/screener/fintz/run      -- screener via query string
GET  /api/v1/screener/fintz/tickers  -- lista tickers disponiveis no banco
GET  /api/v1/screener/fintz/fields   -- campos disponiveis e seus ranges

Diferenca do screener BRAPI (/api/v1/screener/run):
  - Dados do banco local (fintz_indicadores) -- sem chamadas externas
  - 36 indicadores vs 10 da BRAPI
  - Universo dinamico: todos os tickers com dados no banco
  - Resposta mais rapida (sem latencia de rede)
"""
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from finanalytics_ai.domain.screener.engine import FilterCriteria

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/screener/fintz", tags=["Screener Fintz"])


class FintzScreenerRequest(BaseModel):
    # Valuation
    pe_min: float | None = None
    pe_max: float | None = None
    pvp_min: float | None = None
    pvp_max: float | None = None
    # Proventos
    dy_min: float | None = None
    dy_max: float | None = None
    # Rentabilidade
    roe_min: float | None = None
    roe_max: float | None = None
    roic_min: float | None = None
    roic_max: float | None = None
    # Margens
    ebitda_margin_min: float | None = None
    ebitda_margin_max: float | None = None
    net_margin_min: float | None = None
    net_margin_max: float | None = None
    # Solidez
    debt_equity_max: float | None = None
    # Tamanho
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    # Universo
    tickers: list[str] = Field(
        default_factory=list,
        description="Filtrar por tickers especificos. Vazio = todos do banco.",
    )


def _get_service(request: Request) -> Any:
    svc = getattr(request.app.state, "fintz_screener_service", None)
    if svc is None:
        raise HTTPException(
            503,
            "FintzScreenerService nao inicializado. Verifique os logs da API.",
        )
    return svc


@router.post("/run", summary="Screener com dados Fintz (banco local)")
async def screener_fintz_run(
    body: FintzScreenerRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """
    Executa screener usando fintz_indicadores (banco local).

    Retorna o mesmo formato do screener BRAPI para compatibilidade
    com o frontend existente.
    """
    svc = _get_service(request)

    criteria = FilterCriteria(
        pe_min=body.pe_min,
        pe_max=body.pe_max,
        pvp_min=body.pvp_min,
        pvp_max=body.pvp_max,
        dy_min=body.dy_min,
        dy_max=body.dy_max,
        roe_min=body.roe_min,
        roe_max=body.roe_max,
        roic_min=body.roic_min,
        roic_max=body.roic_max,
        ebitda_margin_min=body.ebitda_margin_min,
        ebitda_margin_max=body.ebitda_margin_max,
        net_margin_min=body.net_margin_min,
        net_margin_max=body.net_margin_max,
        debt_equity_max=body.debt_equity_max,
        market_cap_min=body.market_cap_min,
        market_cap_max=body.market_cap_max,
    )

    try:
        result = await svc.screen(
            criteria=criteria,
            tickers_filter=body.tickers or None,
        )
    except Exception as exc:
        logger.exception("screener_fintz.error", error=str(exc))
        raise HTTPException(500, f"Erro no screener Fintz: {exc}") from exc

    response.headers["X-Data-Source"] = "fintz"
    return {
        "total": len(result),
        "matched": len(result),
        "results": [
            {
                "ticker": r.ticker,
                "score": None,
                "pe": r.pe,
                "pvp": r.pvp,
                "dy": r.dy,
                "roe": r.roe,
                "roic": r.roic,
                "ebitda_margin": r.ebitda_margin,
                "net_margin": r.net_margin,
                "debt_equity": r.debt_equity,
                "market_cap": r.market_cap,
            }
            for r in result
        ],
        "source": "fintz",
    }


@router.get("/run", summary="Screener Fintz via query string")
async def screener_fintz_get(
    request: Request,
    response: Response,
    pe_max: float | None = Query(None),
    pvp_max: float | None = Query(None),
    dy_min: float | None = Query(None),
    roe_min: float | None = Query(None),
    roic_min: float | None = Query(None),
    net_margin_min: float | None = Query(None),
    debt_equity_max: float | None = Query(None),
    market_cap_min: float | None = Query(None),
    tickers: str | None = Query(None, description="Comma-separated: PETR4,VALE3"),
) -> dict[str, Any]:
    """Screener Fintz via GET para chamadas diretas do frontend."""
    body = FintzScreenerRequest(
        pe_max=pe_max,
        pvp_max=pvp_max,
        dy_min=dy_min,
        roe_min=roe_min,
        roic_min=roic_min,
        net_margin_min=net_margin_min,
        debt_equity_max=debt_equity_max,
        market_cap_min=market_cap_min,
        tickers=[t.strip() for t in tickers.split(",")] if tickers else [],
    )
    return await screener_fintz_run(body, request, response)


@router.get("/tickers", summary="Lista tickers disponiveis no banco Fintz")
async def get_tickers(request: Request) -> dict[str, Any]:
    """Lista todos os tickers com indicadores em fintz_indicadores."""
    svc = _get_service(request)
    try:
        tickers = await svc.get_available_tickers()
        return {"count": len(tickers), "tickers": tickers}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/fields", summary="Campos disponiveis no screener Fintz")
async def get_fields() -> dict[str, Any]:
    """Descreve os campos de filtro e seus indicadores Fintz correspondentes."""
    return {
        "fields": [
            {"field": "pe_min/pe_max",         "indicador": "P_L",                          "descricao": "Preco/Lucro"},
            {"field": "pvp_min/pvp_max",        "indicador": "P_VP",                         "descricao": "Preco/Valor Patrimonial"},
            {"field": "dy_min/dy_max",          "indicador": "DividendYield",                "descricao": "Dividend Yield (%)"},
            {"field": "roe_min/roe_max",        "indicador": "ROE",                          "descricao": "Return on Equity (%)"},
            {"field": "roic_min/roic_max",      "indicador": "ROIC",                         "descricao": "Return on Invested Capital (%)"},
            {"field": "ebitda_margin_min/max",  "indicador": "MargemEBITDA",                 "descricao": "Margem EBITDA (%)"},
            {"field": "net_margin_min/max",     "indicador": "MargemLiquida",                "descricao": "Margem Liquida (%)"},
            {"field": "debt_equity_max",        "indicador": "DividaLiquida_PatrimonioLiquido", "descricao": "Divida Liquida / PL"},
            {"field": "market_cap_min/max",     "indicador": "ValorDeMercado",               "descricao": "Market Cap (R$ bilhoes)"},
        ],
        "source": "fintz_indicadores",
        "nota": "Dados PIT -- valor mais recente disponivel por ticker",
    }

"""
Rotas de ranking de acoes.

GET /api/v1/ranking/run              -- executa ranking
GET /api/v1/ranking/metodologias     -- lista metodologias disponíveis

Exemplos:
  GET /api/v1/ranking/run?metodologia=magic_formula&top_n=10
  GET /api/v1/ranking/run?metodologia=barsi&top_n=20&min_market_cap_bi=5
  GET /api/v1/ranking/run?metodologia=composite&tickers=PETR4,VALE3,ITUB4
"""
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from finanalytics_ai.application.services.ranking_service import METODOLOGIAS

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/ranking", tags=["Ranking"])


def _get_service(request: Request) -> Any:
    svc = getattr(request.app.state, "ranking_service", None)
    if svc is None:
        raise HTTPException(503, "RankingService nao inicializado")
    return svc


@router.get("/run", summary="Ranking de acoes por metodologia")
async def run_ranking(
    request: Request,
    metodologia: str = Query(
        default="composite",
        description="magic_formula | barsi | quality | value | composite",
    ),
    top_n: int = Query(default=20, ge=1, le=100, description="Numero de acoes no ranking"),
    min_market_cap_bi: float = Query(
        default=1.0,
        ge=0.0,
        description="Market cap minimo em bilhoes R$ (0 = sem filtro)",
    ),
    tickers: str | None = Query(
        default=None,
        description="Comma-separated. Ex: PETR4,VALE3,ITUB4. Vazio = todos do banco",
    ),
) -> dict[str, Any]:
    """
    Gera ranking de acoes usando dados Fintz.

    Metodologias:
    - **magic_formula**: Greenblatt — ROIC alto + EV/EBIT baixo
    - **barsi**: DY alto + ROE solido + divida controlada
    - **quality**: ROE, ROIC, margens e liquidez
    - **value**: P/L, P/VP, EV/EBITDA baixos
    - **composite**: qualidade (40%) + value (30%) + proventos (30%)
    """
    svc = _get_service(request)

    tickers_list = None
    if tickers:
        tickers_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    try:
        result = await svc.rank(
            metodologia=metodologia,
            top_n=top_n,
            min_market_cap_bi=min_market_cap_bi,
            tickers_filter=tickers_list,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("ranking.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/metodologias", summary="Lista metodologias de ranking disponíveis")
async def get_metodologias() -> dict[str, Any]:
    """Lista as metodologias de ranking disponíveis."""
    return {
        "metodologias": [
            {"key": k, "descricao": v}
            for k, v in METODOLOGIAS.items()
        ],
        "nota": "Todos os dados sao de fintz_indicadores (banco local, zero latencia)",
    }

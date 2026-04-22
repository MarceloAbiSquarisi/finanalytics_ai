"""finanalytics_ai.interfaces.api.routes.patrimony"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
import structlog

from finanalytics_ai.interfaces.api.dependencies import get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/patrimony", tags=["Patrimônio"])

DEFAULT_CDI = 10.65
DEFAULT_SELIC = 10.65
DEFAULT_IPCA = 4.83


def _svc(request: Request, session: AsyncSession):
    from fastapi import HTTPException

    from finanalytics_ai.application.services.patrimony_service import PatrimonyService
    from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
        SQLPortfolioRepository as PortfolioRepository,
    )

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "Market data client não disponível")

    port_repo = PortfolioRepository(session)

    # RF repo — acessa via service para reutilizar a lógica de list_portfolios
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import RFPortfolioRepository

    rf_repo = _RFRepoAdapter(RFPortfolioRepository(session))

    return PatrimonyService(port_repo, rf_repo, market)


class _RFRepoAdapter:
    """Adapta RFPortfolioRepository para interface esperada pelo PatrimonyService."""

    def __init__(self, repo) -> None:
        self._repo = repo

    async def list_portfolios(self, user_id: str) -> list[dict]:
        portfolios = await self._repo.list_portfolios(user_id)
        return [{"portfolio_id": p.portfolio_id, "name": p.name} for p in portfolios]

    async def get_portfolio(self, portfolio_id: str):
        return await self._repo.get_portfolio(portfolio_id)


@router.get("/consolidated/{user_id}")
async def consolidated_snapshot(
    user_id: str,
    target_eq: float = Query(default=40.0, description="Meta % Ações"),
    target_etf: float = Query(default=20.0, description="Meta % ETFs"),
    target_rf: float = Query(default=35.0, description="Meta % Renda Fixa"),
    target_cash: float = Query(default=5.0, description="Meta % Caixa"),
    cdi: float = Query(default=DEFAULT_CDI),
    selic: float = Query(default=DEFAULT_SELIC),
    ipca: float = Query(default=DEFAULT_IPCA),
    session: AsyncSession = Depends(get_db_session),
    request: Request = None,
) -> dict:
    """
    Patrimônio consolidado: Ações + ETFs + Renda Fixa + Caixa.
    Inclui breakdown por classe, P&L total e desvio vs metas de alocação.
    """
    from fastapi import HTTPException

    try:
        if request is None:
            raise HTTPException(503, "Request context unavailable")
        targets = {
            "Ações": target_eq,
            "ETFs": target_etf,
            "Renda Fixa": target_rf,
            "Caixa": target_cash,
        }
        return await _svc(request, session).consolidated_snapshot(
            user_id=user_id, targets=targets, cdi=cdi / 100, selic=selic / 100, ipca=ipca / 100
        )
    except Exception as e:
        logger.error("patrimony.error", error=str(e))
        raise HTTPException(500, str(e)) from e


@router.get("/ir-planning/{user_id}")
async def ir_planning(
    user_id: str,
    cdi: float = Query(default=DEFAULT_CDI),
    selic: float = Query(default=DEFAULT_SELIC),
    ipca: float = Query(default=DEFAULT_IPCA),
    session: AsyncSession = Depends(get_db_session),
    request: Request = None,
) -> list[dict]:
    """
    Planejamento tributário: para cada título RF, mostra quanto de IR
    pagaria hoje vs em cada breakpoint fiscal (180/360/720 dias).
    Inclui recomendação de timing em linguagem natural.
    """
    from fastapi import HTTPException

    try:
        if request is None:
            raise HTTPException(503, "Request context unavailable")
        return await _svc(request, session).ir_planning(
            user_id=user_id, cdi=cdi / 100, selic=selic / 100, ipca=ipca / 100
        )
    except Exception as e:
        logger.error("ir_planning.error", error=str(e))
        raise HTTPException(500, str(e)) from e

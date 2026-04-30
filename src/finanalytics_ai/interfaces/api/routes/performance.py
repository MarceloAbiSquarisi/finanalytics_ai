"""
finanalytics_ai.interfaces.api.routes.performance
───────────────────────────────────────────────────
GET /api/v1/portfolios/{portfolio_id}/performance?period=1y

Períodos válidos: 1mo, 3mo, 6mo, 1y, 2y, 3y, 5y, ytd, max
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
import structlog

from finanalytics_ai.application.services.performance_service import (
    PerformanceError,
    PerformanceService,
)
from finanalytics_ai.interfaces.api.dependencies import get_current_user, get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter()


async def _get_performance_svc(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> PerformanceService:
    from fastapi import HTTPException

    from finanalytics_ai.application.services.performance_service import PerformanceService
    from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
        SQLPortfolioRepository,
    )

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, detail="Market client não disponível.")
    return PerformanceService(SQLPortfolioRepository(session), market)


@router.get("/api/v1/portfolios/{portfolio_id}/performance")
async def get_portfolio_performance(
    portfolio_id: str,
    period: str = Query("1y", description="1mo|3mo|6mo|1y|2y|3y|5y|ytd|max"),
    svc: PerformanceService = Depends(_get_performance_svc),
) -> dict[str, Any]:
    try:
        result = await svc.get_performance(portfolio_id, period)
        m = result.metrics
        return {
            "portfolio_id": result.portfolio_id,
            "portfolio_name": result.portfolio_name,
            "period": result.period,
            "benchmark": "BOVA11 (Ibovespa ETF)",
            "metrics": {
                "total_return_pct": m.total_return_pct,
                "annualized_return_pct": m.annualized_return_pct,
                "benchmark_total_pct": m.benchmark_total_pct,
                "benchmark_annualized_pct": m.benchmark_annualized_pct,
                "excess_return_pct": m.excess_return_pct,
                "volatility_annual_pct": m.volatility_annual_pct,
                "max_drawdown_pct": m.max_drawdown_pct,
                "max_drawdown_start": m.max_drawdown_start,
                "max_drawdown_end": m.max_drawdown_end,
                "var_95_pct": m.var_95_pct,
                "cvar_95_pct": m.cvar_95_pct,
                "sharpe_ratio": m.sharpe_ratio,
                "calmar_ratio": m.calmar_ratio,
                "beta": m.beta,
                "alpha_pct": m.alpha_pct,
                "correlation": m.correlation,
                "period_days": m.period_days,
                "start_date": m.start_date,
                "end_date": m.end_date,
                "best_day_pct": m.best_day_pct,
                "worst_day_pct": m.worst_day_pct,
                "positive_days": m.positive_days,
                "negative_days": m.negative_days,
                "win_rate_pct": m.win_rate_pct,
            },
            "equity_curve": [
                {
                    "date": p.date,
                    "portfolio": p.portfolio,
                    "benchmark": p.benchmark,
                    "drawdown": p.drawdown,
                }
                for p in result.equity_curve
            ],
            "monthly_returns": [
                {
                    "year": r.year,
                    "month": r.month,
                    "portfolio_pct": r.portfolio_pct,
                    "benchmark_pct": r.benchmark_pct,
                }
                for r in result.monthly_returns
            ],
            "positions_contribution": result.positions_contribution,
        }
    except PerformanceError as e:
        msg = str(e)
        status_code = 404 if "não encontrado" in msg else 422
        raise HTTPException(status_code, detail=msg) from e
    except Exception as e:
        logger.error("performance.route_error", error=str(e))
        raise HTTPException(500, detail=f"Erro interno: {e}") from e


@router.get("/api/v1/wallet/accounts/{account_id}/performance")
async def get_account_performance(
    account_id: str,
    request: Request,
    period: str = Query("1y", description="1mo|3mo|6mo|1y|2y|3y|5y|ytd|max"),
    session: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """Endpoint canônico: aceita account_id e resolve para o portfolio 1:1.

    Refactor 25/abr (1 portfolio por conta): API exposta usa account_id.
    O portfolio_id continua existindo internamente (FK) mas não é mais
    user-facing. Retorna mesmo payload de /portfolios/{id}/performance,
    com `account_id` e `account_label` em vez de `portfolio_id`/`portfolio_name`.
    """
    from sqlalchemy import text as sql_text

    from finanalytics_ai.application.services.performance_service import PerformanceService
    from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
        SQLPortfolioRepository,
    )

    # Validação de propriedade da conta + resolve portfolio_id
    row = (
        (
            await session.execute(
                sql_text("""
            SELECT p.id AS portfolio_id, p.name, ia.apelido, ia.institution_name, ia.user_id
              FROM investment_accounts ia
              LEFT JOIN portfolios p ON p.investment_account_id = ia.id
             WHERE ia.id = :acc_id AND ia.is_active
             LIMIT 1
        """),
                {"acc_id": account_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, detail="Conta não encontrada")
    if str(row["user_id"]) != str(user.user_id):
        raise HTTPException(403, detail="Conta não pertence ao usuário")
    if not row["portfolio_id"]:
        raise HTTPException(422, detail="Conta sem portfolio interno (estado inválido)")
    portfolio_id = row["portfolio_id"]
    label = row["apelido"] or row["institution_name"] or f"Conta {account_id[:8]}"

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, detail="Market client não disponível.")
    svc = PerformanceService(SQLPortfolioRepository(session), market)
    try:
        result = await svc.get_performance(portfolio_id, period)
        m = result.metrics
        return {
            "account_id": account_id,
            "account_label": label,
            "period": result.period,
            "benchmark": "BOVA11 (Ibovespa ETF)",
            "metrics": {
                "total_return_pct": m.total_return_pct,
                "annualized_return_pct": m.annualized_return_pct,
                "benchmark_total_pct": m.benchmark_total_pct,
                "benchmark_annualized_pct": m.benchmark_annualized_pct,
                "excess_return_pct": m.excess_return_pct,
                "volatility_annual_pct": m.volatility_annual_pct,
                "max_drawdown_pct": m.max_drawdown_pct,
                "max_drawdown_start": m.max_drawdown_start,
                "max_drawdown_end": m.max_drawdown_end,
                "var_95_pct": m.var_95_pct,
                "cvar_95_pct": m.cvar_95_pct,
                "sharpe_ratio": m.sharpe_ratio,
                "calmar_ratio": m.calmar_ratio,
                "beta": m.beta,
                "alpha_pct": m.alpha_pct,
                "correlation": m.correlation,
                "period_days": m.period_days,
                "start_date": m.start_date,
                "end_date": m.end_date,
                "best_day_pct": m.best_day_pct,
                "worst_day_pct": m.worst_day_pct,
                "positive_days": m.positive_days,
                "negative_days": m.negative_days,
                "win_rate_pct": m.win_rate_pct,
            },
            "equity_curve": [
                {
                    "date": p.date,
                    "portfolio": p.portfolio,
                    "benchmark": p.benchmark,
                    "drawdown": p.drawdown,
                }
                for p in result.equity_curve
            ],
            "monthly_returns": [
                {
                    "year": r.year,
                    "month": r.month,
                    "portfolio_pct": r.portfolio_pct,
                    "benchmark_pct": r.benchmark_pct,
                }
                for r in result.monthly_returns
            ],
            "positions_contribution": result.positions_contribution,
        }
    except PerformanceError as e:
        msg = str(e)
        status_code = 404 if "não encontrado" in msg else 422
        raise HTTPException(status_code, detail=msg) from e
    except Exception as e:
        logger.error("performance.account_route_error", error=str(e))
        raise HTTPException(500, detail=f"Erro interno: {e}") from e

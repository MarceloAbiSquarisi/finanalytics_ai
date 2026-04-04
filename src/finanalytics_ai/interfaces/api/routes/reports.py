"""
finanalytics_ai.interfaces.api.routes.reports
Rota de exportacao de relatorios.

GET /api/v1/portfolios/{portfolio_id}/report.pdf
  Gera e retorna o relatorio PDF do portfolio em tempo real.
  Enriquece o snapshot com indicadores Fintz dos ativos da carteira.
"""

import asyncio
import io
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from finanalytics_ai.interfaces.api.dependencies import get_portfolio_service
from finanalytics_ai.application.services.portfolio_service import PortfolioService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Reports"])


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump()
    if hasattr(snapshot, "dict"):
        return snapshot.dict()
    import dataclasses; return dataclasses.asdict(snapshot) if dataclasses.is_dataclass(snapshot) else vars(snapshot)


async def _fetch_fintz_indicators(tickers: list[str]) -> list[dict[str, Any]]:
    """
    Busca indicadores Fintz para os tickers da carteira.
    Usa FintzScreenerService se disponivel, senao retorna lista vazia.
    """
    if not tickers:
        return []
    try:
        from finanalytics_ai.application.services.fintz_screener_service import (
            FintzScreenerService,
        )
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.domain.screener.engine import FilterCriteria

        svc = FintzScreenerService(get_session_factory())
        criteria = FilterCriteria()  # sem filtros -- pega todos
        result = await svc.screen(criteria=criteria, tickers_filter=tickers)

        indicadores = []
        for fd in result:
            indicadores.append({
                "ticker":       fd.ticker,
                "pe":           fd.pe,
                "pvp":          fd.pvp,
                "dy":           fd.dy,
                "roe":          fd.roe,
                "roic":         fd.roic,
                "ebitda_margin": fd.ebitda_margin,
                "net_margin":   fd.net_margin,
                "debt_equity":  fd.debt_equity,
                "market_cap":   fd.market_cap,
            })
        return indicadores
    except Exception as exc:
        logger.warning("report.fintz_indicators_failed", error=str(exc))
        return []


@router.get(
    "/api/v1/portfolios/{portfolio_id}/report.pdf",
    response_class=StreamingResponse,
    summary="Exportar relatorio PDF mensal da carteira",
    description=(
        "Gera relatorio PDF com posicoes, P&L, alocacao e "
        "indicadores fundamentalistas Fintz dos ativos."
    ),
)
async def export_portfolio_pdf(
    portfolio_id: str,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> StreamingResponse:
    """
    Exporta relatorio PDF mensal do portfolio.

    Fluxo:
      1. Busca snapshot (posicoes, P&L, cotacoes)
      2. Enriquece com indicadores Fintz dos ativos
      3. Gera PDF em asyncio.to_thread (CPU-bound)
      4. Retorna StreamingResponse
    """
    try:
        snapshot = await svc.get_snapshot(portfolio_id)
    except Exception as exc:
        logger.warning("report.snapshot_failed", portfolio_id=portfolio_id, error=str(exc))
        raise HTTPException(
            status_code=404,
            detail={"error": "PORTFOLIO_NOT_FOUND", "message": str(exc)},
        ) from exc

    snap_dict = _snapshot_to_dict(snapshot)

    # Enriquece com indicadores Fintz
    positions = snap_dict.get("positions", [])
    tickers = [p.get("ticker", "") for p in positions if p.get("ticker")]
    if tickers:
        snap_dict["indicadores_fintz"] = await _fetch_fintz_indicators(tickers)

    logger.info(
        "report.pdf.generating",
        portfolio_id=portfolio_id,
        positions=len(positions),
        indicadores=len(snap_dict.get("indicadores_fintz", [])),
    )

    try:
        from finanalytics_ai.infrastructure.reports.portfolio_pdf import generate_portfolio_pdf

        pdf_bytes: bytes = await asyncio.to_thread(generate_portfolio_pdf, snap_dict)
    except Exception as exc:
        logger.error("report.pdf.failed", portfolio_id=portfolio_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail={"error": "REPORT_GENERATION_FAILED", "message": "Erro ao gerar PDF."},
        ) from exc

    from datetime import datetime
    month_year = datetime.now().strftime("%Y%m")
    filename = f"relatorio_{portfolio_id[:8]}_{month_year}.pdf"

    logger.info("report.pdf.ready", portfolio_id=portfolio_id, size_kb=len(pdf_bytes) // 1024)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
            "X-Report-Positions": str(len(positions)),
        },
    )

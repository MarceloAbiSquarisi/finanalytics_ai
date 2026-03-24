"""
interfaces/api/routes/fundamental_analysis.py
Rotas de análise fundamentalista e geração de PDF.

GET  /api/v1/fundamental/{ticker}           — dados JSON de empresa única
GET  /api/v1/fundamental/{ticker}/report.pdf — PDF empresa única
POST /api/v1/fundamental/compare            — dados JSON comparativo
POST /api/v1/fundamental/compare/report.pdf — PDF comparativo
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from finanalytics_ai.interfaces.api.dependencies import get_current_user

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fundamental", tags=["Fundamental Analysis"])


# ── Modelos ───────────────────────────────────────────────────────────────────
class CompareRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=10,
                                description="2 a 10 tickers para comparar")
    periodo_anos: int = Field(default=5, ge=1, le=10)


# ── Dependencies ──────────────────────────────────────────────────────────────
def _get_svc(request: Request) -> Any:
    svc = getattr(request.app.state, "fundamental_analysis_service", None)
    if svc is None:
        raise HTTPException(503, "FundamentalAnalysisService não inicializado")
    return svc


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/{ticker}", summary="Dados fundamentalistas de uma empresa")
async def get_fundamental_data(
    ticker: str,
    request: Request,
    periodo_anos: int = Query(default=5, ge=1, le=10),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Retorna dados fundamentalistas completos de uma empresa (JSON)."""
    svc = _get_svc(request)
    try:
        data = await svc.get_single_company_data(ticker.upper(), periodo_anos)
        return data
    except Exception as exc:
        logger.error("fundamental.get_data.failed", ticker=ticker, error=str(exc))
        raise HTTPException(500, f"Erro ao buscar dados: {exc}") from exc


@router.get(
    "/{ticker}/report.pdf",
    summary="Relatório PDF de empresa única",
    response_class=StreamingResponse,
)
async def export_single_pdf(
    ticker: str,
    request: Request,
    periodo_anos: int = Query(default=5, ge=1, le=10),
    current_user: Any = Depends(get_current_user),
) -> StreamingResponse:
    """Gera e retorna relatório PDF completo de análise fundamentalista."""
    svc = _get_svc(request)
    try:
        data = await svc.get_single_company_data(ticker.upper(), periodo_anos)
    except Exception as exc:
        raise HTTPException(500, f"Erro ao buscar dados: {exc}") from exc

    try:
        from finanalytics_ai.infrastructure.reports.fundamental_pdf import (
            generate_fundamental_single,
        )
        pdf_bytes: bytes = await asyncio.to_thread(generate_fundamental_single, data)
    except Exception as exc:
        logger.error("fundamental.pdf.failed", ticker=ticker, error=str(exc))
        raise HTTPException(500, "Erro ao gerar PDF") from exc

    filename = f"analise_{ticker.upper()}_{periodo_anos}a.pdf"
    logger.info("fundamental.pdf.ready", ticker=ticker,
                size_kb=len(pdf_bytes) // 1024)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/compare", summary="Dados comparativos (JSON)")
async def get_comparative_data(
    body: CompareRequest,
    request: Request,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    svc = _get_svc(request)
    try:
        data = await svc.get_comparative_data(body.tickers, body.periodo_anos)
        return data
    except Exception as exc:
        raise HTTPException(500, f"Erro ao buscar dados: {exc}") from exc


@router.post(
    "/compare/report.pdf",
    summary="Relatório PDF comparativo",
    response_class=StreamingResponse,
)
async def export_comparative_pdf(
    body: CompareRequest,
    request: Request,
    current_user: Any = Depends(get_current_user),
) -> StreamingResponse:
    """Gera relatório PDF comparativo entre 2 a 10 empresas."""
    svc = _get_svc(request)
    try:
        data = await svc.get_comparative_data(body.tickers, body.periodo_anos)
    except Exception as exc:
        raise HTTPException(500, f"Erro ao buscar dados: {exc}") from exc

    try:
        from finanalytics_ai.infrastructure.reports.fundamental_pdf import (
            generate_fundamental_comparative,
        )
        pdf_bytes: bytes = await asyncio.to_thread(generate_fundamental_comparative, data)
    except Exception as exc:
        logger.error("fundamental.compare.pdf.failed", error=str(exc))
        raise HTTPException(500, "Erro ao gerar PDF comparativo") from exc

    tickers_str = "_".join(body.tickers[:5])
    filename = f"comparativo_{tickers_str}_{body.periodo_anos}a.pdf"
    logger.info("fundamental.compare.pdf.ready",
                tickers=body.tickers, size_kb=len(pdf_bytes) // 1024)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

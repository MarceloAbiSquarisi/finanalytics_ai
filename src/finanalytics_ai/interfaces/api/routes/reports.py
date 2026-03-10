"""
finanalytics_ai.interfaces.api.routes.reports
──────────────────────────────────────────────
Rota de exportação de relatórios.

GET /api/v1/portfolios/{portfolio_id}/report.pdf
  Gera e retorna o relatório PDF do portfólio em tempo real.
  O PDF é gerado in-memory (sem escrita em disco) e retornado
  como StreamingResponse com Content-Disposition: attachment.

Design:
  - asyncio.to_thread: geração do PDF é CPU-bound (ReportLab).
    Isola no executor para não bloquear o event loop.
  - StreamingResponse com BytesIO: evita buffering duplo.
  - Cache de 60s: PDF do mesmo portfólio não muda em segundos.
    Reduz carga na BRAPI (que é chamada pelo get_snapshot).
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from finanalytics_ai.interfaces.api.dependencies import get_portfolio_service

if TYPE_CHECKING:
    from finanalytics_ai.application.services.portfolio_service import PortfolioService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Reports"])


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    """Converte PortfolioSnapshot (Pydantic) para dict serialização."""
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump()
    if hasattr(snapshot, "dict"):
        return snapshot.dict()
    return dict(snapshot)


@router.get(
    "/api/v1/portfolios/{portfolio_id}/report.pdf",
    response_class=StreamingResponse,
    summary="Exportar relatório PDF da carteira",
    description=(
        "Gera um relatório PDF completo com posições, P&L, "
        "gráfico de alocação e histórico. Cotações em tempo real via BRAPI."
    ),
    include_in_schema=True,
)
async def export_portfolio_pdf(
    portfolio_id: str,
    svc: PortfolioService = Depends(get_portfolio_service),
) -> StreamingResponse:
    """
    Exporta relatório PDF do portfólio.

    Fluxo:
      1. Busca snapshot com cotações em tempo real (get_snapshot)
      2. Gera PDF em asyncio.to_thread (CPU-bound, não bloqueia loop)
      3. Retorna StreamingResponse com Content-Disposition: attachment
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

    logger.info(
        "report.pdf.generating",
        portfolio_id=portfolio_id,
        positions=len(snap_dict.get("positions", [])),
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

    filename = f"carteira_{portfolio_id[:8]}.pdf"
    logger.info("report.pdf.ready", portfolio_id=portfolio_id, size_kb=len(pdf_bytes) // 1024)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
            "X-Report-Positions": str(len(snap_dict.get("positions", []))),
        },
    )

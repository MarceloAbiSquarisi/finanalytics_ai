"""finanalytics_ai.interfaces.api.routes.fund_analysis"""

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fund-analysis", tags=["Análise de Lâminas"])

_MAX_UPLOAD = 20 * 1024 * 1024  # 20 MB

def _get_service():
    from finanalytics_ai.application.services.fund_analysis_service import (
        ConfigurationError,
        FundAnalysisError,
        FundAnalysisService
    )

    return FundAnalysisService(), FundAnalysisError, ConfigurationError

@router.get("/status")
async def fund_analysis_status() -> dict:
    """Verifica se a análise de lâminas está disponível (API key configurada)."""
    svc, _, _ = _get_service()
    return {
        "available": svc.is_available(),
        "message": (
            "Análise de lâminas disponível."
            if svc.is_available()
            else "ANTHROPIC_API_KEY não configurada. Adicione ao .env e reinicie o container."
        ),
    }

@router.post("/analyze")
async def analyze_fund(file: UploadFile = File(...)) -> JSONResponse:
    """
    Recebe um PDF de lâmina de fundo e retorna análise completa com recomendação.

    Suporta PDFs de até 20MB.
    Tempo de resposta esperado: 30–90 segundos (processamento pela IA).
    """
    svc, FundAnalysisError, ConfigurationError = _get_service()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(422, "Somente arquivos PDF são aceitos.")

    content = await file.read()
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(413, "PDF muito grande. Máximo: 20MB.")

    try:
        result = await svc.analyze_pdf(content, filename=file.filename)
        return JSONResponse(content=result)
    except ConfigurationError as e:
        raise HTTPException(503, str(e)) from e
    except FundAnalysisError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        logger.error("fund_analysis.unexpected", error=str(e))
        raise HTTPException(500, f"Erro inesperado: {e}") from e

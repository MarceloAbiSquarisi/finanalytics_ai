"""
finanalytics_ai.interfaces.api.routes.sentiment
------------------------------------------------
Rotas de analise de sentimento de noticias.

GET  /api/v1/sentiment/scan          -- busca + analisa noticias RSS
POST /api/v1/sentiment/analyze       -- analisa texto livre
GET  /api/v1/sentiment/scan/{ticker} -- noticias de um ticker especifico
"""
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/sentiment", tags=["Sentimento"])


def _get_service(request: Request) -> Any:
    svc = getattr(request.app.state, "sentiment_service", None)
    if svc is None:
        raise HTTPException(503, "SentimentService nao inicializado — configure ANTHROPIC_API_KEY")
    return svc


# ─── Scan RSS ─────────────────────────────────────────────────────────────────

@router.get("/scan", summary="Busca e analisa noticias do RSS")
async def scan_news(
    request: Request,
    tickers: str = Query(None, description="Tickers filtro (ex: PETR4,VALE3)"),
    max_items: int = Query(10, ge=1, le=50, description="Max noticias a analisar"),
) -> dict[str, Any]:
    """
    Busca noticias de feeds RSS financeiros brasileiros e analisa o sentimento.

    Fontes: InfoMoney, Investing.com BR
    Modelo: Claude Haiku 4.5 (~$0.0016 por noticia)
    """
    svc = _get_service(request)
    ticker_list = [t.strip().upper() for t in tickers.split(",")] if tickers else None

    try:
        news_items = await svc.fetch_news_rss(tickers=ticker_list, max_items=max_items)

        if not news_items:
            return {
                "total": 0,
                "positivas": 0, "negativas": 0, "neutras": 0,
                "score_medio": 0.0,
                "tickers_impactados": {},
                "results": [],
                "scanned_at": "",
                "message": "Nenhuma noticia encontrada nos feeds RSS",
            }

        result = await svc.analyze_batch(news_items)
        return result.to_dict()

    except Exception as exc:
        logger.exception("sentiment.scan.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


# ─── Ticker especifico ────────────────────────────────────────────────────────

@router.get("/scan/{ticker}", summary="Noticias de um ticker especifico")
async def scan_ticker(
    ticker: str,
    request: Request,
    max_items: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """Busca e analisa noticias mencionando o ticker especifico."""
    svc = _get_service(request)
    try:
        news_items = await svc.fetch_news_rss(
            tickers=[ticker.upper()],
            max_items=max_items,
        )
        if not news_items:
            return {"total": 0, "results": [], "ticker": ticker.upper()}

        result = await svc.analyze_batch(news_items)
        d = result.to_dict()
        d["ticker"] = ticker.upper()
        return d

    except Exception as exc:
        logger.exception("sentiment.ticker.error", ticker=ticker, error=str(exc))
        raise HTTPException(500, str(exc)) from exc


# ─── Analise de texto livre ───────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    title: str = Field(..., description="Titulo da noticia")
    content: str = Field(..., description="Conteudo da noticia")
    source: str = Field("", description="Fonte (opcional)")


@router.post("/analyze", summary="Analisa texto livre")
async def analyze_text(
    body: AnalyzeRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Analisa o sentimento de um texto livre (nao precisa ser de RSS).
    Util para testar ou integrar com outras fontes de noticias.
    """
    svc = _get_service(request)
    from finanalytics_ai.application.services.sentiment_service import NewsItem

    news = NewsItem(
        title=body.title,
        content=body.content,
        source=body.source,
    )
    try:
        result = await svc.analyze(news)
        return result.to_dict()
    except Exception as exc:
        logger.exception("sentiment.analyze.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc

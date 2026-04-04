"""
finanalytics_ai.interfaces.api.routes.dividendos
-------------------------------------------------
Painel de dividendos usando fintz_indicadores.

GET /api/v1/dividendos/ranking      -- ranking por DY
GET /api/v1/dividendos/historico    -- DY historico de um ticker
GET /api/v1/dividendos/carteira     -- DY dos tickers da carteira
"""
from typing import Any
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/dividendos", tags=["Dividendos"])


async def _get_session(request: Request):
    sf = getattr(request.app.state, "session_factory", None)
    if sf is None:
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        sf = get_session_factory()
    return sf


@router.get("/ranking", summary="Ranking de acoes por Dividend Yield")
async def ranking_dy(
    request: Request,
    limit: int = Query(30, ge=5, le=100),
    min_dy: float = Query(0.0, ge=0, description="DY minimo (%)"),
) -> dict[str, Any]:
    """
    Retorna ranking de acoes com maior DY (Dividend Yield) mais recente.
    Dados de fintz_indicadores (DividendYield).
    """
    sf = await _get_session(request)
    try:
        async with sf() as s:
            q = text("""
                SELECT DISTINCT ON (ticker)
                    ticker,
                    valor AS dy,
                    data_publicacao
                FROM fintz_indicadores
                WHERE indicador = 'DividendYield'
                  AND valor IS NOT NULL
                  AND valor > :min_dy
                  AND valor < 100
                ORDER BY ticker, data_publicacao DESC
            """)
            result = await s.execute(q, {"min_dy": min_dy / 100})
            rows = result.fetchall()

        # Ordena por DY desc e pega top N
        data = sorted(
            [{"ticker": r[0], "dy_pct": round(float(r[1]) * 100, 2), "data": str(r[2])[:10]} for r in rows],
            key=lambda x: x["dy_pct"],
            reverse=True,
        )[:limit]

        return {
            "total": len(data),
            "min_dy_filtro": min_dy,
            "ranking": data,
        }
    except Exception as exc:
        logger.exception("dividendos.ranking.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/historico", summary="DY historico de um ticker")
async def historico_dy(
    request: Request,
    ticker: str = Query(..., description="Ticker (ex: PETR4)"),
) -> dict[str, Any]:
    """
    Retorna o DY historico anual de um ticker especifico.
    """
    sf = await _get_session(request)
    try:
        ticker = ticker.upper().strip()
        async with sf() as s:
            q = text("""
                SELECT
                    EXTRACT(YEAR FROM data_publicacao)::int AS ano,
                    AVG(valor) AS dy_medio,
                    MAX(valor) AS dy_max,
                    MIN(valor) AS dy_min,
                    COUNT(*) AS observacoes
                FROM fintz_indicadores
                WHERE indicador = 'DividendYield'
                  AND ticker = :ticker
                  AND valor IS NOT NULL
                GROUP BY EXTRACT(YEAR FROM data_publicacao)
                ORDER BY ano DESC
                LIMIT 10
            """)
            result = await s.execute(q, {"ticker": ticker})
            rows = result.fetchall()

        if not rows:
            raise HTTPException(404, f"Sem dados de DY para {ticker}")

        historico = [
            {
                "ano":          row[0],
                "dy_medio_pct": round(float(row[1]) * 100, 2),
                "dy_max_pct":   round(float(row[2]) * 100, 2),
                "dy_min_pct":   round(float(row[3]) * 100, 2),
                "observacoes":  row[4],
            }
            for row in rows
        ]

        dy_atual = historico[0]["dy_medio_pct"] if historico else 0
        media_5a = sum(h["dy_medio_pct"] for h in historico[:5]) / min(5, len(historico))

        return {
            "ticker":     ticker,
            "dy_atual":   dy_atual,
            "media_5a":   round(media_5a, 2),
            "historico":  historico,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("dividendos.historico.error", ticker=ticker, error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.get("/carteira", summary="DY dos tickers da carteira")
async def carteira_dy(
    request: Request,
    tickers: str = Query(..., description="Tickers separados por virgula (ex: PETR4,VALE3)"),
) -> dict[str, Any]:
    """
    Retorna DY atual e historico para uma lista de tickers.
    """
    sf = await _get_session(request)
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(400, "Informe pelo menos 1 ticker")

    try:
        async with sf() as s:
            placeholders = ", ".join([f"'{t}'" for t in ticker_list])
            q = text(f"""
                SELECT DISTINCT ON (ticker)
                    ticker,
                    valor AS dy,
                    data_publicacao
                FROM fintz_indicadores
                WHERE indicador = 'DividendYield'
                  AND ticker IN ({placeholders})
                  AND valor IS NOT NULL
                ORDER BY ticker, data_publicacao DESC
            """)
            result = await s.execute(q)
            rows = result.fetchall()

        data = {
            r[0]: {
                "ticker": r[0],
                "dy_pct": round(float(r[1]) * 100, 2),
                "data":   str(r[2])[:10],
            }
            for r in rows
        }

        dy_medio = sum(v["dy_pct"] for v in data.values()) / len(data) if data else 0

        return {
            "tickers":  ticker_list,
            "dy_medio_carteira": round(dy_medio, 2),
            "ativos":   [data.get(t, {"ticker": t, "dy_pct": None, "data": None}) for t in ticker_list],
        }
    except Exception as exc:
        logger.exception("dividendos.carteira.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc
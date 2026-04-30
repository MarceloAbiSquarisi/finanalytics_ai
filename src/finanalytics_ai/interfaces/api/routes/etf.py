"""finanalytics_ai.interfaces.api.routes.etf — Rotas REST para análise de ETFs."""

from datetime import UTC

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.requests import Request
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/etf", tags=["ETF"])


def _svc(request: Request):
    from finanalytics_ai.application.services.etf_service import ETFService

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "Market data client não disponível")
    return ETFService(market)


# ── Catálogo ──────────────────────────────────────────────────────────────────


@router.get("/catalog")
async def etf_catalog(category: str | None = Query(None)) -> list[dict]:
    """Lista todos os ETFs do catálogo, opcionalmente filtrado por categoria."""
    from finanalytics_ai.domain.etf.entities import ETF_CATALOG

    etfs = ETF_CATALOG
    if category:
        etfs = [e for e in etfs if e.category.lower() == category.lower()]
    return [
        {
            "ticker": e.ticker,
            "name": e.name,
            "benchmark": e.benchmark,
            "category": e.category,
            "ter": e.ter,
            "currency": e.currency,
            "description": e.description,
        }
        for e in etfs
    ]


@router.get("/categories")
async def etf_categories() -> list[str]:
    from finanalytics_ai.domain.etf.entities import ETF_CATEGORIES

    return ETF_CATEGORIES


# ── Metadados editaveis (C3b 24/abr) ──────────────────────────────────────


class EtfMetadataUpsert(BaseModel):
    name: str | None = Field(None, max_length=200)
    benchmark: str | None = Field(None, max_length=100)
    mgmt_fee: float | None = Field(None, ge=0, le=100, description="Taxa administração em %")
    perf_fee: float | None = Field(None, ge=0, le=100, description="Taxa performance em %")
    isin: str | None = Field(None, max_length=12)
    note: str | None = Field(None, max_length=500)


def _repo():
    from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository

    return WalletRepository()


@router.get("/metadata")
async def list_etf_metadata(
    tickers: str | None = Query(None, description="CSV de tickers (opcional)"),
) -> list[dict]:
    """Lista metadata de ETFs. Sem filtro: retorna todos seed + cadastrados."""
    ts = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    return await _repo().list_etf_metadata(ts)


@router.get("/metadata/{ticker}")
async def get_etf_metadata(ticker: str) -> dict:
    data = await _repo().get_etf_metadata(ticker)
    if not data:
        raise HTTPException(404, "ETF não cadastrado")
    return data


@router.put("/metadata/{ticker}")
async def upsert_etf_metadata(ticker: str, body: EtfMetadataUpsert) -> dict:
    """Cria ou atualiza metadata do ETF. Idempotente."""
    from finanalytics_ai.interfaces.api.dependencies import get_current_user  # noqa: F401

    # TODO: require_admin ou require_master aqui? Por enquanto aberto a qualquer usuario logado.
    data = body.model_dump(exclude_none=False)
    return await _repo().upsert_etf_metadata(ticker, data, updated_by=None)


@router.delete("/metadata/{ticker}", status_code=204)
async def delete_etf_metadata(ticker: str) -> None:
    ok = await _repo().delete_etf_metadata(ticker)
    if not ok:
        raise HTTPException(404, "ETF não cadastrado")


# ── Comparativo ───────────────────────────────────────────────────────────────


class CompareRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=10)
    period: str = Field(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$")
    risk_free: float = Field(default=10.65, gt=0, description="CDI % a.a.")


@router.post("/compare")
async def compare_etfs(body: CompareRequest, request: Request) -> dict:
    """
    Compara N ETFs: retorno total, retorno anual, volatilidade, Sharpe,
    drawdown máximo, VaR 95%. Retorna também séries normalizadas (base 100)
    para gráfico de performance.
    """
    try:
        return await _svc(request).compare(
            tickers=body.tickers, period=body.period, risk_free=body.risk_free / 100
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Tracking Error ────────────────────────────────────────────────────────────


@router.get("/tracking-error/{ticker}")
async def tracking_error(
    ticker: str,
    period: str = Query(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$"),
    request: Request = None,
) -> dict:
    """
    Tracking error do ETF vs benchmark definido no catálogo.
    Calcula: TE anualizado, tracking difference, correlação, beta, R², information ratio.
    """
    try:
        if request is None:
            raise HTTPException(503, "Request context unavailable")
        return await _svc(request).tracking_error(ticker, period)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Correlação ────────────────────────────────────────────────────────────────


class CorrelationRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=12)
    period: str = Field(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$")


@router.post("/correlation")
async def etf_correlation(body: CorrelationRequest, request: Request) -> dict:
    """
    Matriz de correlação entre ETFs.
    Retorna matriz NxN, pares mais/menos correlacionados.
    """
    try:
        return await _svc(request).correlation_heatmap(body.tickers, body.period)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Rebalanceamento ───────────────────────────────────────────────────────────


class RebalancePosition(BaseModel):
    ticker: str
    current_value: float = Field(..., ge=0)


class RebalanceRequest(BaseModel):
    positions: list[RebalancePosition]
    target_weights: dict[str, float]  # {ticker: weight_pct}
    new_contribution: float = Field(default=0.0, ge=0)


@router.post("/rebalance")
async def rebalance(body: RebalanceRequest, request: Request) -> dict:
    """
    Calcula rebalanceamento da carteira de ETFs.
    Retorna ações (COMPRAR/VENDER/MANTER) com valores em R$ e unidades aproximadas.
    """
    try:
        return await _svc(request).rebalance(
            positions=[p.model_dump() for p in body.positions],
            target_weights=body.target_weights,
            new_contribution=body.new_contribution,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Sync e Overview ───────────────────────────────────────────────────────────


@router.post("/sync")
async def sync_etf(
    tickers: str = Query(default="", description="Tickers separados por vírgula. Vazio = todos"),
    range_: str = Query(default="1y", alias="range"),
) -> dict:
    """Sincroniza preços de ETFs via BRAPI e persiste no banco."""
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from finanalytics_ai.application.services.etf_sync_service import ETF_TICKERS, sync_etf_prices

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] or ETF_TICKERS
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://finanalytics:secret@postgres:5432/finanalytics"
    )
    engine = create_async_engine(db_url)
    SM = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SM() as s, s.begin():
        result = await sync_etf_prices(s, ticker_list, range_)
    await engine.dispose()
    return {"ok": True, "registros": result, "tickers": ticker_list}


@router.get("/overview")
async def etf_overview(request: Request) -> list[dict]:
    """Retorna overview de todos os ETFs com preço, variação e retorno 12m."""
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from finanalytics_ai.application.services.etf_sync_service import get_etf_overview

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://finanalytics:secret@postgres:5432/finanalytics"
    )
    engine = create_async_engine(db_url)
    SM = sessionmaker(engine, class_=AsyncSession)
    async with SM() as s:
        result = await get_etf_overview(s)
    await engine.dispose()
    return result


@router.get("/history/{ticker}")
async def etf_history(
    ticker: str,
    days: int = Query(252, ge=5, le=1260),
    request: Request = None,
) -> dict:
    """Retorna histórico de preços de um ETF para gráfico."""
    from datetime import datetime
    import os as _os

    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import AsyncSession as _AS, create_async_engine as _cae
    from sqlalchemy.orm import sessionmaker as _SM

    db_url = _os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://finanalytics:secret@postgres:5432/finanalytics"
    )
    _engine = _cae(db_url)
    _SMF = _SM(_engine, class_=_AS)
    async with _SMF() as session:
        rows = await session.execute(
            _text("""
            SELECT data, fechamento, var_dia, volume
            FROM   etf_precos
            WHERE  ticker = :t
              AND  data >= CURRENT_DATE - (:d * INTERVAL '1 day')
              AND  fechamento IS NOT NULL
            ORDER  BY data ASC
        """),
            {"t": ticker.upper(), "d": days},
        )
        candles = []
        for r in rows:
            ts = int(datetime.combine(r.data, datetime.min.time()).replace(tzinfo=UTC).timestamp())
            candles.append(
                {
                    "time": ts,
                    "value": float(r.fechamento),
                    "var_dia": float(r.var_dia) if r.var_dia else None,
                }
            )
    await _engine.dispose()
    return {"ticker": ticker.upper(), "candles": candles, "total": len(candles)}

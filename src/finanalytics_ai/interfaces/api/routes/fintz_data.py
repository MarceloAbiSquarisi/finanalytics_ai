"""
interfaces/api/routes/fintz_data.py

Endpoints de dados históricos Fintz via TimescaleDB.

Todos os endpoints são read-only e servem dados das hypertables:
  - fintz_cotacoes_ts      → /api/v1/fintz/cotacoes/{ticker}
  - fintz_indicadores_ts   → /api/v1/fintz/indicadores/{ticker}
  - fintz_itens_contabeis_ts → /api/v1/fintz/itens/{ticker}

Autenticação: requer Bearer token (get_current_user).
Cache: 5 minutos para dados históricos (raramente mudam intraday).

Design:
  - Dependency injection via Request.app.state (padrão do app existente)
  - 503 graceful quando TimescaleDB indisponível
  - Paginação via limit/offset para listas longas
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import structlog

from finanalytics_ai.interfaces.api.dependencies import get_current_user

router = APIRouter()
logger = structlog.get_logger(__name__)

# Indicadores mais usados — sugestão para o frontend
INDICADORES_VALUATION = ["P/L", "P/VP", "EV/EBITDA", "P/EBITDA", "P/Receita Líquida"]
INDICADORES_RENTABILIDADE = ["ROE", "ROIC", "ROA", "Margem Líquida", "Margem EBITDA"]
INDICADORES_DIVIDENDOS = ["DY", "Payout"]
INDICADORES_ENDIVIDAMENTO = ["Dívida Líquida/EBITDA", "Dívida Líquida/Patrimônio Líquido"]


def _get_repo(request: Request) -> Any:
    """Dependency: retorna o TimescaleFintzRepository do app.state."""
    repo = getattr(request.app.state, "fintz_ts_repo", None)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TimescaleDB indisponível. Tente novamente em instantes.",
        )
    return repo


# ── Cotações ──────────────────────────────────────────────────────────────────


@router.get(
    "/cotacoes/{ticker}",
    summary="Série histórica de cotações",
    response_description="Lista de candles diários ajustados",
)
async def get_cotacoes(
    ticker: str,
    request: Request,
    start: date | None = Query(default=None, description="Data inicial (YYYY-MM-DD)"),
    end: date | None = Query(default=None, description="Data final (YYYY-MM-DD)"),
    limit: Annotated[int, Query(ge=1, le=2520)] = 252,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Retorna série histórica de cotações para um ticker.

    - **limit**: máximo 2520 (10 anos de pregões). Default: 252 (~1 ano).
    - Dados ordenados do mais recente para o mais antigo.
    - Preços ajustados por splits e proventos.
    """
    repo = _get_repo(request)
    data = await repo.get_cotacoes(ticker=ticker.upper(), start=start, end=end, limit=limit)
    return {
        "ticker": ticker.upper(),
        "count": len(data),
        "cotacoes": data,
    }


@router.get("/cotacoes/{ticker}/agregado", summary="Cotações agregadas por período")
async def get_cotacoes_agregadas(
    ticker: str,
    request: Request,
    bucket: Annotated[
        str,
        Query(
            description="Intervalo de agregação", enum=["1 week", "1 month", "3 months", "1 year"]
        ),
    ] = "1 month",
    start: date | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=120)] = 60,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Cotações OHLCV agregadas por bucket temporal (powered by TimescaleDB time_bucket).

    Útil para gráficos de longo prazo sem sobrecarregar o cliente com dados diários.
    """
    repo = _get_repo(request)
    data = await repo.get_cotacoes_agregadas(
        ticker=ticker.upper(), bucket=bucket, start=start, limit=limit
    )
    return {"ticker": ticker.upper(), "bucket": bucket, "count": len(data), "periodos": data}


# ── Indicadores ───────────────────────────────────────────────────────────────


@router.get(
    "/indicadores/{ticker}/latest", summary="Snapshot atual de indicadores fundamentalistas"
)
async def get_indicadores_latest(
    ticker: str,
    request: Request,
    grupo: Annotated[
        str | None,
        Query(
            description="Grupo pré-definido",
            enum=["valuation", "rentabilidade", "dividendos", "endividamento"],
        ),
    ] = None,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Snapshot mais recente de todos os indicadores fundamentalistas.

    Use **grupo** para filtrar por categoria:
    - `valuation`: P/L, P/VP, EV/EBITDA...
    - `rentabilidade`: ROE, ROIC, Margem Líquida...
    - `dividendos`: DY, Payout
    - `endividamento`: Dívida Líquida/EBITDA...
    """
    grupo_map = {
        "valuation": INDICADORES_VALUATION,
        "rentabilidade": INDICADORES_RENTABILIDADE,
        "dividendos": INDICADORES_DIVIDENDOS,
        "endividamento": INDICADORES_ENDIVIDAMENTO,
    }
    indicadores = grupo_map.get(grupo) if grupo else None
    repo = _get_repo(request)
    data = await repo.get_indicadores_latest(ticker=ticker.upper(), indicadores=indicadores)
    return {"ticker": ticker.upper(), "grupo": grupo, "indicadores": data}


@router.get("/indicadores/{ticker}/serie/{indicador}", summary="Série temporal de um indicador")
async def get_indicador_serie(
    ticker: str,
    indicador: str,
    request: Request,
    start: date | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=1000)] = 252,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Série temporal de um único indicador fundamentalista.

    Útil para visualizar evolução de P/L, ROE etc. ao longo do tempo.
    """
    repo = _get_repo(request)
    data = await repo.get_indicadores_serie(
        ticker=ticker.upper(), indicador=indicador, start=start, limit=limit
    )
    return {
        "ticker": ticker.upper(),
        "indicador": indicador,
        "count": len(data),
        "serie": data,
    }


# ── Itens Contábeis ───────────────────────────────────────────────────────────


@router.get("/itens/{ticker}/latest", summary="Snapshot atual de itens contábeis")
async def get_itens_latest(
    ticker: str,
    request: Request,
    tipo_periodo: Annotated[str, Query(enum=["12M", "TRIMESTRAL"])] = "12M",
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Snapshot mais recente dos itens contábeis (DRE, Balanço, Fluxo de Caixa).

    - `12M`: últimos 12 meses (LTM)
    - `TRIMESTRAL`: último trimestre reportado
    """
    repo = _get_repo(request)
    data = await repo.get_itens_latest(ticker=ticker.upper(), tipo_periodo=tipo_periodo)
    return {
        "ticker": ticker.upper(),
        "tipo_periodo": tipo_periodo,
        "itens": data,
    }


@router.get("/itens/{ticker}/serie", summary="Série histórica de itens contábeis")
async def get_itens_serie(
    ticker: str,
    request: Request,
    itens: Annotated[
        list[str] | None,
        Query(description="Itens a filtrar, ex: Receita Líquida, EBITDA"),
    ] = None,
    tipo_periodo: Annotated[str, Query(enum=["12M", "TRIMESTRAL"])] = "12M",
    start: date | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=500)] = 80,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Série histórica de itens contábeis para análise de tendência.

    Útil para gráficos de evolução de receita, EBITDA, dívida etc.
    """
    repo = _get_repo(request)
    data = await repo.get_itens_contabeis(
        ticker=ticker.upper(), itens=itens, tipo_periodo=tipo_periodo, start=start, limit=limit
    )
    return {
        "ticker": ticker.upper(),
        "tipo_periodo": tipo_periodo,
        "count": len(data),
        "itens": data,
    }


# ── Utilitários ───────────────────────────────────────────────────────────────


@router.get("/coverage/{ticker}", summary="Cobertura temporal de dados para um ticker")
async def get_coverage(
    ticker: str, request: Request, current_user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """
    Retorna período de cobertura e contagem de registros por dataset.
    Útil para verificar disponibilidade de dados antes de fazer queries longas.
    """
    repo = _get_repo(request)
    return await repo.get_coverage(ticker=ticker.upper())


@router.get("/tickers", summary="Lista tickers disponíveis")
async def list_tickers(
    request: Request,
    dataset: Annotated[
        str,
        Query(enum=["cotacoes", "indicadores", "itens"]),
    ] = "cotacoes",
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Lista todos os tickers com dados disponíveis em um dataset."""
    repo = _get_repo(request)
    tickers = await repo.list_tickers(dataset=dataset)
    return {"dataset": dataset, "count": len(tickers), "tickers": tickers}

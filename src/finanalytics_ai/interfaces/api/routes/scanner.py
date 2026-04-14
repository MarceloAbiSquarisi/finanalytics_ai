"""
Scanner router — /api/v1/analytics/scanner

Endpoints for scanning technical setups across active tickers.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
import structlog

from finanalytics_ai.application.analytics.indicator_engine import compute
from finanalytics_ai.application.analytics.setup_scanner import (
    SETUP_DEFS,
    aggregate_weekly,
    scan_all,
    scan_ticker,
)
from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.analytics.exceptions import InsufficientDataError
from finanalytics_ai.infrastructure.market_data.candle_repository import fetch_candles
from finanalytics_ai.interfaces.api.routes.scanner_schemas import (
    HistoryEntrySchema,
    HistoryResponse,
    ScanResultResponse,
    SetupDetectionSchema,
    SetupInfoSchema,
    SetupListResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/analytics/scanner")

# ── SQL: active tickers from profit_history_tickers ─────────────────────────

_SQL_ACTIVE_TICKERS = """
SELECT ticker FROM profit_history_tickers WHERE active = true ORDER BY ticker
"""


async def _get_active_tickers() -> list[str]:
    from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

    pool = await get_timescale_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL_ACTIVE_TICKERS)
    return [r["ticker"] for r in rows]


# ── 1. Scan ───────────────────────────────────────────────────────────────────


@router.get("/scan", response_model=ScanResultResponse, tags=["Scanner Setups"])
async def scan_setups(
    setups: str | None = Query(None, description="Comma-separated setup names"),
    direcao: str | None = Query(None, description="long|short|neutral|all"),
    min_volume: float = Query(0, ge=0),
    excluir_futuros: bool = Query(False),
) -> ScanResultResponse:
    """Scan all active tickers for technical setups."""
    settings = get_settings()
    cache_ttl = settings.analytics_scan_cache_ttl

    setup_list = [s.strip() for s in setups.split(",")] if setups else None

    # Validate setup names
    if setup_list:
        invalid = [s for s in setup_list if s not in SETUP_DEFS]
        if invalid:
            raise HTTPException(422, f"Setups desconhecidos: {invalid}")

    try:
        tickers = await _get_active_tickers()
    except Exception as exc:
        logger.error("scanner.tickers.failed", error=str(exc))
        raise HTTPException(503, "TimescaleDB indisponivel") from exc

    if not tickers:
        raise HTTPException(404, "Nenhum ticker ativo em profit_history_tickers")

    # Fetch candles for all tickers
    tickers_candles: dict[str, list] = {}
    for ticker in tickers:
        try:
            candles, _src = await fetch_candles(ticker)
            tickers_candles[ticker] = candles
        except Exception:
            tickers_candles[ticker] = []

    result = scan_all(
        tickers_candles,
        setups=setup_list,
        direcao=direcao,
        min_volume=min_volume,
        excluir_futuros=excluir_futuros,
        cache_ttl=cache_ttl,
    )

    return ScanResultResponse(
        scanned_at=result.scanned_at,
        total_tickers=result.total_tickers,
        tickers_com_dados=result.tickers_com_dados,
        total_signals=result.total_signals,
        duracao_ms=result.duracao_ms,
        signals=[
            SetupDetectionSchema(
                ticker=s.ticker, tipo=s.tipo, setup_name=s.setup_name,
                descricao=s.descricao, direcao=s.direcao, timeframe=s.timeframe,
                strength=s.strength, date=s.date, details=s.details,
                entry_price=s.entry_price, stop_price=s.stop_price,
            )
            for s in result.signals
        ],
        tickers_sem_dados=result.tickers_sem_dados,
    )


# ── 2. List setups ────────────────────────────────────────────────────────────


@router.get("/setups", response_model=SetupListResponse, tags=["Scanner Setups"])
async def list_setups() -> SetupListResponse:
    """List the 9 available technical setups with metadata."""
    items = [
        SetupInfoSchema(
            nome=name,
            descricao=info["descricao"],
            direcao=info["direcao"],
            timeframe=info["timeframe"],
            minimo_candles=info["min_candles"],
        )
        for name, info in SETUP_DEFS.items()
    ]
    return SetupListResponse(total=len(items), setups=items)


# ── 3. History ────────────────────────────────────────────────────────────────


@router.get("/history/{ticker}", response_model=HistoryResponse, tags=["Scanner Setups"])
async def scan_history(
    ticker: str,
    desde: date | None = Query(None, description="Start date (default: 60 days ago)"),
    setup: str | None = Query(None, description="Filter by setup name"),
) -> HistoryResponse:
    """Scan historical candles for setup detections on a single ticker."""
    if setup and setup not in SETUP_DEFS:
        raise HTTPException(422, f"Setup desconhecido: {setup}")

    since = desde or (date.today() - timedelta(days=60))
    setup_filter = [setup] if setup else None

    try:
        candles, _src = await fetch_candles(ticker.upper(), since=since)
    except Exception as exc:
        logger.error("scanner.history.fetch_failed", ticker=ticker, error=str(exc))
        raise HTTPException(503, f"Falha ao buscar candles para {ticker}") from exc

    if not candles:
        raise HTTPException(404, f"Sem candles para {ticker} desde {since}")

    # Walk through each day looking for setups
    all_detections: list[HistoryEntrySchema] = []

    wanted = set(setup_filter) if setup_filter else set(SETUP_DEFS.keys())
    daily_wanted = {s for s in wanted if SETUP_DEFS.get(s, {}).get("timeframe") == "daily"}
    weekly_wanted = {s for s in wanted if SETUP_DEFS.get(s, {}).get("timeframe") == "weekly"}

    # Daily: slide a window through candles
    if daily_wanted:
        min_needed = max(
            (SETUP_DEFS[s]["min_candles"] for s in daily_wanted),
            default=1,
        )
        if len(candles) >= min_needed:
            try:
                indicators = compute(candles, min_candles=min_needed, ticker=ticker.upper())
            except InsufficientDataError:
                indicators = []

            for i in range(1, len(indicators)):
                curr = indicators[i]
                window_candles = candles[: i + 1]
                detections = scan_ticker(
                    window_candles, ticker.upper(),
                    setups=list(daily_wanted), cache_ttl=0,
                )
                for d in detections:
                    if d.date == curr.date:
                        all_detections.append(
                            HistoryEntrySchema(
                                setup_name=d.setup_name, descricao=d.descricao,
                                direcao=d.direcao, timeframe=d.timeframe,
                                strength=d.strength, date=d.date,
                                details=d.details,
                                entry_price=d.entry_price, stop_price=d.stop_price,
                            )
                        )

    # Weekly setups on aggregated weekly candles
    if weekly_wanted and len(candles) >= 10:
        weekly = aggregate_weekly(candles)
        for i in range(2, len(weekly) + 1):
            window = weekly[:i]
            detections = scan_ticker(
                candles,  # original daily candles for compute
                ticker.upper(),
                setups=list(weekly_wanted),
                cache_ttl=0,
            )
            for d in detections:
                if d.date == window[-1].date:
                    all_detections.append(
                        HistoryEntrySchema(
                            setup_name=d.setup_name, descricao=d.descricao,
                            direcao=d.direcao, timeframe=d.timeframe,
                            strength=d.strength, date=d.date,
                            details=d.details,
                            entry_price=d.entry_price, stop_price=d.stop_price,
                        )
                    )

    # Deduplicate by (setup_name, date)
    seen: set[tuple[str, date]] = set()
    unique: list[HistoryEntrySchema] = []
    for d in all_detections:
        key = (d.setup_name, d.date)
        if key not in seen:
            seen.add(key)
            unique.append(d)

    unique.sort(key=lambda x: x.date, reverse=True)

    return HistoryResponse(
        ticker=ticker.upper(),
        desde=since,
        total=len(unique),
        detections=unique,
    )

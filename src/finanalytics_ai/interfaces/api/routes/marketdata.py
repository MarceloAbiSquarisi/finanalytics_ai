"""
marketdata.py - rotas de market data (historico + live via TimescaleDB)

Melhorias (2026-04-11):
- Imports deduplicados e organizados no topo
- SQL injection: ticker e resolution sanitizados com regex antes de interpolacao
- Constantes extraidas (_LIVE_CONTAINER, _VALID_RES)
- Tipos adicionados nas funcoes live
- SSE: intervalo minimo 0.2s para nao sobrecarregar docker exec
- pattern= substituiu regex= deprecado
"""

from __future__ import annotations

import asyncio as _aio
import json as _json
import os
import re
import subprocess
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()


# ── /bars/{ticker} - resampled OHLC (5m, 15m, 30m, 60m...) ─────────────────
from datetime import date as _date

from finanalytics_ai.infrastructure.market_data.resampled_repository import (
    fetch_resampled,
)


@router.get("/bars/{ticker}")
async def get_bars(
    ticker: str,
    interval: int = Query(5, ge=1, le=1440, description="Intervalo em minutos (1..1440)"),
    since: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    materialized_only: bool = Query(False, description="Se true, nao faz fallback on-the-fly"),
    limit: int = Query(2000, ge=1, le=20000),
):
    """Retorna bars OHLCV agregadas de ohlc_1m em N minutos.
    Tenta ohlc_resampled (materialized) primeiro; fallback aggrega on-the-fly.
    """
    t = _sanitize_ticker(ticker)
    since_dt: _date | None = None
    if since:
        try:
            since_dt = _date.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, detail=f"since invalido (YYYY-MM-DD): {since}")
    try:
        bars, source = await fetch_resampled(
            ticker=t,
            interval_minutes=interval,
            since=since_dt,
            allow_on_the_fly=not materialized_only,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    bars = bars[-limit:] if len(bars) > limit else bars
    return {
        "ticker": t,
        "interval_minutes": interval,
        "source": source,
        "count": len(bars),
        "bars": [
            {
                **b,
                "time": b["time"].isoformat()
                if hasattr(b["time"], "isoformat")
                else str(b["time"]),
            }
            for b in bars
        ],
    }


# ── Constantes ────────────────────────────────────────────────────────────────
_LIVE_CONTAINER = os.getenv("TIMESCALE_CONTAINER", "finanalytics_timescale")
_LIVE_USER = os.getenv("TIMESCALE_USER", "finanalytics")
_LIVE_DB = os.getenv("TIMESCALE_DB", "market_data")
_VALID_RES = {"1", "5", "15", "60", "D"}
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,12}$")

_INTERVALS = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 hour",
    "1d": "1 day",
}


def _sanitize_ticker(ticker: str) -> str:
    t = ticker.upper().strip()
    if not _TICKER_RE.match(t):
        raise HTTPException(400, detail=f"Ticker invalido: {ticker!r}")
    return t


# ── Resolução de aliases de futuros ──────────────────────────────────────────
# Espelha _resolve_active_contract do profit_agent.py. Quando agent restartou
# com fix de subscribe alias resolution (commit 30e5772), ticks novos passaram
# a chegar com código vigente (WDOK26) em vez do alias (WDOFUT). Histórico
# misturado: backend de candles deve unificar via `ticker = ANY([alias, vigente])`.
_MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
# Mensais (qualquer mês): WDO, DOL, BGI, OZM
_FUTURES_MONTHLY = {"WDOFUT", "DOLFUT", "BGIFUT", "OZMFUT"}
# Bimestre par (G/J/M/Q/V/Z): WIN, IND
_FUTURES_BIMESTER_EVEN = {"WINFUT", "INDFUT"}
# CCM (Milho): F/H/K/N/U/X (impares)
_FUTURES_ALIASES = _FUTURES_MONTHLY | _FUTURES_BIMESTER_EVEN | {"CCMFUT"}


def _resolve_futures_aliases(ticker: str) -> list[str]:
    """Para alias FUT retorna [alias, contrato_vigente, próximo].
    Espelha _resolve_active_contract do profit_agent.py.
    """
    if ticker not in _FUTURES_ALIASES:
        return [ticker]
    today = _date.today()
    yy = today.year % 100
    out = [ticker]
    if ticker in _FUTURES_MONTHLY:
        prefix = ticker[:3]
        for offset in (1, 2):
            m = today.month + offset
            y = yy
            while m > 12:
                m -= 12
                y += 1
            out.append(f"{prefix}{_MONTH_CODE[m]}{y:02d}")
    elif ticker in _FUTURES_BIMESTER_EVEN:
        prefix = ticker[:3]
        m = today.month
        if m % 2 != 0:
            m += 1
        elif today.day > 15:
            m += 2
        for _ in range(2):
            cur_m = m
            y = yy
            while cur_m > 12:
                cur_m -= 12
                y += 1
            out.append(f"{prefix}{_MONTH_CODE[cur_m]}{y:02d}")
            m += 2
    elif ticker == "CCMFUT":
        ccm_months = {1, 3, 5, 7, 9, 11}  # F/H/K/N/U/X
        m = today.month
        while m not in ccm_months:
            m += 1
            if m > 12:
                m = 1
                yy += 1
        for _ in range(2):
            cur_m = m
            y = yy
            if cur_m > 12:
                cur_m -= 12
                y += 1
            out.append(f"CCM{_MONTH_CODE[cur_m]}{y:02d}")
            m += 2
            while m not in ccm_months and m <= 12:
                m += 1
            if m > 12:
                m -= 12
                yy += 1
    return out


def _sanitize_resolution(resolution: str) -> str:
    if resolution not in _VALID_RES:
        raise HTTPException(400, detail=f"Resolucao invalida. Use: {sorted(_VALID_RES)}")
    return resolution


# ── TimescaleDB historico (asyncpg) ───────────────────────────────────────────
# Sprint Fix UI 22/abr: prioriza TIMESCALE_URL (docker network: timescale:5432)
# sobre PROFIT_TIMESCALE_DSN (hardcoded localhost:5433 — so funciona fora docker).
# Normaliza driver suffix (postgresql+asyncpg -> postgres) para asyncpg standalone.
_TS_DSN_RAW = (
    os.getenv("TIMESCALE_URL")
    or os.getenv("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@timescale:5432/market_data"
)
_TS_DSN = _TS_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


async def _conn() -> asyncpg.Connection:  # type: ignore[type-arg]
    return await asyncpg.connect(_TS_DSN)


# Conexão alternativa para Postgres principal (finanalytics DB) — onde mora
# ohlc_prices (Yahoo daily) usado como fallback de candles para tickers que
# não estão no feed Profit/BRAPI (ETFs, BDRs, FIIs comuns).
_PG_DSN_RAW = os.getenv("DATABASE_URL") or "postgresql://finanalytics:secret@postgres:5432/finanalytics"
_PG_DSN = _PG_DSN_RAW.replace("postgresql+asyncpg://", "postgres://").replace(
    "postgresql://", "postgres://"
)


async def _pg_conn() -> asyncpg.Connection:  # type: ignore[type-arg]
    return await asyncpg.connect(_PG_DSN)


# ── Crypto price fallback via CoinGecko ──────────────────────────────────────
# Cripto não está em ohlc_1m nem ohlc_prices (Yahoo). Quando ticker é crypto e
# fontes locais vazias, busca CoinGecko on-demand com cache 5min em memória.
# CoinGecko API gratuita, sem auth, rate limit ~30/min anonymous.

_CRYPTO_SYMBOL_TO_CG = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
    "LINK": "chainlink", "MATIC": "matic-network", "DOT": "polkadot",
    "USDT": "tether", "USDC": "usd-coin",
    "BTCBRL": "bitcoin", "ETHBRL": "ethereum",
}

_crypto_candles_cache: dict[str, tuple[float, list[dict]]] = {}  # ticker → (ts, candles)
_CRYPTO_CACHE_TTL = 300  # 5 min


async def _fetch_crypto_candles(ticker: str, days: int = 30) -> list[dict]:
    """Busca candles diárias na CoinGecko. Retorna [{ts, open, high, low, close, volume, trades}]."""
    import time as _t
    cg_id = _CRYPTO_SYMBOL_TO_CG.get(ticker.upper().replace("-BRL", "").replace("BRL", ""))
    if not cg_id:
        return []
    cache_key = f"{cg_id}:{days}"
    now = _t.time()
    cached = _crypto_candles_cache.get(cache_key)
    if cached and now - cached[0] < _CRYPTO_CACHE_TTL:
        return cached[1]
    try:
        import httpx
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
        params = {"vs_currency": "brl", "days": str(days), "interval": "daily"}
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                return []
            data = r.json()
        prices = data.get("prices", [])  # [[ts_ms, price_brl], ...]
        volumes = {int(v[0] / 1000 / 86400): v[1] for v in data.get("total_volumes", [])}
        candles = []
        for ts_ms, price in prices:
            day = int(ts_ms / 1000 / 86400)
            from datetime import datetime as _dtmod, UTC
            dt_iso = _dtmod.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None)
            # CoinGecko 'daily' retorna 1 ponto/dia (close); usa mesmo valor pra OHLC
            candles.append({
                "ts": dt_iso,
                "open": price, "high": price, "low": price, "close": price,
                "volume": volumes.get(day, 0), "trades": None,
            })
        _crypto_candles_cache[cache_key] = (now, candles)
        return candles
    except Exception as exc:
        import structlog as _sl
        _sl.get_logger(__name__).warning(
            "marketdata.crypto.coingecko_fail", ticker=ticker, error=str(exc)
        )
        return []


# ── prev_close cache (refresh diario) ────────────────────────────────────────
# Sprint Fix UI 22/abr: change_pct na watchlist exige previous_close por ticker.
# JOIN ohlc_1m a cada request custa ~1s; cacheamos 24h pois muda 1x/dia.
from datetime import datetime as _dt, timezone as _tz

_PREV_CLOSE_CACHE: dict[str, float] = {}
_PREV_CLOSE_REFRESHED_AT: _dt | None = None
_PREV_CLOSE_LOCK = _aio.Lock()


async def _get_prev_close_map() -> dict[str, float]:
    """Retorna prev_close por ticker. Cache 24h, refresh on first call de cada dia.
    Usa o ultimo close disponivel em ohlc_1m anterior a CURRENT_DATE.
    """
    global _PREV_CLOSE_CACHE, _PREV_CLOSE_REFRESHED_AT
    now = _dt.now(_tz.utc)
    if _PREV_CLOSE_REFRESHED_AT and _PREV_CLOSE_REFRESHED_AT.date() == now.date():
        return _PREV_CLOSE_CACHE
    async with _PREV_CLOSE_LOCK:
        if _PREV_CLOSE_REFRESHED_AT and _PREV_CLOSE_REFRESHED_AT.date() == now.date():
            return _PREV_CLOSE_CACHE
        try:
            conn = await _conn()
            rows = await conn.fetch("""
                WITH latest_day AS (
                    SELECT MAX(time::date) AS d
                    FROM ohlc_1m
                    WHERE time < CURRENT_DATE
                )
                SELECT o.ticker, last(o.close, o.time) AS prev_close
                FROM ohlc_1m o, latest_day ld
                WHERE o.time::date = ld.d
                GROUP BY o.ticker
            """)
            await conn.close()
            _PREV_CLOSE_CACHE = {r["ticker"]: float(r["prev_close"]) for r in rows if r["prev_close"] is not None}
            _PREV_CLOSE_REFRESHED_AT = now
        except Exception:
            pass
        return _PREV_CLOSE_CACHE


@router.get("/quotes")
async def get_quotes() -> Any:
    try:
        conn = await _conn()
        rows = await conn.fetch("""
            SELECT DISTINCT ON (ticker)
                ticker, exchange, price AS last_price,
                quantity AS last_qty, time AS last_ts
            FROM profit_ticks ORDER BY ticker, time DESC
        """)
        await conn.close()
        prev_map = await _get_prev_close_map()
        out = []
        for r in rows:
            d = dict(r)
            pc = prev_map.get(d["ticker"])
            if pc and pc > 0:
                d["previous_close"] = pc
                d["change_pct"] = (float(d["last_price"]) - pc) / pc * 100.0
            out.append(d)
        return out
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/ticks/{ticker}")
async def get_ticks(ticker: str, limit: int = Query(500, le=1000)) -> Any:
    t = _sanitize_ticker(ticker)
    try:
        conn = await _conn()
        rows = await conn.fetch(
            "SELECT time, price, quantity, volume, trade_type "
            "FROM profit_ticks WHERE ticker=$1 ORDER BY time DESC LIMIT $2",
            t,
            limit,
        )
        await conn.close()
        return {"ticker": t, "count": len(rows), "ticks": [dict(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/candles/{ticker}")
async def get_candles(
    ticker: str,
    resolution: str = Query("5m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
    limit: int = Query(1000, ge=1, le=10000),
) -> Any:
    t = _sanitize_ticker(ticker)
    bucket = _INTERVALS.get(resolution, "5 minutes")
    # Janela temporal: limit * resolution em minutos.
    _RES_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
    window_min = min(limit * _RES_MIN.get(resolution, 5) * 2, 525600)  # <= 1 ano

    # Sprint Fix UI 22/abr: fallback chain em Python (sem NOT EXISTS view —
    # subquery explodia em 56s). Tenta ohlc_1m primeiro (BRAPI ingestor,
    # acoes Bovespa); se vazio, tenta ohlc_1m_from_ticks (continuous
    # aggregate sobre profit_ticks, futuros DLL). <500ms cada.
    # NOTA: filtro relativo ao último close válido exclui bars com escala errada
    # (~31k/ticker em ohlc_1m populados por ingestor legacy `tick_agg_v1` que dividia
    # por 100). Janela [last*0.4, last*2.5] suporta penny stocks legítimos B3 (ex: VIIA3 ~R$0.40)
    # mas filtra dados em escala 100x menor.
    # Fix backend definitivo: migration para reescalar (×100) ou deletar bars antigos.
    # `ticker = ANY($1)` permite unificar ticks de aliases de futuros (WDOFUT)
    # com ticks do contrato vigente (WDOK26). Histórico mixed após fix
    # subscribe.alias_resolved (commit 30e5772 do profit_agent).
    #
    # Sprint 29/abr: query SEMPRE faz UNION de ohlc_1m + ohlc_1m_from_ticks.
    # Antes era fallback OR, mas se ohlc_1m tinha dados antigos (BRAPI ingestor
    # stale), o fallback nunca era acionado e dados recentes (que estão em
    # ohlc_1m_from_ticks via tick aggregator) ficavam invisíveis. UNION com
    # bucket merge resolve.
    sql_template = """
        WITH source_union AS (
            SELECT time, open, high, low, close, volume, trades
            FROM ohlc_1m WHERE ticker = ANY($1)
              AND time >= NOW() - (INTERVAL '1 minute' * $3)
            UNION ALL
            SELECT time, open, high, low, close, volume, trades
            FROM ohlc_1m_from_ticks WHERE ticker = ANY($1)
              AND time >= NOW() - (INTERVAL '1 minute' * $3)
        ),
        last_valid AS (
            SELECT close AS ref
            FROM source_union
            WHERE close > 0.001
            ORDER BY time DESC
            LIMIT 1
        ),
        bucketed AS (
            SELECT
                time_bucket('{bucket}', time)              AS ts,
                (array_agg(open  ORDER BY time ASC))[1]    AS open,
                MAX(high)                                  AS high,
                MIN(low)                                   AS low,
                (array_agg(close ORDER BY time DESC))[1]   AS close,
                SUM(volume)                                AS volume,
                SUM(trades)                                AS trades
            FROM source_union, last_valid
            WHERE close BETWEEN last_valid.ref * 0.4 AND last_valid.ref * 2.5
              AND open  BETWEEN last_valid.ref * 0.4 AND last_valid.ref * 2.5
            GROUP BY 1
        )
        SELECT ts, open, high, low, close, volume, trades
        FROM (
            SELECT * FROM bucketed
            ORDER BY ts DESC
            LIMIT $2
        ) sub
        ORDER BY ts ASC
    """
    # Fallback final: ohlc_prices (Yahoo daily) — schema com `date` em vez de `time`,
    # sem volume buckets. Usado para tickers fora do feed Profit/BRAPI mas com
    # cobertura Yahoo (ETFs, BDRs, FIIs comuns).
    sql_yahoo_daily = """
        SELECT date::timestamp AS ts, open, high, low, close, volume,
               NULL::int AS trades
        FROM ohlc_prices
        WHERE ticker = ANY($1)
          AND date >= (NOW() - (INTERVAL '1 day' * $3))::date
        ORDER BY date DESC
        LIMIT $2
    """
    # Resolve aliases de futuros: WDOFUT -> [WDOFUT, WDOK26, WDOM26]
    ticker_set = _resolve_futures_aliases(t)
    try:
        conn = await _conn()
        try:
            rows = await conn.fetch(
                sql_template.format(bucket=bucket),
                ticker_set,
                limit,
                window_min,
            )
        finally:
            await conn.close()
        # Fallback Yahoo daily — em conexão Postgres separada (DB diferente)
        if not rows:
            _days = max(int(window_min / 1440), limit)
            try:
                pg = await _pg_conn()
                try:
                    rows = await pg.fetch(sql_yahoo_daily, ticker_set, limit, _days)
                finally:
                    await pg.close()
                if rows:
                    rows = list(reversed(rows))  # ASC
            except Exception as exc_pg:
                # Falha silenciosa do fallback — retorna lista vazia
                import structlog as _sl
                _sl.get_logger(__name__).warning(
                    "marketdata.candles.yahoo_fallback_fail", ticker=t, error=str(exc_pg)
                )
        # Crypto fallback (CoinGecko) — quando ticker é crypto e Yahoo daily não cobriu
        if not rows and t.upper() in _CRYPTO_SYMBOL_TO_CG:
            _days = max(int(window_min / 1440), limit)
            crypto_candles = await _fetch_crypto_candles(t, days=_days)
            if crypto_candles:
                # Já vem ASC da CoinGecko (ts crescente). Aplica limit.
                return {"ticker": t, "resolution": resolution, "candles": crypto_candles[-limit:]}
        return {"ticker": t, "resolution": resolution, "candles": [dict(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/volume/{ticker}")
async def get_volume(ticker: str) -> Any:
    t = _sanitize_ticker(ticker)
    try:
        conn = await _conn()
        row = await conn.fetchrow(
            "SELECT SUM(volume) AS total_volume, SUM(quantity) AS total_qty, COUNT(*) AS trades "
            "FROM profit_ticks WHERE ticker=$1 AND time >= NOW() - INTERVAL '1 day'",
            t,
        )
        await conn.close()
        return {"ticker": t, **(dict(row) if row else {})}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@router.get("/status")
async def get_agent_status() -> Any:
    import aiohttp

    agent_url = os.getenv("PROFIT_AGENT_URL", "http://localhost:8001")
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{agent_url}/status", timeout=aiohttp.ClientTimeout(total=3)) as r,
        ):
            return await r.json()
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


@router.get("/candles/{ticker}/last")
async def get_last_candle(
    ticker: str,
    resolution: str = Query("1m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
) -> Any:
    t = _sanitize_ticker(ticker)
    bucket = _INTERVALS.get(resolution, "1 minute")
    try:
        conn = await _conn()
        row = await conn.fetchrow(
            f"""
            SELECT time_bucket('{bucket}', time) AS ts,
                (array_agg(price ORDER BY time ASC))[1]  AS open,
                MAX(price) AS high, MIN(price) AS low,
                (array_agg(price ORDER BY time DESC))[1] AS close,
                SUM(quantity) AS volume
            FROM profit_ticks
            WHERE ticker=$1 AND time >= NOW() - INTERVAL '2 hours'
            GROUP BY 1 ORDER BY 1 DESC LIMIT 1
        """,
            t,
        )
        await conn.close()
        if not row:
            return {"ticker": t, "candle": None}
        return {"ticker": t, "candle": dict(row)}
    except Exception as e:
        return {"ticker": t, "candle": None, "error": str(e)}


# ── Live Market Data (docker exec psql) ───────────────────────────────────────


def _live_query(sql: str) -> list[dict]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            _LIVE_CONTAINER,
            "psql",
            "-U",
            _LIVE_USER,
            "-d",
            _LIVE_DB,
            "--no-psqlrc",
            "-A",
            "--csv",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    lines = [l for l in result.stdout.strip().splitlines() if l]
    if not lines:
        return []
    header = lines[0].split(",")
    return [dict(zip(header, l.split(","))) for l in lines[1:]]


def _parse_floats(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    for r in rows:
        for k in keys:
            try:
                r[k] = float(r[k])
            except (ValueError, KeyError, TypeError):
                pass
    return rows


@router.get("/live/tickers", summary="Tickers ativos com ultimo preco")
def live_tickers() -> list[dict]:
    rows = _live_query(
        "SELECT DISTINCT ON (ticker) ticker, exchange, "
        "price::text AS last_price, ts::text AS last_ts "
        "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC"
    )
    return _parse_floats(rows, ("last_price",))


@router.get("/live/ticks/{ticker}", summary="Ultimos N ticks brutos")
def live_ticks(ticker: str, limit: int = Query(100, ge=1, le=1000)) -> dict:
    t = _sanitize_ticker(ticker)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, price::text AS price, "
        f"quantity, volume::text AS volume "
        f"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Ticker '{t}' nao encontrado")
    return {"ticker": t, "count": len(rows), "ticks": _parse_floats(rows, ("price", "volume"))}


@router.get("/live/ohlc/{ticker}/latest", summary="Ultima barra OHLCV")
def live_ohlc_latest(ticker: str, resolution: str = Query("1")) -> dict:
    t, r = _sanitize_ticker(ticker), _sanitize_resolution(resolution)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{t}' AND resolution='{r}' ORDER BY ts DESC LIMIT 1"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem dados para '{t}'")
    return _parse_floats(rows, ("open", "high", "low", "close", "volume"))[0]


@router.get("/live/ohlc/{ticker}", summary="Barras OHLCV")
def live_ohlc(
    ticker: str,
    resolution: str = Query("1", description="1, 5, 15, 60 ou D"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    t, r = _sanitize_ticker(ticker), _sanitize_resolution(resolution)
    rows = _live_query(
        f"SELECT ticker, exchange, ts::text AS ts, resolution, "
        f"open::text, high::text, low::text, close::text, "
        f"volume::text, quantity, trade_count "
        f"FROM ohlc WHERE ticker='{t}' AND resolution='{r}' ORDER BY ts DESC LIMIT {limit}"
    )
    if not rows:
        raise HTTPException(404, detail=f"Sem OHLC para '{t}' res={r}")
    bars = list(reversed(_parse_floats(rows, ("open", "high", "low", "close", "volume"))))
    return {"ticker": t, "resolution": r, "count": len(bars), "bars": bars}


# ── SSE Streaming ─────────────────────────────────────────────────────────────


@router.get("/live/sse/tickers", summary="SSE stream de precos ao vivo")
async def sse_tickers(interval: float = Query(1.0, ge=0.2, le=60.0)) -> StreamingResponse:
    async def gen():
        while True:
            try:
                rows = await _aio.to_thread(
                    _live_query,
                    "SELECT DISTINCT ON (ticker) ticker, exchange, "
                    "price::text AS last_price, ts::text AS last_ts "
                    "FROM ticks WHERE ticker != '__warmup__' ORDER BY ticker, ts DESC",
                )
                _parse_floats(rows, ("last_price",))
                yield f"data: {_json.dumps(rows)}\n\n"
            except Exception:
                yield "data: []\n\n"
            await _aio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/live/sse/ticks/{ticker}", summary="SSE stream de ticks de um ticker")
async def sse_ticks(
    ticker: str,
    interval: float = Query(0.5, ge=0.2, le=60.0),
) -> StreamingResponse:
    t = _sanitize_ticker(ticker)

    async def gen():
        last_ts = None
        while True:
            try:
                rows = await _aio.to_thread(
                    _live_query,
                    f"SELECT ticker, exchange, ts::text AS ts, "
                    f"price::text AS price, quantity, volume::text AS volume "
                    f"FROM ticks WHERE ticker='{t}' ORDER BY ts DESC LIMIT 1",
                )
                if rows:
                    r = rows[0]
                    if r.get("ts") != last_ts:
                        last_ts = r.get("ts")
                        _parse_floats([r], ("price", "volume"))
                        yield f"data: {_json.dumps(r)}\n\n"
            except Exception:
                pass
            await _aio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

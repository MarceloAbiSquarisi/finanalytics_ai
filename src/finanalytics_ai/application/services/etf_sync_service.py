"""
finanalytics_ai.application.services.etf_sync_service
───────────────────────────────────────────────────────
Sincroniza preços de ETFs via BRAPI e persiste no banco.

Design decisions:
  - Tabela etf_precos com UNIQUE(ticker, data) — idempotente
  - Busca histórico de 1 ano na primeira carga, delta diário depois
  - Calcula var_dia e var_12m localmente após sync
  - Sem SDK BRAPI — httpx direto para controle de timeout/retry
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")
BRAPI_URL = "https://brapi.dev/api/quote/{tickers}"

# ETFs monitorados
ETF_TICKERS = [
    "BOVA11", "SMAL11", "IVVB11", "HASH11", "XFIX11",
    "GOLD11", "SPXI11", "DIVO11", "NASD11", "EURP11",
]


async def _fetch_brapi(tickers: list[str], range_: str = "1y") -> dict[str, Any]:
    """Busca preços históricos via BRAPI."""
    url = BRAPI_URL.format(tickers=",".join(tickers))
    params: dict[str, str] = {"range": range_, "interval": "1d", "fundamental": "false"}
    if BRAPI_TOKEN:
        params["token"] = BRAPI_TOKEN
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("etf.brapi_error", error=str(exc))
        return {}


async def _fetch_quotes(tickers: list[str]) -> dict[str, Any]:
    """Busca cotação atual (sem histórico) — para overview rápido."""
    url = BRAPI_URL.format(tickers=",".join(tickers))
    params: dict[str, str] = {"fundamental": "false"}
    if BRAPI_TOKEN:
        params["token"] = BRAPI_TOKEN
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("etf.quotes_error", error=str(exc))
        return {}


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


async def sync_etf_prices(
    session: AsyncSession,
    tickers: list[str] | None = None,
    range_: str = "1y",
) -> dict[str, Any]:
    """
    Sincroniza histórico de preços de ETFs via BRAPI.
    Retorna contagem de registros inseridos por ticker.
    """
    tickers = tickers or ETF_TICKERS

    data = await _fetch_brapi(tickers, range_)
    results = data.get("results", [])
    totals: dict[str, int] = {}

    for item in results:
        ticker = item.get("symbol", "").upper()
        historico = item.get("historicalDataPrice", [])
        if not historico:
            continue

        ok = 0
        for bar in historico:
            epoch = bar.get("date")
            if not epoch:
                continue
            try:
                dt = date.fromtimestamp(int(epoch))
            except (ValueError, OSError):
                continue

            close_ = _safe_float(bar.get("close"))
            open_  = _safe_float(bar.get("open"))
            high_  = _safe_float(bar.get("high"))
            low_   = _safe_float(bar.get("low"))
            vol_   = bar.get("volume")
            vol_i  = int(vol_) if vol_ else None
            var_dia = _safe_float(bar.get("changePercent"))

            await session.execute(text("""
                INSERT INTO etf_precos
                    (ticker, data, abertura, fechamento, maxima, minima, volume, var_dia)
                VALUES (:t, :d, :o, :c, :h, :l, :v, :var)
                ON CONFLICT (ticker, data) DO UPDATE SET
                    abertura   = EXCLUDED.abertura,
                    fechamento = EXCLUDED.fechamento,
                    maxima     = EXCLUDED.maxima,
                    minima     = EXCLUDED.minima,
                    volume     = EXCLUDED.volume,
                    var_dia    = EXCLUDED.var_dia
            """), {"t": ticker, "d": dt, "o": open_, "c": close_,
                   "h": high_, "l": low_, "v": vol_i, "var": var_dia})
            ok += 1

        # Upsert info básica do ETF
        nome = item.get("shortName") or item.get("longName") or ticker
        await session.execute(text("""
            INSERT INTO etf_info (ticker, nome)
            VALUES (:t, :n)
            ON CONFLICT (ticker) DO UPDATE SET nome = EXCLUDED.nome, updated_at = NOW()
        """), {"t": ticker, "n": nome})

        totals[ticker] = ok
        logger.info("etf.sync_ok", ticker=ticker, registros=ok)

    await session.commit()
    return totals


async def get_etf_overview(session: AsyncSession) -> list[dict]:
    """
    Retorna overview dos ETFs com último preço, variação, e retorno 12m.
    Tenta banco primeiro, se vazio busca via BRAPI quotes.
    """
    rows = await session.execute(text("""
        SELECT
            e.ticker,
            ei.nome,
            e.fechamento   AS preco,
            e.var_dia,
            e.data,
            e.volume,
            first_p.fechamento AS preco_12m_atras
        FROM etf_precos e
        LEFT JOIN etf_info ei ON ei.ticker = e.ticker
        LEFT JOIN LATERAL (
            SELECT fechamento FROM etf_precos
            WHERE ticker = e.ticker
              AND data <= CURRENT_DATE - INTERVAL '252 days'
            ORDER BY data DESC LIMIT 1
        ) first_p ON true
        WHERE (e.ticker, e.data) IN (
            SELECT ticker, MAX(data) FROM etf_precos GROUP BY ticker
        )
        ORDER BY e.ticker
    """))

    result = []
    for r in rows:
        rent_12m = None
        if r.preco_12m_atras and r.preco_12m_atras > 0:
            rent_12m = round((float(r.preco) / float(r.preco_12m_atras) - 1) * 100, 2)

        result.append({
            "ticker":    r.ticker,
            "nome":      r.nome or r.ticker,
            "preco":     float(r.preco) if r.preco else None,
            "var_dia":   float(r.var_dia) if r.var_dia else None,
            "data":      r.data.isoformat() if r.data else None,
            "volume":    r.volume,
            "rent_12m":  rent_12m,
        })

    # Fallback: se banco vazio, busca quotes ao vivo
    if not result:
        data = await _fetch_quotes(ETF_TICKERS)
        for item in data.get("results", []):
            result.append({
                "ticker":   item.get("symbol", "").upper(),
                "nome":     item.get("shortName") or item.get("symbol"),
                "preco":    _safe_float(item.get("regularMarketPrice")),
                "var_dia":  _safe_float(item.get("regularMarketChangePercent")),
                "data":     None,
                "volume":   item.get("regularMarketVolume"),
                "rent_12m": _safe_float(item.get("fiftyTwoWeekHigh")),
            })

    return result

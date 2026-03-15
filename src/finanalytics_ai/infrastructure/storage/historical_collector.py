"""
HistoricalCollector — Coleta histórico completo de todos os tickers B3.

Estratégia:
  - Lista de tickers via BRAPI /api/available (ou lista local hardcoded como fallback)
  - Para cada ticker: busca histórico máximo disponível (range="max" ou "5y")
  - Salva em /data/ohlcv/{TICKER}/{ANO}.parquet via DataStorageService
  - Controle de progresso: pula tickers já coletados (a menos que force=True)
  - Rate limiting respeitoso: 0.3s entre requests para não banir a API
  - Retomável: se o processo morrer, continua de onde parou

Design decision — por que não usar yfinance diretamente?
  O stack já tem BrapiClient integrado e testado. Manter um único adaptador
  de dados de mercado reduz surface area de bugs e mantém a DI limpa.
  Yahoo Finance é fallback automático quando BRAPI não tem o ticker.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from finanalytics_ai.infrastructure.storage.data_storage_service import (
    DataStorageService,
    get_storage,
)

logger = structlog.get_logger(__name__)

# Principais tickers B3 — fallback se a API de listagem falhar
_B3_CORE_TICKERS = [
    # Ibovespa
    "PETR4","VALE3","ITUB4","BBDC4","ABEV3","BBAS3","WEGE3","RENT3","SUZB3","RADL3",
    "EQTL3","VIVT3","JBSS3","RAIL3","SBSP3","GGBR4","CMIG4","CSAN3","HAPV3","UGPA3",
    "BRFS3","PRIO3","EMBR3","TOTS3","MULT3","KLBN11","ENEV3","CPLE6","AZUL4","GOLL4",
    "MRFG3","LWSA3","CYRE3","MRVE3","EVEN3","PDGR3","TEND3","TRIS3","DIRR3","PLPL3",
    # ETFs relevantes
    "BOVA11","SMAL11","IVVB11","SPXI11","HASH11","GOLD11","XINA11",
    # FIIs relevantes
    "HGLG11","XPML11","MXRF11","KNRI11","BTLG11","HGRE11","BCFF11","RBRF11",
    # Índices (BRAPI suporta)
    "^BVSP",
]


class HistoricalCollector:
    """
    Coleta e persiste histórico OHLCV diário para todos os tickers B3.

    Usage:
        collector = HistoricalCollector(brapi_token="...", storage=get_storage())
        await collector.collect_all(force=False)    # pula já coletados
        await collector.collect_ticker("PETR4")     # ticker individual
    """

    RANGE = "5y"          # máximo prático via BRAPI sem timeout
    DELAY_BETWEEN = 0.35  # segundos entre requests (rate limiting)
    BATCH_SIZE = 10       # tickers em paralelo (cuidado com rate limit)

    def __init__(
        self,
        brapi_token: str = "",
        brapi_base_url: str = "https://brapi.dev/api",
        storage: DataStorageService | None = None,
    ) -> None:
        self._token = brapi_token
        self._base = brapi_base_url.rstrip("/")
        self._storage = storage or get_storage()
        self._headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def collect_all(
        self,
        tickers: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Coleta histórico completo para todos os tickers.

        Args:
            tickers: lista customizada, ou None para usar lista padrão B3
            force: se True, recoleta mesmo tickers já presentes

        Returns:
            Relatório {ticker: {"rows": int, "status": "ok"|"skip"|"error"}}
        """
        target = tickers or await self._get_ticker_list()
        logger.info("collector.start", total=len(target), force=force)

        report: dict[str, Any] = {}
        start_time = time.time()

        for i, ticker in enumerate(target):
            # Skip se já temos dados recentes e force=False
            if not force:
                _, newest = self._storage.ohlcv_date_range(ticker)
                if newest is not None:
                    from datetime import date
                    # newest pode ser string "YYYY-MM-DD" ou objeto date
                    if isinstance(newest, str):
                        newest = date.fromisoformat(newest[:10])
                    days_old = (datetime.now(tz=timezone.utc).date() - newest).days
                    if days_old < 3:  # dados com menos de 3 dias = fresh
                        report[ticker] = {"status": "skip", "rows": 0}
                        continue

            try:
                rows = await self.collect_ticker(ticker)
                report[ticker] = {"status": "ok", "rows": rows}
                logger.info(
                    "collector.ticker.ok",
                    ticker=ticker,
                    rows=rows,
                    progress=f"{i+1}/{len(target)}",
                )
            except Exception as e:
                report[ticker] = {"status": "error", "error": str(e)[:100]}
                logger.warning("collector.ticker.error", ticker=ticker, error=str(e)[:100])

            await asyncio.sleep(self.DELAY_BETWEEN)

        elapsed = time.time() - start_time
        ok = sum(1 for v in report.values() if v["status"] == "ok")
        skip = sum(1 for v in report.values() if v["status"] == "skip")
        errors = sum(1 for v in report.values() if v["status"] == "error")
        total_rows = sum(v.get("rows", 0) for v in report.values())

        logger.info(
            "collector.complete",
            ok=ok, skip=skip, errors=errors,
            total_rows=total_rows,
            elapsed_min=round(elapsed / 60, 1),
        )
        return {
            "summary": {"ok": ok, "skip": skip, "errors": errors,
                        "total_rows": total_rows, "elapsed_seconds": round(elapsed)},
            "tickers": report,
        }

    async def collect_ticker(self, ticker: str, range_period: str | None = None) -> int:
        """Coleta e persiste histórico de um ticker. Retorna número de linhas salvas."""
        r = range_period or self.RANGE
        bars = await self._fetch_brapi(ticker, r)

        if not bars:
            bars = await self._fetch_yahoo(ticker, r)

        if not bars:
            raise RuntimeError(f"Sem dados para {ticker} via BRAPI ou Yahoo")

        return self._storage.write_ohlcv(ticker, bars)

    async def collect_intraday(self, ticker: str, interval: str = "1m") -> int:
        """Coleta barras intraday (1m/5m) dos últimos 5 dias."""
        bars = await self._fetch_brapi(ticker, "5d", interval=interval)
        if not bars:
            return 0
        return self._storage.write_intraday(ticker, bars, interval=interval)

    # ── Fetch helpers ─────────────────────────────────────────────────────

    async def _fetch_brapi(
        self,
        ticker: str,
        range_period: str,
        interval: str = "1d",
    ) -> list[dict[str, Any]]:
        url = f"{self._base}/quote/{ticker}"
        params: dict[str, str] = {
            "range": range_period,
            "interval": interval,
            "fundamental": "false",
        }
        if self._token:
            params["token"] = self._token

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=self._headers)
            if resp.status_code != 200:
                raise RuntimeError(f"BRAPI {resp.status_code} para {ticker}")
            data = resp.json()

        results = data.get("results", [])
        if not results:
            return []

        raw_bars = results[0].get("historicalDataPrice") or results[0].get("prices") or []
        return [
            {
                "time":   b.get("date", b.get("time")),
                "open":   b.get("open", b.get("o")),
                "high":   b.get("high", b.get("h")),
                "low":    b.get("low", b.get("l")),
                "close":  b.get("close", b.get("c")),
                "volume": b.get("volume", b.get("v", 0)),
            }
            for b in raw_bars
            if b.get("close") or b.get("c")
        ]

    async def _fetch_yahoo(
        self,
        ticker: str,
        range_period: str,
    ) -> list[dict[str, Any]]:
        """Fallback via Yahoo Finance API (sem autenticação)."""
        # Adapta sufixo para Yahoo: PETR4 → PETR4.SA
        yf_ticker = ticker if "." in ticker or ticker.startswith("^") else f"{ticker}.SA"
        range_map = {"5d": "5d", "1mo": "1mo", "3mo": "3mo", "1y": "1y",
                     "2y": "2y", "5y": "5y", "max": "max"}
        yf_range = range_map.get(range_period, "2y")

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        params = {"range": yf_range, "interval": "1d", "includePrePost": "false"}
        headers = {"User-Agent": "Mozilla/5.0"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()

        try:
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            ohlcv = result["indicators"]["quote"][0]
            bars = []
            for i, ts in enumerate(timestamps):
                c = ohlcv["close"][i]
                if c is None:
                    continue
                bars.append({
                    "time":   ts,
                    "open":   ohlcv["open"][i],
                    "high":   ohlcv["high"][i],
                    "low":    ohlcv["low"][i],
                    "close":  c,
                    "volume": ohlcv.get("volume", [0] * len(timestamps))[i] or 0,
                })
            return bars
        except (KeyError, IndexError, TypeError):
            return []

    async def _get_ticker_list(self) -> list[str]:
        """Busca lista de tickers disponíveis via BRAPI. Fallback para lista interna."""
        try:
            url = f"{self._base}/available"
            params = {"token": self._token} if self._token else {}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    stocks = data.get("stocks", [])
                    if stocks:
                        logger.info("collector.tickers_from_api", count=len(stocks))
                        return [str(t) for t in stocks]
        except Exception as e:
            logger.warning("collector.ticker_list_failed", error=str(e))

        logger.info("collector.using_core_tickers", count=len(_B3_CORE_TICKERS))
        return _B3_CORE_TICKERS

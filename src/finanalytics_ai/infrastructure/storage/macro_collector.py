"""
MacroCollector — Coleta dados macroeconômicos do BCB e Yahoo Finance.

Séries coletadas:
  BCB SGS (gratuito, sem autenticação):
    - SELIC diária (código 11)
    - IPCA mensal (código 433)
    - USD/BRL diário (código 1)
    - EUR/BRL diário (código 21619)
    - IGP-M mensal (código 189)

  Yahoo Finance:
    - IBOV (^BVSP) — índice referência
    - VIX  (^VIX)  — volatilidade global
    - S&P 500 (^GSPC) — mercado americano

Todos salvos como /data/macro/{series}.parquet com colunas [date, value].
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx
import pandas as pd
import structlog

from finanalytics_ai.infrastructure.storage.data_storage_service import (
    DataStorageService,
    get_storage,
)

logger = structlog.get_logger(__name__)

# BCB SGS series: nome → código
_BCB_SERIES: dict[str, int] = {
    "selic":  11,
    "ipca":   433,
    "usd_brl": 1,
    "eur_brl": 21619,
    "igpm":   189,
}

# Yahoo Finance series: nome → símbolo
_YAHOO_SERIES: dict[str, str] = {
    "ibov":  "^BVSP",
    "vix":   "^VIX",
    "sp500": "^GSPC",
}


class MacroCollector:
    """
    Coleta e persiste séries macroeconômicas.

    Usage:
        collector = MacroCollector(storage=get_storage())
        await collector.collect_all()
        df = collector.storage.read_macro("selic")
    """

    def __init__(self, storage: DataStorageService | None = None) -> None:
        self._storage = storage or get_storage()

    @property
    def storage(self) -> DataStorageService:
        return self._storage

    async def collect_all(self) -> dict[str, Any]:
        """Coleta todas as séries macro. Retorna relatório."""
        report: dict[str, Any] = {}

        for name, code in _BCB_SERIES.items():
            try:
                rows = await self._collect_bcb(name, code)
                report[name] = {"source": "BCB", "rows": rows, "status": "ok"}
                logger.info("macro.bcb.ok", series=name, rows=rows)
            except Exception as e:
                report[name] = {"source": "BCB", "status": "error", "error": str(e)[:80]}
                logger.warning("macro.bcb.error", series=name, error=str(e)[:80])

        for name, symbol in _YAHOO_SERIES.items():
            try:
                rows = await self._collect_yahoo(name, symbol)
                report[name] = {"source": "Yahoo", "rows": rows, "status": "ok"}
                logger.info("macro.yahoo.ok", series=name, rows=rows)
            except Exception as e:
                report[name] = {"source": "Yahoo", "status": "error", "error": str(e)[:80]}
                logger.warning("macro.yahoo.error", series=name, error=str(e)[:80])

        return report

    async def _collect_bcb(self, name: str, code: int) -> int:
        """Fetch from BCB SGS API and persist."""
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
            f"?formato=json&dataInicial=01/01/2020"
        )
        _headers = {"User-Agent": "Mozilla/5.0 FinAnalytics/1.0"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=_headers)
            resp.raise_for_status()
            raw = resp.json()

        rows = []
        for item in raw:
            try:
                d = datetime.strptime(item["data"], "%d/%m/%Y").date()
                v = float(str(item["valor"]).replace(",", "."))
                rows.append({"date": d, "value": v})
            except (KeyError, ValueError):
                continue

        if not rows:
            return 0

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        self._storage.write_macro(name, df)
        return len(df)

    async def _collect_yahoo(self, name: str, symbol: str) -> int:
        """Fetch index from Yahoo Finance and persist."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"range": "max", "interval": "1d", "includePrePost": "false"}
        headers = {"User-Agent": "Mozilla/5.0"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        rows = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            rows.append({"date": d, "value": float(close)})

        if not rows:
            return 0

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        self._storage.write_macro(name, df)
        return len(df)

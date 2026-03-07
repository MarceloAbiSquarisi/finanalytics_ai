"""
finanalytics_ai.infrastructure.adapters.yahoo_client
──────────────────────────────────────────────────────
Cliente Yahoo Finance via yfinance como fallback para BRAPI.

Design decisions:

  yfinance vs httpx direto:
    yfinance abstrai a lógica de autenticação dinâmica do Yahoo
    (cookies, crumb), que muda frequentemente. Usar httpx direto
    exigiria reimplementar esse handshake. yfinance é o padrão
    da indústria para dados históricos gratuitos.

  asyncio.to_thread:
    yfinance é síncrono (usa requests internamente). Isolar em
    asyncio.to_thread evita bloquear o event loop. Para uso
    em fallback (baixa frequência), o overhead é aceitável.

  Normalização de tickers:
    Yahoo Finance usa sufixos para bolsas:
      B3 (Brasil): PETR4.SA, VALE3.SA, BOVA11.SA
      BDR:         GOGL34.SA (mesmo sufixo)
      FII:         XPLG11.SA
    A função _to_yahoo_ticker() adiciona .SA se não houver sufixo.
    Tickers com sufixo explícito (ex: AAPL, já sem .SA) são mantidos.

  Mapeamento de ranges:
    Yahoo suporta: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
    BRAPI suporta: mesmos + limitações práticas em >2y
    Ranges > 2y são o principal motivo de fallback.

  Formato de retorno:
    Mantém exato mesmo contrato de BrapiClient.get_ohlc_bars():
      list[dict] com chaves: time, open, high, low, close, volume
    Timestamps Unix em segundos (mesmo que BRAPI/TradingView).

  Tratamento de dados ausentes:
    yfinance pode retornar NaN para dias sem negociação.
    Filtramos barras com close=NaN ou 0 antes de retornar.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Ranges Yahoo → intervalos padrão
YAHOO_INTERVAL_MAP: dict[str, str] = {
    "1d":  "1m",
    "5d":  "5m",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1d",
    "1y":  "1d",
    "2y":  "1d",
    "5y":  "1wk",
    "10y": "1wk",
    "ytd": "1d",
    "max": "1mo",
}

# Bolsas B3 — tickers que precisam do sufixo .SA
# Heurística: ticker com 4-6 chars alfanuméricos sem ponto → B3
_B3_PATTERN_LEN_MIN = 4
_B3_PATTERN_LEN_MAX = 7


def _to_yahoo_ticker(ticker: str) -> str:
    """
    Converte ticker da B3 para formato Yahoo Finance.

    PETR4    → PETR4.SA
    PETR4.SA → PETR4.SA  (idempotente)
    AAPL     → AAPL       (mercado americano, mantém)
    BOVA11   → BOVA11.SA  (ETF B3)
    """
    ticker = ticker.upper().strip()
    if "." in ticker:
        return ticker   # Já tem sufixo (PETR4.SA, etc.)

    # Heurística: se tem dígito no final → provavelmente B3
    # PETR4, VALE3, ITUB4, BOVA11, XPLG11...
    if ticker and ticker[-1].isdigit():
        return f"{ticker}.SA"

    # Tickers sem dígito e sem sufixo → assume americano (AAPL, MSFT)
    return ticker


def _normalize_volume(vol: Any) -> int:
    """Converte volume que pode ser float/NaN/None para int."""
    try:
        v = float(vol)
        if v != v:   # NaN check (NaN != NaN)
            return 0
        return int(v)
    except (TypeError, ValueError):
        return 0


def _fetch_ohlc_sync(
    yahoo_ticker: str,
    range_period: str,
    interval: str,
) -> list[dict[str, Any]]:
    """
    Busca dados OHLC do Yahoo Finance de forma síncrona.
    Deve ser chamada via asyncio.to_thread.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yahoo.yfinance_not_installed")
        return []

    try:
        tk = yf.Ticker(yahoo_ticker)
        df = tk.history(period=range_period, interval=interval, auto_adjust=True)

        if df is None or df.empty:
            logger.warning("yahoo.no_data", ticker=yahoo_ticker, range=range_period)
            return []

        bars: list[dict[str, Any]] = []
        for idx, row in df.iterrows():
            close = row.get("Close", 0)
            # Filtra barras inválidas
            if not close or close != close:   # 0 ou NaN
                continue

            # Converte timestamp pandas para Unix segundos
            try:
                ts = int(idx.timestamp())
            except Exception:
                continue

            bars.append({
                "time":   ts,
                "open":   float(row.get("Open", close)),
                "high":   float(row.get("High", close)),
                "low":    float(row.get("Low", close)),
                "close":  float(close),
                "volume": _normalize_volume(row.get("Volume", 0)),
            })

        bars.sort(key=lambda x: x["time"])

        logger.info(
            "yahoo.ohlc.fetched",
            ticker    = yahoo_ticker,
            range     = range_period,
            interval  = interval,
            bars      = len(bars),
        )
        return bars

    except Exception as exc:
        logger.warning("yahoo.fetch_failed", ticker=yahoo_ticker, error=str(exc))
        return []


class YahooFinanceClient:
    """
    Cliente assíncrono para Yahoo Finance via yfinance.

    Implementa subset da interface de BrapiClient necessário para
    o CompositeMarketDataClient:
      - get_ohlc_bars()
      - is_healthy() (verifica se yfinance está instalado)

    NÃO implementa:
      - get_quote() (precisa de BrapiClient para quotes em tempo real)
      - get_fundamentals_batch() (Yahoo não tem dados B3 fundamentalistas)
      - get_quote_full() (mantido no BrapiClient)
    """

    async def get_ohlc_bars(
        self,
        ticker: Any,   # Ticker value object ou str
        range_period: str = "1y",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca OHLC do Yahoo Finance.
        Ticker é convertido para formato .SA automaticamente.
        """
        ticker_str   = str(ticker).upper()
        yahoo_ticker = _to_yahoo_ticker(ticker_str)
        resolved_interval = interval or YAHOO_INTERVAL_MAP.get(range_period, "1d")

        return await asyncio.to_thread(
            _fetch_ohlc_sync,
            yahoo_ticker,
            range_period,
            resolved_interval,
        )

    async def is_healthy(self) -> bool:
        """Verifica se yfinance está instalado e acessível."""
        try:
            import yfinance  # noqa: F401
            return True
        except ImportError:
            return False

    async def close(self) -> None:
        """Sem estado para fechar."""
        pass

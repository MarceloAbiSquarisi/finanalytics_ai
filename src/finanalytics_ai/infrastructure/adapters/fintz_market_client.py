"""
finanalytics_ai.infrastructure.adapters.fintz_market_client
------------------------------------------------------------
FintzMarketDataClient: busca OHLC da tabela fintz_cotacoes (PostgreSQL).

Usado como fonte primaria no CompositeMarketDataClient para backtesting,
substituindo chamadas externas ao Yahoo Finance / BRAPI com dados locais
pre-carregados pela Fintz API.

Formato de saida (igual ao BrapiClient/YahooFinanceClient):
  [{"time": <unix_ts>, "open": float, "high": float, "low": float,
    "close": float, "volume": float}, ...]

Ordenados por data ASC (mais antigo primeiro) - exigido pelo engine.

Notas:
  - Usa preco_fechamento_ajustado como close (corrigido por splits/dividendos)
  - Se ajustado for None, cai para preco_fechamento
  - range_period mapeado para dias: 1mo=30, 3mo=90, 6mo=180, 1y=365, 2y=730, 5y=1825
  - Sem cache proprio: o CompositeClient gerencia fallback
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import text

from finanalytics_ai.infrastructure.database.connection import get_session

logger = structlog.get_logger(__name__)

# Mapeamento range_period -> dias
_RANGE_DAYS: dict[str, int] = {
    "1mo":  30,
    "3mo":  90,
    "6mo":  180,
    "1y":   365,
    "2y":   730,
    "5y":   1825,
    "10y":  3650,
    "ytd":  0,   # calculado dinamicamente
    "max":  36500,
}

# Minimo de barras para considerar resultado util
MIN_BARS = 10


def _range_to_start_date(range_period: str) -> date:
    """Converte range_period em data de inicio."""
    today = date.today()
    if range_period == "ytd":
        return date(today.year, 1, 1)
    days = _RANGE_DAYS.get(range_period, 365)
    return today - timedelta(days=days)


def _date_to_unix(d: Any) -> int:
    """Converte date/str para unix timestamp (UTC)."""
    if isinstance(d, datetime):
        return int(d.replace(tzinfo=timezone.utc).timestamp())
    if isinstance(d, date):
        return calendar.timegm(d.timetuple())
    # string "YYYY-MM-DD"
    try:
        parsed = datetime.strptime(str(d)[:10], "%Y-%m-%d")
        return calendar.timegm(parsed.timetuple())
    except Exception:
        return 0


class FintzMarketDataClient:
    """
    Cliente de market data usando fintz_cotacoes como fonte.

    Implementa a mesma interface de get_ohlc_bars que BrapiClient e
    YahooFinanceClient para ser usado no CompositeMarketDataClient.
    """

    async def get_ohlc_bars(
        self,
        ticker: Any,
        range_period: str = "1y",
        interval: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca OHLC da fintz_cotacoes e retorna no formato do engine.

        ticker pode ser Ticker (value object) ou str.
        """
        ticker_str = str(ticker).upper()
        # Remove sufixo .SA se vier do Yahoo
        if ticker_str.endswith(".SA"):
            ticker_str = ticker_str[:-3]

        start = _range_to_start_date(range_period)

        sql = text("""
            SELECT
                data,
                preco_abertura                           AS abertura,
                preco_maximo                             AS maximo,
                preco_minimo                             AS minimo,
                COALESCE(
                    preco_fechamento_ajustado,
                    preco_fechamento
                )                                        AS fechamento,
                volume_negociado                         AS volume
            FROM fintz_cotacoes
            WHERE ticker = :ticker
              AND data >= :start
            ORDER BY data ASC
        """)

        try:
            async with get_session() as session:
                result = await session.execute(sql, {
                    "ticker": ticker_str,
                    "start": start,
                })
                rows = result.fetchall()
        except Exception as exc:
            logger.warning(
                "fintz_market_client.query_failed",
                ticker=ticker_str,
                error=str(exc),
            )
            return []

        if not rows:
            logger.info(
                "fintz_market_client.no_data",
                ticker=ticker_str,
                range=range_period,
                start=str(start),
            )
            return []

        bars = []
        for row in rows:
            close = float(row.fechamento) if row.fechamento is not None else None
            if close is None:
                continue
            bars.append({
                "time":   _date_to_unix(row.data),
                "open":   float(row.abertura)  if row.abertura  is not None else close,
                "high":   float(row.maximo)    if row.maximo    is not None else close,
                "low":    float(row.minimo)    if row.minimo    is not None else close,
                "close":  close,
                "volume": float(row.volume)    if row.volume    is not None else 0.0,
            })

        logger.info(
            "fintz_market_client.ok",
            ticker=ticker_str,
            range=range_period,
            bars=len(bars),
        )
        return bars

    async def is_healthy(self) -> bool:
        """Verifica se fintz_cotacoes tem dados recentes (ultimos 10 dias)."""
        try:
            cutoff = date.today() - timedelta(days=10)
            sql = text(
                "SELECT COUNT(*) FROM fintz_cotacoes WHERE data >= :cutoff"
            )
            async with get_session() as session:
                result = await session.execute(sql, {"cutoff": cutoff})
                count = result.scalar()
            return (count or 0) > 0
        except Exception:
            return False

    async def get_available_tickers(self) -> list[str]:
        """Lista tickers com dados em fintz_cotacoes."""
        try:
            sql = text(
                "SELECT DISTINCT ticker FROM fintz_cotacoes ORDER BY ticker"
            )
            async with get_session() as session:
                result = await session.execute(sql)
                return [r[0] for r in result.fetchall()]
        except Exception:
            return []

    async def get_ticker_coverage(self, ticker: str) -> dict[str, Any]:
        """Retorna cobertura de datas para um ticker."""
        ticker_str = ticker.upper()
        try:
            sql = text("""
                SELECT
                    MIN(data) AS data_inicio,
                    MAX(data) AS data_fim,
                    COUNT(*)  AS total_barras
                FROM fintz_cotacoes
                WHERE ticker = :ticker
            """)
            async with get_session() as session:
                result = await session.execute(sql, {"ticker": ticker_str})
                row = result.fetchone()
            if row and row.total_barras:
                return {
                    "ticker": ticker_str,
                    "data_inicio": str(row.data_inicio),
                    "data_fim": str(row.data_fim),
                    "total_barras": int(row.total_barras),
                    "disponivel": True,
                }
        except Exception:
            pass
        return {"ticker": ticker_str, "disponivel": False, "total_barras": 0}

    async def close(self) -> None:
        pass  # sem conexao propria para fechar
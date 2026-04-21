"""
FintzScreenerService -- screener com dados locais do banco Fintz.

Vantagens sobre ScreenerService (BRAPI):
  - Zero rate limit: dados locais em fintz_indicadores
  - 36 indicadores vs 10 da BRAPI
  - Universo dinamico: todos os tickers com dados no banco
  - Dados PIT (point-in-time): indicadores na data de publicacao

Query estrategia:
  Um unico PIVOT via ROW_NUMBER() por ticker.
  Retorna todos os indicadores de todos os tickers em uma unica roundtrip.
  Em 46M linhas com indice em (ticker, indicador), a query leva ~200ms.

Mapeamento BRAPI -> Fintz (para compatibilidade com FilterCriteria existente):
  pe              -> P_L
  pvp             -> P_VP
  dy              -> DividendYield  (ja em percentual no Fintz: 6.0 = 6%)
  roe             -> ROE            (ja em percentual: 15.0 = 15%)
  roic            -> ROIC
  ebitda_margin   -> MargemEBITDA
  net_margin      -> MargemLiquida
  debt_equity     -> DividaLiquida_PatrimonioLiquido
  market_cap      -> ValorDeMercado (em bilhoes R$)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
import structlog

from finanalytics_ai.domain.screener.engine import (
    FilterCriteria,
    FundamentalData,
    apply_filters,
)

logger = structlog.get_logger(__name__)

# Mapeamento: campo FundamentalData -> nome do indicador em fintz_indicadores
_FIELD_TO_INDICADOR: dict[str, str] = {
    "pe": "P_L",
    "pvp": "P_VP",
    "dy": "DividendYield",
    "roe": "ROE",
    "roic": "ROIC",
    "ebitda_margin": "MargemEBITDA",
    "net_margin": "MargemLiquida",
    "debt_equity": "DividaLiquida_PatrimonioLiquido",
    "market_cap": "ValorDeMercado",
    "ev_ebitda": "EV_EBITDA",
    "roa": "ROA",
    "margem_bruta": "MargemBruta",
    "margem_ebit": "MargemEBIT",
    "div_liq_ebitda": "DividaLiquida_EBITDA",
    "liquidez": "LiquidezCorrente",
    "giro_ativos": "GiroAtivos",
}

_INDICADORES_NEEDED = list(_FIELD_TO_INDICADOR.values())


class FintzScreenerService:
    """
    Screener usando dados locais de fintz_indicadores.

    session_factory: callable que retorna AsyncSession.
    """

    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def screen(
        self,
        criteria: FilterCriteria,
        extra_tickers: list[str] | None = None,
        use_universe: bool = True,
        tickers_filter: list[str] | None = None,
    ) -> ScreenerResult:
        """
        Executa screener com dados do banco Fintz.

        tickers_filter: se fornecido, limita a busca a esses tickers.
        """
        log = logger.bind(criteria=_criteria_summary(criteria))
        log.info("fintz_screener.starting")

        raw = await self._fetch_indicators(tickers_filter)
        log.info("fintz_screener.data_fetched", tickers=len(raw))

        stocks: list[FundamentalData] = []
        for ticker, indicators in raw.items():
            fd = _to_fundamental_data(ticker, indicators)
            stocks.append(fd)

        results = apply_filters(stocks, criteria)
        log.info("fintz_screener.done", matched=len(results), total=len(stocks))
        return results

    async def _fetch_indicators(
        self,
        tickers_filter: list[str] | None = None,
    ) -> dict[str, dict[str, float | None]]:
        """
        Busca os ultimos indicadores por ticker via SQL.

        Usa ROW_NUMBER() OVER (PARTITION BY ticker, indicador ORDER BY data_publicacao DESC)
        para pegar o valor mais recente de cada indicador por ticker.
        """
        indicadores_list = ", ".join(f"'{i}'" for i in _INDICADORES_NEEDED)

        ticker_filter_clause = ""
        if tickers_filter:
            tickers_str = ", ".join(f"'{t}'" for t in tickers_filter)
            ticker_filter_clause = f"AND ticker IN ({tickers_str})"

        query = text(f"""
            SELECT ticker, indicador, valor
            FROM (
                SELECT
                    ticker,
                    indicador,
                    valor,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker, indicador
                        ORDER BY data_publicacao DESC
                    ) as rn
                FROM fintz_indicadores
                WHERE indicador IN ({indicadores_list})
                {ticker_filter_clause}
            ) ranked
            WHERE rn = 1
            ORDER BY ticker, indicador
        """)

        try:
            async with self._sf() as session:
                result = await session.execute(query)
                rows = result.fetchall()
        except Exception as exc:
            logger.error("fintz_screener.db_error", error=str(exc))
            raise

        data: dict[str, dict[str, float | None]] = {}
        for row in rows:
            ticker, indicador, valor = row
            if ticker not in data:
                data[ticker] = {}
            try:
                data[ticker][indicador] = float(valor) if valor is not None else None
            except (TypeError, ValueError):
                data[ticker][indicador] = None

        return data

    async def get_available_tickers(self) -> list[str]:
        """Lista todos os tickers com dados em fintz_indicadores."""
        query = text("""
            SELECT DISTINCT ticker
            FROM fintz_indicadores
            ORDER BY ticker
        """)
        async with self._sf() as session:
            result = await session.execute(query)
            return [row[0] for row in result.fetchall()]


def _pct(val: float | None) -> float | None:
    """Converte decimal para percentual. 0.163 -> 16.3"""
    if val is None:
        return None
    return round(val * 100, 4)


def _to_fundamental_data(
    ticker: str,
    indicators: dict[str, float | None],
) -> FundamentalData:
    """Converte dict de indicadores Fintz para FundamentalData."""

    def get(fintz_key: str) -> float | None:
        val = indicators.get(fintz_key)
        if val is None:
            return None
        if isinstance(val, float) and val != val:  # NaN
            return None
        return val

    market_cap_raw = get("ValorDeMercado")
    market_cap = market_cap_raw / 1_000_000_000 if market_cap_raw else None

    return FundamentalData(
        ticker=ticker,
        pe=get("P_L"),
        pvp=get("P_VP"),
        dy=_pct(get("DividendYield")),
        roe=_pct(get("ROE")),
        roic=_pct(get("ROIC")),
        ebitda_margin=_pct(get("MargemEBITDA")),
        net_margin=_pct(get("MargemLiquida")),
        debt_equity=get("DividaLiquida_PatrimonioLiquido"),
        market_cap=market_cap,
    )


def _criteria_summary(criteria: FilterCriteria) -> dict[str, Any]:
    return {k: v for k, v in vars(criteria).items() if v is not None}

"""
ScreenerService — busca fundamentalistas em batch e aplica filtros.

Design decisions:

  Batch de 20 tickers por requisicao:
    BRAPI aceita ate ~50 tickers em /quote/A,B,C?fundamental=true
    Usamos batches de 20 por conservadorismo e para distribuir carga.
    Com Semaphore(3) e ~1s por request: 60 tickers = ~3 batches = ~1s.

  Campos mapeados da BRAPI (fundamental=true):
    priceEarnings    -> pe
    priceToBook      -> pvp
    dividendYield    -> dy (BRAPI retorna em decimal, ex: 0.06 = 6%)
    returnOnEquity   -> roe (idem, decimal)
    ebitdaMargins    -> ebitda_margin
    profitMargins    -> net_margin
    debtToEquity     -> debt_equity
    revenueGrowth    -> revenue_growth
    earningsPerShare -> eps
    marketCap        -> market_cap

  Conversao de decimal para percentual:
    BRAPI retorna ROE, DY e margens como fracao (0.15 = 15%).
    Multiplicamos por 100 na normalizacao para apresentar ao usuario.

  Setor via campo 'sector' da BRAPI:
    Disponivel quando fundamental=true. Nem todos os ativos tem setor —
    nesses casos, setor fica "" e o filtro por setor os exclui apenas
    se o usuario especificar um setor explicitamente.

  Cache de resultados:
    Nao ha cache aqui — o servico e chamado on-demand.
    Para producao, um cache Redis com TTL=1h seria adequado dado que
    fundamentalistas mudam no maximo diariamente.
    Trade-off aceito: simplicidade > performance para o MVP.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.domain.screener.engine import (
    IBOV_UNIVERSE,
    FilterCriteria,
    FundamentalData,
    ScreenerResult,
    apply_filters,
)

if TYPE_CHECKING:
    from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient

logger = structlog.get_logger(__name__)

BATCH_SIZE = 20  # tickers por requisicao BRAPI
MAX_CONCURRENT = 3  # batches em paralelo
MAX_CUSTOM = 20  # tickers customizados adicionais


class ScreenerService:
    def __init__(self, brapi_client: BrapiClient) -> None:
        self._brapi = brapi_client

    async def screen(
        self,
        criteria: FilterCriteria,
        extra_tickers: list[str] | None = None,
        use_universe: bool = True,
    ) -> ScreenerResult:
        """
        Busca fundamentalistas e aplica filtros.

        Parametros:
          criteria:      Filtros a aplicar
          extra_tickers: Tickers adicionais ao universo padrao
          use_universe:  Se False, usa apenas extra_tickers

        Fluxo:
          1. Monta universo de tickers
          2. Busca fundamentalistas em batches paralelos
          3. Aplica filtros
          4. Retorna ScreenerResult ordenado por score
        """
        # Monta universo
        tickers: list[str] = []
        if use_universe:
            tickers.extend(IBOV_UNIVERSE)
        if extra_tickers:
            extra = [t.upper().strip() for t in extra_tickers if t.strip()][:MAX_CUSTOM]
            tickers.extend(t for t in extra if t not in tickers)

        if not tickers:
            raise BacktestError("Nenhum ticker no universo de busca.")

        log = logger.bind(universe=len(tickers), criteria=_criteria_summary(criteria))
        log.info("screener.starting")

        # Divide em batches
        batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
            async with sem:
                try:
                    return await self._brapi.get_fundamentals_batch(batch)
                except Exception as exc:
                    log.warning("screener.batch_failed", tickers=batch, error=str(exc))
                    return []

        raw_batches = await asyncio.gather(*[_fetch_batch(b) for b in batches])

        # Converte para FundamentalData
        all_stocks: list[FundamentalData] = []
        errors: list[dict[str, str]] = []

        for raw_list in raw_batches:
            for raw in raw_list:
                try:
                    stock = _parse_fundamental(raw)
                    all_stocks.append(stock)
                except Exception as exc:
                    ticker = raw.get("symbol", "?")
                    errors.append({"ticker": ticker, "error": str(exc)})

        # Coleta setores disponíveis para o frontend
        sectors = sorted({s.sector for s in all_stocks if s.sector})

        # Aplica filtros
        passed = apply_filters(all_stocks, criteria)

        log.info(
            "screener.done",
            total=len(all_stocks),
            passed=len(passed),
            errors=len(errors),
        )

        # Métricas Prometheus
        try:
            from finanalytics_ai.metrics import record_screener_run

            record_screener_run(
                total_scanned=len(all_stocks),
                total_passed=len(passed),
            )
        except Exception:
            pass

        return ScreenerResult(
            total_universe=len(all_stocks),
            total_passed=len(passed),
            criteria=_criteria_to_dict(criteria),
            stocks=passed,
            errors=errors,
            sectors=list(sectors),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pct(value: Any) -> float | None:
    """Converte fracao decimal da BRAPI para percentual. None se invalido."""
    if value is None:
        return None
    try:
        v = float(value)
        return round(v * 100, 2)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fundamental(raw: dict[str, Any]) -> FundamentalData:
    """
    Converte resposta bruta da BRAPI para FundamentalData.

    Campos BRAPI (fundamental=true):
      priceEarnings, priceToBook, dividendYield (decimal),
      returnOnEquity (decimal), ebitdaMargins (decimal),
      profitMargins (decimal), debtToEquity, revenueGrowth (decimal),
      earningsPerShare, marketCap, sector, regularMarketPrice, etc.
    """
    return FundamentalData(
        ticker=raw.get("symbol", ""),
        name=raw.get("longName") or raw.get("shortName") or "",
        sector=raw.get("sector") or "",
        price=_num(raw.get("regularMarketPrice")),
        market_cap=_num(raw.get("marketCap")),
        pe=_num(raw.get("priceEarnings")),
        pvp=_num(raw.get("priceToBook")),
        dy=_pct(raw.get("dividendYield")),
        roe=_pct(raw.get("returnOnEquity")),
        roic=_pct(raw.get("returnOnInvestedCapital")),
        ebitda_margin=_pct(raw.get("ebitdaMargins")),
        net_margin=_pct(raw.get("profitMargins")),
        debt_equity=_num(raw.get("debtToEquity")),
        revenue_growth=_pct(raw.get("revenueGrowth")),
        eps=_num(raw.get("earningsPerShare")),
        high_52w=_num(raw.get("fiftyTwoWeekHigh")),
        low_52w=_num(raw.get("fiftyTwoWeekLow")),
        volume=_num(raw.get("regularMarketVolume")),
    )


def _criteria_summary(c: FilterCriteria) -> dict[str, Any]:
    return {k: v for k, v in _criteria_to_dict(c).items() if v is not None}


def _criteria_to_dict(c: FilterCriteria) -> dict[str, Any]:
    from dataclasses import fields as dc_fields

    return {f.name: getattr(c, f.name) for f in dc_fields(c)}

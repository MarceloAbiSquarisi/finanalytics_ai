"""
finanalytics_ai.application.services.performance_service
──────────────────────────────────────────────────────────
Orquestra busca de dados históricos e cálculo de performance.

Fluxo:
  1. Carrega snapshot do portfolio (posições + cotações atuais)
  2. Calcula pesos atuais de cada ativo
  3. Busca histórico OHLC de cada ticker em paralelo
  4. Busca histórico do benchmark (BOVA11) em paralelo
  5. Alinha datas (intersecção)
  6. Constrói série de retornos ponderada
  7. Delega cálculo de métricas ao domain engine

Design decisions:

  Semaphore(3) nas buscas paralelas:
    Mesmo padrão dos outros services. Evita burst na BRAPI/Yahoo.

  Benchmark BOVA11 com fallback IBOV:
    BOVA11 é o ETF do Ibovespa, mais confiável na BRAPI.
    Se falhar, tenta IBOV diretamente. Se ambos falharem,
    usa série de zeros (retorno 0% ao dia) — o painel mostra
    métricas da carteira mas benchmark em linha reta.

  Mínimo de 20 barras:
    Menos que isso não faz sentido calcular Sharpe/drawdown.
    Retorna erro claro ao invés de métricas sem significado.

  Período "ytd" e "max":
    Delegados ao Yahoo Finance via CompositeMarketDataClient.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from typing import Any

import structlog

from finanalytics_ai.domain.performance.engine import (
    PerformanceResult,
    _align_price_series,
    _prices_to_returns,
    build_portfolio_returns,
    compute_performance,
)
from finanalytics_ai.domain.value_objects.money import Ticker

logger = structlog.get_logger(__name__)

BENCHMARK_TICKERS = ["BOVA11", "IBOV"]  # tenta em ordem
MIN_BARS = 20
SEMAPHORE = 3

VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "ytd", "max"}


class PerformanceError(Exception):
    pass


class PerformanceService:
    def __init__(self, portfolio_repo: Any, market_client: Any) -> None:
        self._repo = portfolio_repo
        self._market = market_client

    async def get_performance(
        self,
        portfolio_id: str,
        period: str = "1y",
    ) -> PerformanceResult:
        if period not in VALID_PERIODS:
            raise PerformanceError(f"Período inválido: {period}. Válidos: {sorted(VALID_PERIODS)}")

        # ── 1. Carrega portfolio ──────────────────────────────────────────────
        portfolio = await self._repo.find_by_id(portfolio_id)
        if not portfolio:
            raise PerformanceError(f"Portfólio não encontrado: {portfolio_id}")

        if not portfolio.positions:
            raise PerformanceError("Portfólio sem posições — adicione ativos primeiro.")

        # ── 2. Pesos atuais (por valor de mercado) ────────────────────────────
        sem = asyncio.Semaphore(SEMAPHORE)

        async def _get_price(ticker_sym: str) -> tuple[str, float]:
            async with sem:
                try:
                    price = await self._market.get_quote(Ticker(ticker_sym))
                    return ticker_sym, float(price.amount)
                except Exception:
                    pos = portfolio.positions.get(ticker_sym)
                    fallback = float(pos.average_price.amount) if pos else 1.0
                    return ticker_sym, fallback

        tickers = list(portfolio.positions.keys())
        price_results = await asyncio.gather(*[_get_price(t) for t in tickers])
        current_prices = dict(price_results)

        # Valor de mercado de cada posição
        market_values: dict[str, float] = {}
        for sym, pos in portfolio.positions.items():
            qty = float(pos.quantity.value)
            price = current_prices.get(sym, float(pos.average_price.amount))
            market_values[sym] = qty * price

        total_mkt = sum(market_values.values())
        if total_mkt == 0:
            raise PerformanceError("Valor total da carteira é zero.")

        weights = {sym: mv / total_mkt for sym, mv in market_values.items()}

        # ── 3. Histórico OHLC por ticker + benchmark ──────────────────────────
        all_tickers = [*tickers, BENCHMARK_TICKERS[0]]

        async def _get_ohlc(ticker_sym: str) -> tuple[str, dict[str, float]]:
            """Retorna (ticker, {date: close_price})."""
            async with sem:
                try:
                    bars = await self._market.get_ohlc_bars(Ticker(ticker_sym), range_period=period)
                    series = {}
                    for b in bars:
                        if b.get("close") and b.get("close") > 0:
                            # timestamp → date string
                            ts = b.get("time", 0)
                            try:
                                from datetime import datetime

                                dt = datetime.fromtimestamp(ts, tz=UTC)
                                date_str = dt.strftime("%Y-%m-%d")
                            except Exception:
                                date_str = str(ts)
                            series[date_str] = float(b["close"])
                    return ticker_sym, series
                except Exception as exc:
                    logger.warning("performance.ohlc_failed", ticker=ticker_sym, error=str(exc))
                    return ticker_sym, {}

        ohlc_results = await asyncio.gather(*[_get_ohlc(t) for t in all_tickers])
        ohlc_map: dict[str, dict[str, float]] = dict(ohlc_results)

        # Tenta benchmark secundário se BOVA11 falhou
        bench_sym = BENCHMARK_TICKERS[0]
        if len(ohlc_map.get(bench_sym, {})) < MIN_BARS:
            logger.warning("performance.benchmark_primary_failed", sym=bench_sym)
            _, fallback_series = await _get_ohlc(BENCHMARK_TICKERS[1])
            if len(fallback_series) >= MIN_BARS:
                bench_sym = BENCHMARK_TICKERS[1]
                ohlc_map[bench_sym] = fallback_series
            else:
                logger.warning("performance.benchmark_both_failed")

        # Valida dados dos ativos da carteira
        valid_tickers = [t for t in tickers if len(ohlc_map.get(t, {})) >= MIN_BARS]
        if not valid_tickers:
            raise PerformanceError(
                f"Dados históricos insuficientes para o período '{period}'. "
                "Tente um período mais curto (ex: 3mo) ou verifique os tickers."
            )

        missing = set(tickers) - set(valid_tickers)
        if missing:
            logger.warning("performance.tickers_excluded", tickers=list(missing))
            # Recalcula pesos excluindo tickers sem dados
            total_mkt = sum(market_values[t] for t in valid_tickers)
            weights = {t: market_values[t] / total_mkt for t in valid_tickers}

        # ── 4. Alinha séries ──────────────────────────────────────────────────
        series_for_align = {t: ohlc_map[t] for t in valid_tickers}
        if ohlc_map.get(bench_sym):
            series_for_align[bench_sym] = ohlc_map[bench_sym]

        dates, aligned = _align_price_series(series_for_align)

        if len(dates) < MIN_BARS:
            raise PerformanceError(
                f"Apenas {len(dates)} datas em comum entre os ativos para '{period}'. "
                "Tente um período maior ou reduza o número de ativos."
            )

        # ── 5. Retornos ───────────────────────────────────────────────────────
        ticker_returns_map: dict[str, list[float]] = {}
        price_series_for_weights: dict[str, list[float]] = {}

        for t in valid_tickers:
            prices = aligned[t]
            rets = _prices_to_returns(prices)
            ticker_returns_map[t] = rets
            price_series_for_weights[t] = prices

        portfolio_rets = build_portfolio_returns(price_series_for_weights, weights)

        # Benchmark
        if bench_sym in aligned:
            bench_prices = aligned[bench_sym]
            bench_rets = _prices_to_returns(bench_prices)
        else:
            bench_rets = [0.0] * len(portfolio_rets)

        # Datas para o engine (uma por retorno, começando do índice 1)
        return_dates = dates[1:] if len(dates) > len(portfolio_rets) else dates

        # ── 6. Calcula métricas ───────────────────────────────────────────────
        result = compute_performance(
            portfolio_id=portfolio_id,
            portfolio_name=portfolio.name,
            period=period,
            dates=return_dates,
            portfolio_returns=portfolio_rets,
            benchmark_returns=bench_rets,
            weights=weights,
            ticker_returns_map=ticker_returns_map,
        )

        logger.info(
            "performance.computed",
            portfolio_id=portfolio_id,
            period=period,
            n_days=result.metrics.period_days,
            sharpe=result.metrics.sharpe_ratio,
            max_dd=result.metrics.max_drawdown_pct,
        )
        return result

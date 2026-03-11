"""
finanalytics_ai.application.services.portfolio_optimizer_service
─────────────────────────────────────────────────────────────────
Orquestra a otimização de portfólio com os três algoritmos.

Responsabilidades:
  1. Busca dados históricos de N ativos em paralelo
  2. Alinha as séries de retornos por data
  3. Calcula matriz de covariância e retornos médios
  4. Executa os três algoritmos em thread (CPU-bound)
  5. Retorna OptimizationComparison serializado

Fontes de ativos suportadas:
  - Ações: qualquer ticker (VALE3, PETR4...)
  - ETFs: qualquer ticker do catálogo ou custom
  - Renda Fixa: simulada como ativo de baixo risco com retorno CDI
    (RF não tem série de preços negociáveis — modelamos como ativo
     de retorno constante + volatilidade zero. Isso subestima a RF
     mas é honesto: incluir RF num portfólio Markowitz é correto
     apenas para fins ilustrativos de diversificação.)
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from finanalytics_ai.domain.portfolio_optimizer.engine import (
    TRADING_DAYS,
    OptimizationComparison,
    _covariance_matrix,
    black_litterman_optimize,
    equal_weight,
    markowitz_optimize,
    risk_parity_optimize,
)

logger = structlog.get_logger(__name__)

DEFAULT_CDI = 0.1065
DEFAULT_SELIC = 0.1065
MAX_TICKERS = 15
MIN_BARS = 60
MAX_CONCURRENT = 4


class PortfolioOptimizerService:
    def __init__(self, market_client: Any) -> None:
        self._market = market_client

    async def optimize(
        self,
        tickers: list[str],
        period: str = "1y",
        risk_free: float = DEFAULT_CDI,
        views: list[dict] | None = None,  # [{"ticker": "BOVA11", "return": 0.15}]
        market_weights: list[float] | None = None,
        rf_tickers: list[str] | None = None,  # tickers tratados como RF
        bl_tau: float = 0.05,
        bl_risk_aversion: float = 3.0,
    ) -> dict[str, Any]:
        """
        Executa otimização completa: Markowitz + Risk Parity + Black-Litterman.

        rf_tickers: tickers que representam renda fixa — modelados como ativo
                    com retorno = CDI e volatilidade mínima (~0.5% a.a.)
        """
        from finanalytics_ai.domain.value_objects.ticker import Ticker

        tickers = [t.upper().strip() for t in tickers if t.strip()]
        if len(tickers) < 2:
            raise ValueError("Informe pelo menos 2 ativos.")
        if len(tickers) > MAX_TICKERS:
            raise ValueError(f"Máximo de {MAX_TICKERS} ativos por otimização.")

        rf_set = {t.upper() for t in (rf_tickers or [])}

        log = logger.bind(tickers=tickers, period=period)
        log.info("portfolio_optimizer.starting")

        # ── Busca dados em paralelo ───────────────────────────────────────────
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch(t: str) -> tuple[str, list[dict] | Exception]:
            if t in rf_set:
                return t, []  # RF: sem dados de mercado, tratado abaixo
            async with sem:
                try:
                    bars = await self._market.get_ohlc_bars(Ticker(t), range_period=period)
                    return t, bars
                except Exception as e:
                    return t, e

        raw = await asyncio.gather(*[_fetch(t) for t in tickers])

        # ── Extrai e alinha retornos ──────────────────────────────────────────
        price_series: dict[str, tuple[list[str], list[float]]] = {}
        errors: list[dict] = []

        for t, fetch_result in raw:
            if t in rf_set:
                # RF: série sintética constante (CDI diário)
                daily_rf = (1 + risk_free) ** (1 / TRADING_DAYS) - 1
                # 252 dias de retorno CDI com ruído mínimo
                price_series[t] = (
                    [f"RF-{i}" for i in range(253)],
                    [100.0 * (1 + daily_rf) ** i for i in range(253)],
                )
                continue
            if isinstance(fetch_result, Exception):
                errors.append({"ticker": t, "error": str(fetch_result)})
                continue
            dates, prices = _extract_prices(fetch_result)
            if len(prices) < MIN_BARS:
                errors.append({"ticker": t, "error": f"Apenas {len(prices)} barras (mín. {MIN_BARS})"})
                continue
            price_series[t] = (dates, prices)

        valid_tickers = [t for t in tickers if t in price_series]
        if len(valid_tickers) < 2:
            raise ValueError(
                f"Dados válidos para apenas {len(valid_tickers)} ativo(s). "
                f"Erros: {[e['ticker'] for e in errors]}"
            )

        # Alinha datas (interseção)
        returns_matrix, final_tickers = _align_and_compute_returns(price_series, valid_tickers)
        mean_rets = [sum(r) / len(r) if r else 0.0 for r in returns_matrix]
        cov = _covariance_matrix(returns_matrix)

        log.info(
            "portfolio_optimizer.data_ready",
            tickers=final_tickers,
            n_days=len(returns_matrix[0]) if returns_matrix else 0,
        )

        # ── Executa algoritmos em thread ──────────────────────────────────────
        bl_views = [(v["ticker"], v["return"]) for v in (views or []) if v.get("ticker") in final_tickers]

        def _run_all() -> OptimizationComparison:
            mz, frontier = markowitz_optimize(final_tickers, mean_rets, cov, risk_free)
            rp = risk_parity_optimize(final_tickers, mean_rets, cov, risk_free)
            bl = black_litterman_optimize(
                final_tickers,
                mean_rets,
                cov,
                risk_free,
                views=bl_views,
                market_weights=market_weights,
                tau=bl_tau,
                risk_aversion=bl_risk_aversion,
            )
            ew = equal_weight(final_tickers, mean_rets, cov, risk_free)

            # Seta period em todos
            for p in [mz, rp, bl, ew]:
                p.period = period

            return OptimizationComparison(
                tickers=final_tickers,
                period=period,
                risk_free=risk_free,
                markowitz=mz,
                risk_parity=rp,
                black_litterman=bl,
                equal_weight=ew,
                frontier=frontier,
            )

        comparison: OptimizationComparison = await asyncio.to_thread(_run_all)
        result: dict[str, Any] = comparison.to_dict()
        result["errors"] = errors
        result["n_days"] = len(returns_matrix[0]) if returns_matrix else 0

        log.info(
            "portfolio_optimizer.done",
            best_method=result.get("best_sharpe_method"),
            markowitz_sharpe=round(comparison.markowitz.sharpe, 3),
        )

        return result  # type: ignore[no-any-return]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_prices(bars: list[dict]) -> tuple[list[str], list[float]]:
    dates, prices = [], []
    for b in bars:
        close = b.get("close") or b.get("adjclose") or b.get("regularMarketPrice")
        date = b.get("date") or b.get("datetime") or b.get("timestamp")
        if close and date and float(close) > 0:
            dates.append(str(date)[:10])
            prices.append(float(close))
    return dates, prices


def _align_and_compute_returns(
    price_series: dict[str, tuple[list[str], list[float]]],
    tickers: list[str],
) -> tuple[list[list[float]], list[str]]:
    """
    Alinha séries por data (interseção) e calcula retornos diários.
    Remove tickers que ficam com menos de MIN_BARS retornos após alinhamento.
    """
    # Interseção de datas
    date_sets = []
    for t in tickers:
        dates, _ = price_series[t]
        date_sets.append(set(dates))

    common = date_sets[0]
    for ds in date_sets[1:]:
        common &= ds
    common_dates = sorted(common)

    if len(common_dates) < MIN_BARS + 1:
        # Fallback: usa todas as datas sem interseção estrita
        common_dates = sorted(set().union(*date_sets))

    returns_matrix = []
    valid_tickers = []

    for t in tickers:
        dates, prices = price_series[t]
        date_map = dict(zip(dates, prices, strict=False))
        ps = [date_map[d] for d in common_dates if d in date_map]
        if len(ps) < MIN_BARS + 1:
            continue
        rets = [(ps[i] - ps[i - 1]) / ps[i - 1] for i in range(1, len(ps))]
        returns_matrix.append(rets)
        valid_tickers.append(t)

    return returns_matrix, valid_tickers

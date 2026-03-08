"""
finanalytics_ai.application.services.etf_service
──────────────────────────────────────────────────
Casos de uso para análise de ETFs.

Reutiliza:
  CompositeMarketDataClient.get_ohlc_bars() — séries históricas
  Padrão de retornos logarítmicos do correlation_service

Design decisions:
  Retornos logarítmicos vs simples:
    Usamos retornos simples (pct_change) para Sharpe e métricas de
    performance — mais intuitivo para o usuário final.
    Tracking error usa diferença de retornos simples para consistência
    com a convenção do mercado brasileiro.

  Risk-free rate (CDI):
    CDI ≈ SELIC atual. Usando 10.65% a.a. como default.
    Convertido para diário: (1 + cdi)^(1/252) - 1.

  Normalização de preços para comparação:
    Base 100 na primeira data disponível de cada série.
    Permite comparar ETFs com preços nominais muito diferentes.

  Cálculo de correlação:
    Reutilizamos o mesmo padrão do correlation_service para não duplicar
    a lógica de alinhamento de datas e preenchimento de gaps.

  Rebalanceamento:
    Algoritmo: calcula o target_value de cada posição, compara com current_value.
    Se houver aporte, aloca primeiro para os underweight antes de vender overweight.
    Threshold de 1%: posições dentro de 1 p.p. do target recebem ação "MANTER".
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

import structlog

from finanalytics_ai.domain.etf.entities import (
    ETF_CATALOG, ETFInfo, ETFMetrics, ETFComparison,
    TrackingErrorResult, RebalanceRecommendation, RebalancePosition,
    get_etf,
)

logger = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
DEFAULT_CDI          = 0.1065   # risk-free rate (SELIC/CDI)
TRADING_DAYS_YEAR    = 252
REBALANCE_THRESHOLD  = 1.0      # p.p. — abaixo disso, "MANTER"
MAX_CONCURRENT       = 4        # semáforo para fetch paralelo


# ── Helpers de cálculo ────────────────────────────────────────────────────────

def _daily_risk_free(annual: float) -> float:
    return (1 + annual) ** (1 / TRADING_DAYS_YEAR) - 1


def _returns(prices: list[float]) -> list[float]:
    """Retornos simples diários."""
    return [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]


def _annualized_vol(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    n  = len(rets)
    mu = sum(rets) / n
    variance = sum((r - mu) ** 2 for r in rets) / (n - 1)
    return math.sqrt(variance * TRADING_DAYS_YEAR)


def _max_drawdown(prices: list[float]) -> float:
    peak = prices[0]
    max_dd = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (p - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _cagr(start: float, end: float, n_days: int) -> float:
    if start <= 0 or n_days <= 0:
        return 0.0
    years = n_days / TRADING_DAYS_YEAR
    return (end / start) ** (1 / years) - 1


def _sharpe(rets: list[float], risk_free_annual: float) -> float:
    if len(rets) < 2:
        return 0.0
    rf_daily = _daily_risk_free(risk_free_annual)
    excess   = [r - rf_daily for r in rets]
    mu       = sum(excess) / len(excess)
    n        = len(excess)
    std      = math.sqrt(sum((r - mu) ** 2 for r in excess) / (n - 1)) if n > 1 else 0
    if std == 0:
        return 0.0
    return (mu / std) * math.sqrt(TRADING_DAYS_YEAR)


def _var_95(rets: list[float]) -> float:
    """Historical VaR 95% (perda positiva)."""
    if not rets:
        return 0.0
    sorted_rets = sorted(rets)
    idx = max(0, int(len(sorted_rets) * 0.05) - 1)
    return abs(sorted_rets[idx])


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    cov  = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx   = math.sqrt(sum((x - mx) ** 2 for x in xs[:n]))
    sy   = math.sqrt(sum((y - my) ** 2 for y in ys[:n]))
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def _extract_prices(bars: list[dict]) -> tuple[list[str], list[float]]:
    """Extrai datas e preços de fechamento de uma série OHLC."""
    dates, prices = [], []
    for b in bars:
        close = b.get("close") or b.get("adjclose") or b.get("regularMarketPrice")
        date  = b.get("date") or b.get("datetime") or b.get("timestamp")
        if close and date and close > 0:
            dates.append(str(date)[:10])
            prices.append(float(close))
    return dates, prices


def _align_series(
    series_a: tuple[list[str], list[float]],
    series_b: tuple[list[str], list[float]],
) -> tuple[list[float], list[float]]:
    """Alinha duas séries por data, retornando apenas datas comuns."""
    dates_a, prices_a = series_a
    dates_b, prices_b = series_b
    map_a = dict(zip(dates_a, prices_a))
    map_b = dict(zip(dates_b, prices_b))
    common = sorted(set(map_a) & set(map_b))
    return [map_a[d] for d in common], [map_b[d] for d in common]


def _compute_metrics(
    ticker: str,
    bars:   list[dict],
    period: str,
    risk_free: float = DEFAULT_CDI,
) -> ETFMetrics:
    """Calcula todas as métricas de um ETF a partir de barras OHLC."""
    dates, prices = _extract_prices(bars)
    if len(prices) < 5:
        raise ValueError(f"{ticker}: dados insuficientes ({len(prices)} barras)")

    rets    = _returns(prices)
    vol     = _annualized_vol(rets)
    ret_tot = (prices[-1] - prices[0]) / prices[0]
    ret_ann = _cagr(prices[0], prices[-1], len(prices))
    mdd     = _max_drawdown(prices)
    sharpe  = _sharpe(rets, risk_free)
    var95   = _var_95(rets)
    calmar  = ret_ann / abs(mdd) if mdd != 0 else 0.0

    info = get_etf(ticker)
    return ETFMetrics(
        ticker        = ticker,
        name          = info.name if info else ticker,
        period        = period,
        total_return  = ret_tot,
        annual_return = ret_ann,
        volatility    = vol,
        sharpe        = sharpe,
        max_drawdown  = mdd,
        var_95        = var95,
        calmar        = calmar,
        n_days        = len(prices),
        start_price   = prices[0],
        end_price     = prices[-1],
        category      = info.category if info else "",
        ter           = info.ter if info else 0.0,
    )


# ── ETFService ────────────────────────────────────────────────────────────────

class ETFService:
    def __init__(self, market_client: Any) -> None:
        self._market = market_client

    # ── 1. Comparativo ────────────────────────────────────────────────────────

    async def compare(
        self,
        tickers:    list[str],
        period:     str = "1y",
        risk_free:  float = DEFAULT_CDI,
    ) -> dict[str, Any]:
        """
        Compara N ETFs: retorno, volatilidade, Sharpe, drawdown, VaR.
        Retorna métricas + séries normalizadas para gráfico.
        """
        from finanalytics_ai.domain.value_objects.ticker import Ticker

        tickers = [t.upper().strip() for t in tickers]
        sem     = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch(t: str) -> tuple[str, list[dict] | Exception]:
            async with sem:
                try:
                    bars = await self._market.get_ohlc_bars(Ticker(t), range_period=period)
                    return t, bars
                except Exception as e:
                    return t, e

        results = await asyncio.gather(*[_fetch(t) for t in tickers])

        metrics_list: list[ETFMetrics] = []
        price_series: dict[str, list[dict]] = {}
        errors: list[dict] = []

        for ticker, result in results:
            if isinstance(result, Exception):
                errors.append({"ticker": ticker, "error": str(result)})
                continue
            try:
                m = _compute_metrics(ticker, result, period, risk_free)
                metrics_list.append(m)
                # Série normalizada base 100
                dates, prices = _extract_prices(result)
                base = prices[0] if prices else 1.0
                price_series[ticker] = [
                    {"date": d, "close": p, "normalized": round(p / base * 100, 4)}
                    for d, p in zip(dates, prices)
                ]
            except Exception as e:
                errors.append({"ticker": ticker, "error": str(e)})

        # Ranking por Sharpe
        ranked = sorted(metrics_list, key=lambda m: m.sharpe, reverse=True)

        comp = ETFComparison(
            period=period, risk_free=risk_free,
            metrics=ranked, price_series=price_series,
        )

        return {
            "period":           period,
            "risk_free_pct":    round(risk_free * 100, 2),
            "metrics":          [m.to_dict() for m in ranked],
            "price_series":     price_series,
            "errors":           errors,
            "summary": {
                "best_return":     comp.best_return.ticker if comp.best_return else None,
                "best_sharpe":     comp.best_sharpe.ticker if comp.best_sharpe else None,
                "lowest_vol":      comp.lowest_volatility.ticker if comp.lowest_volatility else None,
            },
        }

    # ── 2. Tracking Error ─────────────────────────────────────────────────────

    async def tracking_error(
        self,
        etf_ticker:  str,
        period:      str = "1y",
        risk_free:   float = DEFAULT_CDI,
    ) -> dict[str, Any]:
        """
        Calcula tracking error do ETF vs seu benchmark definido no catálogo.
        """
        from finanalytics_ai.domain.value_objects.ticker import Ticker

        info = get_etf(etf_ticker.upper())
        if info is None:
            raise ValueError(f"ETF '{etf_ticker}' não encontrado no catálogo.")

        benchmark = info.benchmark

        # Alguns benchmarks não são tradeable na BRAPI/Yahoo com esse ticker
        # Mapeamos para tickers equivalentes disponíveis
        BENCHMARK_MAP = {
            "^BVSP":  "BOVA11",   # Ibovespa → BOVA11 como proxy
            "^GSPC":  "IVVB11",   # S&P500  → IVVB11 como proxy
            "SMLL":   "SMAL11",
            "IDIV":   "DIVO11",
            "QQQ":    "NASD11",
            "ACWI":   "ACWI11",
            "IMA-B":  "NTNB11",
            "IMA-B5+":"B5P211",
            "IRF-M":  "IRFM11",
            "IFIX":   "XFIX11",
            "BTC-USD":"QBTC11",
        }
        bench_ticker = BENCHMARK_MAP.get(benchmark, benchmark)

        async def _fetch(t: str) -> tuple[str, list[dict] | Exception]:
            try:
                bars = await self._market.get_ohlc_bars(Ticker(t), range_period=period)
                return t, bars
            except Exception as e:
                return t, e

        (_, etf_bars), (_, bench_bars) = await asyncio.gather(
            _fetch(etf_ticker.upper()),
            _fetch(bench_ticker),
        )

        if isinstance(etf_bars, Exception):
            raise ValueError(f"Erro ao buscar dados de {etf_ticker}: {etf_bars}")
        if isinstance(bench_bars, Exception):
            raise ValueError(f"Erro ao buscar dados do benchmark {bench_ticker}: {bench_bars}")

        etf_dates,   etf_prices   = _extract_prices(etf_bars)
        bench_dates, bench_prices = _extract_prices(bench_bars)

        aligned_etf, aligned_bench = _align_series(
            (etf_dates, etf_prices), (bench_dates, bench_prices)
        )

        if len(aligned_etf) < 10:
            raise ValueError("Datas comuns insuficientes para calcular tracking error.")

        etf_rets   = _returns(aligned_etf)
        bench_rets = _returns(aligned_bench)
        n          = min(len(etf_rets), len(bench_rets))
        etf_rets, bench_rets = etf_rets[:n], bench_rets[:n]

        # Diferenças diárias
        diffs = [etf_rets[i] - bench_rets[i] for i in range(n)]
        mu_diff = sum(diffs) / n
        te_daily = math.sqrt(sum((d - mu_diff) ** 2 for d in diffs) / (n - 1)) if n > 1 else 0
        te_annual = te_daily * math.sqrt(TRADING_DAYS_YEAR)

        # Tracking difference (custo implícito)
        etf_total   = (aligned_etf[-1]   - aligned_etf[0])   / aligned_etf[0]
        bench_total = (aligned_bench[-1] - aligned_bench[0]) / aligned_bench[0]
        td          = bench_total - etf_total

        corr    = _pearson(etf_rets, bench_rets)
        r2      = corr ** 2
        # Beta: cov(etf, bench) / var(bench)
        mx, my  = sum(bench_rets) / n, sum(etf_rets) / n
        cov     = sum((bench_rets[i] - mx) * (etf_rets[i] - my) for i in range(n))
        var_b   = sum((b - mx) ** 2 for b in bench_rets)
        beta    = cov / var_b if var_b > 0 else 1.0
        # Information ratio
        ir      = (mu_diff * TRADING_DAYS_YEAR) / (te_annual) if te_annual > 0 else 0.0

        # Séries para gráfico (cumulative diff)
        cum_etf, cum_bench = [100.0], [100.0]
        for r in etf_rets:
            cum_etf.append(cum_etf[-1] * (1 + r))
        for r in bench_rets:
            cum_bench.append(cum_bench[-1] * (1 + r))
        daily_diffs = [
            {"idx": i, "diff": round(diffs[i] * 100, 4)} for i in range(n)
        ]

        # Obtém datas alinhadas para o gráfico
        etf_date_map = dict(zip(etf_dates, etf_prices))
        bench_date_map = dict(zip(bench_dates, bench_prices))
        common_dates = sorted(set(etf_dates) & set(bench_dates))

        chart_data = []
        cum_e = cum_b = 100.0
        for i, d in enumerate(common_dates[1:], 1):
            pe = etf_date_map.get(d, 0)
            pb = bench_date_map.get(d, 0)
            if pe > 0 and pb > 0:
                chart_data.append({
                    "date": d,
                    "etf_normalized": round(pe / etf_prices[0] * 100, 4),
                    "bench_normalized": round(pb / bench_prices[0] * 100, 4),
                })

        result = TrackingErrorResult(
            ticker=etf_ticker.upper(), benchmark=bench_ticker,
            period=period,
            tracking_error_pct   = round(te_annual * 100, 4),
            tracking_diff_pct    = round(td * 100, 4),
            correlation          = round(corr, 4),
            beta                 = round(beta, 4),
            r_squared            = round(r2, 4),
            information_ratio    = round(ir, 4),
            etf_return_pct       = round(etf_total * 100, 2),
            benchmark_return_pct = round(bench_total * 100, 2),
            excess_return_pct    = round((etf_total - bench_total) * 100, 2),
            n_days               = n,
            daily_diffs          = daily_diffs,
        )

        etf_info = get_etf(etf_ticker.upper())
        return {
            "etf":               etf_ticker.upper(),
            "etf_name":          etf_info.name if etf_info else etf_ticker,
            "benchmark":         bench_ticker,
            "benchmark_label":   info.benchmark,
            "period":            period,
            "tracking_error_pct":result.tracking_error_pct,
            "tracking_diff_pct": result.tracking_diff_pct,
            "quality_label":     result.quality_label,
            "correlation":       result.correlation,
            "beta":              result.beta,
            "r_squared":         result.r_squared,
            "information_ratio": result.information_ratio,
            "etf_return_pct":    result.etf_return_pct,
            "benchmark_return_pct": result.benchmark_return_pct,
            "excess_return_pct": result.excess_return_pct,
            "n_days":            result.n_days,
            "ter":               etf_info.ter if etf_info else 0.0,
            "chart_data":        chart_data,
            "interpretation": _te_interpretation(result),
        }

    # ── 3. Correlação (heatmap) ───────────────────────────────────────────────

    async def correlation_heatmap(
        self,
        tickers: list[str],
        period:  str = "1y",
    ) -> dict[str, Any]:
        """
        Matriz de correlação entre ETFs + rolling correlation.
        """
        from finanalytics_ai.domain.value_objects.ticker import Ticker

        tickers = [t.upper().strip() for t in tickers]
        sem     = asyncio.Semaphore(MAX_CONCURRENT)

        async def _fetch(t: str) -> tuple[str, list[dict] | Exception]:
            async with sem:
                try:
                    return t, await self._market.get_ohlc_bars(Ticker(t), range_period=period)
                except Exception as e:
                    return t, e

        results = await asyncio.gather(*[_fetch(t) for t in tickers])

        series_map: dict[str, tuple[list[str], list[float]]] = {}
        errors = []
        for t, r in results:
            if isinstance(r, Exception):
                errors.append({"ticker": t, "error": str(r)})
            else:
                series_map[t] = _extract_prices(r)

        valid = list(series_map.keys())
        if len(valid) < 2:
            raise ValueError("Dados válidos para menos de 2 ETFs.")

        # Matriz de correlação
        matrix: list[list[float]] = []
        for ta in valid:
            row = []
            for tb in valid:
                if ta == tb:
                    row.append(1.0)
                else:
                    pa, pb = _align_series(series_map[ta], series_map[tb])
                    ra, rb = _returns(pa), _returns(pb)
                    n = min(len(ra), len(rb))
                    row.append(round(_pearson(ra[:n], rb[:n]), 4))
            matrix.append(row)

        # Pares mais correlacionados e menos
        pairs = []
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                pairs.append({
                    "a": valid[i], "b": valid[j],
                    "correlation": matrix[i][j],
                })
        pairs_sorted = sorted(pairs, key=lambda p: abs(p["correlation"]), reverse=True)

        return {
            "tickers": valid,
            "matrix":  matrix,
            "period":  period,
            "top_correlated":    pairs_sorted[:3],
            "least_correlated":  sorted(pairs_sorted, key=lambda p: abs(p["correlation"]))[:3],
            "errors":            errors,
        }

    # ── 4. Rebalanceamento ────────────────────────────────────────────────────

    async def rebalance(
        self,
        positions:        list[dict],   # [{ticker, current_value}]
        target_weights:   dict[str, float],  # {ticker: weight_pct}
        new_contribution: float = 0.0,
        fetch_prices:     bool  = True,
    ) -> dict[str, Any]:
        """
        Calcula recomendação de rebalanceamento.

        positions:       posições atuais com valor em R$
        target_weights:  pesos alvo em % (deve somar 100)
        new_contribution: aporte adicional em R$
        """
        from finanalytics_ai.domain.value_objects.ticker import Ticker

        # Normaliza pesos
        total_w = sum(target_weights.values())
        if abs(total_w - 100.0) > 0.01:
            target_weights = {k: v / total_w * 100 for k, v in target_weights.items()}

        # Busca preços atuais se solicitado
        current_prices: dict[str, float] = {}
        if fetch_prices:
            sem = asyncio.Semaphore(MAX_CONCURRENT)
            async def _price(t: str) -> tuple[str, float]:
                async with sem:
                    try:
                        bars = await self._market.get_ohlc_bars(Ticker(t), range_period="5d")
                        _, prices = _extract_prices(bars)
                        return t, prices[-1] if prices else 0.0
                    except:
                        return t, 0.0
            price_results = await asyncio.gather(*[_price(t.upper()) for t in target_weights])
            current_prices = dict(price_results)

        total_current = sum(p["current_value"] for p in positions) + new_contribution
        pos_map = {p["ticker"].upper(): float(p["current_value"]) for p in positions}

        # Garante que todos os tickers alvo existem no pos_map
        for t in target_weights:
            if t not in pos_map:
                pos_map[t] = 0.0

        total_portfolio = sum(pos_map.values()) + new_contribution
        rebalance_positions: list[RebalancePosition] = []
        total_movement = 0.0

        for ticker, target_w in target_weights.items():
            current_val  = pos_map.get(ticker, 0.0)
            current_w    = (current_val / total_portfolio * 100) if total_portfolio > 0 else 0.0
            target_val   = total_portfolio * target_w / 100
            amount       = target_val - current_val
            deviation    = current_w - target_w
            price        = current_prices.get(ticker, 0.0)
            units        = amount / price if price > 0 else 0.0

            if abs(deviation) <= REBALANCE_THRESHOLD:
                action = "MANTER"
            elif amount > 0:
                action = "COMPRAR"
            else:
                action = "VENDER"

            total_movement += abs(amount)
            info = get_etf(ticker)
            rebalance_positions.append(RebalancePosition(
                ticker=ticker,
                name=info.name if info else ticker,
                current_value=current_val,
                current_weight=round(current_w, 2),
                target_weight=round(target_w, 2),
                deviation=round(deviation, 2),
                action=action,
                amount=round(amount, 2),
                units_approx=round(units, 2),
                current_price=price,
            ))

        rebalance_positions.sort(key=lambda p: abs(p.deviation), reverse=True)
        n_buys  = sum(1 for p in rebalance_positions if p.action == "COMPRAR")
        n_sells = sum(1 for p in rebalance_positions if p.action == "VENDER")

        rec = RebalanceRecommendation(
            total_current=sum(pos_map.values()),
            total_after=total_portfolio,
            new_contribution=new_contribution,
            positions=rebalance_positions,
            rebalance_cost=round(total_movement / 2, 2),
            n_buys=n_buys,
            n_sells=n_sells,
        )

        return {
            "total_current":    round(rec.total_current, 2),
            "total_after":      round(rec.total_after, 2),
            "new_contribution": rec.new_contribution,
            "rebalance_cost":   rec.rebalance_cost,
            "turnover_pct":     rec.turnover_pct,
            "n_buys":           n_buys,
            "n_sells":          n_sells,
            "positions": [
                {
                    "ticker":         p.ticker,
                    "name":           p.name,
                    "current_value":  round(p.current_value, 2),
                    "current_weight": p.current_weight,
                    "target_weight":  p.target_weight,
                    "deviation":      p.deviation,
                    "action":         p.action,
                    "amount":         p.amount,
                    "units_approx":   p.units_approx,
                    "current_price":  p.current_price,
                }
                for p in rec.positions
            ],
        }


# ── Interpretação tracking error ──────────────────────────────────────────────

def _te_interpretation(r: TrackingErrorResult) -> dict[str, str]:
    te    = r.tracking_error_pct
    td    = r.tracking_diff_pct
    corr  = r.correlation
    ir    = r.information_ratio

    if te < 0.5:
        te_msg = f"Tracking error de {te:.2f}% — replicação quase perfeita."
    elif te < 1.5:
        te_msg = f"Tracking error de {te:.2f}% — dentro do esperado para ETFs passivos."
    elif te < 3.0:
        te_msg = f"Tracking error de {te:.2f}% — desvio moderado. Verifique erros de replicação."
    else:
        te_msg = f"Tracking error de {te:.2f}% — desvio elevado. ETF pode ter problemas de liquidez."

    if td > 0:
        td_msg = f"Custo implícito de {td:.2f}% no período (benchmark superou o ETF). Normal para ETFs com TER."
    else:
        td_msg = f"ETF superou o benchmark em {abs(td):.2f}% (tracking difference positiva — raro)."

    corr_msg = f"Correlação de {corr:.2f} com benchmark — {'alta fidelidade' if corr > 0.98 else 'fidelidade moderada'}."

    return {"tracking_error": te_msg, "tracking_diff": td_msg, "correlation": corr_msg}

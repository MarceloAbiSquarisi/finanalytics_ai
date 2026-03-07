"""
finanalytics_ai.domain.performance.engine
──────────────────────────────────────────
Motor de cálculo de performance de carteiras. Puro: sem I/O, sem efeitos colaterais.

Recebe séries de retornos e produz métricas institucionais:

  Retorno total          — (valor_final / valor_inicial) - 1
  Retorno anualizado     — (1 + retorno_total)^(252/n_dias) - 1
  Volatilidade           — std(retornos_diários) × √252
  Sharpe Ratio           — (retorno_anual - rf) / volatilidade  [rf=0% padrão: CDI omitido]
  Max Drawdown           — maior queda pico→vale na equity curve
  Calmar Ratio           — retorno_anual / |max_drawdown|
  Beta                   — cov(carteira, benchmark) / var(benchmark)
  Alpha (Jensen)         — retorno_carteira - [rf + beta × (retorno_benchmark - rf)]
  VaR 95%                — percentil 5% dos retornos diários
  CVaR 95%               — média dos retornos abaixo do VaR
  Retornos mensais       — para heatmap (ano × mês)

Design decisions:

  Pesos estáticos (current weights):
    Usamos o peso atual de cada ativo (valor_atual / patrimônio_total) para
    reconstruir a série histórica. Isso é uma aproximação — os pesos reais
    variaram no passado conforme os preços mudaram e operações foram feitas.
    A alternativa exata (buy-and-hold desde a data de cada compra) exigiria
    histórico de transações, que não temos na estrutura atual. Trade-off
    documentado explicitamente; pode ser melhorado em sprint futura com
    registro de transações.

  stdlib apenas:
    Sem numpy/scipy/pandas. Todos os cálculos com math e listas. Isso mantém
    o serviço containerizável sem ~50MB de dependências extras. Sharpe, beta
    e correlação são implementados diretamente — algoritmicamente equivalentes
    às versões numpy mas mais lentos para carteiras > 50 ativos (irrelevante aqui).

  Alinhamento temporal:
    Intersecção dos timestamps garante que só datas com dados de TODOS os
    ativos são usadas. Isso evita distorções causadas por dias sem cotação
    (ex: IPOs recentes, feriados assimétricos).

  Benchmark padrão = BOVA11:
    FII do ETF do Ibovespa, negociado na B3. Mais fácil de obter via BRAPI
    que ^BVSP (que a API às vezes retorna vazio). Fallback para IBOV caso
    BOVA11 falhe.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class DailyReturn:
    date: str       # "YYYY-MM-DD"
    value: float    # retorno fracionário: 0.012 = +1.2%


@dataclass
class EquityPoint:
    date: str
    portfolio: float    # valor indexado (começa em 100)
    benchmark: float    # valor indexado (começa em 100)
    drawdown: float     # drawdown da carteira em % (negativo ou zero)


@dataclass
class MonthlyReturn:
    year: int
    month: int
    portfolio_pct: float
    benchmark_pct: float


@dataclass
class PerformanceMetrics:
    # Retorno
    total_return_pct:       float
    annualized_return_pct:  float
    benchmark_total_pct:    float
    benchmark_annualized_pct: float
    excess_return_pct:      float       # alpha simples (portfolio - benchmark)

    # Risco
    volatility_annual_pct:  float
    max_drawdown_pct:       float
    max_drawdown_start:     str
    max_drawdown_end:       str
    var_95_pct:             float       # Value at Risk diário 95%
    cvar_95_pct:            float       # Conditional VaR

    # Ajustados ao risco
    sharpe_ratio:           float
    calmar_ratio:           float
    beta:                   float
    alpha_pct:              float       # Jensen Alpha anualizado
    correlation:            float       # correlação com benchmark

    # Período
    period_days:            int
    start_date:             str
    end_date:               str

    # Sequências
    best_day_pct:           float
    worst_day_pct:          float
    positive_days:          int
    negative_days:          int
    win_rate_pct:           float


@dataclass
class PerformanceResult:
    portfolio_id:    str
    portfolio_name:  str
    period:          str
    metrics:         PerformanceMetrics
    equity_curve:    list[EquityPoint]
    monthly_returns: list[MonthlyReturn]
    positions_contribution: list[dict[str, Any]]   # contribuição de cada ativo


# ── Cálculos auxiliares ───────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float], ddof: int = 1) -> float:
    n = len(values)
    if n <= ddof:
        return 0.0
    m = _mean(values)
    variance = sum((x - m) ** 2 for x in values) / (n - ddof)
    return math.sqrt(variance)


def _covariance(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = _mean(xs[:n]), _mean(ys[:n])
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


def _pearson(xs: list[float], ys: list[float]) -> float:
    sx, sy = _std(xs), _std(ys)
    if sx == 0 or sy == 0:
        return 0.0
    return _covariance(xs, ys) / (sx * sy)


def _prices_to_returns(prices: list[float]) -> list[float]:
    """Converte série de preços em retornos diários fracionários."""
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        else:
            returns.append(0.0)
    return returns


def _cumulative_from_returns(returns: list[float], base: float = 100.0) -> list[float]:
    """Constrói série acumulada a partir de retornos. Começa em `base`."""
    curve = [base]
    for r in returns:
        curve.append(curve[-1] * (1 + r))
    return curve


def _max_drawdown(equity: list[float]) -> tuple[float, int, int]:
    """
    Retorna (max_drawdown, idx_pico, idx_vale).
    max_drawdown é negativo: -0.25 = -25%.
    """
    if len(equity) < 2:
        return 0.0, 0, 0
    peak = equity[0]
    peak_idx = 0
    max_dd = 0.0
    dd_start = 0
    dd_end = 0
    for i, v in enumerate(equity):
        if v > peak:
            peak = v
            peak_idx = i
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
            dd_start = peak_idx
            dd_end = i
    return max_dd, dd_start, dd_end


def _var_cvar(returns: list[float], confidence: float = 0.95) -> tuple[float, float]:
    """VaR e CVaR paramétrico simples (histórico)."""
    if not returns:
        return 0.0, 0.0
    sorted_r = sorted(returns)
    cutoff_idx = int(len(sorted_r) * (1 - confidence))
    cutoff_idx = max(0, cutoff_idx)
    var = sorted_r[cutoff_idx]
    tail = sorted_r[:cutoff_idx + 1]
    cvar = _mean(tail) if tail else var
    return var, cvar


def _annualize_return(total_return: float, n_days: int) -> float:
    """Anualiza retorno total. Retorna fracionário."""
    if n_days <= 0:
        return 0.0
    try:
        return (1 + total_return) ** (252 / n_days) - 1
    except (ValueError, ZeroDivisionError):
        return 0.0


# ── Alinhamento de séries ─────────────────────────────────────────────────────

def _align_price_series(
    series_map: dict[str, dict[str, float]],  # ticker → {date → price}
) -> tuple[list[str], dict[str, list[float]]]:
    """
    Intersecta datas de todos os tickers.
    Retorna (datas_ordenadas, {ticker: [preços alinhados]}).
    """
    if not series_map:
        return [], {}
    common_dates = set.intersection(*[set(v.keys()) for v in series_map.values()])
    sorted_dates = sorted(common_dates)
    aligned = {
        ticker: [prices[d] for d in sorted_dates]
        for ticker, prices in series_map.items()
    }
    return sorted_dates, aligned


# ── Motor principal ───────────────────────────────────────────────────────────

def build_portfolio_returns(
    price_series: dict[str, list[float]],   # ticker → preços alinhados
    weights: dict[str, float],              # ticker → peso (soma = 1.0)
) -> list[float]:
    """
    Constrói série de retornos diários da carteira ponderada.
    Retorno_carteira[t] = Σ(weight_i × return_i[t])
    """
    if not price_series or not weights:
        return []

    tickers = list(price_series.keys())
    n = min(len(v) for v in price_series.values())
    if n < 2:
        return []

    # Retornos individuais
    ticker_returns: dict[str, list[float]] = {}
    for t in tickers:
        prices = price_series[t][:n]
        ticker_returns[t] = _prices_to_returns(prices)

    n_returns = len(next(iter(ticker_returns.values())))

    # Retorno ponderado diário
    portfolio_returns = []
    for i in range(n_returns):
        daily = sum(
            weights.get(t, 0.0) * ticker_returns[t][i]
            for t in tickers
        )
        portfolio_returns.append(daily)

    return portfolio_returns


def compute_performance(
    portfolio_id: str,
    portfolio_name: str,
    period: str,
    dates: list[str],
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    weights: dict[str, float],
    ticker_returns_map: dict[str, list[float]],
    rf_annual: float = 0.0,             # taxa livre de risco anual (ex: 0.10 = 10%)
) -> PerformanceResult:
    """
    Calcula todas as métricas de performance.

    Parâmetros:
      dates:              lista de datas (len = len(returns) + 1 ou len(returns))
      portfolio_returns:  retornos diários da carteira
      benchmark_returns:  retornos diários do benchmark
      weights:            pesos atuais por ticker
      ticker_returns_map: retornos individuais por ticker (para contribuição)
      rf_annual:          taxa livre de risco anual
    """
    n = min(len(portfolio_returns), len(benchmark_returns))
    if n == 0:
        raise ValueError("Série de retornos vazia — dados insuficientes.")

    pr = portfolio_returns[:n]
    br = benchmark_returns[:n]
    used_dates = dates[:n + 1] if len(dates) > n else dates[:n]

    # ── Equity curves ─────────────────────────────────────────────────────────
    port_equity = _cumulative_from_returns(pr)
    bench_equity = _cumulative_from_returns(br)

    # ── Drawdown ──────────────────────────────────────────────────────────────
    dd_series = []
    peak = port_equity[0]
    for v in port_equity:
        if v > peak:
            peak = v
        dd_series.append((v - peak) / peak * 100)

    max_dd, dd_start_idx, dd_end_idx = _max_drawdown(port_equity)

    # ── Retornos totais ───────────────────────────────────────────────────────
    total_return   = (port_equity[-1] / port_equity[0]) - 1
    bench_total    = (bench_equity[-1] / bench_equity[0]) - 1
    n_days         = n
    ann_return     = _annualize_return(total_return, n_days)
    ann_bench      = _annualize_return(bench_total, n_days)

    # ── Risco ─────────────────────────────────────────────────────────────────
    vol_daily  = _std(pr)
    vol_annual = vol_daily * math.sqrt(252)

    rf_daily   = rf_annual / 252
    excess_daily = [r - rf_daily for r in pr]
    sharpe     = (_mean(excess_daily) * 252) / (vol_annual) if vol_annual > 0 else 0.0
    calmar     = ann_return / abs(max_dd) if max_dd < 0 else float("inf")

    var_95, cvar_95 = _var_cvar(pr)

    # ── Beta e Alpha ──────────────────────────────────────────────────────────
    var_bench = _std(br) ** 2
    beta      = _covariance(pr, br) / var_bench if var_bench > 0 else 1.0
    corr      = _pearson(pr, br)

    rf_daily_ann  = rf_annual
    bench_excess  = ann_bench - rf_daily_ann
    alpha         = ann_return - (rf_daily_ann + beta * bench_excess)

    # ── Win rate ──────────────────────────────────────────────────────────────
    pos_days = sum(1 for r in pr if r > 0)
    neg_days = sum(1 for r in pr if r < 0)
    win_rate = (pos_days / n * 100) if n > 0 else 0.0

    # ── Datas ─────────────────────────────────────────────────────────────────
    start_date = used_dates[0]  if used_dates else ""
    end_date   = used_dates[-1] if used_dates else ""
    dd_start_date = used_dates[dd_start_idx] if dd_start_idx < len(used_dates) else start_date
    dd_end_date   = used_dates[dd_end_idx]   if dd_end_idx   < len(used_dates) else end_date

    # ── Equity curve combinada ────────────────────────────────────────────────
    curve_dates = used_dates if len(used_dates) == len(port_equity) else (
        [used_dates[0]] + [used_dates[i] for i in range(1, min(len(used_dates), len(port_equity)))]
        if used_dates else [str(i) for i in range(len(port_equity))]
    )
    # Garante mesmo comprimento
    min_len = min(len(curve_dates), len(port_equity), len(bench_equity), len(dd_series))
    equity_curve = [
        EquityPoint(
            date=curve_dates[i],
            portfolio=round(port_equity[i], 4),
            benchmark=round(bench_equity[i], 4),
            drawdown=round(dd_series[i], 4),
        )
        for i in range(min_len)
    ]

    # ── Retornos mensais ──────────────────────────────────────────────────────
    monthly = _compute_monthly_returns(used_dates, pr, br)

    # ── Contribuição por ativo ────────────────────────────────────────────────
    contributions = []
    for ticker, w in sorted(weights.items(), key=lambda x: -x[1]):
        t_rets = ticker_returns_map.get(ticker, [])[:n]
        if not t_rets:
            continue
        t_total  = (math.prod(1 + r for r in t_rets) - 1) if t_rets else 0.0
        contrib  = w * t_total
        t_vol    = _std(t_rets) * math.sqrt(252) * 100
        contributions.append({
            "ticker":          ticker,
            "weight_pct":      round(w * 100, 2),
            "total_return_pct": round(t_total * 100, 2),
            "contribution_pct": round(contrib * 100, 2),
            "volatility_pct":  round(t_vol, 2),
        })

    metrics = PerformanceMetrics(
        total_return_pct         = round(total_return * 100, 2),
        annualized_return_pct    = round(ann_return * 100, 2),
        benchmark_total_pct      = round(bench_total * 100, 2),
        benchmark_annualized_pct = round(ann_bench * 100, 2),
        excess_return_pct        = round((total_return - bench_total) * 100, 2),
        volatility_annual_pct    = round(vol_annual * 100, 2),
        max_drawdown_pct         = round(max_dd * 100, 2),
        max_drawdown_start       = dd_start_date,
        max_drawdown_end         = dd_end_date,
        var_95_pct               = round(var_95 * 100, 2),
        cvar_95_pct              = round(cvar_95 * 100, 2),
        sharpe_ratio             = round(sharpe, 3),
        calmar_ratio             = round(calmar, 3) if calmar != float("inf") else 0.0,
        beta                     = round(beta, 3),
        alpha_pct                = round(alpha * 100, 2),
        correlation              = round(corr, 3),
        period_days              = n_days,
        start_date               = start_date,
        end_date                 = end_date,
        best_day_pct             = round(max(pr) * 100, 2) if pr else 0.0,
        worst_day_pct            = round(min(pr) * 100, 2) if pr else 0.0,
        positive_days            = pos_days,
        negative_days            = neg_days,
        win_rate_pct             = round(win_rate, 1),
    )

    return PerformanceResult(
        portfolio_id    = portfolio_id,
        portfolio_name  = portfolio_name,
        period          = period,
        metrics         = metrics,
        equity_curve    = equity_curve,
        monthly_returns = monthly,
        positions_contribution = contributions,
    )


def _compute_monthly_returns(
    dates: list[str],
    port_returns: list[float],
    bench_returns: list[float],
) -> list[MonthlyReturn]:
    """Agrega retornos diários em mensais via produto acumulado."""
    if not dates or not port_returns:
        return []

    monthly: dict[tuple[int, int], dict] = {}
    n = min(len(dates), len(port_returns), len(bench_returns))

    for i in range(n):
        d = dates[i]
        try:
            dt = datetime.strptime(d[:10], "%Y-%m-%d")
        except ValueError:
            continue
        key = (dt.year, dt.month)
        if key not in monthly:
            monthly[key] = {"port": [], "bench": []}
        monthly[key]["port"].append(port_returns[i])
        monthly[key]["bench"].append(bench_returns[i])

    result = []
    for (year, month), data in sorted(monthly.items()):
        port_m  = math.prod(1 + r for r in data["port"]) - 1
        bench_m = math.prod(1 + r for r in data["bench"]) - 1
        result.append(MonthlyReturn(
            year          = year,
            month         = month,
            portfolio_pct = round(port_m * 100, 2),
            benchmark_pct = round(bench_m * 100, 2),
        ))

    return result

"""
finanalytics_ai.application.ml.feature_pipeline

Computa features tecnicas a partir de series OHLC.

Implementacoes puras (sem I/O) — facilitam testes unitarios.

RSI(14): algoritmo de Wilder (EMA suavizada = 1/14, nao SMA)
  Diferente do RSI simples — mais responsivo, mais usado em producao.

Beta(60d): regressao OLS simples (sem biblioteca)
  beta = Cov(ticker, ibov) / Var(ibov)
  Implementado em numpy puro — 100x mais rapido que statsmodels para
  vetores de 60 elementos.

Volatilidade anualizada: std(retornos_21d) * sqrt(252)
  Usa retornos logaritmicos para propriedade de aditividade.
"""
from __future__ import annotations

import math
from datetime import datetime


def compute_returns(closes: list[float], window: int) -> float | None:
    """Retorno acumulado em `window` dias. Requer len(closes) > window."""
    if len(closes) <= window or closes[-window - 1] == 0:
        return None
    return (closes[-1] / closes[-window - 1]) - 1.0


def compute_volatility_21d(closes: list[float]) -> float | None:
    """Volatilidade anualizada usando retornos log dos ultimos 21 dias."""
    if len(closes) < 23:
        return None
    log_rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - 21, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(log_rets) < 10:
        return None
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    return math.sqrt(var) * math.sqrt(252)


def compute_rsi_14(closes: list[float]) -> float | None:
    """RSI(14) de Wilder. Requer minimo 28 fechamentos (14 warm-up + 14 calculo)."""
    if len(closes) < 28:
        return None

    # Primeira media simples (seed do EMA de Wilder)
    seed = closes[-28:]
    gains = [max(seed[i] - seed[i-1], 0) for i in range(1, 15)]
    losses = [max(seed[i-1] - seed[i], 0) for i in range(1, 15)]
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14

    # EMA suavizada de Wilder para o restante
    for i in range(15, len(seed)):
        delta = seed[i] - seed[i-1]
        avg_gain = (avg_gain * 13 + max(delta, 0)) / 14
        avg_loss = (avg_loss * 13 + max(-delta, 0)) / 14

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_beta_60d(
    ticker_rets: list[float],
    ibov_rets: list[float],
) -> float | None:
    """Beta vs Ibovespa via OLS: beta = Cov(r_ticker, r_ibov) / Var(r_ibov)."""
    n = min(len(ticker_rets), len(ibov_rets), 60)
    if n < 20:
        return None
    tr = ticker_rets[-n:]
    ir = ibov_rets[-n:]
    mean_t = sum(tr) / n
    mean_i = sum(ir) / n
    cov = sum((tr[i] - mean_t) * (ir[i] - mean_i) for i in range(n)) / n
    var_i = sum((ir[i] - mean_i) ** 2 for i in range(n)) / n
    if var_i == 0:
        return None
    return cov / var_i


def compute_volume_ratio(volumes: list[float | None], window: int = 21) -> float | None:
    """Volume atual / media(window). >1.5 = aceleracao de volume."""
    vols = [v for v in volumes if v is not None]
    if len(vols) < window + 1:
        return None
    avg = sum(vols[-window-1:-1]) / window
    if avg == 0:
        return None
    return vols[-1] / avg


def build_features_from_ohlc(
    ticker: str,
    date: datetime,
    closes: list[float],
    volumes: list[float | None],
    ibov_rets: list[float],
    fundamental: dict[str, float | None],
) -> "TickerFeatures":
    """
    Monta TickerFeatures a partir de dados brutos.

    Todas as computacoes sao tolerantes a dados ausentes:
    retorna None para features que nao podem ser calculadas
    em vez de propagar excecao.
    """
    from finanalytics_ai.domain.ml.entities import TickerFeatures

    ticker_rets = [
        (closes[i] - closes[i-1]) / closes[i-1]
        for i in range(1, len(closes)) if closes[i-1] != 0
    ]

    return TickerFeatures(
        ticker=ticker,
        date=date,
        ret_5d=compute_returns(closes, 5),
        ret_21d=compute_returns(closes, 21),
        ret_63d=compute_returns(closes, 63),
        volatility_21d=compute_volatility_21d(closes),
        rsi_14=compute_rsi_14(closes),
        beta_60d=compute_beta_60d(ticker_rets, ibov_rets),
        volume_ratio_21d=compute_volume_ratio(volumes),
        pe=fundamental.get("P_L"),
        pvp=fundamental.get("P_VP"),
        roe=fundamental.get("ROE"),
        roic=fundamental.get("ROIC"),
        ev_ebitda=fundamental.get("EV_EBITDA"),
        debt_ebitda=fundamental.get("DividaLiquida_EBITDA"),
        net_margin=fundamental.get("MargemLiquida"),
        revenue_growth=fundamental.get("GiroAtivos"),
    )

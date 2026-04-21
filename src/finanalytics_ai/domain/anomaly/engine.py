"""
Motor de deteccao de anomalias de mercado — dominio puro, sem I/O.

Algoritmos implementados (stdlib only, sem numpy/scipy):

  1. Z-Score sobre retornos diarios
     Detecta spikes de volatilidade. Um retorno e anomalo quando
     |z| > threshold (padrao 2.5). Calcula media e std moveis sobre
     uma janela (padrao 30 dias) para ser adaptativo.
     Por que Z-Score: simples, interpretavel, eficaz para outliers
     em distribuicoes aproximadamente normais. Retornos diarios
     seguem distribuicao leptocurtica (caudas gordas), entao usamos
     threshold maior (2.5 vs 1.96 gaussiano) para reduzir falsos positivos.

  2. Bollinger Band Breakdown / Breakout
     Preco saindo das bandas (media +/- k*std) sobre janela.
     Breakout (acima da banda superior) = possivel inicio de tendencia alta.
     Breakdown (abaixo da banda inferior) = possivel inicio de queda.
     Por que Bollinger: combina nivel de preco com volatilidade atual,
     mais contextual que um threshold absoluto.

  3. CUSUM (Cumulative Sum Control Chart)
     Detecta mudancas persistentes na media dos retornos.
     Acumula desvios da media historica — quando a soma acumulada
     cruza um threshold (k * std), indica mudanca estrutural.
     Por que CUSUM: captura tendencias graduais que Z-Score isolado
     nao detecta (Z-Score e pontual, CUSUM e acumulativo).
     Referencia: Page (1954), usado em controle de qualidade industrial
     e depois adaptado para series temporais financeiras.

  4. Volume Spike
     Volume atual vs media movel de volume.
     Spike quando volume > media * multiplier (padrao 3x).
     Anomalias de volume frequentemente precedem movimentos de preco.
     Tratado separadamente dos algoritmos de preco pois a distribuicao
     de volume e muito mais assimetrica (log-normal).

Design decisions:

  stdlib puro:
    Evita dependencia de numpy/scipy no container, que adicionaria
    ~50MB a imagem. Para N <= 500 barras e 4 algoritmos, a performance
    e negligivel (< 1ms por ativo). Para escala maior, a troca para
    numpy seria uma linha por funcao.

  Janela adaptativa:
    Se a serie historica e menor que a janela configurada, usamos
    max(10, len(series) // 3) para nao retornar vazio sempre.

  Severidade em 3 niveis:
    LOW  (z < 2.5 ou volume 2-3x)
    MEDIUM (z 2.5-3.5 ou volume 3-5x)
    HIGH (z > 3.5 ou volume > 5x)
    Facilita triagem pelo usuario sem sobrecarregar com alertas LOW.

  AnomalyEvent como frozen dataclass:
    Imutavel apos criacao — detectar e relatar, nunca modificar.
    Compativel com serialzacao JSON direta via to_dict().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import statistics
from typing import Any

# ── Tipos ─────────────────────────────────────────────────────────────────────


class AnomalyType(StrEnum):
    ZSCORE_SPIKE = "zscore_spike"  # retorno anomalo (Z-Score)
    BOLLINGER_BREAK = "bollinger_break"  # preco fora das bandas de Bollinger
    CUSUM_SHIFT = "cusum_shift"  # mudanca estrutural (CUSUM)
    VOLUME_SPIKE = "volume_spike"  # volume anomalo


class AnomalySeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnomalyDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


# ── Evento de anomalia ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyEvent:
    """
    Evento de anomalia detectado para um ativo.

    Imutavel — detectado, registrado, nao modificado.
    """

    ticker: str
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    direction: AnomalyDirection
    score: float  # metrica bruta (z-score, ratio, cusum value)
    threshold: float  # threshold que foi cruzado
    current_value: float  # valor atual (preco, retorno ou volume)
    description: str  # mensagem legivel
    timestamp: int  # Unix timestamp da barra detectada
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "anomaly_type": str(self.anomaly_type),
            "severity": str(self.severity),
            "direction": str(self.direction),
            "score": round(self.score, 4),
            "threshold": round(self.threshold, 4),
            "current_value": round(self.current_value, 4),
            "description": self.description,
            "timestamp": self.timestamp,
            "context": self.context,
        }


# ── Resultado da analise ──────────────────────────────────────────────────────


@dataclass
class AnomalyResult:
    """Resultado da analise de anomalias para um ativo."""

    ticker: str
    bars_analyzed: int
    anomalies: list[AnomalyEvent]
    error: str | None = None

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0

    @property
    def max_severity(self) -> AnomalySeverity | None:
        if not self.anomalies:
            return None
        order = {AnomalySeverity.LOW: 0, AnomalySeverity.MEDIUM: 1, AnomalySeverity.HIGH: 2}
        return max(self.anomalies, key=lambda a: order[a.severity]).severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "bars_analyzed": self.bars_analyzed,
            "anomaly_count": len(self.anomalies),
            "has_anomalies": self.has_anomalies,
            "max_severity": str(self.max_severity) if self.max_severity else None,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "error": self.error,
        }


# ── Configuracao dos detectores ───────────────────────────────────────────────


@dataclass
class DetectorConfig:
    """Parametros configuráveis para todos os detectores."""

    # Z-Score
    zscore_window: int = 30
    zscore_threshold: float = 2.5

    # Bollinger
    bollinger_window: int = 20
    bollinger_k: float = 2.0  # numero de desvios padrao

    # CUSUM
    cusum_window: int = 30
    cusum_k: float = 0.5  # slack factor (fracao do std)
    cusum_threshold: float = 5.0  # limiar de deteccao (em unidades de std)

    # Volume
    volume_window: int = 20
    volume_multiplier: float = 3.0  # multiplo da media para spike

    # Geral
    lookback_bars: int = 100  # barras historicas para analise


# ── Helpers estatisticos ──────────────────────────────────────────────────────


def _mean(series: list[float]) -> float:
    return sum(series) / len(series) if series else 0.0


def _std(series: list[float]) -> float:
    if len(series) < 2:
        return 0.0
    try:
        return statistics.stdev(series)
    except statistics.StatisticsError:
        return 0.0


def _severity_from_z(z: float) -> AnomalySeverity:
    abs_z = abs(z)
    if abs_z >= 3.5:
        return AnomalySeverity.HIGH
    if abs_z >= 2.5:
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def _severity_from_ratio(ratio: float) -> AnomalySeverity:
    if ratio >= 5.0:
        return AnomalySeverity.HIGH
    if ratio >= 3.0:
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def _returns(closes: list[float]) -> list[float]:
    """Retornos percentuais simples."""
    if len(closes) < 2:
        return []
    return [(closes[i] - closes[i - 1]) / closes[i - 1] * 100.0 for i in range(1, len(closes))]


# ── Detector 1: Z-Score sobre retornos ───────────────────────────────────────


def detect_zscore(
    bars: list[dict[str, Any]],
    ticker: str,
    config: DetectorConfig,
) -> list[AnomalyEvent]:
    """
    Detecta spikes de retorno usando Z-Score com janela movel.

    Avalia apenas o ultimo retorno (barra mais recente) contra a
    distribuicao historica da janela. Isso evita over-alerting em
    dados historicos — o objetivo e detectar o que e anomalo AGORA.
    """
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < config.zscore_window + 1:
        return []

    rets = _returns(closes)
    window = config.zscore_window

    # Ultimo retorno vs janela historica
    history = rets[-(window + 1) : -1]
    current_ret = rets[-1]

    if len(history) < 5:
        return []

    mu = _mean(history)
    std = _std(history)

    if std < 1e-8:
        return []

    z = (current_ret - mu) / std

    if abs(z) < config.zscore_threshold:
        return []

    direction = AnomalyDirection.UP if z > 0 else AnomalyDirection.DOWN
    severity = _severity_from_z(z)
    ts = bars[-1].get("time", 0)
    price = closes[-1]

    return [
        AnomalyEvent(
            ticker=ticker,
            anomaly_type=AnomalyType.ZSCORE_SPIKE,
            severity=severity,
            direction=direction,
            score=round(z, 3),
            threshold=config.zscore_threshold,
            current_value=current_ret,
            description=(
                f"{ticker}: retorno de {current_ret:+.2f}% e anomalo "
                f"(Z={z:+.2f}, threshold=+/-{config.zscore_threshold}). "
                f"Media historica: {mu:.2f}%, std: {std:.2f}%"
            ),
            timestamp=ts,
            context={
                "return_pct": round(current_ret, 3),
                "hist_mean": round(mu, 3),
                "hist_std": round(std, 3),
                "window": window,
                "price": round(price, 2),
            },
        )
    ]


# ── Detector 2: Bollinger Band ────────────────────────────────────────────────


def detect_bollinger(
    bars: list[dict[str, Any]],
    ticker: str,
    config: DetectorConfig,
) -> list[AnomalyEvent]:
    """
    Detecta preco saindo das bandas de Bollinger.

    Calcula banda sobre os ultimos bollinger_window fechamentos.
    O preco atual e comparado com upper_band e lower_band.
    """
    closes = [b["close"] for b in bars if b.get("close")]
    window = config.bollinger_window

    if len(closes) < window + 1:
        return []

    history = closes[-(window + 1) : -1]
    current_price = closes[-1]
    mu = _mean(history)
    std = _std(history)

    if std < 1e-8:
        return []

    upper = mu + config.bollinger_k * std
    lower = mu - config.bollinger_k * std

    if lower <= current_price <= upper:
        return []

    is_breakout = current_price > upper
    direction = AnomalyDirection.UP if is_breakout else AnomalyDirection.DOWN
    band_crossed = upper if is_breakout else lower
    deviation = abs(current_price - band_crossed) / std  # desvios acima/abaixo da banda
    severity = _severity_from_z(config.bollinger_k + deviation)
    ts = bars[-1].get("time", 0)
    band_label = "superior" if is_breakout else "inferior"

    return [
        AnomalyEvent(
            ticker=ticker,
            anomaly_type=AnomalyType.BOLLINGER_BREAK,
            severity=severity,
            direction=direction,
            score=round((current_price - mu) / std, 3),
            threshold=config.bollinger_k,
            current_value=current_price,
            description=(
                f"{ticker}: preco R${current_price:.2f} rompeu a banda {band_label} "
                f"de Bollinger (R${band_crossed:.2f}). "
                f"Banda: [{lower:.2f}, {upper:.2f}], k={config.bollinger_k}"
            ),
            timestamp=ts,
            context={
                "price": round(current_price, 2),
                "upper_band": round(upper, 2),
                "lower_band": round(lower, 2),
                "middle_band": round(mu, 2),
                "band_std": round(std, 2),
                "window": window,
            },
        )
    ]


# ── Detector 3: CUSUM ─────────────────────────────────────────────────────────


def detect_cusum(
    bars: list[dict[str, Any]],
    ticker: str,
    config: DetectorConfig,
) -> list[AnomalyEvent]:
    """
    CUSUM (Cumulative Sum) para detectar mudancas de tendencia persistentes.

    Algoritmo:
      1. Calcula retornos sobre a janela historica
      2. Estima mu e std da janela de referencia
      3. Acumula s_pos e s_neg sobre os ultimos pontos
      4. Dispara quando max(s_pos, s_neg) > threshold * std

    s_pos acumula desvios positivos (tendencia de alta)
    s_neg acumula desvios negativos (tendencia de queda)
    k = slack que absorve ruido normal (tipico: 0.5 * std)
    """
    closes = [b["close"] for b in bars if b.get("close")]
    window = config.cusum_window

    if len(closes) < window + 2:
        return []

    rets = _returns(closes)
    history = rets[:window]  # janela de referencia
    recent = rets[window:]  # janela de observacao

    if len(recent) < 3:
        return []

    mu = _mean(history)
    std = _std(history)

    if std < 1e-8:
        return []

    k = config.cusum_k * std
    h = config.cusum_threshold * std

    s_pos = 0.0
    s_neg = 0.0
    for r in recent:
        s_pos = max(0.0, s_pos + (r - mu) - k)
        s_neg = max(0.0, s_neg - (r - mu) - k)

    max_cusum = max(s_pos, s_neg)
    if max_cusum < h:
        return []

    is_positive = s_pos >= s_neg
    direction = AnomalyDirection.UP if is_positive else AnomalyDirection.DOWN
    cusum_val = s_pos if is_positive else s_neg

    # Severidade baseada em quantas vezes cruzou o threshold
    ratio = cusum_val / h
    if ratio >= 2.0:
        severity = AnomalySeverity.HIGH
    elif ratio >= 1.5:
        severity = AnomalySeverity.MEDIUM
    else:
        severity = AnomalySeverity.LOW

    ts = bars[-1].get("time", 0)
    price = closes[-1]
    trend = "alta" if is_positive else "queda"

    return [
        AnomalyEvent(
            ticker=ticker,
            anomaly_type=AnomalyType.CUSUM_SHIFT,
            severity=severity,
            direction=direction,
            score=round(cusum_val, 4),
            threshold=round(h, 4),
            current_value=price,
            description=(
                f"{ticker}: CUSUM detectou mudanca de tendencia persistente de {trend} "
                f"(S={cusum_val:.3f} > h={h:.3f}, {len(recent)} periodos recentes)"
            ),
            timestamp=ts,
            context={
                "s_pos": round(s_pos, 4),
                "s_neg": round(s_neg, 4),
                "threshold_h": round(h, 4),
                "slack_k": round(k, 4),
                "ref_mean": round(mu, 3),
                "ref_std": round(std, 3),
                "recent_bars": len(recent),
                "price": round(price, 2),
            },
        )
    ]


# ── Detector 4: Volume Spike ──────────────────────────────────────────────────


def detect_volume_spike(
    bars: list[dict[str, Any]],
    ticker: str,
    config: DetectorConfig,
) -> list[AnomalyEvent]:
    """
    Detecta spikes de volume usando razao volume_atual / media_movel.

    Volume e tratado com media aritmetica simples (nao retornos) pois
    a base de comparacao natural e o volume absoluto medio.
    Ignora barras com volume zero (mercado fechado / feriado).
    """
    volumes = [
        (b.get("volume") or 0, b.get("time", 0), b.get("close", 0))
        for b in bars
        if (b.get("volume") or 0) > 0
    ]

    window = config.volume_window
    if len(volumes) < window + 1:
        return []

    history_vols = [v for v, _, _ in volumes[-(window + 1) : -1]]
    curr_vol, ts, curr_price = volumes[-1]

    if not history_vols:
        return []

    avg_vol = _mean(history_vols)
    if avg_vol < 1:
        return []

    ratio = curr_vol / avg_vol
    if ratio < config.volume_multiplier:
        return []

    severity = _severity_from_ratio(ratio)

    # Direcao baseada no fechamento do dia
    prev_close = volumes[-2][2] if len(volumes) >= 2 else curr_price
    direction = AnomalyDirection.UP if curr_price >= prev_close else AnomalyDirection.DOWN

    return [
        AnomalyEvent(
            ticker=ticker,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=severity,
            direction=direction,
            score=round(ratio, 2),
            threshold=config.volume_multiplier,
            current_value=float(curr_vol),
            description=(
                f"{ticker}: volume {ratio:.1f}x acima da media ({curr_vol:,.0f} vs media {avg_vol:,.0f})"
            ),
            timestamp=ts,
            context={
                "current_volume": int(curr_vol),
                "avg_volume": round(avg_vol, 0),
                "ratio": round(ratio, 2),
                "window": window,
                "price": round(curr_price, 2),
            },
        )
    ]


# ── Orquestrador principal ────────────────────────────────────────────────────


def analyze_ticker(
    bars: list[dict[str, Any]],
    ticker: str,
    config: DetectorConfig | None = None,
) -> AnomalyResult:
    """
    Executa todos os detectores sobre as barras OHLCV de um ativo.

    Retorna AnomalyResult com todos os eventos detectados, ordenados
    por severidade decrescente.
    """
    if config is None:
        config = DetectorConfig()

    if not bars:
        return AnomalyResult(
            ticker=ticker, bars_analyzed=0, anomalies=[], error="Sem barras para analisar"
        )

    # Limita ao lookback configurado
    bars_used = bars[-config.lookback_bars :]

    try:
        anomalies: list[AnomalyEvent] = []
        anomalies.extend(detect_zscore(bars_used, ticker, config))
        anomalies.extend(detect_bollinger(bars_used, ticker, config))
        anomalies.extend(detect_cusum(bars_used, ticker, config))
        anomalies.extend(detect_volume_spike(bars_used, ticker, config))

        # Ordena: HIGH > MEDIUM > LOW, depois por |score| desc
        order = {AnomalySeverity.HIGH: 2, AnomalySeverity.MEDIUM: 1, AnomalySeverity.LOW: 0}
        anomalies.sort(key=lambda a: (order[a.severity], abs(a.score)), reverse=True)

    except Exception as exc:
        return AnomalyResult(
            ticker=ticker, bars_analyzed=len(bars_used), anomalies=[], error=str(exc)
        )

    return AnomalyResult(
        ticker=ticker,
        bars_analyzed=len(bars_used),
        anomalies=anomalies,
    )


# ── Analise multi-ticker ──────────────────────────────────────────────────────


@dataclass
class MultiAnomalyResult:
    """Resultado da analise de anomalias para multiplos ativos."""

    results: list[AnomalyResult]
    total_tickers: int
    tickers_with_anomalies: int
    high_severity_count: int
    range_period: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tickers": self.total_tickers,
            "tickers_with_anomalies": self.tickers_with_anomalies,
            "high_severity_count": self.high_severity_count,
            "range_period": self.range_period,
            "results": [r.to_dict() for r in self.results],
        }


def build_multi_anomaly_result(
    ticker_bars: dict[str, list[dict[str, Any]]],
    range_period: str,
    config: DetectorConfig | None = None,
) -> MultiAnomalyResult:
    """
    Analisa anomalias para todos os tickers em ticker_bars.

    Ordena resultados: primeiro os com anomalias HIGH, depois MEDIUM, etc.
    Tickers sem anomalias aparecem por ultimo.
    """
    if config is None:
        config = DetectorConfig()

    results = [analyze_ticker(bars, ticker, config) for ticker, bars in ticker_bars.items()]

    sev_order = {
        AnomalySeverity.HIGH: 3,
        AnomalySeverity.MEDIUM: 2,
        AnomalySeverity.LOW: 1,
        None: 0,
    }
    results.sort(key=lambda r: sev_order[r.max_severity], reverse=True)

    tickers_with = sum(1 for r in results if r.has_anomalies)
    high_count = sum(1 for r in results for a in r.anomalies if a.severity == AnomalySeverity.HIGH)

    return MultiAnomalyResult(
        results=results,
        total_tickers=len(results),
        tickers_with_anomalies=tickers_with,
        high_severity_count=high_count,
        range_period=range_period,
    )

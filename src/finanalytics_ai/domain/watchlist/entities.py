"""
finanalytics_ai.domain.watchlist.entities
──────────────────────────────────────────
Entidades do domínio de Watchlist.

Watchlist:
  Coleção de tickers monitorados por um usuário.
  Cada item pode ter alertas inteligentes configurados.

SmartAlertType:
  Alertas baseados em análise técnica, sem threshold fixo:

  RSI_OVERSOLD   — RSI(14) < 30: ativo potencialmente barato
  RSI_OVERBOUGHT — RSI(14) > 70: ativo potencialmente caro
  MA_CROSS_UP    — preço cruza MM de curto período para cima (momentum)
  MA_CROSS_DOWN  — preço cruza MM de longo período para baixo
  VOLUME_SPIKE   — volume > 2.5x média (evento relevante)
  NEW_HIGH_52W   — novo máximo de 52 semanas (breakout)
  NEW_LOW_52W    — novo mínimo de 52 semanas (alerta de risco)

Design decisions:

  Separação SmartAlert vs Alert (entities/alert.py):
    Alert = alertas de preço simples com threshold fixo (já existentes).
    SmartAlert = alertas técnicos dinâmicos que precisam de histórico
    para calcular indicadores. São entidades diferentes com ciclos de
    vida diferentes — SmartAlert não dispara uma vez e some.

  Avaliação stateless:
    evaluate_smart_alert() recebe os dados necessários (bars, current_price)
    e retorna resultado sem efeitos colaterais. O service decide se
    notifica o usuário baseado em cooldown (evitar spam).

  Cooldown de 4h:
    Alertas inteligentes não têm status TRIGGERED permanente.
    Após disparar, entram em cooldown configurável. Após o cooldown,
    voltam a avaliar. Isso é diferente dos Price Alerts que disparam
    uma vez e são desativados.

  WatchlistItem:
    Agregado simples: ticker + metadados + lista de smart alerts.
    Não herda de Portfolio — é independente. Um usuário pode monitorar
    ativos que não possui na carteira.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
import uuid

# ── Enums ─────────────────────────────────────────────────────────────────────


class SmartAlertType(StrEnum):
    RSI_OVERSOLD = "rsi_oversold"  # RSI(14) < threshold (default 30)
    RSI_OVERBOUGHT = "rsi_overbought"  # RSI(14) > threshold (default 70)
    MA_CROSS_UP = "ma_cross_up"  # preço cruza MM(20) para cima
    MA_CROSS_DOWN = "ma_cross_down"  # preço cruza MM(20) para baixo
    VOLUME_SPIKE = "volume_spike"  # volume > N * média
    NEW_HIGH_52W = "new_high_52w"  # novo máximo 52 semanas
    NEW_LOW_52W = "new_low_52w"  # novo mínimo 52 semanas
    PRICE_ABOVE = "price_above"  # preço acima de valor fixo
    PRICE_BELOW = "price_below"  # preço abaixo de valor fixo


class SmartAlertStatus(StrEnum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"  # disparou recentemente, aguardando
    PAUSED = "paused"  # pausado pelo usuário
    DELETED = "deleted"


# ── Smart Alert ───────────────────────────────────────────────────────────────


@dataclass
class SmartAlertConfig:
    """Parâmetros de configuração do alerta inteligente."""

    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ma_period: int = 20
    volume_multiplier: float = 2.5
    price_threshold: float = 0.0  # para PRICE_ABOVE / PRICE_BELOW
    cooldown_hours: int = 4


@dataclass
class SmartAlertResult:
    """Resultado da avaliação de um alerta inteligente."""

    triggered: bool
    alert_id: str
    alert_type: SmartAlertType
    ticker: str
    message: str
    severity: str  # "info" | "warning" | "critical"
    indicator_value: float  # valor do indicador no momento
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmartAlert:
    """
    Alerta inteligente baseado em indicadores técnicos.

    Diferente do Alert de preço fixo, reavalia continuamente
    após o período de cooldown.
    """

    ticker: str
    alert_type: SmartAlertType
    user_id: str
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: SmartAlertConfig = field(default_factory=SmartAlertConfig)
    status: SmartAlertStatus = SmartAlertStatus.ACTIVE
    last_triggered_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    note: str = ""

    def is_evaluatable(self) -> bool:
        """Retorna True se o alerta pode ser avaliado agora."""
        if self.status in (SmartAlertStatus.PAUSED, SmartAlertStatus.DELETED):
            return False
        if self.status == SmartAlertStatus.COOLDOWN and self.last_triggered_at:
            cooldown_end = self.last_triggered_at + timedelta(hours=self.config.cooldown_hours)
            if datetime.now(UTC) < cooldown_end:
                return False
            # Cooldown expirou → volta para ACTIVE
            self.status = SmartAlertStatus.ACTIVE
        return True

    def mark_triggered(self) -> None:
        self.last_triggered_at = datetime.now(UTC)
        self.status = SmartAlertStatus.COOLDOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "ticker": self.ticker,
            "alert_type": self.alert_type.value,
            "status": self.status.value,
            "note": self.note,
            "config": {
                "rsi_period": self.config.rsi_period,
                "rsi_oversold": self.config.rsi_oversold,
                "rsi_overbought": self.config.rsi_overbought,
                "ma_period": self.config.ma_period,
                "volume_multiplier": self.config.volume_multiplier,
                "price_threshold": self.config.price_threshold,
                "cooldown_hours": self.config.cooldown_hours,
            },
            "last_triggered_at": self.last_triggered_at.isoformat()
            if self.last_triggered_at
            else None,
            "created_at": self.created_at.isoformat(),
        }


# ── WatchlistItem ─────────────────────────────────────────────────────────────


@dataclass
class WatchlistItem:
    """
    Item da watchlist: um ticker monitorado com metadados e alertas.
    """

    ticker: str
    user_id: str
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    note: str = ""
    tags: list[str] = field(default_factory=list)
    smart_alerts: list[SmartAlert] = field(default_factory=list)
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Cache de dados de mercado (preenchido pelo service)
    current_price: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    last_updated_at: datetime | None = None

    def add_smart_alert(self, alert: SmartAlert) -> None:
        # Evita duplicatas do mesmo tipo
        for existing in self.smart_alerts:
            if (
                existing.alert_type == alert.alert_type
                and existing.status != SmartAlertStatus.DELETED
            ):
                raise ValueError(f"Alerta {alert.alert_type} já existe para {self.ticker}")
        self.smart_alerts.append(alert)

    def active_alerts(self) -> list[SmartAlert]:
        return [a for a in self.smart_alerts if a.status != SmartAlertStatus.DELETED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "ticker": self.ticker,
            "user_id": self.user_id,
            "note": self.note,
            "tags": self.tags,
            "current_price": self.current_price,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "high_52w": self.high_52w,
            "low_52w": self.low_52w,
            "last_updated_at": self.last_updated_at.isoformat() if self.last_updated_at else None,
            "added_at": self.added_at.isoformat(),
            "smart_alerts": [a.to_dict() for a in self.active_alerts()],
        }


# ── Avaliação de indicadores técnicos ─────────────────────────────────────────


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI clássico de Wilder. Retorna None se dados insuficientes."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        closes[-period + i - 1] - closes[-period + i - 2] if i > 0 else 0
        closes[-(period - i + 1)] - closes[-(period - i + 2)] if len(closes) > period - i + 2 else 0

    # Calcula corretamente
    diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = [max(d, 0) for d in diffs]
    losses = [abs(min(d, 0)) for d in diffs]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def evaluate_smart_alert(
    alert: SmartAlert,
    bars: list[dict[str, Any]],
    current_price: float,
) -> SmartAlertResult:
    """
    Avalia um SmartAlert contra dados OHLC históricos.

    Parâmetros:
      alert:         SmartAlert a avaliar
      bars:          list de dicts OHLC {time, open, high, low, close, volume}
      current_price: preço atual (pode ser mais recente que o último bar)

    Retorna SmartAlertResult com triggered=True/False e metadados.
    """
    closes = [b["close"] for b in bars if b.get("close")]
    volumes = [b["volume"] for b in bars if b.get("volume") is not None]

    def _result(
        triggered: bool, msg: str, severity: str, indicator: float, ctx: dict | None = None
    ) -> SmartAlertResult:
        if ctx is None:
            ctx = {}
        return SmartAlertResult(
            triggered=triggered,
            alert_id=alert.alert_id,
            alert_type=alert.alert_type,
            ticker=alert.ticker,
            message=msg,
            severity=severity,
            indicator_value=indicator,
            context=ctx,
        )

    cfg = alert.config

    match alert.alert_type:
        case SmartAlertType.RSI_OVERSOLD:
            rsi = _calc_rsi(closes, cfg.rsi_period)
            if rsi is None:
                return _result(False, "Dados insuficientes para RSI", "info", 0)
            triggered = rsi < cfg.rsi_oversold
            return _result(
                triggered,
                f"RSI({cfg.rsi_period}) = {rsi:.1f} — abaixo de {cfg.rsi_oversold} (oversold)",
                "warning" if triggered else "info",
                round(rsi, 2),
                {"rsi": round(rsi, 2), "threshold": cfg.rsi_oversold},
            )

        case SmartAlertType.RSI_OVERBOUGHT:
            rsi = _calc_rsi(closes, cfg.rsi_period)
            if rsi is None:
                return _result(False, "Dados insuficientes para RSI", "info", 0)
            triggered = rsi > cfg.rsi_overbought
            return _result(
                triggered,
                f"RSI({cfg.rsi_period}) = {rsi:.1f} — acima de {cfg.rsi_overbought} (overbought)",
                "warning" if triggered else "info",
                round(rsi, 2),
                {"rsi": round(rsi, 2), "threshold": cfg.rsi_overbought},
            )

        case SmartAlertType.MA_CROSS_UP:
            ma = _calc_sma(closes, cfg.ma_period)
            prev_close = closes[-2] if len(closes) >= 2 else current_price
            if ma is None:
                return _result(False, "Dados insuficientes para MM", "info", 0)
            triggered = prev_close < ma and current_price > ma
            return _result(
                triggered,
                f"Preco cruzou MM({cfg.ma_period}) para cima: {current_price:.2f} > {ma:.2f}",
                "info",
                round(ma, 2),
                {"ma": round(ma, 2), "price": current_price},
            )

        case SmartAlertType.MA_CROSS_DOWN:
            ma = _calc_sma(closes, cfg.ma_period)
            prev_close = closes[-2] if len(closes) >= 2 else current_price
            if ma is None:
                return _result(False, "Dados insuficientes para MM", "info", 0)
            triggered = prev_close > ma and current_price < ma
            return _result(
                triggered,
                f"Preco cruzou MM({cfg.ma_period}) para baixo: {current_price:.2f} < {ma:.2f}",
                "warning" if triggered else "info",
                round(ma, 2),
                {"ma": round(ma, 2), "price": current_price},
            )

        case SmartAlertType.VOLUME_SPIKE:
            if len(volumes) < 10:
                return _result(False, "Dados insuficientes para volume", "info", 0)
            avg_vol = sum(volumes[-20:]) / min(len(volumes), 20)
            last_vol = volumes[-1] if volumes else 0
            ratio = last_vol / avg_vol if avg_vol > 0 else 0
            triggered = ratio >= cfg.volume_multiplier
            return _result(
                triggered,
                f"Volume {ratio:.1f}x acima da media ({last_vol:,} vs media {int(avg_vol):,})",
                "warning" if triggered else "info",
                round(ratio, 2),
                {"ratio": round(ratio, 2), "volume": last_vol, "avg_volume": int(avg_vol)},
            )

        case SmartAlertType.NEW_HIGH_52W:
            if len(closes) < 2:
                return _result(False, "Dados insuficientes", "info", 0)
            highs = [b.get("high", b.get("close", 0)) for b in bars[-252:]]
            prev_high = max(highs[:-1]) if len(highs) > 1 else 0
            triggered = current_price > prev_high and prev_high > 0
            return _result(
                triggered,
                f"Novo maximo 52 semanas: R$ {current_price:.2f} (anterior: R$ {prev_high:.2f})",
                "info",
                round(prev_high, 2),
                {"new_high": current_price, "prev_high": round(prev_high, 2)},
            )

        case SmartAlertType.NEW_LOW_52W:
            if len(closes) < 2:
                return _result(False, "Dados insuficientes", "info", 0)
            lows = [b.get("low", b.get("close", 0)) for b in bars[-252:]]
            prev_low = min(val for val in lows[:-1] if val > 0) if len(lows) > 1 else float("inf")
            triggered = current_price < prev_low and prev_low < float("inf")
            return _result(
                triggered,
                f"Novo minimo 52 semanas: R$ {current_price:.2f} (anterior: R$ {prev_low:.2f})",
                "critical" if triggered else "info",
                round(prev_low, 2),
                {"new_low": current_price, "prev_low": round(prev_low, 2)},
            )

        case SmartAlertType.PRICE_ABOVE:
            threshold = cfg.price_threshold
            triggered = current_price > threshold > 0
            return _result(
                triggered,
                f"Preco R$ {current_price:.2f} acima de R$ {threshold:.2f}",
                "info",
                current_price,
                {"price": current_price, "threshold": threshold},
            )

        case SmartAlertType.PRICE_BELOW:
            threshold = cfg.price_threshold
            triggered = 0 < current_price < threshold
            return _result(
                triggered,
                f"Preco R$ {current_price:.2f} abaixo de R$ {threshold:.2f}",
                "warning" if triggered else "info",
                current_price,
                {"price": current_price, "threshold": threshold},
            )

        case _:
            return _result(False, f"Tipo desconhecido: {alert.alert_type}", "info", 0)

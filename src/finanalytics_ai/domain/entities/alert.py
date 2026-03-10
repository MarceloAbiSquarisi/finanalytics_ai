"""
Entidade Alert — alerta de preço configurado pelo usuário.

Tipos suportados:
  STOP_LOSS     — dispara quando preço cai abaixo de um nível absoluto
  TAKE_PROFIT   — dispara quando preço sobe acima de um nível absoluto
  PRICE_TARGET  — dispara quando preço cruza um nível (qualquer direção)
  PCT_DROP      — dispara quando preço cai X% do preço de referência
  PCT_RISE      — dispara quando preço sobe X% do preço de referência

Design decisions:
  - Imutável após criação (frozen=False apenas para triggered_at/status)
  - Avaliação síncrona (evaluate) — não precisa de async aqui
  - Status TRIGGERED é final — alerta disparado não reavalia
  - user_id vincula ao portfólio mas alertas podem existir sem posição
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class AlertType(StrEnum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    PRICE_TARGET = "price_target"
    PCT_DROP = "pct_drop"
    PCT_RISE = "pct_rise"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class AlertTriggerResult:
    """Resultado da avaliação de um alerta contra o preço atual."""

    triggered: bool
    alert_id: str
    ticker: str
    alert_type: AlertType
    message: str
    current_price: Decimal
    threshold: Decimal
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Alert:
    """
    Alerta de preço. Avalia-se via evaluate(current_price).

    threshold: preço absoluto (STOP_LOSS, TAKE_PROFIT, PRICE_TARGET)
               ou percentual (PCT_DROP, PCT_RISE)
    reference_price: preço de entrada ou preço no momento da criação
                     (usado em PCT_DROP/PCT_RISE)
    """

    ticker: str
    alert_type: AlertType
    threshold: Decimal  # preço absoluto ou % conforme tipo
    user_id: str
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reference_price: Decimal = Decimal("0")
    status: AlertStatus = AlertStatus.ACTIVE
    note: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    triggered_at: datetime | None = None
    expires_at: datetime | None = None

    def evaluate(self, current_price: Decimal) -> AlertTriggerResult:
        """
        Avalia se o alerta deve disparar com o preço atual.
        Retorna AlertTriggerResult — nunca lança exceção.
        """
        if self.status != AlertStatus.ACTIVE:
            return AlertTriggerResult(
                triggered=False,
                alert_id=self.alert_id,
                ticker=self.ticker,
                alert_type=self.alert_type,
                message="Alerta inativo",
                current_price=current_price,
                threshold=self.threshold,
            )

        if self.expires_at and datetime.utcnow() > self.expires_at:
            return AlertTriggerResult(
                triggered=False,
                alert_id=self.alert_id,
                ticker=self.ticker,
                alert_type=self.alert_type,
                message="Alerta expirado",
                current_price=current_price,
                threshold=self.threshold,
            )

        triggered, message, ctx = self._check(current_price)
        return AlertTriggerResult(
            triggered=triggered,
            alert_id=self.alert_id,
            ticker=self.ticker,
            alert_type=self.alert_type,
            message=message,
            current_price=current_price,
            threshold=self.threshold,
            context=ctx,
        )

    def _check(self, price: Decimal) -> tuple[bool, str, dict[str, Any]]:
        t = self.threshold
        ref = self.reference_price if self.reference_price > 0 else price

        match self.alert_type:
            case AlertType.STOP_LOSS:
                if price <= t:
                    loss = ((ref - price) / ref * 100) if ref > 0 else Decimal("0")
                    return (
                        True,
                        (f"🔴 STOP LOSS {self.ticker}: R$ {price:.2f} ≤ R$ {t:.2f} (queda de {loss:.1f}%)"),
                        {"loss_pct": str(loss)},
                    )

            case AlertType.TAKE_PROFIT:
                if price >= t:
                    gain = ((price - ref) / ref * 100) if ref > 0 else Decimal("0")
                    return (
                        True,
                        (f"🟢 TAKE PROFIT {self.ticker}: R$ {price:.2f} ≥ R$ {t:.2f} (ganho de {gain:.1f}%)"),
                        {"gain_pct": str(gain)},
                    )

            case AlertType.PRICE_TARGET:
                # Dispara independente da direção
                diff = abs(price - t)
                pct = diff / t * 100 if t > 0 else Decimal("0")
                if pct <= Decimal("0.1"):  # dentro de 0.1% do alvo
                    return True, (f"🎯 ALVO {self.ticker}: R$ {price:.2f} ≈ R$ {t:.2f}"), {}

            case AlertType.PCT_DROP:
                if ref > 0:
                    drop = (ref - price) / ref * 100
                    if drop >= t:
                        return (
                            True,
                            (
                                f"🔴 QUEDA {self.ticker}: -{drop:.1f}% "
                                f"(ref R$ {ref:.2f} → atual R$ {price:.2f})"
                            ),
                            {"drop_pct": str(drop)},
                        )

            case AlertType.PCT_RISE:
                if ref > 0:
                    rise = (price - ref) / ref * 100
                    if rise >= t:
                        return (
                            True,
                            (
                                f"🟢 ALTA {self.ticker}: +{rise:.1f}% "
                                f"(ref R$ {ref:.2f} → atual R$ {price:.2f})"
                            ),
                            {"rise_pct": str(rise)},
                        )

        return False, "", {}

    def mark_triggered(self) -> Alert:
        """Retorna nova instância com status TRIGGERED."""
        import dataclasses

        return dataclasses.replace(
            self,
            status=AlertStatus.TRIGGERED,
            triggered_at=datetime.utcnow(),
        )

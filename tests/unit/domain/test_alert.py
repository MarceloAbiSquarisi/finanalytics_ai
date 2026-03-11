"""
Testes unitários para a entidade Alert.

Cobertura:
  - Todos os 5 tipos de alerta: STOP_LOSS, TAKE_PROFIT, PRICE_TARGET, PCT_DROP, PCT_RISE
  - Condições de borda: exatamente no threshold, 1 centavo acima/abaixo
  - Alerta TRIGGERED não reavalia (final state)
  - Alerta CANCELLED não avalia
  - Alerta EXPIRED (com expires_at no passado)
  - mark_triggered: imutabilidade (cria nova instância)
  - AlertTriggerResult: campos corretos no contexto
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from finanalytics_ai.domain.entities.alert import (
    Alert,
    AlertStatus,
    AlertType,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _alert(
    alert_type: AlertType,
    threshold: str,
    reference_price: str = "0",
    status: AlertStatus = AlertStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> Alert:
    return Alert(
        ticker="PETR4",
        alert_type=alert_type,
        threshold=Decimal(threshold),
        user_id="user-001",
        reference_price=Decimal(reference_price),
        status=status,
        expires_at=expires_at,
    )


def p(value: str) -> Decimal:
    """Shorthand para Decimal de preço."""
    return Decimal(value)


# ── STOP LOSS ─────────────────────────────────────────────────────────────────


class TestStopLoss:
    def test_triggers_when_price_below_threshold(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        result = alert.evaluate(p("29.99"))
        assert result.triggered is True

    def test_triggers_when_price_equals_threshold(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        result = alert.evaluate(p("30.00"))
        assert result.triggered is True

    def test_does_not_trigger_above_threshold(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        result = alert.evaluate(p("30.01"))
        assert result.triggered is False

    def test_result_contains_ticker(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        result = alert.evaluate(p("25.00"))
        assert result.ticker == "PETR4"

    def test_result_contains_current_price(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        result = alert.evaluate(p("25.00"))
        assert result.current_price == p("25.00")

    def test_context_contains_loss_pct(self):
        # ref=40, price=30 → queda de 25%
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00", reference_price="40.00")
        result = alert.evaluate(p("30.00"))
        assert result.triggered is True
        assert "loss_pct" in result.context


# ── TAKE PROFIT ───────────────────────────────────────────────────────────────


class TestTakeProfit:
    def test_triggers_when_price_above_threshold(self):
        alert = _alert(AlertType.TAKE_PROFIT, threshold="50.00")
        result = alert.evaluate(p("50.01"))
        assert result.triggered is True

    def test_triggers_when_price_equals_threshold(self):
        alert = _alert(AlertType.TAKE_PROFIT, threshold="50.00")
        result = alert.evaluate(p("50.00"))
        assert result.triggered is True

    def test_does_not_trigger_below_threshold(self):
        alert = _alert(AlertType.TAKE_PROFIT, threshold="50.00")
        result = alert.evaluate(p("49.99"))
        assert result.triggered is False

    def test_context_contains_gain_pct(self):
        # ref=40, price=50 → ganho de 25%
        alert = _alert(AlertType.TAKE_PROFIT, threshold="50.00", reference_price="40.00")
        result = alert.evaluate(p("50.00"))
        assert "gain_pct" in result.context


# ── PRICE TARGET ──────────────────────────────────────────────────────────────


class TestPriceTarget:
    def test_triggers_when_within_tolerance(self):
        # 0.1% de R$100 = R$0.10
        alert = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        result = alert.evaluate(p("100.05"))
        assert result.triggered is True

    def test_triggers_exactly_on_target(self):
        alert = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        result = alert.evaluate(p("100.00"))
        assert result.triggered is True

    def test_triggers_below_target_within_tolerance(self):
        alert = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        result = alert.evaluate(p("99.95"))
        assert result.triggered is True

    def test_does_not_trigger_outside_tolerance(self):
        alert = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        result = alert.evaluate(p("99.00"))  # 1% abaixo — fora da tolerância de 0.1%
        assert result.triggered is False

    def test_triggers_regardless_of_direction(self):
        """PRICE_TARGET dispara tanto em alta quanto em queda."""
        alert_high = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        alert_low = _alert(AlertType.PRICE_TARGET, threshold="100.00")
        assert alert_high.evaluate(p("100.05")).triggered is True
        assert alert_low.evaluate(p("99.95")).triggered is True


# ── PCT DROP ──────────────────────────────────────────────────────────────────


class TestPctDrop:
    def test_triggers_when_drop_exceeds_threshold(self):
        # ref=100, price=90 → queda de 10% ≥ threshold=10%
        alert = _alert(AlertType.PCT_DROP, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("90.00"))
        assert result.triggered is True

    def test_triggers_when_drop_exactly_equals_threshold(self):
        alert = _alert(AlertType.PCT_DROP, threshold="5.0", reference_price="100.00")
        result = alert.evaluate(p("95.00"))
        assert result.triggered is True

    def test_does_not_trigger_below_threshold(self):
        # ref=100, price=96 → queda de 4% < threshold=5%
        alert = _alert(AlertType.PCT_DROP, threshold="5.0", reference_price="100.00")
        result = alert.evaluate(p("96.00"))
        assert result.triggered is False

    def test_does_not_trigger_on_price_rise(self):
        alert = _alert(AlertType.PCT_DROP, threshold="5.0", reference_price="100.00")
        result = alert.evaluate(p("110.00"))
        assert result.triggered is False

    def test_context_contains_drop_pct(self):
        alert = _alert(AlertType.PCT_DROP, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("85.00"))
        assert "drop_pct" in result.context

    def test_uses_current_price_as_ref_when_reference_is_zero(self):
        """Sem reference_price explícito, não consegue calcular drop — não dispara."""
        alert = _alert(AlertType.PCT_DROP, threshold="5.0", reference_price="0")
        # com ref=0, usa price como ref, então drop=0 < threshold
        result = alert.evaluate(p("90.00"))
        assert result.triggered is False


# ── PCT RISE ──────────────────────────────────────────────────────────────────


class TestPctRise:
    def test_triggers_when_rise_exceeds_threshold(self):
        alert = _alert(AlertType.PCT_RISE, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("111.00"))
        assert result.triggered is True

    def test_triggers_when_rise_exactly_equals_threshold(self):
        alert = _alert(AlertType.PCT_RISE, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("110.00"))
        assert result.triggered is True

    def test_does_not_trigger_below_threshold(self):
        alert = _alert(AlertType.PCT_RISE, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("109.00"))
        assert result.triggered is False

    def test_does_not_trigger_on_price_drop(self):
        alert = _alert(AlertType.PCT_RISE, threshold="5.0", reference_price="100.00")
        result = alert.evaluate(p("90.00"))
        assert result.triggered is False

    def test_context_contains_rise_pct(self):
        alert = _alert(AlertType.PCT_RISE, threshold="10.0", reference_price="100.00")
        result = alert.evaluate(p("120.00"))
        assert "rise_pct" in result.context


# ── Status Guard ──────────────────────────────────────────────────────────────


class TestAlertStatusGuard:
    def test_triggered_alert_does_not_reevaluate(self):
        """Alerta já disparado nunca dispara de novo."""
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00", status=AlertStatus.TRIGGERED)
        result = alert.evaluate(p("20.00"))  # preço bem abaixo — mas já foi triggado
        assert result.triggered is False
        assert result.message == "Alerta inativo"

    def test_cancelled_alert_does_not_trigger(self):
        alert = _alert(AlertType.TAKE_PROFIT, threshold="50.00", status=AlertStatus.CANCELLED)
        result = alert.evaluate(p("100.00"))
        assert result.triggered is False

    def test_expired_alert_does_not_trigger(self):
        past = datetime.now(UTC) - timedelta(hours=1)
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00", expires_at=past)
        result = alert.evaluate(p("20.00"))
        assert result.triggered is False
        assert "expirado" in result.message

    def test_future_expiry_still_active(self):
        future = datetime.now(UTC) + timedelta(hours=24)
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00", expires_at=future)
        result = alert.evaluate(p("20.00"))
        assert result.triggered is True


# ── mark_triggered ────────────────────────────────────────────────────────────


class TestMarkTriggered:
    def test_returns_new_instance(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        triggered = alert.mark_triggered()
        assert triggered is not alert

    def test_original_unchanged(self):
        """Imutabilidade — original deve permanecer ACTIVE."""
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        alert.mark_triggered()
        assert alert.status == AlertStatus.ACTIVE

    def test_new_instance_is_triggered(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        triggered = alert.mark_triggered()
        assert triggered.status == AlertStatus.TRIGGERED

    def test_triggered_at_is_set(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        triggered = alert.mark_triggered()
        assert triggered.triggered_at is not None

    def test_preserves_alert_id(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        triggered = alert.mark_triggered()
        assert triggered.alert_id == alert.alert_id

    def test_triggered_alert_evaluate_returns_not_triggered(self):
        alert = _alert(AlertType.STOP_LOSS, threshold="30.00")
        triggered = alert.mark_triggered()
        result = triggered.evaluate(p("20.00"))
        assert result.triggered is False

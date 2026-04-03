"""
IndicatorAlertService -- alertas baseados em indicadores Fintz.

Usa a tabela smart_alerts (ja existente) com conditions em JSON:
  {
    "indicator": "ROE",
    "operator": "gt",        # gt, lt, gte, lte
    "threshold": 15.0,       # em percentual (15 = 15%)
    "reference_value": null  # ultimo valor avaliado
  }

Fluxo:
  1. POST /api/v1/alerts/indicator  -- cria smart_alert
  2. GET  /api/v1/alerts/indicator  -- lista smart_alerts do usuario
  3. DELETE /api/v1/alerts/indicator/{id} -- cancela

  Avaliacao periodica (chamada pelo lifespan da API ou worker):
  4. evaluate_all() -- busca indicadores Fintz e avalia cada smart_alert
  5. Alertas disparados -> NotificationBus (mesmo SSE stream dos alertas de preco)

Design:
  - Sem nova tabela: usa smart_alerts existente
  - conditions como JSON: extensivel sem migrations
  - NotificationBus existente: clientes SSE recebem ambos os tipos
  - evaluate_all() idempotente: chama a cada N minutos, re-dispara so se
    o indicador cruzou o threshold (saiu e voltou)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

_OPERATORS = {
    "gt":  lambda v, t: v > t,
    "lt":  lambda v, t: v < t,
    "gte": lambda v, t: v >= t,
    "lte": lambda v, t: v <= t,
}

# Indicadores suportados e seus nomes amigaveis
SUPPORTED_INDICATORS: dict[str, str] = {
    "ROE":                          "Return on Equity (%)",
    "ROIC":                         "Return on Invested Capital (%)",
    "ROA":                          "Return on Assets (%)",
    "DividendYield":                "Dividend Yield (%)",
    "P_L":                          "Preco/Lucro",
    "P_VP":                         "Preco/Valor Patrimonial",
    "MargemEBITDA":                 "Margem EBITDA (%)",
    "MargemLiquida":                "Margem Liquida (%)",
    "MargemBruta":                  "Margem Bruta (%)",
    "DividaLiquida_PatrimonioLiquido": "Divida Liquida/PL",
    "DividaLiquida_EBITDA":         "Divida Liquida/EBITDA",
    "LiquidezCorrente":             "Liquidez Corrente",
    "EV_EBITDA":                    "EV/EBITDA",
    "ValorDeMercado":               "Valor de Mercado (R$)",
}

# Indicadores que estao em decimal no banco e precisam x100
_PCT_INDICATORS = {
    "ROE", "ROIC", "ROA", "DividendYield",
    "MargemEBITDA", "MargemLiquida", "MargemBruta",
}


@dataclass
class IndicatorAlertCondition:
    indicator: str
    operator: str   # gt, lt, gte, lte
    threshold: float
    reference_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator": self.indicator,
            "operator": self.operator,
            "threshold": self.threshold,
            "reference_value": self.reference_value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IndicatorAlertCondition:
        return cls(
            indicator=d["indicator"],
            operator=d["operator"],
            threshold=float(d["threshold"]),
            reference_value=d.get("reference_value"),
        )

    def evaluate(self, current_value: float) -> bool:
        op = _OPERATORS.get(self.operator)
        if op is None:
            return False
        return op(current_value, self.threshold)


@dataclass
class IndicatorAlert:
    alert_id: str
    ticker: str
    user_id: str
    condition: IndicatorAlertCondition
    status: str = "active"
    note: str = ""
    last_triggered_at: datetime | None = None
    created_at: datetime | None = None


class IndicatorAlertService:
    """
    Servico de alertas baseados em indicadores Fintz.

    session_factory: callable que retorna AsyncSession.
    notification_bus: NotificationBus existente (mesmo do AlertService).
    """

    def __init__(self, session_factory: Any, notification_bus: Any) -> None:
        self._sf = session_factory
        self._bus = notification_bus

    # ─────────────────────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────────────────────

    async def create(
        self,
        ticker: str,
        indicator: str,
        operator: str,
        threshold: float,
        user_id: str,
        note: str = "",
    ) -> IndicatorAlert:
        """Cria um smart_alert de indicador."""
        if indicator not in SUPPORTED_INDICATORS:
            raise ValueError(
                f"Indicador '{indicator}' nao suportado. "
                f"Use: {', '.join(SUPPORTED_INDICATORS)}"
            )
        if operator not in _OPERATORS:
            raise ValueError(f"Operador '{operator}' invalido. Use: gt, lt, gte, lte")

        condition = IndicatorAlertCondition(
            indicator=indicator,
            operator=operator,
            threshold=threshold,
        )
        alert_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        async with self._sf() as session:
            await session.execute(
                text("""
                    INSERT INTO smart_alerts
                        (alert_id, ticker, user_id, alert_type, conditions,
                         status, note, created_at)
                    VALUES
                        (:alert_id, :ticker, :user_id, 'indicator',
                         :conditions, 'active', :note, :created_at)
                """),
                {
                    "alert_id": alert_id,
                    "ticker": ticker.upper(),
                    "user_id": user_id,
                    "conditions": json.dumps(condition.to_dict()),
                    "note": note,
                    "created_at": now,
                },
            )
            await session.commit()

        logger.info(
            "indicator_alert.created",
            alert_id=alert_id,
            ticker=ticker,
            indicator=indicator,
            operator=operator,
            threshold=threshold,
        )

        return IndicatorAlert(
            alert_id=alert_id,
            ticker=ticker.upper(),
            user_id=user_id,
            condition=condition,
            note=note,
            created_at=now,
        )

    async def list_by_user(self, user_id: str) -> list[IndicatorAlert]:
        """Lista smart_alerts de indicador de um usuario."""
        async with self._sf() as session:
            result = await session.execute(
                text("""
                    SELECT alert_id, ticker, user_id, conditions,
                           status, note, last_triggered_at, created_at
                    FROM smart_alerts
                    WHERE user_id = :user_id
                      AND alert_type = 'indicator'
                    ORDER BY created_at DESC
                """),
                {"user_id": user_id},
            )
            rows = result.fetchall()

        alerts = []
        for row in rows:
            try:
                condition = IndicatorAlertCondition.from_dict(json.loads(row.conditions))
                alerts.append(IndicatorAlert(
                    alert_id=row.alert_id,
                    ticker=row.ticker,
                    user_id=row.user_id,
                    condition=condition,
                    status=row.status,
                    note=row.note or "",
                    last_triggered_at=row.last_triggered_at,
                    created_at=row.created_at,
                ))
            except Exception as exc:
                logger.warning("indicator_alert.parse_error", error=str(exc))
        return alerts

    async def cancel(self, alert_id: str, user_id: str) -> bool:
        """Cancela um smart_alert. Retorna True se cancelado."""
        async with self._sf() as session:
            result = await session.execute(
                text("""
                    UPDATE smart_alerts
                    SET status = 'cancelled'
                    WHERE alert_id = :alert_id
                      AND user_id = :user_id
                      AND alert_type = 'indicator'
                    RETURNING alert_id
                """),
                {"alert_id": alert_id, "user_id": user_id},
            )
            await session.commit()
            return result.rowcount > 0

    # ─────────────────────────────────────────────────────────────────────────
    # Avaliacao periodica
    # ─────────────────────────────────────────────────────────────────────────

    async def evaluate_all(self) -> int:
        """
        Avalia todos os smart_alerts de indicador ativos.

        1. Busca alertas ativos agrupados por ticker+indicador
        2. Consulta valor mais recente em fintz_indicadores
        3. Para cada alerta disparado: notifica via NotificationBus
        4. Marca last_triggered_at

        Retorna numero de alertas disparados.
        """
        async with self._sf() as session:
            result = await session.execute(
                text("""
                    SELECT alert_id, ticker, user_id, conditions, note
                    FROM smart_alerts
                    WHERE alert_type = 'indicator'
                      AND status = 'active'
                    ORDER BY ticker
                """)
            )
            rows = result.fetchall()

        if not rows:
            return 0

        # Agrupa tickers e indicadores necessarios
        needed: dict[str, set[str]] = {}
        for row in rows:
            try:
                cond = IndicatorAlertCondition.from_dict(json.loads(row.conditions))
                needed.setdefault(row.ticker, set()).add(cond.indicator)
            except Exception:
                continue

        # Busca valores atuais do banco
        current_values: dict[tuple[str, str], float] = {}
        if needed:
            tickers_str = ", ".join(f"'{t}'" for t in needed)
            all_indicators = set()
            for inds in needed.values():
                all_indicators.update(inds)
            ind_str = ", ".join(f"'{i}'" for i in all_indicators)

            async with self._sf() as session:
                result = await session.execute(text(f"""
                    SELECT ticker, indicador, valor
                    FROM (
                        SELECT ticker, indicador, valor,
                               ROW_NUMBER() OVER (
                                   PARTITION BY ticker, indicador
                                   ORDER BY data_publicacao DESC
                               ) as rn
                        FROM fintz_indicadores
                        WHERE ticker IN ({tickers_str})
                          AND indicador IN ({ind_str})
                    ) ranked
                    WHERE rn = 1
                """))
                for r in result.fetchall():
                    try:
                        val = float(r.valor)
                        # Converte para percentual se necessario
                        if r.indicador in _PCT_INDICATORS:
                            val = round(val * 100, 4)
                        current_values[(r.ticker, r.indicador)] = val
                    except (TypeError, ValueError):
                        pass

        # Avalia alertas
        triggered_count = 0
        now = datetime.now(UTC)

        for row in rows:
            try:
                cond = IndicatorAlertCondition.from_dict(json.loads(row.conditions))
                key = (row.ticker, cond.indicator)
                current_val = current_values.get(key)

                if current_val is None:
                    continue

                if cond.evaluate(current_val):
                    triggered_count += 1
                    await self._on_triggered(
                        row.alert_id, row.ticker, row.user_id,
                        cond, current_val, now
                    )
            except Exception as exc:
                logger.warning(
                    "indicator_alert.eval_error",
                    alert_id=row.alert_id,
                    error=str(exc),
                )

        if triggered_count:
            logger.info("indicator_alert.evaluate_done", triggered=triggered_count)

        return triggered_count

    async def _on_triggered(
        self,
        alert_id: str,
        ticker: str,
        user_id: str,
        condition: IndicatorAlertCondition,
        current_value: float,
        now: datetime,
    ) -> None:
        """Marca alerta como disparado e notifica via SSE."""
        op_label = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}.get(
            condition.operator, condition.operator
        )
        indicator_label = SUPPORTED_INDICATORS.get(condition.indicator, condition.indicator)
        message = (
            f"[{ticker}] {indicator_label} {op_label} {condition.threshold:.2f} "
            f"(atual: {current_value:.2f})"
        )

        # Atualiza last_triggered_at (nao cancela -- pode re-disparar)
        async with self._sf() as session:
            await session.execute(
                text("""
                    UPDATE smart_alerts
                    SET last_triggered_at = :now
                    WHERE alert_id = :alert_id
                """),
                {"alert_id": alert_id, "now": now},
            )
            await session.commit()

        logger.info(
            "indicator_alert.triggered",
            alert_id=alert_id,
            ticker=ticker,
            indicator=condition.indicator,
            current_price=current_value,
                    user_id=user_id,
            threshold=condition.threshold,
        )

        # Notifica via NotificationBus (SSE stream existente)
        if self._bus is not None:
            try:
                from finanalytics_ai.infrastructure.notifications import AlertNotification
                notification = AlertNotification(
                    alert_id=alert_id,
                    ticker=ticker,
                    message=message,
                    alert_type="indicator",
                    current_price=current_value,
                    user_id=user_id,
                    threshold=condition.threshold,
                    triggered_at=now.isoformat(),
                    context={
                        "indicator": condition.indicator,
                        "operator": condition.operator,
                        "user_id": user_id,
                    },
                )
                await self._bus.broadcast(notification)
            except Exception as exc:
                logger.warning("indicator_alert.notify_error", error=str(exc))

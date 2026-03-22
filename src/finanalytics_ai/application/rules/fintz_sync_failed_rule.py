"""
BusinessRule: FintzSyncFailedRule.

Quando um sync Fintz falha (status != 'ok' no sync_log), esta regra:
1. Classifica o tipo de falha (timeout, parse error, API error).
2. Registra métricas de falha detalhadas no log estruturado.
3. Decide se deve escalar (alertar) ou apenas registrar.

Regras de escalonamento:
- Falha em dataset crítico (cotacoes, itens_contabeis) → escala sempre.
- Falha em dataset secundário → tolera até 3 falhas consecutivas.

Design: intencionalmente simples. Lógica de alerta real (e-mail, Slack, PagerDuty)
deve ser injetada via AlarmPort quando implementada, não hard-coded aqui.
"""

from __future__ import annotations

from typing import Any

from finanalytics_ai.domain.events.entities import Event, EventType
from finanalytics_ai.exceptions import BusinessRuleError
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)

# Datasets considerados críticos — falha deles sempre produz log de erro escalável
_CRITICAL_DATASETS = frozenset({"cotacoes", "itens_contabeis", "indicadores"})

_REQUIRED_FIELDS = frozenset({"dataset", "error_type", "error_message"})


class FintzSyncFailedRule:
    """Regra de negócio para falhas de sync Fintz.

    Implementa a lógica de triagem de falhas:
    - Falha em dataset crítico → BusinessRuleError (vai para dead-letter para revisão manual).
    - Falha em dataset não-crítico → registra e retorna (não escala).

    Nota de design: a decisão de enviar o evento para dead-letter ao invés de
    apenas logar é intencional. Dead-letter cria um registro persistente que pode
    ser auditado pelo time de operações, enquanto um log pode ser perdido.
    """

    handles: frozenset[EventType] = frozenset({EventType.FINTZ_SYNC_FAILED})

    def __init__(self, escalate_critical: bool = True) -> None:
        self._escalate_critical = escalate_critical

    async def apply(self, event: Event) -> dict[str, Any]:
        self._validate_payload(event.payload)

        dataset: str = event.payload["dataset"]
        error_type: str = event.payload["error_type"]
        error_message: str = event.payload["error_message"]
        attempt: int = event.payload.get("attempt", 1)

        is_critical = dataset in _CRITICAL_DATASETS

        log.warning(
            "fintz_sync_failed_rule_applied",
            dataset=dataset,
            error_type=error_type,
            is_critical=is_critical,
            attempt=attempt,
        )

        if is_critical and self._escalate_critical:
            raise BusinessRuleError(
                f"Sync falhou em dataset crítico '{dataset}' "
                f"({error_type}): {error_message[:200]}. "
                f"Requer atenção manual — verifique fintz_sync_log."
            )

        # Dataset não-crítico: registra e retorna sem escalar
        return {
            "dataset": dataset,
            "error_type": error_type,
            "escalated": False,
            "attempt": attempt,
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        missing = _REQUIRED_FIELDS - set(payload.keys())
        if missing:
            raise BusinessRuleError(
                f"Payload de FINTZ_SYNC_FAILED incompleto, faltando: {missing}"
            )

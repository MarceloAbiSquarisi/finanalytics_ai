"""
Exemplo de BusinessRule: FintzSyncCompletedRule.

Quando um sync Fintz completa, esta regra:
1. Valida que o payload contém os campos esperados.
2. Calcula estatísticas do sync.
3. Dispara alertas se a taxa de erro estiver alta.

Serve como template para novas regras. Copie e adapte.
"""

from __future__ import annotations

from typing import Any

from finanalytics_ai.domain.events.entities import Event, EventType
from finanalytics_ai.exceptions import BusinessRuleError
from finanalytics_ai.observability.logging import get_logger

log = get_logger(__name__)

# Payload esperado para FINTZ_SYNC_COMPLETED (tipagem explícita)
_REQUIRED_FIELDS = frozenset({"dataset", "rows_synced", "errors", "duration_s"})


class FintzSyncCompletedRule:
    """Regra de negócio para finalização de sync Fintz.

    Demonstra como regras podem carregar configuração via __init__
    sem acoplar ao Settings global.
    """

    handles: frozenset[EventType] = frozenset({EventType.FINTZ_SYNC_COMPLETED})

    def __init__(self, error_rate_threshold: float = 0.10) -> None:
        """
        Args:
            error_rate_threshold: Percentual máximo de erros tolerado (0.0–1.0).
                                  Acima disso, o evento vai para dead-letter com BusinessRuleError.
        """
        self._threshold = error_rate_threshold

    async def apply(self, event: Event) -> dict[str, Any]:
        self._validate_payload(event.payload)

        dataset: str = event.payload["dataset"]
        rows_synced: int = event.payload["rows_synced"]
        errors: int = event.payload["errors"]
        duration_s: float = event.payload["duration_s"]

        total = rows_synced + errors
        error_rate = errors / total if total > 0 else 0.0

        log.info(
            "fintz_sync_rule_applied",
            dataset=dataset,
            rows_synced=rows_synced,
            errors=errors,
            error_rate=f"{error_rate:.1%}",
            duration_s=duration_s,
        )

        if error_rate > self._threshold:
            raise BusinessRuleError(
                f"Taxa de erro do sync '{dataset}' ({error_rate:.1%}) "
                f"excede o limite de {self._threshold:.1%}. "
                f"Evento movido para dead-letter para revisão manual."
            )

        return {
            "dataset": dataset,
            "error_rate": error_rate,
            "rows_per_second": rows_synced / duration_s if duration_s > 0 else 0.0,
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        missing = _REQUIRED_FIELDS - set(payload.keys())
        if missing:
            raise BusinessRuleError(
                f"Payload de FINTZ_SYNC_COMPLETED está faltando campos: {missing}"
            )

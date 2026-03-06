"""
Base para regras de negócio desacopladas.

Design decision: BusinessRule como Protocol permite que qualquer objeto
com o método evaluate() seja usado como regra. Isso facilita composição
(RuleChain) e testes individuais de cada regra.

Cada regra retorna RuleResult — nunca lança exceção por padrão.
Quem decide o que fazer com uma violação é o serviço de aplicação.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RuleResult:
    is_valid: bool
    rule_name: str
    message: str = ""
    context: dict[str, Any] | None = None

    @property
    def is_violation(self) -> bool:
        return not self.is_valid

    @classmethod
    def ok(cls, rule_name: str) -> "RuleResult":
        return cls(is_valid=True, rule_name=rule_name)

    @classmethod
    def violation(cls, rule_name: str, message: str, context: dict[str, Any] | None = None) -> "RuleResult":
        return cls(is_valid=False, rule_name=rule_name, message=message, context=context)


@runtime_checkable
class BusinessRule(Protocol):
    """Contrato para qualquer regra de negócio."""
    async def evaluate(self, context: dict[str, Any]) -> RuleResult: ...


@dataclass
class RuleChain:
    """
    Encadeia múltiplas regras. Para na primeira violação (fail-fast)
    ou avalia todas conforme o modo.
    """
    rules: list[BusinessRule]
    fail_fast: bool = True

    async def evaluate_all(self, context: dict[str, Any]) -> list[RuleResult]:
        results: list[RuleResult] = []
        for rule in self.rules:
            result = await rule.evaluate(context)
            results.append(result)
            if self.fail_fast and result.is_violation:
                break
        return results

    async def is_valid(self, context: dict[str, Any]) -> bool:
        results = await self.evaluate_all(context)
        return all(r.is_valid for r in results)

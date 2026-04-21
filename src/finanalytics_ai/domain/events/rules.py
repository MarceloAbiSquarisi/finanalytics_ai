"""
Protocols (interfaces) para regras de negocio.

Decisao: Protocol em vez de ABC.
- Protocol e estruturalmente tipado (duck typing verificado pelo mypy)
- Nao exige heranca explicita — facilita testing com mocks simples
- runtime_checkable permite isinstance() quando necessario (ex: logging de qual
  regra foi aplicada sem precisar de atributo especial)

Trade-off: Protocol nao da erro em runtime se uma classe nao implementar o metodo.
Mitigamos isso com testes unitarios que verificam a interface explicitamente.

Todas as regras sao async por padrao: uma regra pode precisar consultar o banco
(ex: verificar historico de trades) e nao queremos two-tier design (sync/async).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from finanalytics_ai.domain.events.models import DomainEvent, ProcessingResult


@runtime_checkable
class BusinessRule(Protocol):
    """
    Contrato para regras de negocio aplicaveis a eventos.

    Cada regra e responsavel por:
    1. Verificar se se aplica ao evento (metodo applies_to)
    2. Executar a logica (metodo apply)

    Design: regras retornam ProcessingResult em vez de levantar excecoes
    para resultados esperados (ex: validacao falhou). Excecoes sao reservadas
    para erros inesperados (banco caiu, servico fora).
    """

    @property
    def name(self) -> str:
        """Nome unico da regra para logging e rastreabilidade."""
        ...

    def applies_to(self, event: DomainEvent) -> bool:
        """
        Filtragem rapida e sincrona — evita await desnecessario.
        Retorna True se esta regra deve ser executada para este evento.
        """
        ...

    async def apply(self, event: DomainEvent) -> ProcessingResult:
        """
        Executa a regra. Levanta excecoes de dominio se algo inesperado ocorrer.
        Para resultados esperados (validacao falhou), retorna ProcessingResult.failure().
        """
        ...


@runtime_checkable
class EventFilter(Protocol):
    """
    Filtro pre-processamento — descarta eventos antes de chegar nas regras.
    Exemplo: filtrar eventos duplicados por hash de payload, ou eventos
    de fontes nao confiáveis.
    """

    @property
    def name(self) -> str: ...

    async def should_process(self, event: DomainEvent) -> bool:
        """True = processar, False = descartar silenciosamente."""
        ...

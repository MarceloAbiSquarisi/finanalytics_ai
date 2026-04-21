"""
finanalytics_ai.domain.fintz.entities
──────────────────────────────────────
Entidades de domínio para o pipeline de ingestão Fintz.

Design decisions:
  - TypedDict para rows de parquet: estrutura de dados proveniente
    de fonte externa, sem comportamento — não justifica dataclass.
  - FintzDatasetSpec frozen dataclass: especificação imutável de um
    dataset. Usada para construir o catálogo em tempo de importação.
  - ALL_DATASETS centraliza o catálogo completo em um único lugar,
    evitando que a lógica de "quais endpoints chamar" vaze para
    o service ou o worker.
  - Itens contábeis com 12M e TRIMESTRAL são datasets separados
    (cada um = 1 arquivo parquet na Fintz), portanto 1 FintzDatasetSpec
    por combinação item × período.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

# ── Row types (dados brutos dos parquets) ────────────────────────────────────


class CotacaoRow(TypedDict):
    """Linha do parquet de cotações OHLC (todos os tickers)."""

    ticker: str
    data: str  # "YYYY-MM-DD"
    precoFechamento: float | None
    precoFechamentoAjustado: float | None
    precoAbertura: float | None
    precoMinimo: float | None
    precoMaximo: float | None
    volumeNegociado: int | None
    fatorAjuste: float | None


class ItemContabilRow(TypedDict):
    """Linha do parquet de itens contábeis point-in-time."""

    ticker: str
    item: str
    tipoPeriodo: str  # "12M" | "TRIMESTRAL"
    tipoDemonstracao: str | None  # "CONSOLIDADO" | "INDIVIDUAL"
    data: str  # data de publicação — ponto no tempo (PIT)
    ano: int
    trimestre: int
    valor: float | None


class IndicadorRow(TypedDict):
    """Linha do parquet de indicadores point-in-time."""

    ticker: str
    indicador: str
    data: str  # data de publicação — ponto no tempo (PIT)
    valor: float | None


# ── Dataset spec ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FintzDatasetSpec:
    """
    Especificação imutável de um dataset Fintz.

    Cada spec representa uma chamada à API que retorna um link para
    download de um arquivo parquet. O campo `key` é usado como
    identificador único no sync_log (tabela de idempotência).

    Fields:
        key:         identificador único, ex: "cotacoes_ohlc",
                     "item_EBIT_12M", "indicador_ROE"
        endpoint:    path relativo à URL base, ex:
                     "/bolsa/b3/avista/cotacoes/historico/arquivos"
        params:      query params para a chamada, ex: {"item": "EBIT",
                     "tipoPeriodo": "12M"}
        dataset_type: "cotacoes" | "item_contabil" | "indicador"
        description: descrição legível para logs
    """

    key: str
    endpoint: str
    params: dict[str, str]
    dataset_type: str
    description: str


# ── Catálogo de itens contábeis ───────────────────────────────────────────────

# Itens disponíveis em 12M e TRIMESTRAL
_ITEMS_12M_AND_TRI: tuple[str, ...] = (
    "ReceitaLiquida",
    "Custos",
    "ResultadoBruto",
    "DespesasReceitasOperacionaisOuAdministrativas",
    "EBIT",
    "ResultadoFinanceiro",
    "ReceitasFinanceiras",
    "LAIR",
    "Impostos",
    "LucroLiquidoOperacoesContinuadas",
    "LucroLiquidoOperacoesDescontinuadas",
    "LucroLiquido",
    "LucroLiquidoSociosControladora",
    "DepreciacaoAmortizacao",
    "EquivalenciaPatrimonial",
)

# Itens disponíveis apenas em TRIMESTRAL (balanço — não acumulável)
_ITEMS_TRI_ONLY: tuple[str, ...] = (
    "AtivoCirculante",
    "AtivoNaoCirculante",
    "AtivoTotal",
    "CaixaEquivalentes",
    "DespesasFinanceiras",
    "Disponibilidades",
    "DividaBruta",
    "DividaLiquida",
    "EBITDA",
    "PassivoCirculante",
    "PassivoNaoCirculante",
    "PassivoTotal",
    "PatrimonioLiquido",
)

# Indicadores PIT disponíveis
_INDICADORES: tuple[str, ...] = (
    "ValorDeMercado",
    "EV",
    "P_L",
    "P_VP",
    "VPA",
    "LPA",
    "DividendYield",
    "EV_EBITDA",
    "EV_EBIT",
    "P_EBITDA",
    "P_EBIT",
    "P_Ativos",
    "P_SR",
    "P_CapitalDeGiro",
    "P_AtivoCirculanteLiquido",
    "ROE",
    "ROA",
    "ROIC",
    "GiroAtivos",
    "MargemBruta",
    "MargemEBITDA",
    "MargemEBIT",
    "MargemLiquida",
    "DividaLiquida_PatrimonioLiquido",
    "DividaLiquida_EBITDA",
    "DividaLiquida_EBIT",
    "PatrimonioLiquido_Ativos",
    "Passivos_Ativos",
    "LiquidezCorrente",
    "DividaBruta_PatrimonioLiquido",
    "EBIT_Ativos",
    "EBIT_DespesasFinanceiras",
    "EBITDA_DespesasFinanceiras",
    "EBITDA_EV",
    "EBIT_EV",
    "L_P",
)


def _build_catalog() -> list[FintzDatasetSpec]:
    """Constrói o catálogo completo de datasets Fintz."""
    catalog: list[FintzDatasetSpec] = []

    # ── Cotações OHLC (1 arquivo, todos os tickers) ──────────────────────────
    catalog.append(
        FintzDatasetSpec(
            key="cotacoes_ohlc",
            endpoint="/bolsa/b3/avista/cotacoes/historico/arquivos",
            params={},
            dataset_type="cotacoes",
            description="Cotações OHLC diárias — todos os tickers B3 desde 2010",
        )
    )

    # ── Itens contábeis PIT ──────────────────────────────────────────────────
    _item_endpoint = "/bolsa/b3/avista/itens-contabeis/point-in-time/arquivos"

    for item in _ITEMS_12M_AND_TRI:
        for periodo in ("12M", "TRIMESTRAL"):
            catalog.append(
                FintzDatasetSpec(
                    key=f"item_{item}_{periodo}",
                    endpoint=_item_endpoint,
                    params={"item": item, "tipoPeriodo": periodo},
                    dataset_type="item_contabil",
                    description=f"Item contábil PIT: {item} ({periodo})",
                )
            )

    for item in _ITEMS_TRI_ONLY:
        catalog.append(
            FintzDatasetSpec(
                key=f"item_{item}_TRIMESTRAL",
                endpoint=_item_endpoint,
                params={"item": item, "tipoPeriodo": "TRIMESTRAL"},
                dataset_type="item_contabil",
                description=f"Item contábil PIT: {item} (TRIMESTRAL)",
            )
        )

    # ── Indicadores PIT ──────────────────────────────────────────────────────
    _ind_endpoint = "/bolsa/b3/avista/indicadores/point-in-time/arquivos"

    for indicador in _INDICADORES:
        catalog.append(
            FintzDatasetSpec(
                key=f"indicador_{indicador}",
                endpoint=_ind_endpoint,
                params={"indicador": indicador},
                dataset_type="indicador",
                description=f"Indicador PIT: {indicador}",
            )
        )

    return catalog


ALL_DATASETS: list[FintzDatasetSpec] = _build_catalog()
"""
Catálogo completo de datasets Fintz.

Composição:
  - 1  cotação OHLC
  - 43 itens contábeis PIT (15 items × 2 períodos + 13 items TRIMESTRAL)
  - 36 indicadores PIT
  Total: 80 datasets / sync diário
"""

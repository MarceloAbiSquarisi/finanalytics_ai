"""
tests.unit.domain.test_fintz_entities
───────────────────────────────────────
Testes do catálogo de datasets e entidades de domínio Fintz.
"""

from __future__ import annotations

from finanalytics_ai.domain.fintz.entities import (
    _INDICADORES,
    _ITEMS_12M_AND_TRI,
    _ITEMS_TRI_ONLY,
    ALL_DATASETS,
    FintzDatasetSpec,
)


def test_all_datasets_not_empty() -> None:
    assert len(ALL_DATASETS) > 0


def test_all_datasets_keys_are_unique() -> None:
    keys = [d.key for d in ALL_DATASETS]
    assert len(keys) == len(set(keys)), "Chaves duplicadas no catálogo!"


def test_cotacoes_dataset_present() -> None:
    keys = {d.key for d in ALL_DATASETS}
    assert "cotacoes_ohlc" in keys


def test_items_12m_and_tri_generate_two_specs_each() -> None:
    """Cada item com suporte a 12M e TRIMESTRAL deve ter 2 entradas."""
    for item in _ITEMS_12M_AND_TRI:
        key_12m = f"item_{item}_12M"
        key_tri = f"item_{item}_TRIMESTRAL"
        keys = {d.key for d in ALL_DATASETS}
        assert key_12m in keys, f"Faltando: {key_12m}"
        assert key_tri in keys, f"Faltando: {key_tri}"


def test_items_tri_only_generate_one_spec_each() -> None:
    """Itens somente TRIMESTRAL não devem ter entrada 12M."""
    for item in _ITEMS_TRI_ONLY:
        keys = {d.key for d in ALL_DATASETS}
        assert f"item_{item}_TRIMESTRAL" in keys
        assert f"item_{item}_12M" not in keys


def test_all_indicadores_present() -> None:
    keys = {d.key for d in ALL_DATASETS}
    for ind in _INDICADORES:
        assert f"indicador_{ind}" in keys, f"Faltando indicador: {ind}"


def test_dataset_spec_is_frozen() -> None:
    """FintzDatasetSpec deve ser imutável (frozen=True)."""
    spec = FintzDatasetSpec(
        key="test",
        endpoint="/test",
        params={},
        dataset_type="cotacoes",
        description="test",
    )
    try:
        spec.key = "modified"  # type: ignore[misc]
        assert False, "Deveria ter levantado FrozenInstanceError"
    except Exception:
        pass


def test_dataset_types_are_valid() -> None:
    valid_types = {"cotacoes", "item_contabil", "indicador"}
    for spec in ALL_DATASETS:
        assert spec.dataset_type in valid_types, (
            f"Tipo inválido em {spec.key}: {spec.dataset_type!r}"
        )


def test_item_contabil_specs_have_required_params() -> None:
    item_specs = [d for d in ALL_DATASETS if d.dataset_type == "item_contabil"]
    for spec in item_specs:
        assert "item" in spec.params, f"{spec.key}: falta 'item' nos params"
        assert "tipoPeriodo" in spec.params, f"{spec.key}: falta 'tipoPeriodo' nos params"


def test_indicador_specs_have_required_params() -> None:
    ind_specs = [d for d in ALL_DATASETS if d.dataset_type == "indicador"]
    for spec in ind_specs:
        assert "indicador" in spec.params, f"{spec.key}: falta 'indicador' nos params"


def test_total_dataset_count() -> None:
    """
    Valida o total esperado do catálogo.
      - 1  cotação OHLC
      - 15 itens × 2 períodos = 30  (12M + TRIMESTRAL)
      - 13 itens × 1 período  = 13  (somente TRIMESTRAL)
      - 36 indicadores
      Total = 80
    """
    cotacoes = sum(1 for d in ALL_DATASETS if d.dataset_type == "cotacoes")
    itens = sum(1 for d in ALL_DATASETS if d.dataset_type == "item_contabil")
    indicadores = sum(1 for d in ALL_DATASETS if d.dataset_type == "indicador")

    assert cotacoes == 1
    assert itens == len(_ITEMS_12M_AND_TRI) * 2 + len(_ITEMS_TRI_ONLY)
    assert indicadores == len(_INDICADORES)

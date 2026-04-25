"""
tests/unit/domain/test_portfolio_multi.py
Testes unitários para múltiplas carteiras — novos campos e comportamentos.
"""

from __future__ import annotations

import pytest

from finanalytics_ai.domain.entities.portfolio import Portfolio


class TestPortfolioNewFields:
    def test_default_description_empty(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        assert p.description == ""

    def test_default_benchmark_empty(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        assert p.benchmark == ""

    def test_can_set_description(self) -> None:
        p = Portfolio(user_id="u1", name="Teste", description="Carteira conservadora")
        assert p.description == "Carteira conservadora"

    def test_can_set_benchmark(self) -> None:
        p = Portfolio(user_id="u1", name="Teste", benchmark="IBOV")
        assert p.benchmark == "IBOV"


class TestUpdateMetadata:
    def test_update_name(self) -> None:
        p = Portfolio(user_id="u1", name="Antigo")
        p.update_metadata(name="Novo")
        assert p.name == "Novo"

    def test_update_name_strips_whitespace(self) -> None:
        p = Portfolio(user_id="u1", name="Antigo")
        p.update_metadata(name="  Novo  ")
        assert p.name == "Novo"

    def test_update_empty_name_raises(self) -> None:
        p = Portfolio(user_id="u1", name="Antigo")
        with pytest.raises(ValueError, match="vazio"):
            p.update_metadata(name="   ")

    def test_update_description(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        p.update_metadata(description="Renda variável agressiva")
        assert p.description == "Renda variável agressiva"

    def test_update_benchmark_uppercased(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        p.update_metadata(benchmark="ibov")
        assert p.benchmark == "IBOV"

    def test_update_none_fields_not_changed(self) -> None:
        p = Portfolio(user_id="u1", name="Original", description="Desc", benchmark="CDI")
        p.update_metadata(name=None, description=None, benchmark=None)
        assert p.name == "Original"
        assert p.description == "Desc"
        assert p.benchmark == "CDI"

    def test_update_sets_updated_at(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        before = p.updated_at
        p.update_metadata(name="Novo")
        assert p.updated_at >= before

    def test_partial_update_only_changes_sent_fields(self) -> None:
        p = Portfolio(user_id="u1", name="Original", description="Desc original", benchmark="CDI")
        p.update_metadata(name="Novo Nome")
        assert p.name == "Novo Nome"
        assert p.description == "Desc original"  # não alterado
        assert p.benchmark == "CDI"  # não alterado


# Refactor 25/abr: removido TestPortfolioIsDefault — modelo simplificado
# para 1 portfolio por conta (1:1). Conceito de "default" entre varios
# portfolios deixa de existir.

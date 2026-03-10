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

    def test_default_is_default_false(self) -> None:
        p = Portfolio(user_id="u1", name="Teste")
        assert p.is_default is False

    def test_can_set_description(self) -> None:
        p = Portfolio(user_id="u1", name="Teste", description="Carteira conservadora")
        assert p.description == "Carteira conservadora"

    def test_can_set_benchmark(self) -> None:
        p = Portfolio(user_id="u1", name="Teste", benchmark="IBOV")
        assert p.benchmark == "IBOV"

    def test_can_set_is_default(self) -> None:
        p = Portfolio(user_id="u1", name="Principal", is_default=True)
        assert p.is_default is True


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


class TestPortfolioIsDefault:
    def test_multiple_portfolios_only_one_default(self) -> None:
        """Invariante: apenas um portfolio por usuário deve ser default.
        O domínio não enforce isso — é responsabilidade do service.
        Este teste documenta o comportamento esperado ao nível de entidade."""
        p1 = Portfolio(user_id="u1", name="P1", is_default=True)
        p2 = Portfolio(user_id="u1", name="P2", is_default=False)
        p3 = Portfolio(user_id="u1", name="P3", is_default=False)

        # Simula o que o service faz ao definir p2 como default
        p1.is_default = False
        p2.is_default = True

        defaults = [p for p in [p1, p2, p3] if p.is_default]
        assert len(defaults) == 1
        assert defaults[0].name == "P2"

    def test_is_default_false_by_default(self) -> None:
        p = Portfolio(user_id="u1", name="Qualquer")
        assert not p.is_default

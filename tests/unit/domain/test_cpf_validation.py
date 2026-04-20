"""Testes unitarios — validacao CPF (DV oficial)."""
from __future__ import annotations

import pytest

from finanalytics_ai.domain.validation.cpf import (
    format_cpf, is_valid_cpf, normalize_cpf,
)


# ── normalize ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("12345678909", "12345678909"),
    ("123.456.789-09", "12345678909"),
    ("123 456 789 09", "12345678909"),
    ("123.456.789/09", "12345678909"),
    ("", ""),
    (None, ""),
])
def test_normalize(raw, expected):
    assert normalize_cpf(raw) == expected


# ── valid CPFs ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cpf", [
    "11144477735",        # CPF de teste comum
    "111.444.777-35",
    "529.982.247-25",     # outro CPF valido
    "01234567890",        # com zero a esquerda
])
def test_valid_cpf(cpf):
    assert is_valid_cpf(cpf) is True


# ── invalid CPFs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("cpf, reason", [
    ("",                       "vazio"),
    ("123",                    "muito curto"),
    ("123456789012",           "muito longo"),
    ("00000000000",            "todos zeros"),
    ("11111111111",            "todos um"),
    ("99999999999",            "todos nove"),
    ("12345678900",            "DV invalido"),
    ("11144477734",            "DV2 errado"),
    ("11144477725",            "DV1 errado"),
    ("abc.def.ghi-jk",         "nao numerico"),
])
def test_invalid_cpf(cpf, reason):
    assert is_valid_cpf(cpf) is False, f"deveria rejeitar ({reason}): {cpf}"


# ── format ─────────────────────────────────────────────────────────────────

def test_format_valid():
    assert format_cpf("11144477735") == "111.444.777-35"
    assert format_cpf("111.444.777-35") == "111.444.777-35"


def test_format_invalid_raises():
    with pytest.raises(ValueError, match="CPF invalido"):
        format_cpf("00000000000")
    with pytest.raises(ValueError):
        format_cpf("123")

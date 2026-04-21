"""Validacao de CPF — algoritmo dos digitos verificadores oficiais.

Aceita formatos: '12345678909', '123.456.789-09', '123 456 789 09'.
Rejeita: tamanho != 11, sequencias repetidas (00000000000, 11111111111...),
DV invalido.
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def normalize_cpf(raw: str) -> str:
    """Remove tudo que nao for digito. Retorna string vazia se input None."""
    if not raw:
        return ""
    return _DIGITS_RE.sub("", str(raw))


def is_valid_cpf(raw: str) -> bool:
    """True se CPF tem 11 digitos validos (DV correto)."""
    cpf = normalize_cpf(raw)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:  # rejeita 00000000000, 11111111111, ...
        return False
    digits = [int(c) for c in cpf]

    # Primeiro DV
    s1 = sum(digits[i] * (10 - i) for i in range(9))
    dv1 = 0 if (s1 * 10) % 11 == 10 else (s1 * 10) % 11
    if dv1 != digits[9]:
        return False

    # Segundo DV
    s2 = sum(digits[i] * (11 - i) for i in range(10))
    dv2 = 0 if (s2 * 10) % 11 == 10 else (s2 * 10) % 11
    if dv2 != digits[10]:
        return False

    return True


def format_cpf(raw: str) -> str:
    """Formata para '123.456.789-09'. Levanta ValueError se invalido."""
    cpf = normalize_cpf(raw)
    if not is_valid_cpf(cpf):
        raise ValueError(f"CPF invalido: {raw}")
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

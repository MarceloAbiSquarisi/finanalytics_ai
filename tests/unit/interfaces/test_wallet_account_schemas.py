"""Schemas Pydantic AccountCreate / AccountUpdate (validacao CPF)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from finanalytics_ai.interfaces.api.routes.wallet import AccountCreate, AccountUpdate


_BASE = {
    "titular": "Marcelo Abi Squarisi",
    "cpf": "111.444.777-35",
    "institution_code": "341",
    "institution_name": "Itau Unibanco",
    "agency": "1234",
    "account_number": "56789-0",
    "apelido": "Conta principal",
}


def test_create_ok_normalizes_cpf():
    a = AccountCreate(**_BASE)
    assert a.cpf == "11144477735"
    assert a.titular == "Marcelo Abi Squarisi"
    assert a.apelido == "Conta principal"


def test_create_rejects_invalid_cpf():
    with pytest.raises(ValidationError, match="CPF invalido"):
        AccountCreate(**{**_BASE, "cpf": "00000000000"})
    with pytest.raises(ValidationError):
        AccountCreate(**{**_BASE, "cpf": "123"})


def test_create_requires_titular():
    body = {k: v for k, v in _BASE.items() if k != "titular"}
    with pytest.raises(ValidationError):
        AccountCreate(**body)


def test_create_requires_apelido():
    body = {k: v for k, v in _BASE.items() if k != "apelido"}
    with pytest.raises(ValidationError):
        AccountCreate(**body)


def test_create_requires_agency():
    body = {k: v for k, v in _BASE.items() if k != "agency"}
    with pytest.raises(ValidationError):
        AccountCreate(**body)


def test_update_partial_ok():
    u = AccountUpdate(apelido="Outro nome", titular="Marcelo")
    assert u.apelido == "Outro nome"
    assert u.titular == "Marcelo"
    assert u.cpf is None


def test_update_cpf_validation_when_provided():
    u = AccountUpdate(cpf="111.444.777-35")
    assert u.cpf == "11144477735"
    with pytest.raises(ValidationError):
        AccountUpdate(cpf="00000000000")


def test_update_cpf_none_skipped():
    u = AccountUpdate()
    assert u.cpf is None

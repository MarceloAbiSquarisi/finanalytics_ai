"""
Rotas de contas de negociação.

Endpoints:
  POST   /accounts/                    — cadastra conta
  GET    /accounts/                    — lista contas (filtro: ?type=real|simulator)
  GET    /accounts/active              — retorna conta ativa
  PUT    /accounts/{uuid}/activate     — define conta ativa
  GET    /accounts/{uuid}              — detalhe de uma conta
  PATCH  /accounts/{uuid}              — atualiza label/senha
  DELETE /accounts/{uuid}              — remove conta (não pode ser a ativa)
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from finanalytics_ai.domain.accounts import (
    AccountDomainError,
    AccountNotFoundError,
    AccountType,
    DuplicateAccountError,
    NoActiveAccountError,
)

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AccountCreateRequest(BaseModel):
    broker_id: str = Field(..., min_length=1, description="Código da corretora (ex: '227')")
    account_id: str = Field(..., min_length=1, description="Número da conta")
    account_type: AccountType
    label: str = Field(..., min_length=1, max_length=100)
    routing_password: str | None = Field(None, description="Senha de roteamento")
    sub_account_id: str | None = None


class AccountUpdateRequest(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    routing_password: str | None = None


class AccountResponse(BaseModel):
    uuid: str
    broker_id: str
    account_id: str
    account_type: str
    label: str
    status: str
    broker_name: str | None
    sub_account_id: str | None
    has_routing_password: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

def _get_service() -> Any:
    from finanalytics_ai.interfaces.api.app import get_account_service

    svc = get_account_service()
    if svc is None:
        raise HTTPException(503, detail="AccountService não disponível")
    return svc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreateRequest) -> AccountResponse:
    """Cadastra uma nova conta real ou simulador."""
    svc = _get_service()
    try:
        account = await svc.create(body.model_dump())
    except DuplicateAccountError as e:
        raise HTTPException(409, detail=str(e))
    return _to_response(account)


@router.get("/active", response_model=AccountResponse)
async def get_active_account() -> AccountResponse:
    """Retorna a conta atualmente ativa."""
    svc = _get_service()
    try:
        account = await svc.get_active()
    except NoActiveAccountError as e:
        raise HTTPException(404, detail=str(e))
    return _to_response(account)


@router.get("/", response_model=list[AccountResponse])
async def list_accounts(
    type: str | None = Query(None, description="Filtrar por tipo: real | simulator"),
) -> list[AccountResponse]:
    """Lista todas as contas, com filtro opcional por tipo."""
    svc = _get_service()
    accounts = await svc.list(account_type=type)
    return [_to_response(a) for a in accounts]


@router.get("/{account_uuid}", response_model=AccountResponse)
async def get_account(account_uuid: str) -> AccountResponse:
    svc = _get_service()
    try:
        account = await svc.get(account_uuid)
    except AccountNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    return _to_response(account)


@router.put("/{account_uuid}/activate", response_model=AccountResponse)
async def activate_account(account_uuid: str) -> AccountResponse:
    """Define esta conta como ativa. Desativa todas as outras."""
    svc = _get_service()
    try:
        account = await svc.set_active(account_uuid)
    except AccountNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    return _to_response(account)


@router.patch("/{account_uuid}", response_model=AccountResponse)
async def update_account(account_uuid: str, body: AccountUpdateRequest) -> AccountResponse:
    """Atualiza label e/ou senha de roteamento."""
    svc = _get_service()
    try:
        account = await svc.update(account_uuid, body.model_dump(exclude_none=True))
    except AccountNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    return _to_response(account)


@router.delete("/{account_uuid}", status_code=204)
async def delete_account(account_uuid: str) -> None:
    """Remove uma conta. Não é permitido remover a conta ativa."""
    svc = _get_service()
    try:
        await svc.delete(account_uuid)
    except AccountNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except AccountDomainError as e:
        raise HTTPException(409, detail=str(e))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _to_response(account: Any) -> AccountResponse:
    return AccountResponse(
        uuid=account.uuid,
        broker_id=account.broker_id,
        account_id=account.account_id,
        account_type=account.account_type.value,
        label=account.label,
        status=account.status.value,
        broker_name=account.broker_name,
        sub_account_id=account.sub_account_id,
        has_routing_password=bool(account.routing_password),
        created_at=account.created_at.isoformat(),
        updated_at=account.updated_at.isoformat(),
    )

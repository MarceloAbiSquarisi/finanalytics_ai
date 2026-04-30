"""
Rotas de contas de negociacao (DEPRECATED — unificacao U3, 24/abr/2026).

As credenciais Profit DLL foram unificadas em investment_accounts.
Use:
  - CRUD basico: /api/v1/wallet/accounts/*
  - Credenciais DLL: /api/v1/wallet/accounts/{id}/connect-dll
  - Ativar DLL: /api/v1/wallet/accounts/{id}/activate-dll
  - Desconectar DLL: /api/v1/wallet/accounts/{id}/disconnect-dll

Toda rota aqui retorna 410 Gone com mensagem explicando o novo path.
A tabela trading_accounts foi removida apos migracao dos dados.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


_DEPRECATED = {
    "detail": "Deprecated: as credenciais Profit DLL foram unificadas em /api/v1/wallet/accounts/*. "
    "Use /api/v1/wallet/accounts/{id}/connect-dll para cadastrar credenciais, "
    "/activate-dll para marcar como ativa, e /disconnect-dll para remover.",
    "new_endpoints": {
        "list": "GET  /api/v1/wallet/accounts",
        "create": "POST /api/v1/wallet/accounts",
        "update": "PATCH /api/v1/wallet/accounts/{id}",
        "delete": "DELETE /api/v1/wallet/accounts/{id}",
        "connect_dll": "POST /api/v1/wallet/accounts/{id}/connect-dll",
        "activate_dll": "POST /api/v1/wallet/accounts/{id}/activate-dll",
        "disconnect_dll": "POST /api/v1/wallet/accounts/{id}/disconnect-dll",
    },
}


def _gone():
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_DEPRECATED)


@router.get("/", tags=["Accounts (DEPRECATED)"])
async def list_accounts_deprecated():
    _gone()


@router.post("/", tags=["Accounts (DEPRECATED)"])
async def create_account_deprecated():
    _gone()


@router.get("/active", tags=["Accounts (DEPRECATED)"])
async def get_active_deprecated():
    _gone()


@router.get("/{uuid}", tags=["Accounts (DEPRECATED)"])
async def get_account_deprecated(uuid: str):
    _gone()


@router.put("/{uuid}/activate", tags=["Accounts (DEPRECATED)"])
async def activate_deprecated(uuid: str):
    _gone()


@router.patch("/{uuid}", tags=["Accounts (DEPRECATED)"])
async def update_deprecated(uuid: str):
    _gone()


@router.delete("/{uuid}", tags=["Accounts (DEPRECATED)"])
async def delete_deprecated(uuid: str):
    _gone()

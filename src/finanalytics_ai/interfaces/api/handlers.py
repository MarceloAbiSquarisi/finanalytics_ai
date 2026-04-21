"""
Camada HTTP para o módulo de contas.

Schemas Pydantic para validação de entrada/saída.
Handlers são funções puras que recebem request e retornam response —
compatíveis com aiohttp e facilmente adaptáveis para FastAPI.

`routing_password` nunca aparece em respostas de listagem (apenas confirma
se está cadastrada via campo booleano `has_routing_password`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from finanalytics_ai.domain.accounts import AccountType, TradingAccount

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AccountCreateRequest(BaseModel):
    broker_id: str = Field(..., min_length=1, description="Código da corretora (ex: '227')")
    account_id: str = Field(..., min_length=1, description="Número da conta na corretora")
    account_type: AccountType
    label: str = Field(..., min_length=1, max_length=100, description="Nome amigável")
    routing_password: str | None = Field(None, description="Senha de roteamento")
    sub_account_id: str | None = Field(None, description="Sub-conta, se houver")

    @field_validator("broker_id", "account_id")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


class AccountUpdateRequest(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    routing_password: str | None = None


class AccountResponse(BaseModel):
    uuid: str
    broker_id: str
    account_id: str
    account_type: AccountType
    label: str
    status: str
    broker_name: str | None
    sub_account_id: str | None
    has_routing_password: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, acc: TradingAccount) -> AccountResponse:
        return cls(
            uuid=acc.uuid,
            broker_id=acc.broker_id,
            account_id=acc.account_id,
            account_type=acc.account_type,
            label=acc.label,
            status=acc.status.value,
            broker_name=acc.broker_name,
            sub_account_id=acc.sub_account_id,
            has_routing_password=bool(acc.routing_password),
            created_at=acc.created_at.isoformat(),
            updated_at=acc.updated_at.isoformat(),
        )


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    total: int


# ---------------------------------------------------------------------------
# Container de use cases (injetado no startup via DI manual)
# ---------------------------------------------------------------------------

from finanalytics_ai.application.use_cases import (
    CreateAccount,
    CreateAccountCmd,
    DeleteAccount,
    GetAccount,
    GetActiveAccount,
    ListAccounts,
    SetActiveAccount,
    UpdateAccount,
    UpdateAccountCmd,
)


class AccountHandlers:
    """
    Agrupa os handlers HTTP. Recebe use cases no __init__.
    Instanciado uma vez no startup e compartilhado entre requests.
    """

    def __init__(
        self,
        create: CreateAccount,
        list_: ListAccounts,
        get: GetAccount,
        set_active: SetActiveAccount,
        get_active: GetActiveAccount,
        update: UpdateAccount,
        delete: DeleteAccount,
    ) -> None:
        self._create = create
        self._list = list_
        self._get = get
        self._set_active = set_active
        self._get_active = get_active
        self._update = update
        self._delete = delete

    async def handle_create(self, body: dict) -> AccountResponse:
        cmd_data = AccountCreateRequest.model_validate(body)
        cmd = CreateAccountCmd(
            broker_id=cmd_data.broker_id,
            account_id=cmd_data.account_id,
            account_type=cmd_data.account_type,
            label=cmd_data.label,
            routing_password=cmd_data.routing_password,
            sub_account_id=cmd_data.sub_account_id,
        )
        account = await self._create.execute(cmd)
        return AccountResponse.from_domain(account)

    async def handle_list(self, account_type: str | None = None) -> AccountListResponse:
        at = AccountType(account_type) if account_type else None
        accounts = await self._list.execute(at)
        items = [AccountResponse.from_domain(a) for a in accounts]
        return AccountListResponse(accounts=items, total=len(items))

    async def handle_get(self, account_uuid: str) -> AccountResponse:
        account = await self._get.execute(account_uuid)
        return AccountResponse.from_domain(account)

    async def handle_set_active(self, account_uuid: str) -> AccountResponse:
        account = await self._set_active.execute(account_uuid)
        return AccountResponse.from_domain(account)

    async def handle_get_active(self) -> AccountResponse:
        account = await self._get_active.execute()
        return AccountResponse.from_domain(account)

    async def handle_update(self, account_uuid: str, body: dict) -> AccountResponse:
        req = AccountUpdateRequest.model_validate(body)
        cmd = UpdateAccountCmd(
            account_uuid=account_uuid,
            label=req.label,
            routing_password=req.routing_password,
        )
        account = await self._update.execute(cmd)
        return AccountResponse.from_domain(account)

    async def handle_delete(self, account_uuid: str) -> None:
        await self._delete.execute(account_uuid)

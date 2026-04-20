"""
interfaces/api/routes/wallet.py
API REST para carteira multi-usuário:
  /api/v1/wallet/accounts     — contas de investimento
  /api/v1/wallet/trades       — histórico de trades (ações, ETFs, FIIs, BDRs)
  /api/v1/wallet/positions    — posições consolidadas com preço médio
  /api/v1/wallet/crypto       — criptomoedas
  /api/v1/wallet/other        — outros ativos
  /api/v1/wallet/summary      — visão geral da carteira
  /api/v1/wallet/master       — visão master (ADMIN/MASTER apenas)
"""
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from finanalytics_ai.domain.auth.entities import User, UserRole
from finanalytics_ai.interfaces.api.dependencies import get_current_user
from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository

router = APIRouter(prefix="/api/v1/wallet", tags=["Carteira"])

def _repo() -> WalletRepository:
    return WalletRepository()

def _require_master_or_admin(user: User) -> User:
    if user.role not in (UserRole.ADMIN, UserRole.MASTER):
        raise HTTPException(status_code=403, detail="Acesso negado: requer perfil MASTER ou ADMIN")
    return user


async def _resolve_portfolio_id(user_id: str, supplied: Optional[str]) -> str:
    """Resolve portfolio_id para INSERT em trades/positions/etc.

    - Se supplied: valida que pertence ao user; 422 se nao.
    - Se nao: pega o default do user; cria 'Carteira Principal' se nenhum.

    Garante invariante DB (portfolio_id NOT NULL + FK).
    """
    repo = _repo()
    if supplied:
        if not await repo.validate_portfolio_belongs_to_user(supplied, user_id):
            raise HTTPException(422, f"portfolio_id {supplied} nao pertence ao usuario")
        return supplied
    return await repo.ensure_default_portfolio(user_id)

# ── Schemas ───────────────────────────────────────────────────────────────

from finanalytics_ai.domain.validation import is_valid_cpf, normalize_cpf  # noqa: E402

class AccountCreate(BaseModel):
    titular: str            = Field(..., min_length=2, max_length=200,
                                    description="Nome do titular da conta")
    cpf: str                = Field(..., description="CPF (com ou sem mascara)")
    institution_code: str   = Field(..., min_length=1, max_length=20,
                                    description="Codigo da instituicao (ex: '341' Itau)")
    institution_name: str   = Field(..., min_length=2, max_length=200)
    agency: str             = Field(..., min_length=1, max_length=20,
                                    description="Codigo da agencia")
    account_number: str     = Field(..., min_length=1, max_length=50)
    apelido: str            = Field(..., min_length=1, max_length=100,
                                    description="Apelido da conta (UI-friendly)")
    country: str            = Field("BRA", max_length=3)
    currency: str           = Field("BRL", max_length=3)
    account_type: str       = Field("corretora")
    note: Optional[str]     = None

    @field_validator("cpf")
    @classmethod
    def _v_cpf(cls, v: str) -> str:
        v = normalize_cpf(v)
        if not is_valid_cpf(v):
            raise ValueError("CPF invalido (DV ou tamanho)")
        return v

class AccountUpdate(BaseModel):
    titular: Optional[str]          = Field(None, min_length=2, max_length=200)
    cpf: Optional[str]              = None
    institution_code: Optional[str] = None
    institution_name: Optional[str] = Field(None, min_length=2)
    agency: Optional[str]           = None
    account_number: Optional[str]   = None
    apelido: Optional[str]          = Field(None, min_length=1, max_length=100)
    is_active: Optional[bool]       = None
    note: Optional[str]             = None

    @field_validator("cpf")
    @classmethod
    def _v_cpf(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = normalize_cpf(v)
        if not is_valid_cpf(v):
            raise ValueError("CPF invalido (DV ou tamanho)")
        return v
    note: Optional[str] = None

class TradeCreate(BaseModel):
    ticker: str = Field(..., min_length=1)
    asset_class: str = Field("stock")      # stock|etf|crypto|fii|bdr
    operation: str = Field("buy")          # buy|sell|split|bonus
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    trade_date: date
    fees: Decimal = Field(Decimal("0"), ge=0)
    currency: str = Field("BRL", max_length=3)
    investment_account_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    note: Optional[str] = None

class CryptoUpsert(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: Decimal = Field(..., gt=0)
    average_price_brl: Decimal = Field(..., ge=0)
    average_price_usd: Optional[Decimal] = None
    investment_account_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    exchange: Optional[str] = None
    wallet_address: Optional[str] = None
    note: Optional[str] = None

class OtherAssetCreate(BaseModel):
    name: str = Field(..., min_length=2)
    asset_type: str = Field("outro")  # imovel|previdencia|coe|debenture|outro
    current_value: Decimal = Field(..., ge=0)
    invested_value: Optional[Decimal] = None
    currency: str = Field("BRL", max_length=3)
    acquisition_date: Optional[date] = None
    maturity_date: Optional[date] = None
    ir_exempt: bool = False
    investment_account_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    note: Optional[str] = None

class OtherAssetUpdate(BaseModel):
    name: Optional[str] = None
    current_value: Optional[Decimal] = None
    invested_value: Optional[Decimal] = None
    maturity_date: Optional[date] = None
    note: Optional[str] = None

# ── Investment Accounts ───────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts(
    include_inactive: bool = False,
    user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().list_accounts(str(user.user_id), include_inactive)

@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    user: User = Depends(get_current_user)
) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    return await _repo().create_account(data)

@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str,
    user: User = Depends(get_current_user)
) -> dict:
    acc = await _repo().get_account(account_id, str(user.user_id))
    if not acc:
        raise HTTPException(404, "Conta não encontrada")
    return acc

@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: str,
    body: AccountUpdate,
    user: User = Depends(get_current_user)
) -> dict:
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    acc = await _repo().update_account(account_id, str(user.user_id), data)
    if not acc:
        raise HTTPException(404, "Conta não encontrada")
    return acc

@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_account(
    account_id: str,
    user: User = Depends(get_current_user)
) -> None:
    ok = await _repo().delete_account(account_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Conta não encontrada")

# ── Trades ────────────────────────────────────────────────────────────────

@router.get("/trades")
async def list_trades(
    ticker: Optional[str] = None,
    asset_class: Optional[str] = None,
    account_id: Optional[str] = None,
    user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().list_trades(str(user.user_id), ticker, asset_class, account_id)

@router.post("/trades", status_code=status.HTTP_201_CREATED)
async def create_trade(
    body: TradeCreate,
    user: User = Depends(get_current_user)
) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    data["ticker"] = data["ticker"].upper()
    data["portfolio_id"] = await _resolve_portfolio_id(str(user.user_id), data.get("portfolio_id"))
    data["total_cost"] = float(data["quantity"]) * float(data["unit_price"]) + float(data["fees"])
    for k in ("quantity", "unit_price", "fees", "total_cost"):
        data[k] = float(data[k])
    # trade_date permanece como objeto date para o SQLAlchemy
    return await _repo().create_trade(data)

@router.delete("/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trade(
    trade_id: str,
    user: User = Depends(get_current_user)
) -> None:
    ok = await _repo().delete_trade(trade_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Trade não encontrado")

# ── Positions (preço médio calculado) ────────────────────────────────────

@router.get("/positions")
async def get_positions(
    asset_class: Optional[str] = None,
    user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().get_positions_summary(str(user.user_id), asset_class)

# ── Crypto ────────────────────────────────────────────────────────────────

@router.get("/crypto")
async def list_crypto(user: User = Depends(get_current_user)) -> list[dict]:
    return await _repo().list_crypto(str(user.user_id))

@router.put("/crypto", status_code=status.HTTP_200_OK)
async def upsert_crypto(
    body: CryptoUpsert,
    user: User = Depends(get_current_user)
) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    data["portfolio_id"] = await _resolve_portfolio_id(str(user.user_id), data.get("portfolio_id"))
    for k in ("quantity", "average_price_brl", "average_price_usd"):
        if data[k] is not None:
            data[k] = float(data[k])
    return await _repo().upsert_crypto(data)

@router.delete("/crypto/{crypto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_crypto(
    crypto_id: str,
    user: User = Depends(get_current_user)
) -> None:
    ok = await _repo().delete_crypto(crypto_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Cripto não encontrada")

# ── Other Assets ──────────────────────────────────────────────────────────

@router.get("/other")
async def list_other(
    asset_type: Optional[str] = None,
    user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().list_other_assets(str(user.user_id), asset_type)

@router.post("/other", status_code=status.HTTP_201_CREATED)
async def create_other(
    body: OtherAssetCreate,
    user: User = Depends(get_current_user)
) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    data["portfolio_id"] = await _resolve_portfolio_id(str(user.user_id), data.get("portfolio_id"))
    for k in ("current_value", "invested_value"):
        if data[k] is not None:
            data[k] = float(data[k])
    # datas permanecem como objetos date para o SQLAlchemy
    return await _repo().create_other_asset(data)

@router.patch("/other/{asset_id}")
async def update_other(
    asset_id: str,
    body: OtherAssetUpdate,
    user: User = Depends(get_current_user)
) -> dict:
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    for k in ("current_value", "invested_value"):
        if k in data:
            data[k] = float(data[k])
    # maturity_date permanece como objeto date
    asset = await _repo().update_other_asset(asset_id, str(user.user_id), data)
    if not asset:
        raise HTTPException(404, "Ativo não encontrado")
    return asset

@router.delete("/other/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_other(
    asset_id: str,
    user: User = Depends(get_current_user)
) -> None:
    ok = await _repo().delete_other_asset(asset_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Ativo não encontrado")

# ── Summary ───────────────────────────────────────────────────────────────

@router.get("/summary")
async def wallet_summary(user: User = Depends(get_current_user)) -> dict:
    uid = str(user.user_id)
    repo = _repo()
    accounts, positions, crypto, other = await __import__("asyncio").gather(
        repo.list_accounts(uid),
        repo.get_positions_summary(uid),
        repo.list_crypto(uid),
        repo.list_other_assets(uid)
    )
    return {
        "accounts": accounts,
        "positions": positions,
        "crypto": crypto,
        "other_assets": other,
        "totals": {
            "num_accounts": len(accounts),
            "num_tickers": len(positions),
            "num_crypto": len(crypto),
            "num_other": len(other),
        }
    }

# ── Master view ───────────────────────────────────────────────────────────

@router.get("/master")
async def master_view(
    user_id: Optional[str] = Query(None),
    user: User = Depends(get_current_user)
) -> list[dict]:
    _require_master_or_admin(user)
    return await _repo().list_all_users_summary(user_id)


# ── Master CRUD: contas de outros usuarios ────────────────────────────────

@router.get("/admin/accounts")
async def admin_list_accounts(
    user_id: Optional[str] = Query(None, description="Filtra por user_id; sem filtro = todos"),
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Master/Admin: lista contas de outro usuario (ou todos)."""
    _require_master_or_admin(user)
    if user_id:
        return await _repo().list_accounts(user_id, include_inactive)
    return await _repo().list_all_accounts(include_inactive)


@router.post("/admin/accounts", status_code=status.HTTP_201_CREATED)
async def admin_create_account(
    body: AccountCreate,
    user_id: str = Query(..., description="user_id alvo do registro"),
    actor: User = Depends(get_current_user),
) -> dict:
    """Master/Admin: cria conta para outro usuario (user_id no query)."""
    _require_master_or_admin(actor)
    data = body.model_dump()
    data["user_id"] = user_id
    return await _repo().create_account(data)


@router.get("/admin/accounts/{account_id}")
async def admin_get_account(
    account_id: str,
    actor: User = Depends(get_current_user),
) -> dict:
    """Master/Admin: detalhe de conta (qualquer user)."""
    _require_master_or_admin(actor)
    acc = await _repo().get_account_any_user(account_id)
    if not acc:
        raise HTTPException(404, "Conta nao encontrada")
    return acc


@router.patch("/admin/accounts/{account_id}")
async def admin_update_account(
    account_id: str,
    body: AccountUpdate,
    actor: User = Depends(get_current_user),
) -> dict:
    """Master/Admin: atualiza conta (qualquer user)."""
    _require_master_or_admin(actor)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    acc = await _repo().update_account_any_user(account_id, data)
    if not acc:
        raise HTTPException(404, "Conta nao encontrada")
    return acc


@router.delete("/admin/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_deactivate_account(
    account_id: str,
    actor: User = Depends(get_current_user),
) -> None:
    """Master/Admin: desativa conta (qualquer user)."""
    _require_master_or_admin(actor)
    ok = await _repo().delete_account_any_user(account_id)
    if not ok:
        raise HTTPException(404, "Conta nao encontrada")

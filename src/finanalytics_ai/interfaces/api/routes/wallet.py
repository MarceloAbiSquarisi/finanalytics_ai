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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository
from finanalytics_ai.interfaces.api.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/wallet", tags=["Carteira"])


def _repo() -> WalletRepository:
    return WalletRepository()


def _require_master_or_admin(user: User) -> User:
    if not user.has_admin_access:
        raise HTTPException(status_code=403, detail="Acesso negado: requer perfil MASTER ou ADMIN")
    return user


async def _resolve_portfolio_id(user_id: str, supplied: str | None) -> str:
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

from finanalytics_ai.domain.validation import is_valid_cpf, normalize_cpf


class AccountCreate(BaseModel):
    titular: str = Field(..., min_length=2, max_length=200, description="Nome do titular da conta")
    cpf: str = Field(..., description="CPF (com ou sem mascara)")
    institution_code: str = Field(
        ..., min_length=1, max_length=20, description="Codigo da instituicao (ex: '341' Itau)"
    )
    institution_name: str = Field(..., min_length=2, max_length=200)
    agency: str = Field(..., min_length=1, max_length=20, description="Codigo da agencia")
    account_number: str = Field(..., min_length=1, max_length=50)
    apelido: str = Field(
        ..., min_length=1, max_length=100, description="Apelido da conta (UI-friendly)"
    )
    country: str = Field("BRA", max_length=3)
    currency: str = Field("BRL", max_length=3)
    account_type: str = Field("corretora")
    note: str | None = None

    @field_validator("cpf")
    @classmethod
    def _v_cpf(cls, v: str) -> str:
        v = normalize_cpf(v)
        if not is_valid_cpf(v):
            raise ValueError("CPF invalido (DV ou tamanho)")
        return v


class AccountUpdate(BaseModel):
    titular: str | None = Field(None, min_length=2, max_length=200)
    cpf: str | None = None
    institution_code: str | None = None
    institution_name: str | None = Field(None, min_length=2)
    agency: str | None = None
    account_number: str | None = None
    apelido: str | None = Field(None, min_length=1, max_length=100)
    is_active: bool | None = None
    note: str | None = None

    @field_validator("cpf")
    @classmethod
    def _v_cpf(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = normalize_cpf(v)
        if not is_valid_cpf(v):
            raise ValueError("CPF invalido (DV ou tamanho)")
        return v

    note: str | None = None


class TradeCreate(BaseModel):
    ticker: str = Field(..., min_length=1)
    asset_class: str = Field("stock")  # stock|etf|crypto|fii|bdr
    operation: str = Field("buy")  # buy|sell|split|bonus
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    trade_date: date
    fees: Decimal = Field(Decimal("0"), ge=0)
    currency: str = Field("BRL", max_length=3)
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    note: str | None = None


class CryptoUpsert(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: Decimal = Field(..., gt=0)
    average_price_brl: Decimal = Field(..., ge=0)
    average_price_usd: Decimal | None = None
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    exchange: str | None = None
    wallet_address: str | None = None
    note: str | None = None


class OtherAssetCreate(BaseModel):
    name: str = Field(..., min_length=2)
    asset_type: str = Field("outro")  # imovel|previdencia|coe|debenture|outro
    current_value: Decimal = Field(..., ge=0)
    invested_value: Decimal | None = None
    currency: str = Field("BRL", max_length=3)
    acquisition_date: date | None = None
    maturity_date: date | None = None
    ir_exempt: bool = False
    investment_account_id: str | None = None
    portfolio_id: str | None = None
    note: str | None = None


class OtherAssetUpdate(BaseModel):
    name: str | None = None
    current_value: Decimal | None = None
    invested_value: Decimal | None = None
    maturity_date: date | None = None
    note: str | None = None


# ── Investment Accounts ───────────────────────────────────────────────────


@router.get("/accounts")
async def list_accounts(
    include_inactive: bool = False, user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().list_accounts(str(user.user_id), include_inactive)


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountCreate, user: User = Depends(get_current_user)) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    try:
        return await _repo().create_account(data)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "uq_inv_accounts_user_inst_ag_acc" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Já existe uma conta cadastrada para esta corretora "
                    "com a mesma agência e número. Verifique os dados."
                ),
            ) from exc
        if "duplicate key" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conta duplicada (constraint violada): " + msg[:200],
            ) from exc
        raise


@router.get("/accounts/{account_id}")
async def get_account(account_id: str, user: User = Depends(get_current_user)) -> dict:
    acc = await _repo().get_account(account_id, str(user.user_id))
    if not acc:
        raise HTTPException(404, "Conta não encontrada")
    return acc


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: str, body: AccountUpdate, user: User = Depends(get_current_user)
) -> dict:
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    acc = await _repo().update_account(account_id, str(user.user_id), data)
    if not acc:
        raise HTTPException(404, "Conta não encontrada")
    return acc


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_account(account_id: str, user: User = Depends(get_current_user)) -> None:
    try:
        ok = await _repo().delete_account(account_id, str(user.user_id))
    except ValueError as e:  # saldo != 0 (F7)
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    if not ok:
        raise HTTPException(404, "Conta não encontrada")


# ── Credenciais Profit DLL na conta (unificacao U3, 24/abr) ──────────────


class DLLConnectRequest(BaseModel):
    account_type: str = Field(..., description="'real' ou 'simulator'")
    broker_id: str | None = Field(None, max_length=20)
    dll_account_id: str | None = Field(None, max_length=50)
    routing_password: str | None = Field(None, max_length=200)
    sub_account_id: str | None = Field(None, max_length=50)


@router.post("/accounts/{account_id}/connect-dll")
async def connect_dll(
    account_id: str,
    body: DLLConnectRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Conecta credenciais Profit DLL a uma conta de investimento.

    Para account_type='simulator', broker_id/dll_account_id/password
    sao ignorados — o profit_agent usa fallback PROFIT_SIM_* do .env.
    Apenas uma conta 'simulator' pode existir no sistema (unique index).

    account_type e imutavel apos a primeira conexao — se precisar mudar,
    desconecte primeiro via /disconnect-dll.
    """
    try:
        data = await _repo().connect_dll(
            account_id=account_id,
            user_id=str(user.user_id),
            account_type=body.account_type,
            broker_id=body.broker_id,
            dll_account_id=body.dll_account_id,
            routing_password=body.routing_password,
            sub_account_id=body.sub_account_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    if not data:
        raise HTTPException(404, "Conta não encontrada")
    return data


@router.post("/accounts/{account_id}/disconnect-dll")
async def disconnect_dll(account_id: str, user: User = Depends(get_current_user)) -> dict:
    """Remove credenciais Profit DLL de uma conta (e desativa se ativa)."""
    data = await _repo().disconnect_dll(account_id, str(user.user_id))
    if not data:
        raise HTTPException(404, "Conta não encontrada")
    return data


@router.post("/accounts/{account_id}/activate-dll")
async def activate_dll(account_id: str, user: User = Depends(get_current_user)) -> dict:
    """Marca a conta como DLL ativa (desativa qualquer outra do mesmo user)."""
    try:
        data = await _repo().set_dll_active(account_id, str(user.user_id))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    if not data:
        raise HTTPException(404, "Conta não encontrada")
    return data


class RealOperationsRequest(BaseModel):
    allowed: bool = Field(..., description="TRUE libera envio de ordens reais pela conta")


# ── Feature C: cash ledger (depositos, saques, resumo) ────────────────────


class CashMoveRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Valor em BRL (sempre positivo)")
    reference_date: str | None = Field(None, description="YYYY-MM-DD — default hoje")
    note: str | None = Field(None, max_length=300)


@router.post("/accounts/{account_id}/deposit")
async def deposit(
    account_id: str,
    body: CashMoveRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Credita cash na conta. Status=settled imediato (dinheiro ja entrou)."""
    from datetime import date as _date
    ref = _date.fromisoformat(body.reference_date) if body.reference_date else _date.today()
    from decimal import Decimal as _Dec
    tx = await _repo().create_transaction(
        user_id=str(user.user_id),
        account_id=account_id,
        tx_type="deposit",
        amount=_Dec(str(body.amount)),
        reference_date=ref,
        settlement_date=ref,
        status="settled",
        note=body.note,
    )
    return tx


@router.post("/accounts/{account_id}/withdraw")
async def withdraw(
    account_id: str,
    body: CashMoveRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Debita cash da conta. Permite saldo negativo (com aviso no response)."""
    from datetime import date as _date
    from decimal import Decimal as _Dec

    ref = _date.fromisoformat(body.reference_date) if body.reference_date else _date.today()
    amount = -_Dec(str(body.amount))  # debito = negativo
    summary = await _repo().get_cash_summary(account_id, str(user.user_id))
    if not summary:
        raise HTTPException(404, "Conta não encontrada")
    will_be = summary["cash_balance"] + float(amount)
    tx = await _repo().create_transaction(
        user_id=str(user.user_id),
        account_id=account_id,
        tx_type="withdraw",
        amount=amount,
        reference_date=ref,
        settlement_date=ref,
        status="settled",
        note=body.note,
    )
    tx["warning"] = (
        f"Saldo ficará negativo (R$ {will_be:.2f}). Considere aportar antes."
        if will_be < 0
        else None
    )
    return tx


@router.get("/accounts/{account_id}/cash-summary")
async def cash_summary(account_id: str, user: User = Depends(get_current_user)) -> dict:
    """cash_balance + pending_in + pending_out + available_to_invest."""
    data = await _repo().get_cash_summary(account_id, str(user.user_id))
    if not data:
        raise HTTPException(404, "Conta não encontrada")
    return data


@router.get("/accounts/{account_id}/transactions")
async def list_account_transactions(
    account_id: str,
    status_filter: str | None = Query(None, alias="status", description="pending|settled|cancelled"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    date_from: str | None = Query(None, description="YYYY-MM-DD — default sem filtro"),
    date_to: str | None = Query(None, description="YYYY-MM-DD — default hoje"),
    direction: str | None = Query(None, pattern="^(debit|credit)$", description="debit=saídas, credit=entradas"),
    include_pending: bool = Query(True, description="Incluir lançamentos futuros (pending)"),
    user: User = Depends(get_current_user),
) -> list[dict]:
    from datetime import date as _date
    df = _date.fromisoformat(date_from) if date_from else None
    dt = _date.fromisoformat(date_to) if date_to else None
    return await _repo().list_transactions(
        user_id=str(user.user_id),
        account_id=account_id,
        status=status_filter,
        limit=limit,
        offset=offset,
        date_from=df,
        date_to=dt,
        direction=direction,
        include_pending=include_pending,
    )


@router.post("/transactions/{tx_id}/cancel")
async def cancel_transaction(tx_id: str, user: User = Depends(get_current_user)) -> dict:
    """Cancela tx. Se settled, reverte o efeito no cash_balance."""
    tx = await _repo().cancel_transaction(tx_id, str(user.user_id))
    if not tx:
        raise HTTPException(404, "Transação não encontrada")
    return tx


class CreatePortfolioForAccount(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


@router.post("/accounts/{account_id}/portfolios", status_code=status.HTTP_201_CREATED)
async def create_portfolio_for_account(
    account_id: str,
    body: CreatePortfolioForAccount,
    user: User = Depends(get_current_user),
) -> dict:
    """G2 (24/abr): cria portfolio ja vinculado a esta investment_account.
    Permite criar portfolios extras alem dos 2 auto-criados (Principal + RF)."""
    data = await _repo().create_portfolio_in_account(
        user_id=str(user.user_id),
        account_id=account_id,
        name=body.name,
    )
    if not data:
        raise HTTPException(404, "Conta não encontrada ou inativa.")
    return data


@router.patch("/accounts/{account_id}/real-operations")
async def set_real_operations(
    account_id: str,
    body: RealOperationsRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """ADMIN/MASTER-only: libera ou bloqueia envio de ordens REAIS para esta conta.

    Motivacao: cada conta real na Nelogica consome 1 licenca separada + expoe
    risco financeiro real. Por default a flag e FALSE — o usuario comum pode
    conectar DLL real mas nao consegue enviar ordens ate um admin liberar.
    """
    if not user.has_admin_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas ADMIN ou MASTER pode alterar permissao de operacoes reais.",
        )
    data = await _repo().set_real_operations(account_id, str(user.user_id), body.allowed)
    if not data:
        raise HTTPException(404, "Conta não encontrada")
    return data


# ── Trades ────────────────────────────────────────────────────────────────


@router.get("/trades")
async def list_trades(
    ticker: str | None = None,
    asset_class: str | None = None,
    account_id: str | None = None,
    portfolio_id: str | None = None,
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await _repo().list_trades(
        str(user.user_id), ticker, asset_class, account_id, portfolio_id=portfolio_id
    )


@router.post("/trades", status_code=status.HTTP_201_CREATED)
async def create_trade(body: TradeCreate, user: User = Depends(get_current_user)) -> dict:
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
async def delete_trade(trade_id: str, user: User = Depends(get_current_user)) -> None:
    ok = await _repo().delete_trade(trade_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Trade não encontrado")


# ── Positions (preço médio calculado) ────────────────────────────────────


@router.get("/positions")
async def get_positions(
    asset_class: str | None = None,
    portfolio_id: str | None = None,
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await _repo().get_positions_summary(
        str(user.user_id), asset_class, portfolio_id=portfolio_id
    )


# ── Crypto ────────────────────────────────────────────────────────────────


@router.get("/crypto")
async def list_crypto(
    portfolio_id: str | None = None, user: User = Depends(get_current_user)
) -> list[dict]:
    return await _repo().list_crypto(str(user.user_id), portfolio_id=portfolio_id)


@router.put("/crypto", status_code=status.HTTP_200_OK)
async def upsert_crypto(body: CryptoUpsert, user: User = Depends(get_current_user)) -> dict:
    data = body.model_dump()
    data["user_id"] = str(user.user_id)
    data["portfolio_id"] = await _resolve_portfolio_id(str(user.user_id), data.get("portfolio_id"))
    for k in ("quantity", "average_price_brl", "average_price_usd"):
        if data[k] is not None:
            data[k] = float(data[k])
    return await _repo().upsert_crypto(data)


class CryptoRedeemRequest(BaseModel):
    quantity: Decimal = Field(..., gt=0, description="Quantidade resgatada (decremento da posicao)")


@router.post("/crypto/{crypto_id}/redeem")
async def redeem_crypto(
    crypto_id: str, body: CryptoRedeemRequest, user: User = Depends(get_current_user)
) -> dict:
    """Resgate parcial de cripto. Decrementa quantity. Se zerar, deleta o holding."""
    result = await _repo().redeem_crypto(crypto_id, str(user.user_id), float(body.quantity))
    if result is None:
        raise HTTPException(404, "Cripto não encontrada")
    if result.get("removed"):
        return {"status": "removed", "remaining_quantity": 0}
    return {"status": "redeemed", **result}


@router.delete("/crypto/{crypto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_crypto(crypto_id: str, user: User = Depends(get_current_user)) -> None:
    ok = await _repo().delete_crypto(crypto_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Cripto não encontrada")


# ── Other Assets ──────────────────────────────────────────────────────────


@router.get("/other")
async def list_other(
    asset_type: str | None = None,
    portfolio_id: str | None = None,
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await _repo().list_other_assets(str(user.user_id), asset_type, portfolio_id=portfolio_id)


@router.post("/other", status_code=status.HTTP_201_CREATED)
async def create_other(body: OtherAssetCreate, user: User = Depends(get_current_user)) -> dict:
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
    asset_id: str, body: OtherAssetUpdate, user: User = Depends(get_current_user)
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
async def delete_other(asset_id: str, user: User = Depends(get_current_user)) -> None:
    ok = await _repo().delete_other_asset(asset_id, str(user.user_id))
    if not ok:
        raise HTTPException(404, "Ativo não encontrado")


# ── Summary ───────────────────────────────────────────────────────────────


@router.get("/summary")
async def wallet_summary(
    portfolio_id: str | None = None, user: User = Depends(get_current_user)
) -> dict:
    uid = str(user.user_id)
    repo = _repo()
    accounts, positions, crypto, other = await __import__("asyncio").gather(
        repo.list_accounts(uid),
        repo.get_positions_summary(uid, portfolio_id=portfolio_id),
        repo.list_crypto(uid, portfolio_id=portfolio_id),
        repo.list_other_assets(uid, portfolio_id=portfolio_id),
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
        },
        "portfolio_id": portfolio_id,
    }


# ── Master view ───────────────────────────────────────────────────────────


@router.get("/master")
async def master_view(
    user_id: str | None = Query(None), user: User = Depends(get_current_user)
) -> list[dict]:
    _require_master_or_admin(user)
    return await _repo().list_all_users_summary(user_id)


# ── Master CRUD: contas de outros usuarios ────────────────────────────────


@router.get("/admin/accounts")
async def admin_list_accounts(
    user_id: str | None = Query(None, description="Filtra por user_id; sem filtro = todos"),
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

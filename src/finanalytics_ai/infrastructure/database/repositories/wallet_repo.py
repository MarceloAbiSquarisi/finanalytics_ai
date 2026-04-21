"""
infrastructure/database/repositories/wallet_repo.py
Repositório para contas de investimento, trades, cripto e outros ativos.
"""
from __future__ import annotations
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Any
import structlog
from sqlalchemy import String, Boolean, Numeric, Date, DateTime, Text, func, select, delete, text
from sqlalchemy.orm import Mapped, mapped_column
from finanalytics_ai.infrastructure.database.connection import Base, get_session

log = structlog.get_logger(__name__)


# ── SQLAlchemy Models ─────────────────────────────────────────────────────

class InvestmentAccountModel(Base):
    __tablename__ = "investment_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    titular: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    apelido: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    institution_name: Mapped[str] = mapped_column(String(200), nullable=False)
    institution_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    agency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    account_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    country: Mapped[str] = mapped_column(String(3), nullable=False, default="BRA")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    account_type: Mapped[str] = mapped_column(String(30), nullable=False, default="corretora")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TradeModel(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(30), nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CryptoHoldingModel(Base):
    __tablename__ = "crypto_holdings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    average_price_brl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    average_price_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    wallet_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OtherAssetModel(Base):
    __tablename__ = "other_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    investment_account_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    portfolio_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    current_value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    invested_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    acquisition_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ir_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Helper ────────────────────────────────────────────────────────────────

def _model_to_dict(m: Any) -> dict:
    d = {}
    for c in m.__table__.columns:
        v = getattr(m, c.name)
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()
        d[c.name] = v
    return d


# ── WalletRepository ──────────────────────────────────────────────────────

class WalletRepository:
    """Repo unificado para carteira multi-usuário."""

    # ── Investment Accounts ───────────────────────────────────────────────

    async def create_account(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            m = InvestmentAccountModel(**{k: v for k, v in data.items()
                                         if k in InvestmentAccountModel.__table__.columns.keys()})
            s.add(m)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_accounts(self, user_id: str, include_inactive: bool = False) -> list[dict]:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(InvestmentAccountModel.user_id == user_id)
            if not include_inactive:
                q = q.where(InvestmentAccountModel.is_active.is_(True))
            q = q.order_by(InvestmentAccountModel.institution_name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def get_account(self, account_id: str, user_id: str) -> dict | None:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            return _model_to_dict(m) if m else None

    async def update_account(self, account_id: str, user_id: str, data: dict) -> dict | None:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def delete_account(self, account_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(InvestmentAccountModel).where(
                InvestmentAccountModel.id == account_id,
                InvestmentAccountModel.user_id == user_id,
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            m.is_active = False
            await s.commit()
            return True

    # ── Variantes Master/Admin (sem filtro user_id) ───────────────────────

    async def list_all_accounts(self, include_inactive: bool = False) -> list[dict]:
        async with get_session() as s:
            q = select(InvestmentAccountModel)
            if not include_inactive:
                q = q.where(InvestmentAccountModel.is_active.is_(True))
            q = q.order_by(InvestmentAccountModel.user_id,
                           InvestmentAccountModel.institution_name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def get_account_any_user(self, account_id: str) -> dict | None:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            return _model_to_dict(m) if m else None

    async def update_account_any_user(self, account_id: str, data: dict) -> dict | None:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def delete_account_any_user(self, account_id: str) -> bool:
        async with get_session() as s:
            m = await s.get(InvestmentAccountModel, account_id)
            if not m:
                return False
            m.is_active = False
            await s.commit()
            return True

    # ── Portfolio resolution ─────────────────────────────────────────────

    async def get_default_portfolio_id(self, user_id: str) -> str | None:
        """Retorna id do portfolio default do usuario, ou None se nao existir."""
        async with get_session() as s:
            r = await s.execute(text(
                "SELECT id FROM portfolios WHERE user_id=:u AND is_default=true LIMIT 1"
            ), {"u": user_id})
            row = r.first()
            if row:
                return row[0]
            r2 = await s.execute(text(
                "SELECT id FROM portfolios WHERE user_id=:u ORDER BY created_at LIMIT 1"
            ), {"u": user_id})
            row2 = r2.first()
            return row2[0] if row2 else None

    async def ensure_default_portfolio(self, user_id: str, name: str = "Carteira Principal") -> str:
        """Garante portfolio para o usuario; cria 'Carteira Principal' como
        is_default=true se nenhum existir. Retorna sempre o id."""
        existing = await self.get_default_portfolio_id(user_id)
        if existing:
            return existing
        new_id = str(uuid.uuid4())
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO portfolios (id, user_id, name, currency, cash, is_default)
                VALUES (:id, :u, :n, 'BRL', 0, true)
            """), {"id": new_id, "u": user_id, "n": name})
            await s.commit()
        return new_id

    async def validate_portfolio_belongs_to_user(self, portfolio_id: str, user_id: str) -> bool:
        async with get_session() as s:
            r = await s.execute(text(
                "SELECT 1 FROM portfolios WHERE id=:p AND user_id=:u"
            ), {"p": portfolio_id, "u": user_id})
            return r.first() is not None

    # ── Trades ────────────────────────────────────────────────────────────

    async def create_trade(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        if "total_cost" not in data:
            data["total_cost"] = float(data["quantity"]) * float(data["unit_price"]) + float(data.get("fees", 0))
        async with get_session() as s:
            m = TradeModel(**{k: v for k, v in data.items()
                              if k in TradeModel.__table__.columns.keys()})
            s.add(m)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_trades(self, user_id: str, ticker: str | None = None,
                          asset_class: str | None = None,
                          account_id: str | None = None,
                          portfolio_id: str | None = None) -> list[dict]:
        async with get_session() as s:
            q = select(TradeModel).where(TradeModel.user_id == user_id)
            if ticker:
                q = q.where(TradeModel.ticker == ticker.upper())
            if asset_class:
                q = q.where(TradeModel.asset_class == asset_class)
            if account_id:
                q = q.where(TradeModel.investment_account_id == account_id)
            if portfolio_id:
                q = q.where(TradeModel.portfolio_id == portfolio_id)
            q = q.order_by(TradeModel.trade_date.desc())
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def delete_trade(self, trade_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(TradeModel).where(
                TradeModel.id == trade_id, TradeModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            await s.delete(m)
            await s.commit()
            return True

    async def get_positions_summary(self, user_id: str,
                                    asset_class: str | None = None,
                                    portfolio_id: str | None = None) -> list[dict]:
        """Calcula posição consolidada (preço médio) por ticker."""
        trades = await self.list_trades(
            user_id, asset_class=asset_class, portfolio_id=portfolio_id
        )
        from collections import defaultdict
        by_ticker: dict[str, list] = defaultdict(list)
        for t in trades:
            by_ticker[t["ticker"]].append(t)
        result = []
        for ticker, tlist in sorted(by_ticker.items()):
            total_qty = Decimal("0")
            total_cost = Decimal("0")
            for t in sorted(tlist, key=lambda x: x["trade_date"]):
                qty = Decimal(str(t["quantity"]))
                cost = Decimal(str(t["total_cost"]))
                op = t["operation"]
                if op == "buy":
                    total_qty += qty
                    total_cost += cost
                elif op == "sell" and total_qty > 0:
                    avg = total_cost / total_qty
                    total_qty -= qty
                    total_cost = avg * total_qty
                elif op in ("split", "bonus"):
                    total_qty += qty
            if total_qty > 0:
                avg_price = float(total_cost / total_qty)
                result.append({
                    "ticker": ticker,
                    "asset_class": tlist[0]["asset_class"],
                    "quantity": float(total_qty),
                    "average_price": round(avg_price, 6),
                    "total_invested": float(total_cost),
                    "trade_count": len(tlist),
                })
        return result

    # ── Crypto ────────────────────────────────────────────────────────────

    async def upsert_crypto(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.user_id == data["user_id"],
                CryptoHoldingModel.symbol == data["symbol"].upper(),
                CryptoHoldingModel.investment_account_id == data.get("investment_account_id"),
            )
            m = (await s.execute(q)).scalar_one_or_none()
            if m:
                for k, v in data.items():
                    if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                        setattr(m, k, v)
            else:
                data["symbol"] = data["symbol"].upper()
                m = CryptoHoldingModel(**{k: v for k, v in data.items()
                                          if k in CryptoHoldingModel.__table__.columns.keys()})
                s.add(m)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_crypto(self, user_id: str,
                          portfolio_id: str | None = None) -> list[dict]:
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.user_id == user_id)
            if portfolio_id:
                q = q.where(CryptoHoldingModel.portfolio_id == portfolio_id)
            q = q.order_by(CryptoHoldingModel.symbol)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def delete_crypto(self, crypto_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.id == crypto_id,
                CryptoHoldingModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            await s.delete(m)
            await s.commit()
            return True

    async def redeem_crypto(
        self, crypto_id: str, user_id: str, qty: float
    ) -> dict | None:
        """Decrementa quantity. Se chegar a zero ou negativo, remove o holding."""
        async with get_session() as s:
            q = select(CryptoHoldingModel).where(
                CryptoHoldingModel.id == crypto_id,
                CryptoHoldingModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            new_qty = float(m.quantity) - qty
            if new_qty <= 0:
                await s.delete(m)
                await s.commit()
                return {"removed": True, "remaining_quantity": 0}
            m.quantity = new_qty
            await s.commit()
            await s.refresh(m)
            return {"removed": False, "remaining_quantity": float(m.quantity)}

    # ── Other Assets ──────────────────────────────────────────────────────

    async def create_other_asset(self, data: dict) -> dict:
        data.setdefault("id", str(uuid.uuid4()))
        async with get_session() as s:
            m = OtherAssetModel(**{k: v for k, v in data.items()
                                   if k in OtherAssetModel.__table__.columns.keys()})
            s.add(m)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def list_other_assets(self, user_id: str,
                                 asset_type: str | None = None,
                                 portfolio_id: str | None = None) -> list[dict]:
        async with get_session() as s:
            q = select(OtherAssetModel).where(OtherAssetModel.user_id == user_id)
            if asset_type:
                q = q.where(OtherAssetModel.asset_type == asset_type)
            if portfolio_id:
                q = q.where(OtherAssetModel.portfolio_id == portfolio_id)
            q = q.order_by(OtherAssetModel.name)
            rows = (await s.execute(q)).scalars().all()
            return [_model_to_dict(r) for r in rows]

    async def update_other_asset(self, asset_id: str, user_id: str, data: dict) -> dict | None:
        async with get_session() as s:
            q = select(OtherAssetModel).where(
                OtherAssetModel.id == asset_id, OtherAssetModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return None
            for k, v in data.items():
                if hasattr(m, k) and k not in ("id", "user_id", "created_at"):
                    setattr(m, k, v)
            await s.commit()
            await s.refresh(m)
            return _model_to_dict(m)

    async def delete_other_asset(self, asset_id: str, user_id: str) -> bool:
        async with get_session() as s:
            q = select(OtherAssetModel).where(
                OtherAssetModel.id == asset_id, OtherAssetModel.user_id == user_id)
            m = (await s.execute(q)).scalar_one_or_none()
            if not m:
                return False
            await s.delete(m)
            await s.commit()
            return True

    # ── Master view ───────────────────────────────────────────────────────

    async def list_all_users_summary(self, target_user_id: str | None = None) -> list[dict]:
        """Visão master: totais consolidados por usuário (somente leitura)."""
        async with get_session() as s:
            from sqlalchemy import text
            sql = text("""
                SELECT u.user_id,
                       COUNT(DISTINCT ia.id) as num_accounts,
                       COUNT(DISTINCT t.id) as num_trades,
                       COUNT(DISTINCT ch.id) as num_crypto,
                       COUNT(DISTINCT oa.id) as num_other_assets
                FROM (SELECT DISTINCT user_id FROM investment_accounts) u
                LEFT JOIN investment_accounts ia ON ia.user_id = u.user_id AND ia.is_active
                LEFT JOIN trades t ON t.user_id = u.user_id
                LEFT JOIN crypto_holdings ch ON ch.user_id = u.user_id
                LEFT JOIN other_assets oa ON oa.user_id = u.user_id
                WHERE (:uid IS NULL OR u.user_id = :uid)
                GROUP BY u.user_id
                ORDER BY u.user_id
            """)
            rows = (await s.execute(sql, {"uid": target_user_id})).fetchall()
            return [dict(r._mapping) for r in rows]

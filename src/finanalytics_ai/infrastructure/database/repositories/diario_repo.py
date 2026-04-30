"""
Repositório do Diário de Trade.

Modelo: trade_journal — armazena entradas qualitativas e quantitativas.
Usa a mesma Base do resto do projeto para aproveitar o create_all() no startup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, desc, extract, func, select
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.infrastructure.database.connection import Base

logger = structlog.get_logger(__name__)


# ── Modelo ────────────────────────────────────────────────────────────────────


class DiarioModel(Base):
    __tablename__ = "trade_journal"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="user-demo", index=True
    )

    # Dados quantitativos
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY / SELL
    entry_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    exit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)

    # Setup
    setup: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trade_objective: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # daytrade|swing|buy_hold

    # Qualitativo
    reason_entry: Mapped[str | None] = mapped_column(Text, nullable=True)
    expectation: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_happened: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotional_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[str | None] = mapped_column(String(500), nullable=True)  # comma-separated

    # Calculados (armazenados para queries eficientes)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_winner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Workflow de preenchimento qualitativo
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ticker": self.ticker,
            "direction": self.direction,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "setup": self.setup,
            "timeframe": self.timeframe,
            "trade_objective": self.trade_objective,
            "reason_entry": self.reason_entry,
            "expectation": self.expectation,
            "what_happened": self.what_happened,
            "emotional_state": self.emotional_state,
            "mistakes": self.mistakes,
            "lessons": self.lessons,
            "rating": self.rating,
            "tags": self.tags.split(",") if self.tags else [],
            "pnl": round(self.pnl, 2) if self.pnl is not None else None,
            "pnl_pct": round(self.pnl_pct, 4) if self.pnl_pct is not None else None,
            "is_winner": self.is_winner,
            "is_complete": bool(self.is_complete),
            "external_order_id": self.external_order_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Repositório ───────────────────────────────────────────────────────────────


class DiarioRepository:
    """CRUD assíncrono para o diário de trade."""

    def __init__(self, session_factory: Any) -> None:
        self._session = session_factory

    @staticmethod
    def _compute_pnl(
        entry_price: float,
        exit_price: float | None,
        quantity: float,
        direction: str,
    ) -> tuple[float | None, float | None, bool | None]:
        """Calcula P&L, P&L% e is_winner."""
        if exit_price is None:
            return None, None, None
        if direction == "BUY":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity
        pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity != 0 else 0.0
        return round(pnl, 2), round(pnl_pct, 4), pnl > 0

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        pnl, pnl_pct, is_winner = self._compute_pnl(
            data.get("entry_price", 0),
            data.get("exit_price"),
            data.get("quantity", 0),
            data.get("direction", "BUY"),
        )
        tags = data.get("tags", [])
        tags_str = ",".join(str(t).strip() for t in tags if t) if tags else None

        async with self._session() as session:
            entry = DiarioModel(
                id=str(uuid4()),
                user_id=data.get("user_id", "user-demo"),
                ticker=data["ticker"].upper(),
                direction=data.get("direction", "BUY").upper(),
                entry_date=data["entry_date"],
                exit_date=data.get("exit_date"),
                entry_price=float(data["entry_price"]),
                exit_price=float(data["exit_price"]) if data.get("exit_price") else None,
                quantity=float(data["quantity"]),
                setup=data.get("setup"),
                timeframe=data.get("timeframe"),
                trade_objective=data.get("trade_objective"),
                reason_entry=data.get("reason_entry"),
                expectation=data.get("expectation"),
                what_happened=data.get("what_happened"),
                emotional_state=data.get("emotional_state"),
                mistakes=data.get("mistakes"),
                lessons=data.get("lessons"),
                rating=data.get("rating"),
                tags=tags_str,
                pnl=pnl,
                pnl_pct=pnl_pct,
                is_winner=is_winner,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry.to_dict()

    async def get(self, entry_id: str, user_id: str = "user-demo") -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(DiarioModel).where(
                    DiarioModel.id == entry_id,
                    DiarioModel.user_id == user_id,
                )
            )
            entry = result.scalar_one_or_none()
            return entry.to_dict() if entry else None

    async def list(
        self,
        user_id: str = "user-demo",
        ticker: str | None = None,
        setup: str | None = None,
        direction: str | None = None,
        trade_objective: str | None = None,
        is_complete: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            q = select(DiarioModel).where(DiarioModel.user_id == user_id)
            if ticker:
                q = q.where(DiarioModel.ticker == ticker.upper())
            if setup:
                q = q.where(DiarioModel.setup == setup)
            if direction:
                q = q.where(DiarioModel.direction == direction.upper())
            if trade_objective:
                q = q.where(DiarioModel.trade_objective == trade_objective)
            if is_complete is not None:
                q = q.where(DiarioModel.is_complete == is_complete)
            q = q.order_by(desc(DiarioModel.entry_date)).limit(limit).offset(offset)
            result = await session.execute(q)
            return [r.to_dict() for r in result.scalars().all()]

    async def update(
        self, entry_id: str, data: dict[str, Any], user_id: str = "user-demo"
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(DiarioModel).where(
                    DiarioModel.id == entry_id,
                    DiarioModel.user_id == user_id,
                )
            )
            entry = result.scalar_one_or_none()
            if not entry:
                return None

            updatable = [
                "ticker",
                "direction",
                "entry_date",
                "exit_date",
                "entry_price",
                "exit_price",
                "quantity",
                "setup",
                "timeframe",
                "trade_objective",
                "reason_entry",
                "expectation",
                "what_happened",
                "emotional_state",
                "mistakes",
                "lessons",
                "rating",
            ]
            for field in updatable:
                if field in data:
                    val = data[field]
                    if field == "ticker" and isinstance(val, str):
                        val = val.upper()
                    if field == "direction" and isinstance(val, str):
                        val = val.upper()
                    if field in ("entry_price", "exit_price", "quantity") and val is not None:
                        val = float(val)
                    setattr(entry, field, val)

            if "tags" in data:
                tags = data["tags"]
                entry.tags = ",".join(str(t).strip() for t in tags if t) if tags else None

            # Recalcula P&L
            pnl, pnl_pct, is_winner = self._compute_pnl(
                entry.entry_price, entry.exit_price, entry.quantity, entry.direction
            )
            entry.pnl = pnl
            entry.pnl_pct = pnl_pct
            entry.is_winner = is_winner
            entry.updated_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(entry)
            return entry.to_dict()

    async def set_complete(
        self, entry_id: str, value: bool, user_id: str = "user-demo"
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(DiarioModel).where(
                    DiarioModel.id == entry_id,
                    DiarioModel.user_id == user_id,
                )
            )
            entry = result.scalar_one_or_none()
            if not entry:
                return None
            entry.is_complete = bool(value)
            entry.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(entry)
            return entry.to_dict()

    async def count_incomplete(self, user_id: str = "user-demo") -> int:
        async with self._session() as session:
            n = await session.scalar(
                select(func.count()).where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.is_complete == False,  # noqa: E712
                )
            )
            return int(n or 0)

    async def create_from_fill(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Cria entry a partir de fill da DLL.

        Idempotente por external_order_id: se ja existir entry com mesmo
        external_order_id, retorna (entry_existente, False). Caso contrario,
        cria com is_complete=False e retorna (nova_entry, True).
        """
        ext = data.get("external_order_id")
        if not ext:
            raise ValueError("create_from_fill requer external_order_id")
        async with self._session() as session:
            result = await session.execute(
                select(DiarioModel).where(DiarioModel.external_order_id == ext)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return (existing.to_dict(), False)

            entry = DiarioModel(
                id=str(uuid4()),
                user_id=data.get("user_id", "user-demo"),
                ticker=str(data["ticker"]).upper(),
                direction=str(data.get("direction", "BUY")).upper(),
                entry_date=data["entry_date"],
                exit_date=None,
                entry_price=float(data["entry_price"]),
                exit_price=None,
                quantity=float(data["quantity"]),
                setup=None,
                timeframe=data.get("timeframe"),
                trade_objective=data.get("trade_objective"),
                reason_entry=None,
                expectation=None,
                what_happened=None,
                emotional_state=None,
                mistakes=None,
                lessons=None,
                rating=None,
                tags=None,
                pnl=None,
                pnl_pct=None,
                is_winner=None,
                is_complete=False,
                external_order_id=ext,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return (entry.to_dict(), True)

    async def delete(self, entry_id: str, user_id: str = "user-demo") -> bool:
        async with self._session() as session:
            result = await session.execute(
                select(DiarioModel).where(
                    DiarioModel.id == entry_id,
                    DiarioModel.user_id == user_id,
                )
            )
            entry = result.scalar_one_or_none()
            if not entry:
                return False
            await session.delete(entry)
            await session.commit()
            return True

    async def stats(
        self,
        user_id: str = "user-demo",
        trade_objective: str | None = None,
    ) -> dict[str, Any]:
        """Métricas agregadas para o dashboard do diário.

        Quando trade_objective é informado, totais/by_setup/by_emotion/equity_curve
        são filtrados por aquele objetivo. by_objective sempre retorna todos
        (é o eixo de comparação).
        """
        # Filtro de objetivo aplicado em todas as queries exceto by_objective
        obj_filter = (DiarioModel.trade_objective == trade_objective,) if trade_objective else ()

        async with self._session() as session:
            # Totais
            total = await session.scalar(
                select(func.count()).where(DiarioModel.user_id == user_id, *obj_filter)
            )
            closed = await session.scalar(
                select(func.count()).where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.exit_price.is_not(None),
                    *obj_filter,
                )
            )
            winners = await session.scalar(
                select(func.count()).where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.is_winner == True,  # noqa: E712
                    *obj_filter,
                )
            )
            total_pnl = await session.scalar(
                select(func.sum(DiarioModel.pnl)).where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.pnl.is_not(None),
                    *obj_filter,
                )
            )
            avg_rating = await session.scalar(
                select(func.avg(DiarioModel.rating)).where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.rating.is_not(None),
                    *obj_filter,
                )
            )

            # Performance por setup
            setup_q = await session.execute(
                select(
                    DiarioModel.setup,
                    func.count().label("trades"),
                    func.sum(DiarioModel.pnl).label("total_pnl"),
                    func.avg(DiarioModel.pnl_pct).label("avg_pnl_pct"),
                    func.sum(
                        func.cast(DiarioModel.is_winner == True, Integer)  # noqa: E712
                    ).label("wins"),
                )
                .where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.setup.is_not(None),
                    DiarioModel.pnl.is_not(None),
                    *obj_filter,
                )
                .group_by(DiarioModel.setup)
                .order_by(desc(func.sum(DiarioModel.pnl)))
            )
            by_setup = [
                {
                    "setup": r.setup,
                    "trades": r.trades,
                    "total_pnl": round(float(r.total_pnl or 0), 2),
                    "avg_pnl_pct": round(float(r.avg_pnl_pct or 0), 2),
                    "win_rate": round(float(r.wins or 0) / r.trades * 100, 1) if r.trades else 0,
                }
                for r in setup_q.all()
            ]

            # Performance por objetivo (Day Trade / Swing / Buy & Hold)
            obj_q = await session.execute(
                select(
                    DiarioModel.trade_objective,
                    func.count().label("trades"),
                    func.sum(DiarioModel.pnl).label("total_pnl"),
                    func.avg(DiarioModel.pnl_pct).label("avg_pnl_pct"),
                    func.sum(
                        func.cast(DiarioModel.is_winner == True, Integer)  # noqa: E712
                    ).label("wins"),
                )
                .where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.trade_objective.is_not(None),
                    DiarioModel.pnl.is_not(None),
                )
                .group_by(DiarioModel.trade_objective)
                .order_by(desc(func.sum(DiarioModel.pnl)))
            )
            by_objective = [
                {
                    "objective": r.trade_objective,
                    "trades": r.trades,
                    "total_pnl": round(float(r.total_pnl or 0), 2),
                    "avg_pnl_pct": round(float(r.avg_pnl_pct or 0), 2),
                    "win_rate": round(float(r.wins or 0) / r.trades * 100, 1) if r.trades else 0,
                }
                for r in obj_q.all()
            ]

            # Distribuição emocional
            emotion_q = await session.execute(
                select(
                    DiarioModel.emotional_state,
                    func.count().label("count"),
                    func.avg(DiarioModel.pnl).label("avg_pnl"),
                )
                .where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.emotional_state.is_not(None),
                    *obj_filter,
                )
                .group_by(DiarioModel.emotional_state)
                .order_by(desc(func.count()))
            )
            by_emotion = [
                {
                    "state": r.emotional_state,
                    "count": r.count,
                    "avg_pnl": round(float(r.avg_pnl or 0), 2),
                }
                for r in emotion_q.all()
            ]

            # Equity curve (P&L acumulado por data)
            curve_q = await session.execute(
                select(
                    DiarioModel.exit_date,
                    DiarioModel.pnl,
                )
                .where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.pnl.is_not(None),
                    DiarioModel.exit_date.is_not(None),
                    *obj_filter,
                )
                .order_by(DiarioModel.exit_date)
            )
            equity = 0.0
            equity_curve = []
            for r in curve_q.all():
                equity += float(r.pnl or 0)
                equity_curve.append(
                    {
                        "date": r.exit_date.isoformat() if r.exit_date else None,
                        "equity": round(equity, 2),
                        "pnl": round(float(r.pnl or 0), 2),
                    }
                )

            win_rate = round(float(winners or 0) / closed * 100, 1) if closed else 0.0
            return {
                "total_entries": total or 0,
                "closed_trades": closed or 0,
                "open_trades": (total or 0) - (closed or 0),
                "winners": winners or 0,
                "losers": (closed or 0) - (winners or 0),
                "win_rate": win_rate,
                "total_pnl": round(float(total_pnl or 0), 2),
                "avg_rating": round(float(avg_rating or 0), 1),
                "by_setup": by_setup,
                "by_objective": by_objective,
                "by_emotion": by_emotion,
                "equity_curve": equity_curve,
            }

    async def monthly_heatmap(
        self,
        user_id: str = "user-demo",
        trade_objective: str | None = None,
    ) -> dict[str, Any]:
        """Matriz year × month de P&L (estilo planilha Stormer "Resumo dos trades").

        Inclui contagem de trades por celula para tooltip e somatorios
        marginais (por ano e por mes agregado em todos os anos).
        """
        obj_filter = (DiarioModel.trade_objective == trade_objective,) if trade_objective else ()
        async with self._session() as session:
            q = await session.execute(
                select(
                    extract("year", DiarioModel.exit_date).label("yr"),
                    extract("month", DiarioModel.exit_date).label("mo"),
                    func.sum(DiarioModel.pnl).label("pnl"),
                    func.count().label("trades"),
                    func.sum(
                        func.cast(DiarioModel.is_winner == True, Integer)  # noqa: E712
                    ).label("wins"),
                )
                .where(
                    DiarioModel.user_id == user_id,
                    DiarioModel.exit_date.is_not(None),
                    DiarioModel.pnl.is_not(None),
                    *obj_filter,
                )
                .group_by("yr", "mo")
                .order_by("yr", "mo")
            )

            # Estrutura: { year: { month: {pnl, trades, win_rate} } }
            by_year: dict[int, dict[int, dict[str, Any]]] = {}
            year_totals: dict[int, dict[str, Any]] = {}
            month_totals: dict[int, dict[str, Any]] = {
                m: {"pnl": 0.0, "trades": 0, "wins": 0} for m in range(1, 13)
            }
            grand_total = {"pnl": 0.0, "trades": 0, "wins": 0}

            for r in q.all():
                yr = int(r.yr)
                mo = int(r.mo)
                pnl = round(float(r.pnl or 0), 2)
                trades = int(r.trades or 0)
                wins = int(r.wins or 0)
                win_rate = round(wins / trades * 100, 1) if trades else 0.0

                by_year.setdefault(yr, {})[mo] = {
                    "pnl": pnl,
                    "trades": trades,
                    "wins": wins,
                    "win_rate": win_rate,
                }

                yt = year_totals.setdefault(yr, {"pnl": 0.0, "trades": 0, "wins": 0})
                yt["pnl"] = round(yt["pnl"] + pnl, 2)
                yt["trades"] += trades
                yt["wins"] += wins

                mt = month_totals[mo]
                mt["pnl"] = round(mt["pnl"] + pnl, 2)
                mt["trades"] += trades
                mt["wins"] += wins

                grand_total["pnl"] = round(grand_total["pnl"] + pnl, 2)
                grand_total["trades"] += trades
                grand_total["wins"] += wins

            # Calcula win_rate marginal apos somar
            for yt in year_totals.values():
                yt["win_rate"] = round(yt["wins"] / yt["trades"] * 100, 1) if yt["trades"] else 0.0
            for mt in month_totals.values():
                mt["win_rate"] = round(mt["wins"] / mt["trades"] * 100, 1) if mt["trades"] else 0.0
            grand_total["win_rate"] = (
                round(grand_total["wins"] / grand_total["trades"] * 100, 1)
                if grand_total["trades"]
                else 0.0
            )

            years = sorted(by_year.keys())
            return {
                "years": years,
                "months": list(range(1, 13)),
                "by_year": by_year,
                "year_totals": year_totals,
                "month_totals": month_totals,
                "grand_total": grand_total,
                "trade_objective": trade_objective,
            }

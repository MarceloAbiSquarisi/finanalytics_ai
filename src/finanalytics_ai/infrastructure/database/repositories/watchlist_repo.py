"""
finanalytics_ai.infrastructure.database.repositories.watchlist_repo
────────────────────────────────────────────────────────────────────
Repositório de Watchlist usando SQLAlchemy async + PostgreSQL.

Schema:
  watchlist_items  — um item por ticker por usuário
  watchlist_alerts — alertas inteligentes vinculados a um item

Design decisions:
  - Separação watchlist_items / watchlist_alerts: permite CRUD
    independente dos alertas sem reescrever o item inteiro.
  - JSON para config do alerta: evita proliferação de colunas para
    cada parâmetro de cada tipo de alerta.
  - upsert em save_item(): se item já existe (mesmo user_id+ticker),
    atualiza note/tags. Simplifica o service (não precisa checar).
  - Cria tabelas no startup via create_all — consistente com o padrão
    já adotado no alert_repo.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, delete, select

from finanalytics_ai.domain.watchlist.entities import (
    SmartAlert,
    SmartAlertConfig,
    SmartAlertStatus,
    SmartAlertType,
    WatchlistItem,
)
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ── ORM Models ────────────────────────────────────────────────────────────────


class WatchlistItemModel(Base):
    __tablename__ = "watchlist_items"

    item_id = Column(String(36), primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    note = Column(Text, nullable=False, default="")
    tags = Column(Text, nullable=False, default="[]")  # JSON list
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WatchlistAlertModel(Base):
    __tablename__ = "watchlist_alerts"

    alert_id = Column(String(36), primary_key=True)
    item_id = Column(
        String(36),
        ForeignKey("watchlist_items.item_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(100), nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    alert_type = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default=SmartAlertStatus.ACTIVE)
    config_json = Column(Text, nullable=False, default="{}")
    note = Column(Text, nullable=False, default="")
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ── Repository ────────────────────────────────────────────────────────────────


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Items ────────────────────────────────────────────────────────────────

    async def save_item(self, item: WatchlistItem) -> None:
        """Upsert: cria ou atualiza item."""
        existing = await self._session.get(WatchlistItemModel, item.item_id)
        if existing:
            existing.note = item.note
            existing.tags = json.dumps(item.tags)
        else:
            self._session.add(
                WatchlistItemModel(
                    item_id=item.item_id,
                    user_id=item.user_id,
                    ticker=item.ticker.upper(),
                    note=item.note,
                    tags=json.dumps(item.tags),
                    added_at=item.added_at,
                )
            )
        await self._session.flush()

    async def delete_item(self, item_id: str) -> None:
        """Remove item e seus alertas (CASCADE no FK)."""
        await self._session.execute(
            delete(WatchlistItemModel).where(WatchlistItemModel.item_id == item_id)
        )

    async def find_item(self, item_id: str) -> WatchlistItem | None:
        model = await self._session.get(WatchlistItemModel, item_id)
        if not model:
            return None
        item = self._model_to_item(model)
        item.smart_alerts = await self._load_alerts(item_id)
        return item

    async def find_by_user(self, user_id: str) -> list[WatchlistItem]:
        stmt = select(WatchlistItemModel).where(WatchlistItemModel.user_id == user_id)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        items = []
        for m in models:
            item = self._model_to_item(m)
            item.smart_alerts = await self._load_alerts(m.item_id)
            items.append(item)
        return items

    async def find_by_user_and_ticker(self, user_id: str, ticker: str) -> WatchlistItem | None:
        stmt = select(WatchlistItemModel).where(
            WatchlistItemModel.user_id == user_id,
            WatchlistItemModel.ticker == ticker.upper(),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        item = self._model_to_item(model)
        item.smart_alerts = await self._load_alerts(model.item_id)
        return item

    async def find_all_tickers_for_user(self, user_id: str) -> list[str]:
        stmt = select(WatchlistItemModel.ticker).where(WatchlistItemModel.user_id == user_id)
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ── Smart Alerts ─────────────────────────────────────────────────────────

    async def save_alert(self, alert: SmartAlert, item_id: str) -> None:
        existing = await self._session.get(WatchlistAlertModel, alert.alert_id)
        if existing:
            existing.status = alert.status.value
            existing.last_triggered_at = alert.last_triggered_at
            existing.config_json = json.dumps(
                {
                    "rsi_period": alert.config.rsi_period,
                    "rsi_oversold": alert.config.rsi_oversold,
                    "rsi_overbought": alert.config.rsi_overbought,
                    "ma_period": alert.config.ma_period,
                    "volume_multiplier": alert.config.volume_multiplier,
                    "price_threshold": alert.config.price_threshold,
                    "cooldown_hours": alert.config.cooldown_hours,
                }
            )
        else:
            self._session.add(
                WatchlistAlertModel(
                    alert_id=alert.alert_id,
                    item_id=item_id,
                    user_id=alert.user_id,
                    ticker=alert.ticker.upper(),
                    alert_type=alert.alert_type.value,
                    status=alert.status.value,
                    config_json=json.dumps(
                        {
                            "rsi_period": alert.config.rsi_period,
                            "rsi_oversold": alert.config.rsi_oversold,
                            "rsi_overbought": alert.config.rsi_overbought,
                            "ma_period": alert.config.ma_period,
                            "volume_multiplier": alert.config.volume_multiplier,
                            "price_threshold": alert.config.price_threshold,
                            "cooldown_hours": alert.config.cooldown_hours,
                        }
                    ),
                    note=alert.note,
                    last_triggered_at=alert.last_triggered_at,
                    created_at=alert.created_at,
                )
            )

    async def delete_alert(self, alert_id: str) -> None:
        await self._session.execute(
            delete(WatchlistAlertModel).where(WatchlistAlertModel.alert_id == alert_id)
        )

    async def find_all_active_alerts(self) -> list[tuple[SmartAlert, str]]:
        """Retorna todos os alertas ativos para avaliação periódica."""
        stmt = select(WatchlistAlertModel).where(
            WatchlistAlertModel.status.in_([SmartAlertStatus.ACTIVE, SmartAlertStatus.COOLDOWN])
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [(self._alert_from_model(r), r.item_id) for r in rows]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _model_to_item(self, m: WatchlistItemModel) -> WatchlistItem:
        try:
            tags = json.loads(m.tags or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        return WatchlistItem(
            item_id=m.item_id,
            user_id=m.user_id,
            ticker=m.ticker,
            note=m.note or "",
            tags=tags,
            added_at=m.added_at,
        )

    async def _load_alerts(self, item_id: str) -> list[SmartAlert]:
        stmt = select(WatchlistAlertModel).where(WatchlistAlertModel.item_id == item_id)
        result = await self._session.execute(stmt)
        return [self._alert_from_model(r) for r in result.scalars().all()]

    def _alert_from_model(self, m: WatchlistAlertModel) -> SmartAlert:
        try:
            cfg_data = json.loads(m.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            cfg_data = {}

        cfg = SmartAlertConfig(
            rsi_period=cfg_data.get("rsi_period", 14),
            rsi_oversold=cfg_data.get("rsi_oversold", 30.0),
            rsi_overbought=cfg_data.get("rsi_overbought", 70.0),
            ma_period=cfg_data.get("ma_period", 20),
            volume_multiplier=cfg_data.get("volume_multiplier", 2.5),
            price_threshold=cfg_data.get("price_threshold", 0.0),
            cooldown_hours=cfg_data.get("cooldown_hours", 4),
        )
        try:
            alert_type = SmartAlertType(m.alert_type)
        except ValueError:
            alert_type = SmartAlertType.PRICE_BELOW

        return SmartAlert(
            alert_id=m.alert_id,
            ticker=m.ticker,
            user_id=m.user_id,
            alert_type=alert_type,
            status=SmartAlertStatus(m.status),
            config=cfg,
            note=m.note or "",
            last_triggered_at=m.last_triggered_at,
            created_at=m.created_at,
        )

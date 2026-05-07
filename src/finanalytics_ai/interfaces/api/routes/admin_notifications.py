"""
Endpoints da sub-aba /admin → Sistema → Notificações.

Todos sob require_master.

  GET    /api/v1/admin/notifications/settings           toggles + master_enabled
  PUT    /api/v1/admin/notifications/settings           atualiza um toggle
  GET    /api/v1/admin/notifications/log                historico (limit, filter)
  GET    /api/v1/admin/notifications/stats              counts por categoria
  POST   /api/v1/admin/notifications/test               envia notificacao teste
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.database.repositories import notifications_repo
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(
    prefix="/api/v1/admin/notifications", tags=["Admin Notifications"]
)


_VALID_KEYS = {"master_enabled"} | {f"cat_{c}" for c in notifications_repo.CATEGORIES}


class UpdateSettingRequest(BaseModel):
    key: str
    enabled: bool


class TestNotificationRequest(BaseModel):
    title: str = Field(default="Teste de notificação")
    message: str = Field(default="Pushover funcionando — disparado pelo /admin")
    critical: bool = Field(default=False)


@router.get("/settings")
async def get_settings(actor: User = Depends(require_master)) -> dict[str, Any]:
    settings = await notifications_repo.get_settings(force_refresh=True)
    # Serializa em estrutura mais amigavel pro UI.
    out = {
        "master_enabled": settings.get("master_enabled", "true").lower() == "true",
        "categories": {
            c: settings.get(f"cat_{c}", "true").lower() == "true"
            for c in notifications_repo.CATEGORIES
        },
        "raw": settings,
    }
    return out


@router.put("/settings")
async def update_setting(
    body: UpdateSettingRequest,
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    if body.key not in _VALID_KEYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"key inválida. Permitidas: {sorted(_VALID_KEYS)}",
        )
    await notifications_repo.update_setting(
        body.key,
        "true" if body.enabled else "false",
        updated_by=actor.email,
    )
    logger.info(
        "admin_notifications.setting_updated",
        key=body.key, enabled=body.enabled, by=actor.email,
    )
    return {"key": body.key, "enabled": body.enabled, "updated_by": actor.email}


@router.get("/log")
async def list_log(
    limit: int = Query(100, ge=1, le=500),
    category: str | None = Query(None),
    outcome: Literal["sent", "skipped", "failed"] | None = Query(None),
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    rows = await notifications_repo.list_log(
        limit=limit, category=category, outcome=outcome,
    )
    # asyncpg datetimes -> isoformat
    for r in rows:
        if r.get("sent_at") is not None:
            r["sent_at"] = r["sent_at"].isoformat()
    return {"rows": rows, "count": len(rows)}


@router.get("/stats")
async def get_stats(
    days: int = Query(7, ge=1, le=90),
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    return await notifications_repo.stats(days=days)


@router.post("/test")
async def send_test(
    body: TestNotificationRequest,
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    from finanalytics_ai.infrastructure.notifications.pushover import notify_system

    sent = await notify_system(
        title=body.title,
        message=f"{body.message}\n\nDisparado por: {actor.email}",
        critical=body.critical,
        category="test",
    )
    return {"sent": sent, "critical": body.critical}

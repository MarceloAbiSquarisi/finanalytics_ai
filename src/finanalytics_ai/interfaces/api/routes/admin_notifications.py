"""
Endpoints da sub-aba /admin → Sistema → Notificações.

Todos sob require_master.

  GET    /api/v1/admin/notifications/settings           toggles + master_enabled
  PUT    /api/v1/admin/notifications/settings           atualiza um toggle
  GET    /api/v1/admin/notifications/log                historico (limit, filter)
  GET    /api/v1/admin/notifications/stats              counts por categoria
  POST   /api/v1/admin/notifications/test               envia notificacao teste

  GET    /api/v1/admin/notifications/grafana/silences          lista silences ativos
  POST   /api/v1/admin/notifications/grafana/silences          cria silence (silencia tudo)
  DELETE /api/v1/admin/notifications/grafana/silences/{id}     expira silence
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.database.repositories import notifications_repo
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(
    prefix="/api/v1/admin/notifications", tags=["Admin Notifications"]
)

# ── Grafana config ───────────────────────────────────────────────────────────
# GRAFANA_API_TOKEN: service account token (preferido). Criar em
#   Grafana UI > Administration > Service Accounts > Add token.
# GRAFANA_BASIC_AUTH: fallback "user:password" (ex: "admin:admin").
# GRAFANA_URL: default http://grafana:3000 (rede interna).
_GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000").rstrip("/")
_GRAFANA_TOKEN = os.getenv("GRAFANA_API_TOKEN", "").strip()
_GRAFANA_BASIC = os.getenv("GRAFANA_BASIC_AUTH", "").strip()

# Tag do comment usada p/ marcar silences criados via /admin (filtra na UI).
_SILENCE_TAG = "[admin-ui]"


def _grafana_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if _GRAFANA_TOKEN:
        h["Authorization"] = f"Bearer {_GRAFANA_TOKEN}"
    elif _GRAFANA_BASIC:
        b64 = base64.b64encode(_GRAFANA_BASIC.encode()).decode()
        h["Authorization"] = f"Basic {b64}"
    return h


def _grafana_auth_configured() -> bool:
    return bool(_GRAFANA_TOKEN or _GRAFANA_BASIC)


async def _grafana_request(
    method: str, path: str, json: Any | None = None
) -> httpx.Response:
    if not _grafana_auth_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Grafana auth não configurada. Definir GRAFANA_API_TOKEN "
                "(service account token) ou GRAFANA_BASIC_AUTH "
                "(user:password) no .env e recreate api."
            ),
        )
    url = f"{_GRAFANA_URL}{path}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        return await client.request(
            method, url, headers=_grafana_headers(), json=json,
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


# ── Grafana silences (proxy via Alertmanager API) ────────────────────────────


class CreateSilenceRequest(BaseModel):
    duration_hours: float = Field(default=4.0, ge=0.25, le=168.0)
    scope: Literal["all", "critical", "warning"] = Field(default="all")
    comment: str = Field(default="silenced via /admin")


_SILENCE_MATCHERS = {
    # alertname=~".+" casa qualquer alertname (= "tudo").
    "all": [{"name": "alertname", "value": ".+", "isRegex": True, "isEqual": True}],
    "critical": [{"name": "severity", "value": "critical", "isRegex": False, "isEqual": True}],
    "warning":  [{"name": "severity", "value": "warning",  "isRegex": False, "isEqual": True}],
}


@router.get("/grafana/health")
async def grafana_health(
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    """Diagnostico rapido — auth configurada? endpoint atinge Grafana?"""
    if not _grafana_auth_configured():
        return {
            "ok": False,
            "auth_configured": False,
            "detail": "Sem GRAFANA_API_TOKEN nem GRAFANA_BASIC_AUTH.",
        }
    try:
        resp = await _grafana_request("GET", "/api/health")
        return {
            "ok": resp.status_code == 200,
            "auth_configured": True,
            "status": resp.status_code,
            "url": _GRAFANA_URL,
            "auth_method": "token" if _GRAFANA_TOKEN else "basic",
        }
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "ok": False,
            "auth_configured": True,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


@router.get("/grafana/silences")
async def list_grafana_silences(
    only_active: bool = Query(True),
    only_managed: bool = Query(False, description="Apenas silences criados via /admin"),
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    resp = await _grafana_request(
        "GET", "/api/alertmanager/grafana/api/v2/silences"
    )
    if resp.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Grafana retornou {resp.status_code}: {resp.text[:200]}",
        )
    silences = resp.json()
    out = []
    for s in silences:
        state = (s.get("status") or {}).get("state", "")
        if only_active and state != "active":
            continue
        comment = s.get("comment", "") or ""
        if only_managed and _SILENCE_TAG not in comment:
            continue
        out.append({
            "id": s.get("id"),
            "state": state,
            "matchers": s.get("matchers", []),
            "starts_at": s.get("startsAt"),
            "ends_at": s.get("endsAt"),
            "created_by": s.get("createdBy"),
            "comment": comment,
            "managed": _SILENCE_TAG in comment,
        })
    return {"rows": out, "count": len(out)}


@router.post("/grafana/silences")
async def create_grafana_silence(
    body: CreateSilenceRequest,
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    ends = now + timedelta(hours=body.duration_hours)
    payload = {
        "matchers": _SILENCE_MATCHERS[body.scope],
        "startsAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endsAt":   ends.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "createdBy": actor.email,
        "comment": f"{_SILENCE_TAG} {body.comment} (scope={body.scope})",
    }
    resp = await _grafana_request(
        "POST", "/api/alertmanager/grafana/api/v2/silences", json=payload,
    )
    if resp.status_code not in (200, 201, 202):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Grafana retornou {resp.status_code}: {resp.text[:300]}",
        )
    silence_id = (resp.json() or {}).get("silenceID")
    logger.info(
        "admin_notifications.grafana.silence_created",
        silence_id=silence_id, scope=body.scope,
        duration_hours=body.duration_hours, by=actor.email,
    )
    # Tambem loga no notifications_log p/ historico unificado.
    await notifications_repo.log_notification(
        category="system",
        title=f"Grafana silence criado (scope={body.scope})",
        message=f"duracao={body.duration_hours}h ends={ends.isoformat()}",
        priority=0, critical=False, outcome="sent",
    )
    return {"silence_id": silence_id, "ends_at": ends.isoformat(), "scope": body.scope}


@router.delete("/grafana/silences/{silence_id}")
async def expire_grafana_silence(
    silence_id: str = Path(..., min_length=1),
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    resp = await _grafana_request(
        "DELETE", f"/api/alertmanager/grafana/api/v2/silence/{silence_id}",
    )
    if resp.status_code not in (200, 202, 204):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Grafana retornou {resp.status_code}: {resp.text[:300]}",
        )
    logger.info(
        "admin_notifications.grafana.silence_expired",
        silence_id=silence_id, by=actor.email,
    )
    return {"silence_id": silence_id, "expired": True}

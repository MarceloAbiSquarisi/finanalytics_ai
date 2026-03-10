from __future__ import annotations

from fastapi import APIRouter

from finanalytics_ai.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "version": "0.1.0"}

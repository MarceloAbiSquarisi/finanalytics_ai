"""
interfaces/api/routes/fintz_sync_status.py

Endpoints de status e controle do sync Fintz.

GET  /api/v1/fintz/sync/status          -- quais datasets estao desatualizados
GET  /api/v1/fintz/sync/history         -- historico de syncs
POST /api/v1/fintz/sync/trigger         -- dispara sync manual (admin)
POST /api/v1/fintz/sync/trigger/{key}   -- sync de dataset especifico
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from finanalytics_ai.interfaces.api.dependencies import get_current_user

router = APIRouter()
logger = structlog.get_logger(__name__)

# Datasets considerados "criticos" -- atraso > 1 dia gera alerta
CRITICAL_DATASETS = {"cotacoes_ohlc", "indicador_ROE", "indicador_DividendYield"}

# Limite de atraso por tipo
MAX_AGE_HOURS = {
    "cotacoes":    26,  # 1 dia + 2h de tolerancia
    "item_contabil": 72,  # 3 dias (dados fundamentais mudam menos)
    "indicador":   26,
}


def _get_db(request: Request):
    """Dependency: session factory do app."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database nao disponivel")
    return factory


@router.get(
    "/sync/status",
    summary="Status de atualizacao dos datasets Fintz",
    response_description="Lista de datasets com status de atualizacao",
)
async def get_sync_status(
    request: Request,
    outdated_only: bool = Query(default=False, description="Retorna apenas datasets desatualizados"),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Verifica quais datasets Fintz precisam de atualizacao.

    Compara a data do ultimo sync bem-sucedido (fintz_sync_log) com
    o horario atual. Datasets com atraso maior que MAX_AGE_HOURS sao
    marcados como desatualizados.

    Util para:
    - Dashboard de monitoramento
    - Trigger manual de sync seletivo
    - Alertas de SLA de dados
    """
    from sqlalchemy import text
    from finanalytics_ai.domain.fintz.entities import ALL_DATASETS
    from finanalytics_ai.infrastructure.database.connection import get_session

    now = datetime.now(tz=timezone.utc)

    # Busca ultimo sync de cada dataset
    async with get_session() as session:
        rows = await session.execute(text("""
            SELECT dataset_key, status, rows_upserted, synced_at, error_message
            FROM fintz_sync_log
            ORDER BY synced_at DESC
        """))
        sync_log = {r[0]: r for r in rows.fetchall()}

    # Catalogo completo
    catalog_keys = {s.key: s for s in ALL_DATASETS}

    results = []
    outdated_count = 0
    error_count = 0

    for key, spec in catalog_keys.items():
        log_row = sync_log.get(key)
        max_age = MAX_AGE_HOURS.get(spec.dataset_type, 26)

        if log_row is None:
            age_hours = None
            is_outdated = True
            last_status = "never_synced"
            last_sync = None
            rows_last = 0
        else:
            last_sync = log_row[3]
            if last_sync.tzinfo is None:
                last_sync = last_sync.replace(tzinfo=timezone.utc)
            age_hours = (now - last_sync).total_seconds() / 3600
            last_status = log_row[1]
            rows_last = log_row[2] or 0
            is_outdated = age_hours > max_age or last_status == "error"

        if is_outdated:
            outdated_count += 1
        if last_status == "error":
            error_count += 1

        entry = {
            "key": key,
            "dataset_type": spec.dataset_type,
            "description": spec.description,
            "last_sync": last_sync.isoformat() if last_sync else None,
            "last_status": last_status,
            "rows_last_sync": rows_last,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "max_age_hours": max_age,
            "is_outdated": is_outdated,
            "is_critical": key in CRITICAL_DATASETS,
        }

        if not outdated_only or is_outdated:
            results.append(entry)

    # Ordena: criticos primeiro, depois por atraso
    results.sort(key=lambda x: (
        not x["is_critical"],
        not x["is_outdated"],
        -(x["age_hours"] or 9999),
    ))

    return {
        "timestamp": now.isoformat(),
        "summary": {
            "total_datasets": len(catalog_keys),
            "outdated": outdated_count,
            "errors": error_count,
            "ok": len(catalog_keys) - outdated_count,
            "sync_needed": outdated_count > 0,
        },
        "datasets": results,
    }


@router.get(
    "/sync/history",
    summary="Historico de syncs Fintz",
)
async def get_sync_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status_filter: str | None = Query(default=None, description="ok | error | skip"),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Historico dos ultimos syncs por dataset."""
    from sqlalchemy import text
    from finanalytics_ai.infrastructure.database.connection import get_session

    where = ""
    params: dict = {"limit": limit}
    if status_filter:
        where = "WHERE status = :status"
        params["status"] = status_filter

    async with get_session() as session:
        rows = await session.execute(text(f"""
            SELECT dataset_key, status, rows_upserted, synced_at, error_message
            FROM fintz_sync_log
            {where}
            ORDER BY synced_at DESC
            LIMIT :limit
        """), params)
        history = [
            {
                "dataset_key": r[0],
                "status": r[1],
                "rows_upserted": r[2],
                "synced_at": r[3].isoformat() if r[3] else None,
                "error_message": r[4],
            }
            for r in rows.fetchall()
        ]

    return {"count": len(history), "history": history}


@router.post(
    "/sync/trigger",
    summary="Dispara sync manual de todos os datasets",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_sync(
    request: Request,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Dispara sync manual em background.

    Retorna imediatamente com 202 Accepted.
    O sync roda em background -- acompanhe via GET /sync/status.
    """
    import asyncio
    from finanalytics_ai.config import get_settings
    from finanalytics_ai.workers.fintz_sync_worker import run_once

    settings = get_settings()

    # Fire-and-forget em background task
    asyncio.create_task(_run_sync_background(settings))

    logger.info("fintz_sync.manual_trigger", user=getattr(current_user, "email", "?"))

    return {
        "status": "accepted",
        "message": "Sync iniciado em background. Acompanhe via GET /api/v1/fintz/sync/status",
        "triggered_at": datetime.now(tz=timezone.utc).isoformat(),
    }


@router.post(
    "/sync/trigger/{dataset_key}",
    summary="Dispara sync de um dataset especifico",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_sync_dataset(
    dataset_key: str,
    request: Request,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Dispara sync de um dataset especifico em background.

    dataset_key: ex 'cotacoes_ohlc', 'indicador_ROE', 'item_ReceitaLiquida_12M'
    """
    import asyncio
    from finanalytics_ai.config import get_settings
    from finanalytics_ai.domain.fintz.entities import ALL_DATASETS

    # Valida key
    valid_keys = {s.key for s in ALL_DATASETS}
    if dataset_key not in valid_keys:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_key}' nao encontrado. "
                   f"Use GET /api/v1/fintz/tickers para listar datasets validos.",
        )

    settings = get_settings()
    asyncio.create_task(_run_sync_background(settings, datasets=[dataset_key]))

    logger.info(
        "fintz_sync.manual_trigger_dataset",
        dataset_key=dataset_key,
        user=getattr(current_user, "email", "?"),
    )

    return {
        "status": "accepted",
        "dataset_key": dataset_key,
        "message": f"Sync de '{dataset_key}' iniciado. Acompanhe via GET /api/v1/fintz/sync/status",
        "triggered_at": datetime.now(tz=timezone.utc).isoformat(),
    }


async def _run_sync_background(
    settings: Any,
    datasets: list[str] | None = None,
) -> None:
    """Executa sync em background — erros sao logados, nao propagados."""
    from finanalytics_ai.workers.fintz_sync_worker import run_once
    try:
        await run_once(settings, datasets=datasets)
    except Exception as exc:
        logger.exception("fintz_sync.background_error", error=str(exc))

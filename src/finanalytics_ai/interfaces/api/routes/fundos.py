"""
finanalytics_ai.interfaces.api.routes.fundos
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from finanalytics_ai.interfaces.api.dependencies import get_db_session

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fundos", tags=["Fundos"])

@router.get("/buscar")
async def buscar_fundos(
    session: AsyncSession = Depends(get_db_session),
    q: str = Query("", min_length=2, max_length=100),
    tipo: str = Query(""),
    situacao: str = Query(""),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        conditions.append("(denominacao ILIKE :q OR cnpj LIKE :q2 OR gestor ILIKE :q)")
        params["q"] = f"%{q}%"
        params["q2"] = f"%{q}%"
    if tipo:
        conditions.append("tipo = :tipo")
        params["tipo"] = tipo.upper()
    if situacao:
        conditions.append("situacao ILIKE :sit")
        params["sit"] = f"%{situacao}%"
    where = " AND ".join(conditions)
    total_r = await session.execute(text(f"SELECT COUNT(*) FROM fundos_cadastro WHERE {where}"), params)
    total = total_r.scalar() or 0
    rows = await session.execute(text(f"""
        SELECT cnpj, denominacao, nome_abrev, tipo, classe, situacao,
               gestor, administrador, taxa_adm, pl_atual, cotistas, data_registro
        FROM   fundos_cadastro WHERE {where}
        ORDER  BY pl_atual DESC NULLS LAST LIMIT :limit OFFSET :offset
    """), params)
    fundos = []
    for r in rows:
        d = dict(r._mapping)
        for k, v in d.items():
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
            elif hasattr(v, "__float__") and v is not None: d[k] = float(v)
        fundos.append(d)
    return {"fundos": fundos, "total": total, "limit": limit, "offset": offset}

@router.get("/tipos")
async def listar_tipos(session: AsyncSession = Depends(get_db_session)) -> dict:
    rows = await session.execute(text("""
        SELECT tipo, COUNT(*) as total, SUM(pl_atual) as pl_total
        FROM   fundos_cadastro
        WHERE  situacao ILIKE '%FUNCIONAMENTO%' AND tipo IS NOT NULL
        GROUP  BY tipo ORDER BY total DESC
    """))
    return {"tipos": [{"tipo": r.tipo, "total": r.total,
            "pl_total": float(r.pl_total) if r.pl_total else None} for r in rows]}

@router.get("/{cnpj}/cotas")
async def historico_cotas(
    cnpj: str,
    session: AsyncSession = Depends(get_db_session),
    days: int = Query(252, ge=5, le=1260),
) -> dict[str, Any]:
    rows = await session.execute(text("""
        SELECT data_ref, vl_quota FROM fundos_informe_diario
        WHERE  cnpj = :cnpj AND vl_quota > 0
          AND  data_ref >= CURRENT_DATE - (:days * INTERVAL '1 day')
        ORDER  BY data_ref ASC
    """), {"cnpj": cnpj, "days": days})
    candles = [{"time": int(datetime.combine(r.data_ref, datetime.min.time())
                .replace(tzinfo=timezone.utc).timestamp()), "value": float(r.vl_quota)}
               for r in rows]
    return {"cnpj": cnpj, "candles": candles, "total": len(candles)}

@router.get("/{cnpj}")
async def detalhe_fundo(cnpj: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    row = await session.execute(text("SELECT * FROM fundos_cadastro WHERE cnpj = :cnpj"), {"cnpj": cnpj.strip()})
    cad = row.mappings().first()
    if not cad:
        return {"error": "Fundo nao encontrado", "cnpj": cnpj}
    fundo = {}
    for k, v in dict(cad).items():
        if hasattr(v, "isoformat"): fundo[k] = v.isoformat()
        elif hasattr(v, "__float__") and v is not None: fundo[k] = float(v)
        else: fundo[k] = v
    rent_row = await session.execute(text("""
        SELECT * FROM fundos_rentabilidade WHERE cnpj = :cnpj ORDER BY data_ref DESC LIMIT 1
    """), {"cnpj": cnpj})
    rent = rent_row.mappings().first()
    rentabilidade = {}
    if rent:
        for k, v in dict(rent).items():
            if hasattr(v, "isoformat"): rentabilidade[k] = v.isoformat()
            elif hasattr(v, "__float__") and v is not None: rentabilidade[k] = float(v)
            else: rentabilidade[k] = v
    hist = await session.execute(text("""
        SELECT data_ref, vl_quota, vl_patrim_liq, captacao_dia, resgat_dia, nr_cotst
        FROM   fundos_informe_diario WHERE cnpj = :cnpj
          AND  data_ref >= CURRENT_DATE - INTERVAL '365 days' ORDER BY data_ref ASC
    """), {"cnpj": cnpj})
    historico = [{"data": r.data_ref.isoformat(), "cota": float(r.vl_quota) if r.vl_quota else None,
                  "pl": float(r.vl_patrim_liq) if r.vl_patrim_liq else None,
                  "captacao": float(r.captacao_dia) if r.captacao_dia else None,
                  "resgate": float(r.resgat_dia) if r.resgat_dia else None,
                  "cotistas": r.nr_cotst} for r in hist]
    return {"fundo": fundo, "rentabilidade": rentabilidade, "historico": historico}

@router.post("/sync/cadastro")
async def trigger_sync_cadastro(session: AsyncSession = Depends(get_db_session)) -> dict:
    from finanalytics_ai.application.services.fundos_cvm_service import sync_cadastro
    result = await sync_cadastro(session)
    return {"ok": True, **result}

@router.post("/sync/informe")
async def trigger_sync_informe(
    session: AsyncSession = Depends(get_db_session),
    competencia: str = Query(default=""),
) -> dict:
    from finanalytics_ai.application.services.fundos_cvm_service import sync_informe_diario
    result = await sync_informe_diario(session, competencia=competencia or None)
    return {"ok": True, **result}

@router.post("/{cnpj}/rentabilidade")
async def calcular_rent(cnpj: str, session: AsyncSession = Depends(get_db_session)) -> dict:
    from finanalytics_ai.application.services.fundos_cvm_service import calcular_rentabilidade
    result = await calcular_rentabilidade(session, cnpj)
    return {"ok": True, **result}

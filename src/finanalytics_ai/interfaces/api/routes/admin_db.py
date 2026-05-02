"""
finanalytics_ai.interfaces.api.routes.admin_db
Acesso restrito a master — explorador de banco com export parquet.

Endpoints:
  GET  /api/v1/admin/db/list
  GET  /api/v1/admin/db/{db}/tables
  GET  /api/v1/admin/db/{db}/{table}/schema
  POST /api/v1/admin/db/{db}/{table}/preview
  POST /api/v1/admin/db/{db}/{table}/export

Defesa SQL injection:
  - db: whitelist {postgres, timescale}
  - table/column: regex ^[a-zA-Z_][a-zA-Z0-9_]*$
  - value: parametrizado via asyncpg (nunca interpolado)

Limites:
  - preview: max 1000 rows
  - export: max 10_000_000 rows (~ várias dezenas de MB em parquet)

Filtros aceitos: eq, neq, like, ilike, gt, gte, lt, lte, in, is_null, is_not_null
"""

from __future__ import annotations

from io import BytesIO
import os
import re
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
import structlog

from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin/db", tags=["Admin"])


# ── DSN resolution ────────────────────────────────────────────────────────────

def _normalize_dsn(raw: str) -> str:
    """asyncpg aceita 'postgres://' não 'postgresql+asyncpg://'."""
    return raw.replace("postgresql+asyncpg://", "postgres://").replace(
        "postgresql://", "postgres://"
    )


_PG_DSN = _normalize_dsn(
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "")
    or "postgres://finanalytics:secret@postgres:5432/finanalytics"
)
_TS_DSN = _normalize_dsn(
    os.getenv("TIMESCALE_URL")
    or os.getenv("PROFIT_TIMESCALE_DSN", "")
    or "postgres://finanalytics:timescale_secret@timescale:5432/market_data"
)

_DB_REGISTRY: dict[str, dict[str, str]] = {
    "postgres": {
        "label": "Postgres principal (finanalytics)",
        "description": "Multi-tenant, users/portfolios/trades/cointegrated_pairs/email_research",
        "dsn": _PG_DSN,
    },
    "timescale": {
        "label": "TimescaleDB (market_data)",
        "description": "Hypertables OHLC, ticks, signals, robot_*",
        "dsn": _TS_DSN,
    },
}


# ── Validação de identifiers (anti SQL injection) ─────────────────────────────

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_ident(name: str, kind: str = "identifier") -> str:
    if not name or not _IDENT_RE.match(name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} inválido: '{name}'. Apenas [a-zA-Z_][a-zA-Z0-9_]* permitido.",
        )
    return name


def _resolve_dsn(db: str) -> str:
    if db not in _DB_REGISTRY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"db inválido: '{db}'. Disponíveis: {list(_DB_REGISTRY)}",
        )
    return _DB_REGISTRY[db]["dsn"]


# ── Schemas ───────────────────────────────────────────────────────────────────


_OPS = Literal["eq", "neq", "like", "ilike", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"]


class FilterClause(BaseModel):
    column: str = Field(..., max_length=64)
    op: _OPS
    value: Any = None  # ignorado para is_null/is_not_null

    @field_validator("column")
    @classmethod
    def _check_column(cls, v: str) -> str:
        if not _IDENT_RE.match(v):
            raise ValueError(f"column inválido: {v}")
        return v


class PreviewRequest(BaseModel):
    filters: list[FilterClause] = Field(default_factory=list)
    order_by: str | None = Field(default=None, max_length=64)
    order_desc: bool = False
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @field_validator("order_by")
    @classmethod
    def _check_order(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _IDENT_RE.match(v):
            raise ValueError(f"order_by inválido: {v}")
        return v


class ExportRequest(BaseModel):
    filters: list[FilterClause] = Field(default_factory=list)
    order_by: str | None = Field(default=None, max_length=64)
    order_desc: bool = False
    limit: int = Field(default=1_000_000, ge=1, le=10_000_000)


# ── SQL builder ───────────────────────────────────────────────────────────────


def _build_where(filters: list[FilterClause]) -> tuple[str, list[Any]]:
    """Constrói cláusula WHERE parametrizada. Retorna (sql, params)."""
    if not filters:
        return "", []
    parts: list[str] = []
    params: list[Any] = []
    for f in filters:
        col = _validate_ident(f.column, "column")  # double-check
        if f.op == "is_null":
            parts.append(f'"{col}" IS NULL')
        elif f.op == "is_not_null":
            parts.append(f'"{col}" IS NOT NULL')
        elif f.op == "in":
            if not isinstance(f.value, list) or not f.value:
                raise HTTPException(400, f"op=in exige array não-vazio em column={col}")
            params.append(f.value)
            parts.append(f'"{col}" = ANY(${len(params)})')
        else:
            sql_op = {
                "eq": "=", "neq": "<>", "like": "LIKE", "ilike": "ILIKE",
                "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
            }[f.op]
            params.append(f.value)
            if f.op in ("like", "ilike") and isinstance(f.value, str) and "%" not in f.value:
                # auto-wrap se usuário não passou wildcard
                params[-1] = f"%{f.value}%"
            parts.append(f'"{col}" {sql_op} ${len(params)}')
    return "WHERE " + " AND ".join(parts), params


def _build_order(order_by: str | None, desc: bool) -> str:
    if not order_by:
        return ""
    direction = "DESC" if desc else "ASC"
    return f'ORDER BY "{order_by}" {direction}'


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/list")
async def list_dbs(_: User = Depends(require_master)) -> dict:
    """Lista bancos disponíveis (whitelist)."""
    return {
        "items": [
            {"name": k, "label": v["label"], "description": v["description"]}
            for k, v in _DB_REGISTRY.items()
        ]
    }


@router.get("/{db}/tables")
async def list_tables(db: str, _: User = Depends(require_master)) -> dict:
    """Lista tabelas do schema 'public' (filtra views se preferir)."""
    dsn = _resolve_dsn(db)
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name NOT LIKE '\\_%' ESCAPE '\\'
              AND table_name NOT LIKE '_hyper%'
            ORDER BY table_name
            """
        )
        return {"db": db, "items": [{"name": r["table_name"], "type": r["table_type"]} for r in rows]}
    finally:
        await conn.close()


@router.get("/{db}/{table}/schema")
async def table_schema(
    db: str, table: str, _: User = Depends(require_master)
) -> dict:
    """Schema da tabela: colunas + tipos + nullable + default."""
    dsn = _resolve_dsn(db)
    _validate_ident(table, "table")
    conn = await asyncpg.connect(dsn)
    try:
        cols = await conn.fetch(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default,
                   character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
        if not cols:
            raise HTTPException(404, f"Tabela '{table}' não encontrada em {db}.")
        # Row count aproximado via reltuples (rápido em hypertables grandes)
        approx_rows = await conn.fetchval(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = $1", table
        )
        return {
            "db": db,
            "table": table,
            "approx_rows": int(approx_rows or 0),
            "columns": [
                {
                    "name": c["column_name"],
                    "type": c["data_type"],
                    "udt": c["udt_name"],
                    "nullable": c["is_nullable"] == "YES",
                    "default": c["column_default"],
                    "max_length": c["character_maximum_length"],
                }
                for c in cols
            ],
        }
    finally:
        await conn.close()


@router.post("/{db}/{table}/preview")
async def preview_table(
    db: str, table: str, body: PreviewRequest, _: User = Depends(require_master)
) -> dict:
    """Preview com filtros + paginação. Limit 1000."""
    dsn = _resolve_dsn(db)
    _validate_ident(table, "table")
    where_sql, params = _build_where(body.filters)
    order_sql = _build_order(body.order_by, body.order_desc)

    sql = f'SELECT * FROM "{table}" {where_sql} {order_sql} LIMIT {body.limit} OFFSET {body.offset}'
    count_sql = f'SELECT COUNT(*) FROM "{table}" {where_sql}'

    conn = await asyncpg.connect(dsn)
    try:
        # Count com timeout p/ tabelas gigantes (hypertables)
        try:
            total = await conn.fetchval(count_sql, *params, timeout=10.0)
        except asyncpg.exceptions.QueryCanceledError:
            total = None  # tabela grande demais — informa null

        rows = await conn.fetch(sql, *params)
        # Serialize: asyncpg Record → dict; converter datetimes/Decimal pra str
        items = [
            {k: _serialize_value(v) for k, v in dict(r).items()}
            for r in rows
        ]
        return {
            "db": db,
            "table": table,
            "total": total,  # None se timeout no count
            "limit": body.limit,
            "offset": body.offset,
            "count": len(items),
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("admin.db.preview_failed", db=db, table=table, error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        await conn.close()


@router.post("/{db}/{table}/export")
async def export_parquet(
    db: str, table: str, body: ExportRequest, actor: User = Depends(require_master)
) -> Response:
    """Stream parquet com filtros aplicados. Limit padrão 1M, max 10M."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    dsn = _resolve_dsn(db)
    _validate_ident(table, "table")
    where_sql, params = _build_where(body.filters)
    order_sql = _build_order(body.order_by, body.order_desc)

    sql = f'SELECT * FROM "{table}" {where_sql} {order_sql} LIMIT {body.limit}'

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(sql, *params, timeout=300.0)
    except Exception as exc:
        logger.warning("admin.db.export_query_failed", db=db, table=table, error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        await conn.close()

    if not rows:
        # Parquet vazio com schema vazio dá problema; retornar 204
        raise HTTPException(204, "Resultado vazio — sem rows pra exportar.")

    # asyncpg Record → list[dict] → pandas → pyarrow → parquet
    df = pd.DataFrame([dict(r) for r in rows])
    # Sanitiza tipos: tipos especiais de Postgres (uuid, jsonb, etc) viram object
    buf = BytesIO()
    table_arrow = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table_arrow, buf, compression="snappy")
    payload = buf.getvalue()

    logger.info(
        "admin.db.exported",
        db=db, table=table, rows=len(rows), bytes=len(payload), actor=actor.user_id,
    )

    fname = f"{db}_{table}_{len(rows)}rows.parquet"
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Row-Count": str(len(rows)),
        },
    )


# ── Serialização auxiliar ─────────────────────────────────────────────────────


def _serialize_value(v: Any) -> Any:
    """Converte tipos Postgres pra JSON-friendly."""
    import datetime as _dt
    import decimal as _dec
    import uuid as _uuid

    if v is None:
        return None
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dec.Decimal):
        return float(v)
    if isinstance(v, _uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<bytes:{len(v)}>"
    return v

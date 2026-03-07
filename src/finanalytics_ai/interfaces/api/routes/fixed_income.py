"""
finanalytics_ai.interfaces.api.routes.fixed_income
────────────────────────────────────────────────────
GET  /api/v1/fixed-income/bonds          — lista/filtra títulos
GET  /api/v1/fixed-income/rates          — taxas de referência
POST /api/v1/fixed-income/calculate      — rendimento de um título
POST /api/v1/fixed-income/compare        — comparação entre títulos
POST /api/v1/fixed-income/cash-flow      — fluxo de caixa
POST /api/v1/fixed-income/goal           — quanto investir para atingir X
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.fixed_income_service import (
    FixedIncomeService, DEFAULT_CDI, DEFAULT_SELIC, DEFAULT_IPCA, DEFAULT_IGPM,
)
from finanalytics_ai.infrastructure.adapters.tesouro_client import get_tesouro_client

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fixed-income", tags=["Renda Fixa"])


def _svc() -> FixedIncomeService:
    return FixedIncomeService(get_tesouro_client())


# ── Schemas ───────────────────────────────────────────────────────────────────

class RatesInput(BaseModel):
    cdi_rate:   float = Field(DEFAULT_CDI * 100,   description="CDI % a.a.")
    selic_rate: float = Field(DEFAULT_SELIC * 100, description="SELIC % a.a.")
    ipca_rate:  float = Field(DEFAULT_IPCA * 100,  description="IPCA % a.a.")
    igpm_rate:  float = Field(DEFAULT_IGPM * 100,  description="IGPM % a.a.")


class CalculateRequest(RatesInput):
    bond_id:   str
    principal: float = Field(..., gt=0)
    days:      int | None = Field(None, gt=0)


class CompareRequest(RatesInput):
    bond_ids:  list[str] = Field(..., min_length=2, max_length=20)
    principal: float     = Field(..., gt=0)
    days:      int       = Field(..., gt=0)


class CashFlowRequest(RatesInput):
    bond_id:   str
    principal: float = Field(..., gt=0)


class GoalRequest(RatesInput):
    bond_id:       str
    target_amount: float = Field(..., gt=0)
    days:          int   = Field(..., gt=0)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/rates")
async def get_rates() -> dict[str, Any]:
    return await _svc().rates_reference()


@router.get("/bonds")
async def list_bonds(
    bond_type: list[str] | None = Query(None),
    indexer:   list[str] | None = Query(None),
    issuer:    str | None = Query(None),
    min_days:  int | None = Query(None),
    max_days:  int | None = Query(None),
) -> list[dict[str, Any]]:
    try:
        return await _svc().list_bonds(bond_type, indexer, issuer, min_days, max_days)
    except Exception as e:
        logger.error("fixed_income.list_error", error=str(e))
        raise HTTPException(500, detail=str(e))


@router.post("/calculate")
async def calculate(body: CalculateRequest) -> dict[str, Any]:
    try:
        return await _svc().calculate(
            body.bond_id, body.principal, body.days,
            body.cdi_rate / 100, body.ipca_rate / 100, body.selic_rate / 100,
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.error("fixed_income.calculate_error", error=str(e))
        raise HTTPException(500, detail=str(e))


@router.post("/compare")
async def compare(body: CompareRequest) -> dict[str, Any]:
    try:
        return await _svc().compare(
            body.bond_ids, body.principal, body.days,
            body.cdi_rate / 100, body.ipca_rate / 100,
            body.selic_rate / 100, body.igpm_rate / 100,
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.error("fixed_income.compare_error", error=str(e))
        raise HTTPException(500, detail=str(e))


@router.post("/cash-flow")
async def cash_flow(body: CashFlowRequest) -> dict[str, Any]:
    try:
        return await _svc().cash_flow(
            body.bond_id, body.principal,
            body.cdi_rate / 100, body.ipca_rate / 100, body.selic_rate / 100,
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.error("fixed_income.cashflow_error", error=str(e))
        raise HTTPException(500, detail=str(e))


@router.post("/goal")
async def goal(body: GoalRequest) -> dict[str, Any]:
    try:
        return await _svc().goal_calc(
            body.bond_id, body.target_amount, body.days,
            body.cdi_rate / 100, body.ipca_rate / 100, body.selic_rate / 100,
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.error("fixed_income.goal_error", error=str(e))
        raise HTTPException(500, detail=str(e))

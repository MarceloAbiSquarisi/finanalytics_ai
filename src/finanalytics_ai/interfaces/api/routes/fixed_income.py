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


# ── Sprint 28b: Curva de Juros + Stress Test ──────────────────────────────────

from finanalytics_ai.application.services.fixed_income_service import (
    get_yield_curve_with_stress, run_stress_test,
)
from finanalytics_ai.domain.fixed_income.yield_curve import STANDARD_SCENARIOS, StressScenario


class StressTestRequest(BaseModel):
    bond_ids:   list[str]  = Field(..., min_length=1, max_length=10)
    principal:  float      = Field(default=10000.0, gt=0)
    days:       int        = Field(default=365, gt=0, le=7300)
    base_selic: float      = Field(default=10.65, gt=0, description="% a.a.")
    base_cdi:   float      = Field(default=10.65, gt=0, description="% a.a.")
    base_ipca:  float      = Field(default=4.83,  gt=0, description="% a.a.")
    base_igpm:  float      = Field(default=6.20,  gt=0, description="% a.a.")
    # Cenários customizados opcionais — se vazio usa STANDARD_SCENARIOS
    custom_scenarios: list[dict] = Field(default_factory=list)


@router.get("/yield-curve")
async def yield_curve(
    selic: float = Query(default=10.65, description="SELIC % a.a."),
    cdi:   float = Query(default=10.65, description="CDI % a.a."),
    ipca:  float = Query(default=4.83,  description="IPCA % a.a."),
) -> dict:
    """
    Retorna curva de juros DI Futuro com análise contextual.
    Tenta ANBIMA real; fallback para curva sintética baseada na SELIC.
    """
    return await get_yield_curve_with_stress(
        selic = selic / 100,
        cdi   = cdi   / 100,
        ipca  = ipca  / 100,
    )


@router.post("/stress")
async def stress_test(body: StressTestRequest) -> list[dict]:
    """
    Stress test: simula o impacto de choques nos indexadores sobre bonds selecionados.

    Para cada bond × cenário (Base, SELIC ±1/2 p.p., IPCA ±2 p.p., Crise, Desinflação),
    calcula rendimento líquido após IR/IOF e exibe comparativo.
    """
    # Monta cenários customizados se fornecidos
    scenarios = None
    if body.custom_scenarios:
        try:
            scenarios = [
                StressScenario(
                    name        = s["name"],
                    delta_selic = float(s.get("delta_selic", 0)) / 100,
                    delta_cdi   = float(s.get("delta_cdi",   0)) / 100,
                    delta_ipca  = float(s.get("delta_ipca",  0)) / 100,
                    delta_igpm  = float(s.get("delta_igpm",  0)) / 100,
                    color       = s.get("color", "#8899aa"),
                )
                for s in body.custom_scenarios
            ]
        except (KeyError, ValueError):
            scenarios = None  # fallback para cenários padrão

    return await run_stress_test(
        bond_ids   = body.bond_ids,
        principal  = body.principal,
        days       = body.days,
        scenarios  = scenarios,
        base_selic = body.base_selic / 100,
        base_cdi   = body.base_cdi   / 100,
        base_ipca  = body.base_ipca  / 100,
        base_igpm  = body.base_igpm  / 100,
    )


@router.get("/scenarios/default")
async def default_scenarios() -> list[dict]:
    """Retorna a lista de cenários de stress padrão."""
    return [
        {
            "name":        s.name,
            "delta_selic": round(s.delta_selic * 100, 2),
            "delta_cdi":   round(s.delta_cdi   * 100, 2),
            "delta_ipca":  round(s.delta_ipca  * 100, 2),
            "color":       s.color,
        }
        for s in STANDARD_SCENARIOS
    ]

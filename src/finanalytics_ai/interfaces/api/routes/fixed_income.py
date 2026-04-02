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

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from finanalytics_ai.interfaces.api.dependencies import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.fixed_income_service import (
    DEFAULT_CDI,
    DEFAULT_IGPM,
    DEFAULT_IPCA,
    DEFAULT_SELIC,
    FixedIncomeService
)
from finanalytics_ai.infrastructure.adapters.tesouro_client import get_tesouro_client

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/fixed-income", tags=["Renda Fixa"])

def _svc() -> FixedIncomeService:
    return FixedIncomeService(get_tesouro_client())

# ── Schemas ───────────────────────────────────────────────────────────────────

class RatesInput(BaseModel):
    cdi_rate: float = Field(DEFAULT_CDI * 100, description="CDI % a.a.")
    selic_rate: float = Field(DEFAULT_SELIC * 100, description="SELIC % a.a.")
    ipca_rate: float = Field(DEFAULT_IPCA * 100, description="IPCA % a.a.")
    igpm_rate: float = Field(DEFAULT_IGPM * 100, description="IGPM % a.a.")

class CalculateRequest(RatesInput):
    bond_id: str
    principal: float = Field(..., gt=0)
    days: int | None = Field(None, gt=0)

class CompareRequest(RatesInput):
    bond_ids: list[str] = Field(..., min_length=2, max_length=20)
    principal: float = Field(..., gt=0)
    days: int = Field(..., gt=0)

class CashFlowRequest(RatesInput):
    bond_id: str
    principal: float = Field(..., gt=0)

class GoalRequest(RatesInput):
    bond_id: str
    target_amount: float = Field(..., gt=0)
    days: int = Field(..., gt=0)

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/rates")
async def get_rates() -> dict[str, Any]:
    return await _svc().rates_reference()

@router.get("/bonds")
async def list_bonds(
    bond_type: list[str] | None = Query(None),
    indexer: list[str] | None = Query(None),
    issuer: str | None = Query(None),
    min_days: int | None = Query(None),
    max_days: int | None = Query(None)
) -> list[dict[str, Any]]:
    try:
        return await _svc().list_bonds(bond_type, indexer, issuer, min_days, max_days)
    except Exception as e:
        logger.error("fixed_income.list_error", error=str(e))
        raise HTTPException(500, detail=str(e)) from e

@router.post("/calculate")
async def calculate(body: CalculateRequest) -> dict[str, Any]:
    try:
        return await _svc().calculate(
            body.bond_id,
            body.principal,
            body.days,
            body.cdi_rate / 100,
            body.ipca_rate / 100,
            body.selic_rate / 100
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from e
    except Exception as e:
        logger.error("fixed_income.calculate_error", error=str(e))
        raise HTTPException(500, detail=str(e)) from e

@router.post("/compare")
async def compare(body: CompareRequest) -> dict[str, Any]:
    try:
        return await _svc().compare(
            body.bond_ids,
            body.principal,
            body.days,
            body.cdi_rate / 100,
            body.ipca_rate / 100,
            body.selic_rate / 100,
            body.igpm_rate / 100
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from e
    except Exception as e:
        logger.error("fixed_income.compare_error", error=str(e))
        raise HTTPException(500, detail=str(e)) from e

@router.post("/cash-flow")
async def cash_flow(body: CashFlowRequest) -> dict[str, Any]:
    try:
        return await _svc().cash_flow(
            body.bond_id,
            body.principal,
            body.cdi_rate / 100,
            body.ipca_rate / 100,
            body.selic_rate / 100
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from e
    except Exception as e:
        logger.error("fixed_income.cashflow_error", error=str(e))
        raise HTTPException(500, detail=str(e)) from e

@router.post("/goal")
async def goal(body: GoalRequest) -> dict[str, Any]:
    try:
        return await _svc().goal_calc(
            body.bond_id,
            body.target_amount,
            body.days,
            body.cdi_rate / 100,
            body.ipca_rate / 100,
            body.selic_rate / 100
        )
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from e
    except Exception as e:
        logger.error("fixed_income.goal_error", error=str(e))
        raise HTTPException(500, detail=str(e)) from e

# ── Sprint 28b: Curva de Juros + Stress Test ──────────────────────────────────

from finanalytics_ai.application.services.fixed_income_service import (
    get_yield_curve_with_stress,
    run_stress_test
)
from finanalytics_ai.domain.fixed_income.yield_curve import STANDARD_SCENARIOS, StressScenario

class StressTestRequest(BaseModel):
    bond_ids: list[str] = Field(..., min_length=1, max_length=10)
    principal: float = Field(default=10000.0, gt=0)
    days: int = Field(default=365, gt=0, le=7300)
    base_selic: float = Field(default=10.65, gt=0, description="% a.a.")
    base_cdi: float = Field(default=10.65, gt=0, description="% a.a.")
    base_ipca: float = Field(default=4.83, gt=0, description="% a.a.")
    base_igpm: float = Field(default=6.20, gt=0, description="% a.a.")
    # Cenários customizados opcionais — se vazio usa STANDARD_SCENARIOS
    custom_scenarios: list[dict] = Field(default_factory=list)

@router.get("/yield-curve")
async def yield_curve(
    selic: float = Query(default=10.65, description="SELIC % a.a."),
    cdi: float = Query(default=10.65, description="CDI % a.a."),
    ipca: float = Query(default=4.83, description="IPCA % a.a.")
) -> dict:
    """
    Retorna curva de juros DI Futuro com análise contextual.
    Tenta ANBIMA real; fallback para curva sintética baseada na SELIC.
    """
    return await get_yield_curve_with_stress(
        selic=selic / 100,
        cdi=cdi / 100,
        ipca=ipca / 100
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
                    name=s["name"],
                    delta_selic=float(s.get("delta_selic", 0)) / 100,
                    delta_cdi=float(s.get("delta_cdi", 0)) / 100,
                    delta_ipca=float(s.get("delta_ipca", 0)) / 100,
                    delta_igpm=float(s.get("delta_igpm", 0)) / 100,
                    color=s.get("color", "#8899aa")
                )
                for s in body.custom_scenarios
            ]
        except (KeyError, ValueError):
            scenarios = None  # fallback para cenários padrão

    return await run_stress_test(
        bond_ids=body.bond_ids,
        principal=body.principal,
        days=body.days,
        scenarios=scenarios,
        base_selic=body.base_selic / 100,
        base_cdi=body.base_cdi / 100,
        base_ipca=body.base_ipca / 100,
        base_igpm=body.base_igpm / 100
    )


@router.get("/rates/live")
async def get_live_rates(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Retorna taxas BCB atualizadas (SELIC, CDI, IPCA, IGPM)."""
    from finanalytics_ai.application.services.bcb_service import get_taxas_atuais
    rates = await get_taxas_atuais(session)
    return rates

@router.post("/rates/sync")
async def sync_bcb_rates() -> dict:
    """Dispara sync de indicadores BCB manualmente."""
    import os
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from finanalytics_ai.application.services.bcb_service import sync_indicadores
    db_url = os.environ.get("DATABASE_URL","postgresql+asyncpg://finanalytics:secret@postgres:5432/finanalytics")
    engine = create_async_engine(db_url)
    SM = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # 1. Busca HTTP fora de qualquer sessao DB
    from finanalytics_ai.application.services.bcb_service import _fetch_all
    all_data = await _fetch_all()
    # 2. Persiste no banco
    async with SM() as s:
        async with s.begin():
            from finanalytics_ai.application.services.bcb_service import _dt, _flt
            from sqlalchemy import text as _text
            from datetime import date as _date
            result = {}
            for nome, dados in all_data.items():
                ok = 0
                for row in dados:
                    dt = _dt(row.get("data",""))
                    val = _flt(row.get("valor",""))
                    if dt is None or val is None or not isinstance(dt, _date): continue
                    await s.execute(_text("""
                        INSERT INTO macro_indicators (indicador, data, valor)
                        VALUES (:i, :d, :v)
                        ON CONFLICT (indicador, data) DO UPDATE SET valor = EXCLUDED.valor
                    """), {"i": nome, "d": dt, "v": val})
                    ok += 1
                if dados:
                    ultimo = _flt(dados[-1].get("valor",""))
                    if ultimo:
                        anu = round(((1+ultimo/100)**252-1)*100,4) if "diaria" in nome or "diario" in nome else round(((1+ultimo/100)**12-1)*100,4)
                        result[nome] = {"ultimo": ultimo, "anualizado": anu}
    await engine.dispose()
    return {"ok": True, "registros": {k: len(v) for k,v in all_data.items()}, "taxas": result}

@router.get("/rates/historico")
async def get_historico_taxa(
    indicador: str = Query("selic_diaria", description="selic_diaria|cdi_diario|ipca_mensal|igpm_mensal"),
    dias: int = Query(252, ge=30, le=1260),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retorna historico de um indicador para grafico."""
    from finanalytics_ai.application.services.bcb_service import get_historico
    dados = await get_historico(session, indicador, dias)
    return {"indicador": indicador, "dados": dados, "total": len(dados)}

@router.get("/scenarios/default")
async def default_scenarios() -> list[dict]:
    """Retorna a lista de cenários de stress padrão."""
    return [
        {
            "name": s.name,
            "delta_selic": round(s.delta_selic * 100, 2),
            "delta_cdi": round(s.delta_cdi * 100, 2),
            "delta_ipca": round(s.delta_ipca * 100, 2),
            "color": s.color,
        }
        for s in STANDARD_SCENARIOS
    ]

# ── Sprint 30: Carteira RF ────────────────────────────────────────────────────

from datetime import date as _date

from finanalytics_ai.application.services.rf_portfolio_service import RFPortfolioService

class CreatePortfolioRFRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)

class AddHoldingRequest(BaseModel):
    bond_id: str = Field(..., min_length=1)
    invested: float = Field(..., gt=0)
    purchase_date: str = Field(..., description="YYYY-MM-DD")
    # Campos opcionais — preenchidos do catálogo se bond_id for conhecido
    bond_name: str | None = None
    bond_type: str | None = None
    indexer: str | None = None
    issuer: str | None = None
    rate_annual: float | None = Field(None, description="% a.a. ex: 12.5")
    rate_pct_indexer: bool = False
    maturity_date: str | None = Field(None, description="YYYY-MM-DD")
    ir_exempt: bool | None = None
    note: str = ""

def _rf_svc(session) -> RFPortfolioService:
    return RFPortfolioService(session)

@router.post("/portfolio", status_code=201)
async def create_rf_portfolio(
    body: CreatePortfolioRFRequest,
    session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Cria uma nova carteira de Renda Fixa para o usuário."""
    return await _rf_svc(session).create_portfolio(body.user_id, body.name)

@router.get("/portfolio")
async def list_rf_portfolios(
    user_id: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    """Lista todas as carteiras RF do usuário."""
    return await _rf_svc(session).list_portfolios(user_id)

@router.get("/portfolio/{portfolio_id}")
async def get_rf_portfolio(
    portfolio_id: str,
    selic: float = Query(default=10.65),
    cdi: float = Query(default=10.65),
    ipca: float = Query(default=4.83),
    session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    Retorna carteira RF completa com posição atual de cada holding
    (rendimento acumulado calculado com as taxas fornecidas).
    """
    from fastapi import HTTPException

    result = await _rf_svc(session).get_portfolio(
        portfolio_id,
        selic=selic / 100,
        cdi=cdi / 100,
        ipca=ipca / 100
    )
    if result is None:
        raise HTTPException(404, "Carteira não encontrada")
    return result

@router.delete("/portfolio/{portfolio_id}", status_code=204)
async def delete_rf_portfolio(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session)
) -> None:
    await _rf_svc(session).delete_portfolio(portfolio_id)

@router.post("/portfolio/{portfolio_id}/holdings", status_code=201)
async def add_rf_holding(
    portfolio_id: str,
    body: AddHoldingRequest,
    session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Adiciona uma posição à carteira RF."""
    from fastapi import HTTPException

    try:
        purchase = _date.fromisoformat(body.purchase_date)
        maturity = _date.fromisoformat(body.maturity_date) if body.maturity_date else None
    except ValueError as e:
        raise HTTPException(422, f"Data inválida: {e}") from e
    try:
        return await _rf_svc(session).add_holding(
            portfolio_id=portfolio_id,
            bond_id=body.bond_id,
            invested=body.invested,
            purchase_date=purchase,
            bond_name=body.bond_name,
            bond_type=body.bond_type,
            indexer=body.indexer,
            issuer=body.issuer,
            rate_annual=body.rate_annual / 100 if body.rate_annual else None,
            rate_pct_indexer=body.rate_pct_indexer,
            maturity_date=maturity,
            ir_exempt=body.ir_exempt,
            note=body.note
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

@router.delete("/portfolio/{portfolio_id}/holdings/{holding_id}", status_code=204)
async def delete_rf_holding(
    portfolio_id: str,
    holding_id: str,
    session: AsyncSession = Depends(get_db_session)
) -> None:
    await _rf_svc(session).delete_holding(holding_id, portfolio_id)

@router.get("/portfolio/{portfolio_id}/diversification")
async def rf_diversification(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Relatório de diversificação com score, alertas e recomendações."""
    from fastapi import HTTPException

    result = await _rf_svc(session).diversification_report(portfolio_id)
    if result is None:
        raise HTTPException(404, "Carteira não encontrada")
    return result

@router.get("/portfolio/{portfolio_id}/maturities")
async def rf_maturities(
    portfolio_id: str,
    selic: float = Query(default=10.65),
    cdi: float = Query(default=10.65),
    ipca: float = Query(default=4.83),
    session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    """Timeline de vencimentos com valor projetado na data de vencimento."""
    from fastapi import HTTPException

    result = await _rf_svc(session).maturities_timeline(
        portfolio_id,
        selic=selic / 100,
        cdi=cdi / 100,
        ipca=ipca / 100
    )
    if result is None:
        raise HTTPException(404, "Carteira não encontrada")
    return result

@router.get("/portfolio/{portfolio_id}/fgc")
async def rf_fgc_analysis(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    Análise de cobertura FGC da carteira de renda fixa.

    Retorna:
    - Status por holding (coberto / garantia soberana / sem cobertura)
    - Status por instituição (dentro/acima do limite de R$ 250k)
    - Alertas: excesso por inst., total > R$ 1M, títulos sem FGC
    - Score de proteção 0–100
    """
    from fastapi import HTTPException

    result = await _rf_svc(session).fgc_analysis(portfolio_id)
    if result is None:
        raise HTTPException(404, "Carteira não encontrada")
    return result

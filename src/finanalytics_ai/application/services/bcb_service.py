"""bcb_service — sync BCB SGS"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any
import httpx, structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
BCB_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados?formato=json&dataInicial={ini}&dataFinal={fim}"
SERIES = {"selic_diaria": 11, "cdi_diario": 12, "ipca_mensal": 433, "igpm_mensal": 189}

def _flt(v):
    try: return float(str(v).replace(",","."))
    except: return None

def _dt(v):
    for f in ("%d/%m/%Y","%Y-%m-%d"):
        try: return datetime.strptime(v.strip(),f).date()
        except: pass
    return None

async def _fetch_all() -> dict[str, list]:
    all_data: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for nome, sid in SERIES.items():
            try:
                from datetime import timedelta
                hoje = datetime.now().date()
                ini = (hoje - timedelta(days=365)).strftime("%d/%m/%Y")
                fim = hoje.strftime("%d/%m/%Y")
                r = await c.get(BCB_SGS.format(serie=sid, ini=ini, fim=fim))
                r.raise_for_status()
                all_data[nome] = r.json()
                logger.info("bcb.fetch_ok", indicador=nome, n=len(all_data[nome]))
            except Exception as exc:
                logger.warning("bcb.error", serie=sid, error=str(exc))
    return all_data

async def sync_indicadores(session: AsyncSession) -> dict[str, Any]:
    all_data = await _fetch_all()
    result: dict[str, Any] = {}
    for nome, dados in all_data.items():
        ok = 0
        for row in dados:
            dt = _dt(row.get("data", ""))
            val = _flt(row.get("valor", ""))
            if dt is None or val is None:
                continue
            if not isinstance(dt, date):
                continue
            await session.execute(text("""
                INSERT INTO macro_indicators (indicador, data, valor)
                VALUES (:i, :d, :v)
                ON CONFLICT (indicador, data) DO UPDATE SET valor = EXCLUDED.valor
            """), {"i": nome, "d": dt, "v": val})
            ok += 1
        logger.info("bcb.ok", indicador=nome, registros=ok)
        if dados:
            ultimo = _flt(dados[-1].get("valor", ""))
            if ultimo:
                if "diaria" in nome or "diario" in nome:
                    anu = round(((1 + ultimo / 100) ** 252 - 1) * 100, 4)
                else:
                    anu = round(((1 + ultimo / 100) ** 12 - 1) * 100, 4)
                result[nome] = {"ultimo": ultimo, "anualizado": anu}
    return result

async def get_taxas_atuais(session: AsyncSession) -> dict[str, float]:
    defaults = {"selic": 13.75, "cdi": 13.65, "ipca": 4.83, "igpm": 6.20}
    try:
        rows = await session.execute(text("""
            SELECT indicador, valor FROM macro_indicators
            WHERE (indicador, data) IN (
                SELECT indicador, MAX(data) FROM macro_indicators
                WHERE indicador IN ('selic_diaria','cdi_diario','ipca_mensal','igpm_mensal')
                GROUP BY indicador)
        """))
        data = {r.indicador: float(r.valor) for r in rows}
        res = dict(defaults)
        if "selic_diaria" in data: res["selic"] = round(((1+data["selic_diaria"]/100)**252-1)*100, 2)
        if "cdi_diario"   in data: res["cdi"]   = round(((1+data["cdi_diario"]  /100)**252-1)*100, 2)
        if "ipca_mensal"  in data: res["ipca"]  = round(((1+data["ipca_mensal"] /100)**12 -1)*100, 2)
        if "igpm_mensal"  in data: res["igpm"]  = round(((1+data["igpm_mensal"] /100)**12 -1)*100, 2)
        return res
    except: return defaults

async def get_historico(session: AsyncSession, indicador: str, dias: int = 252) -> list[dict]:
    rows = await session.execute(text("""
        SELECT data, valor FROM macro_indicators
        WHERE indicador = :i ORDER BY data DESC LIMIT :n
    """), {"i": indicador, "n": dias})
    return [{"data": str(r.data), "valor": float(r.valor)} for r in reversed(rows.fetchall())]

"""
finanalytics_ai.application.services.fundos_cvm_service
────────────────────────────────────────────────────────
Sincroniza dados de fundos de investimento a partir do Portal Dados Abertos CVM.

Fontes:
  - Cadastro: https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv
  - Informe diário: https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{AAAAMM}.zip

Decisões de design:
  - CSV/ZIP sem autenticação — nenhuma API key necessária
  - Processamento em chunks (10k linhas) para não explodir memória com o CSV de cadastro (~200MB)
  - Idempotência via ON CONFLICT DO UPDATE — re-rodar não duplica
  - Rentabilidade calculada localmente (não depende de API externa)
  - Sync incremental: só baixa o mês atual e o anterior se ainda não sincronizados
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from typing import Any, AsyncGenerator

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

CVM_BASE = "https://dados.cvm.gov.br/dados/FI"
CAD_URL = f"{CVM_BASE}/CAD/DADOS/cad_fi.csv"
INF_URL = f"{CVM_BASE}/DOC/INF_DIARIO/DADOS/inf_diario_fi_{{AAAAMM}}.zip"

CHUNK = 5_000   # linhas por batch de upsert
TIMEOUT = 120.0


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe_dec(val: str) -> float | None:
    if not val or val.strip() in ("", "-"):
        return None
    try:
        return float(val.replace(",", "."))
    except ValueError:
        return None


def _safe_int(val: str) -> int | None:
    if not val or val.strip() in ("", "-"):
        return None
    try:
        return int(float(val.replace(",", ".")))
    except ValueError:
        return None


def _safe_date(val: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


async def _log_sync(
    session: AsyncSession,
    competencia: str,
    tipo: str,
    status: str,
    registros: int | None = None,
    erro: str | None = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO fundos_sync_log (competencia, tipo, status, registros, erro)
            VALUES (:c, :t, :s, :r, :e)
        """),
        {"c": competencia, "t": tipo, "s": status, "r": registros, "e": erro},
    )
    await session.commit()


# ── cadastro ─────────────────────────────────────────────────────────────────

async def sync_cadastro(session: AsyncSession) -> dict[str, Any]:
    """
    Baixa cad_fi.csv e faz upsert em fundos_cadastro.
    Retorna dict com contagens.
    """
    log = logger.bind(task="sync_cadastro")
    log.info("cvm.cadastro.start")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(CAD_URL)
            resp.raise_for_status()
    except Exception as exc:
        await _log_sync(session, "cadastro", "cadastro", "erro", erro=str(exc))
        raise

    lines = resp.content.decode('latin-1').splitlines()
    if not lines:
        raise ValueError("CSV de cadastro vazio")

    header = [h.strip() for h in lines[0].split(";")]
    total = 0
    batch: list[dict] = []

    for raw in lines[1:]:
        cols = raw.split(";")
        if len(cols) < 5:
            continue

        row: dict[str, Any] = dict(zip(header, cols))

        record = {
            "cnpj":            row.get("CNPJ_FUNDO", "").strip(),
            "denominacao":     row.get("DENOM_SOCIAL", "").strip() or None,
            "nome_abrev":      row.get("NOME_ABREV", "").strip() or None,
            "tipo":            row.get("TP_FUNDO", "").strip() or None,
            "classe":          row.get("CLASSE", "").strip() or None,
            "situacao":        row.get("SIT", "").strip() or None,
            "data_registro":   _safe_date(row.get("DT_REG", "")),
            "data_cancel":     _safe_date(row.get("DT_CANCEL", "")),
            "gestor":          row.get("GESTOR", "").strip() or None,
            "administrador":   row.get("ADMIN", "").strip() or None,
            "custodiante":     row.get("CUSTODIANTE", "").strip() or None,
            "auditor":         row.get("AUDITOR", "").strip() or None,
            "publico_alvo":    row.get("PUBLICO_ALVO", "").strip() or None,
            "taxa_adm":        _safe_dec(row.get("TAXA_ADM", "")),
            "taxa_perfm":      _safe_dec(row.get("TAXA_PERFM", "")),
            "benchmark":       row.get("RENTAB_FUNDO", "").strip() or None,
            "prazo_resgate":   None,
        }

        if not record["cnpj"]:
            continue

        batch.append(record)

        if len(batch) >= CHUNK:
            await _upsert_cadastro(session, batch)
            total += len(batch)
            batch = []
            log.info("cvm.cadastro.progress", total=total)

    if batch:
        await _upsert_cadastro(session, batch)
        total += len(batch)

    await _log_sync(session, "cadastro", "cadastro", "ok", registros=total)
    log.info("cvm.cadastro.done", total=total)
    return {"registros": total}


async def _upsert_cadastro(session: AsyncSession, batch: list[dict]) -> None:
    await session.execute(
        text("""
            INSERT INTO fundos_cadastro
                (cnpj, denominacao, nome_abrev, tipo, classe, situacao,
                 data_registro, data_cancel, gestor, administrador,
                 custodiante, auditor, publico_alvo,
                 taxa_adm, taxa_perfm, prazo_resgate)
            VALUES
                (:cnpj, :denominacao, :nome_abrev, :tipo, :classe, :situacao,
                 :data_registro, :data_cancel, :gestor, :administrador,
                 :custodiante, :auditor, :publico_alvo,
                 :taxa_adm, :taxa_perfm, :prazo_resgate)
            ON CONFLICT (cnpj) DO UPDATE SET
                denominacao   = EXCLUDED.denominacao,
                nome_abrev    = EXCLUDED.nome_abrev,
                tipo          = EXCLUDED.tipo,
                classe        = EXCLUDED.classe,
                situacao      = EXCLUDED.situacao,
                data_cancel   = EXCLUDED.data_cancel,
                gestor        = EXCLUDED.gestor,
                administrador = EXCLUDED.administrador,
                custodiante   = EXCLUDED.custodiante,
                auditor       = EXCLUDED.auditor,
                publico_alvo  = EXCLUDED.publico_alvo,
                taxa_adm      = EXCLUDED.taxa_adm,
                taxa_perfm    = EXCLUDED.taxa_perfm,
                prazo_resgate = EXCLUDED.prazo_resgate,
                updated_at    = NOW()
        """),
        batch,
    )
    await session.commit()


# ── informe diário ────────────────────────────────────────────────────────────

async def sync_informe_diario(
    session: AsyncSession,
    competencia: str | None = None,
) -> dict[str, Any]:
    """
    Baixa inf_diario_fi_AAAAMM.zip e faz upsert em fundos_informe_diario.
    competencia: 'AAAAMM' (ex: '202403') — padrão = mês atual
    """
    if competencia is None:
        now = datetime.now(tz=timezone.utc)
        competencia = now.strftime("%Y%m")

    log = logger.bind(task="sync_informe_diario", competencia=competencia)

    # Verifica se já sincronizou hoje
    row = await session.execute(
        text("""
            SELECT id FROM fundos_sync_log
            WHERE competencia = :c AND tipo = 'informe_diario' AND status = 'ok'
              AND created_at >= CURRENT_DATE
            LIMIT 1
        """),
        {"c": competencia},
    )
    if row.first():
        log.info("cvm.informe_diario.skip", reason="ja_sincronizado_hoje")
        return {"skipped": True, "competencia": competencia}

    url = INF_URL.format(AAAAMM=competencia)
    log.info("cvm.informe_diario.download", url=url)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as exc:
        await _log_sync(session, competencia, "informe_diario", "erro", erro=str(exc))
        raise

    # Extrai CSV do ZIP
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
        if not csv_name:
            raise ValueError(f"ZIP sem CSV: {zf.namelist()}")
        csv_bytes = zf.read(csv_name)

    lines = csv_bytes.decode("latin-1").splitlines()
    if not lines:
        raise ValueError("CSV de informe diário vazio")

    header = [h.strip() for h in lines[0].split(";")]
    total = 0
    batch: list[dict] = []

    for raw in lines[1:]:
        cols = raw.split(";")
        if len(cols) < 5:
            continue

        row_d: dict[str, Any] = dict(zip(header, cols))
        dt = _safe_date(row_d.get("DT_COMPTC", ""))
        cnpj = (row_d.get("CNPJ_FUNDO_CLASSE") or row_d.get("CNPJ_FUNDO") or "").strip()
        if not cnpj or not dt:
            continue

        batch.append({
            "cnpj":            cnpj,
            "data_ref":        dt,
            "vl_total":        _safe_dec(row_d.get("VL_TOTAL", "")),
            "vl_quota":        _safe_dec(row_d.get("VL_QUOTA", "")),
            "vl_patrim_liq":   _safe_dec(row_d.get("VL_PATRIM_LIQ", "")),
            "captacao_dia":    _safe_dec(row_d.get("CAPTC_DIA", "")),
            "resgat_dia":      _safe_dec(row_d.get("RESG_DIA", "")),
            "nr_cotst":        _safe_int(row_d.get("NR_COTST", "")),
        })

        if len(batch) >= CHUNK:
            await _upsert_informe(session, batch)
            total += len(batch)
            batch = []

    if batch:
        await _upsert_informe(session, batch)
        total += len(batch)

    # Atualiza PL e cotistas no cadastro
    await session.execute(text("""
        UPDATE fundos_cadastro fc
        SET pl_atual  = fi.vl_patrim_liq,
            cotistas  = fi.nr_cotst,
            updated_at = NOW()
        FROM (
            SELECT DISTINCT ON (cnpj)
                   cnpj, vl_patrim_liq, nr_cotst
            FROM   fundos_informe_diario
            WHERE  data_ref = (SELECT MAX(data_ref) FROM fundos_informe_diario)
            ORDER  BY cnpj, data_ref DESC
        ) fi
        WHERE fc.cnpj = fi.cnpj
    """))
    await session.commit()

    await _log_sync(session, competencia, "informe_diario", "ok", registros=total)
    log.info("cvm.informe_diario.done", total=total, competencia=competencia)
    return {"registros": total, "competencia": competencia}


async def _upsert_informe(session: AsyncSession, batch: list[dict]) -> None:
    await session.execute(
        text("""
            INSERT INTO fundos_informe_diario
                (cnpj, data_ref, vl_total, vl_quota, vl_patrim_liq,
                 captacao_dia, resgat_dia, nr_cotst)
            VALUES
                (:cnpj, :data_ref, :vl_total, :vl_quota, :vl_patrim_liq,
                 :captacao_dia, :resgat_dia, :nr_cotst)
            ON CONFLICT (cnpj, data_ref) DO UPDATE SET
                vl_total      = EXCLUDED.vl_total,
                vl_quota      = EXCLUDED.vl_quota,
                vl_patrim_liq = EXCLUDED.vl_patrim_liq,
                captacao_dia  = EXCLUDED.captacao_dia,
                resgat_dia    = EXCLUDED.resgat_dia,
                nr_cotst      = EXCLUDED.nr_cotst
        """),
        batch,
    )
    await session.commit()


# ── rentabilidade ─────────────────────────────────────────────────────────────

async def calcular_rentabilidade(
    session: AsyncSession,
    cnpj: str,
) -> dict[str, Any]:
    """
    Calcula e persiste rentabilidade de um fundo a partir das cotas diárias.
    """
    rows = await session.execute(
        text("""
            SELECT data_ref, vl_quota
            FROM   fundos_informe_diario
            WHERE  cnpj = :cnpj AND vl_quota > 0
            ORDER  BY data_ref ASC
        """),
        {"cnpj": cnpj},
    )
    records = rows.fetchall()
    if len(records) < 2:
        return {"cnpj": cnpj, "error": "dados insuficientes"}

    import statistics

    def _cota(offset_days: int) -> float | None:
        target = records[-1][0]
        from datetime import timedelta
        cutoff = target - timedelta(days=offset_days)
        for dt, cota in reversed(records):
            if dt <= cutoff:
                return float(cota)
        return None

    last_cota = float(records[-1][1])
    prev_cota = float(records[-2][1])

    def _rent(base: float | None) -> float | None:
        if base is None or base == 0:
            return None
        return round((last_cota / base - 1) * 100, 6)

    # Volatilidade 12m (desvio padrão dos retornos diários)
    vol_12m = None
    rent_12m_records = [r for r in records
                        if (records[-1][0] - r[0]).days <= 365]
    if len(rent_12m_records) > 20:
        daily_rets = [
            float(rent_12m_records[i][1]) / float(rent_12m_records[i-1][1]) - 1
            for i in range(1, len(rent_12m_records))
            if float(rent_12m_records[i-1][1]) > 0
        ]
        if daily_rets:
            vol_12m = round(statistics.stdev(daily_rets) * (252 ** 0.5) * 100, 6)

    result = {
        "cnpj": cnpj,
        "data_ref": records[-1][0],
        "rent_dia": _rent(prev_cota),
        "rent_mes": _rent(_cota(30)),
        "rent_ano": _rent(_cota(365)),    # ano calendario simples
        "rent_12m": _rent(_cota(365)),
        "rent_24m": _rent(_cota(730)),
        "rent_36m": _rent(_cota(1095)),
        "volatilidade_12m": vol_12m,
    }

    await session.execute(
        text("""
            INSERT INTO fundos_rentabilidade
                (cnpj, data_ref, rent_dia, rent_mes, rent_ano,
                 rent_12m, rent_24m, rent_36m, volatilidade_12m)
            VALUES
                (:cnpj, :data_ref, :rent_dia, :rent_mes, :rent_ano,
                 :rent_12m, :rent_24m, :rent_36m, :volatilidade_12m)
            ON CONFLICT (cnpj, data_ref) DO UPDATE SET
                rent_dia       = EXCLUDED.rent_dia,
                rent_mes       = EXCLUDED.rent_mes,
                rent_ano       = EXCLUDED.rent_ano,
                rent_12m       = EXCLUDED.rent_12m,
                rent_24m       = EXCLUDED.rent_24m,
                rent_36m       = EXCLUDED.rent_36m,
                volatilidade_12m = EXCLUDED.volatilidade_12m
        """),
        result,
    )
    await session.commit()
    return result

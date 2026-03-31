"""
finanalytics_ai.workers.maintenance_worker
==========================================

Worker de manutencao de dados — executa apos cada sync Fintz.

Responsabilidades:
  1. Atualiza IBOV via Fintz /indices/historico (nao vem no bulk parquet)
  2. Sincroniza ohlc_prices a partir de fintz_cotacoes
  3. Atualiza materialized view fintz_indicadores_dedup (deduplicada)
  4. Valida integridade dos dados (gaps, nulos criticos, anomalias)
  5. Re-computa ML features para todos os tickers ativos
  6. Exporta relatorio de saude dos dados

Agendamento:
  - Chamado pelo fintz_sync_worker.py apos run_sync()
  - Pode ser executado standalone: python -m finanalytics_ai.workers.maintenance_worker
  - Variavel MAINTENANCE_DRY_RUN=true apenas valida sem alterar dados

Idempotencia:
  - Todas as operacoes usam ON CONFLICT DO UPDATE/NOTHING
  - Re-executar e seguro
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any

import aiohttp
import psycopg2
import structlog

log = structlog.get_logger(__name__)

# ── Configuracao ──────────────────────────────────────────────────────────────

PG_DSN      = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://") \
              or "postgresql://finanalytics:secret@postgres:5432/finanalytics"
FINTZ_KEY   = os.getenv("FINTZ_API_KEY", "")
FINTZ_BASE  = os.getenv("FINTZ_BASE_URL", "https://api.fintz.com.br")
DRY_RUN     = os.getenv("MAINTENANCE_DRY_RUN", "false").lower() == "true"

# Tickers monitorados pelo sistema (ampliavel via .env)
TICKERS_ML  = [t.strip() for t in os.getenv(
    "ML_TICKERS",
    "PETR4,VALE3,ITUB4,BBDC4,ABEV3,WEGE3,BBAS3,LREN3,RENT3,ITSA4"
).split(",") if t.strip()]

# Datas mensais para re-calculo de features (ultimos 2 anos)
def _monthly_dates() -> list[date]:
    """Retorna datas mensais dos ultimos 24 meses ate hoje."""
    dates = []
    today = date.today()
    for i in range(24):
        d = (today.replace(day=1) - timedelta(days=1)) if i == 0 else dates[-1].replace(day=1) - timedelta(days=1)
        # Ultimo dia util aproximado do mes
        dates.append(d.replace(day=min(d.day, 28)))
    return sorted(dates)


# ── Resultado de cada etapa ───────────────────────────────────────────────────

@dataclass
class StepResult:
    name: str
    ok: bool = True
    rows_affected: int = 0
    duration_s: float = 0.0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class MaintenanceReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    steps: list[StepResult] = field(default_factory=list)

    def add(self, result: StepResult) -> None:
        self.steps.append(result)
        emoji = "✓" if result.ok else "✗"
        log.info(
            f"maintenance.step.{result.name}",
            ok=result.ok,
            rows=result.rows_affected,
            duration_s=round(result.duration_s, 2),
            message=result.message,
        )

    @property
    def total_errors(self) -> int:
        return sum(1 for s in self.steps if not s.ok)

    def summary(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "duration_s": round((datetime.now(timezone.utc) - self.started_at).total_seconds(), 2),
            "steps": len(self.steps),
            "errors": self.total_errors,
            "steps_detail": [
                {"name": s.name, "ok": s.ok, "rows": s.rows_affected,
                 "duration_s": round(s.duration_s, 2), "message": s.message}
                for s in self.steps
            ],
        }


# ── Helpers de DB ─────────────────────────────────────────────────────────────

def _pg_conn():
    return psycopg2.connect(PG_DSN)


def _execute(conn, sql: str, params=None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


# ── ETAPA 1: IBOV via Fintz /indices/historico ────────────────────────────────

async def sync_ibov(report: MaintenanceReport) -> None:
    t0 = time.perf_counter()
    step = StepResult(name="ibov_sync")
    try:
        conn = _pg_conn()

        # Descobre ultima data ja sincronizada
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(data) FROM fintz_cotacoes WHERE ticker='IBOV'")
            last_date = cur.fetchone()[0]

        start = (last_date + timedelta(days=1)).isoformat() if last_date else "2010-01-01"
        end   = date.today().isoformat()

        if start > end:
            step.message = f"IBOV ja atualizado ate {last_date}"
            report.add(step)
            conn.close()
            return

        log.info("maintenance.ibov.fetching", start=start, end=end)
        url = f"{FINTZ_BASE}/indices/historico"

        async with aiohttp.ClientSession() as session:
            rows_inserted = 0
            # Busca por chunks de 1 ano para evitar timeout
            from_date = date.fromisoformat(start)
            to_date   = date.fromisoformat(end)
            current   = from_date

            while current <= to_date:
                chunk_end = min(current.replace(year=current.year + 1) - timedelta(days=1), to_date)
                params = {
                    "indice": "IBOV",
                    "dataInicio": current.isoformat(),
                    "dataFim": chunk_end.isoformat(),
                    "ordem": "ASC",
                }
                headers = {"X-API-Key": FINTZ_KEY}

                async with session.get(url, params=params, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        data = await r.json()
                        items = data if isinstance(data, list) else \
                                data.get("historico", data.get("dados", []))
                        if not DRY_RUN:
                            for item in items:
                                dt  = item.get("data") or item.get("date", "")
                                val = item.get("valor") or item.get("fechamento")
                                if dt and val:
                                    _execute(conn, """
                                        INSERT INTO fintz_cotacoes(ticker, data, preco_fechamento_ajustado)
                                        VALUES('IBOV', %s, %s)
                                        ON CONFLICT (ticker, data) DO UPDATE SET
                                            preco_fechamento_ajustado = EXCLUDED.preco_fechamento_ajustado
                                    """, (str(dt)[:10], float(val)))
                                    rows_inserted += 1
                    else:
                        step.warnings.append(f"IBOV chunk {current}: HTTP {r.status}")

                conn.commit()
                current = chunk_end + timedelta(days=1)

        step.rows_affected = rows_inserted
        step.message = f"IBOV: {rows_inserted} novos dias inseridos"
        conn.close()
    except Exception as exc:
        step.ok = False
        step.message = str(exc)
        log.exception("maintenance.ibov.error", error=str(exc))
    finally:
        step.duration_s = time.perf_counter() - t0
        report.add(step)


# ── ETAPA 2: Sincroniza ohlc_prices a partir de fintz_cotacoes ───────────────

def sync_ohlc_prices(report: MaintenanceReport) -> None:
    t0 = time.perf_counter()
    step = StepResult(name="ohlc_prices_sync")
    try:
        conn = _pg_conn()

        # Garante que a tabela existe
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS ohlc_prices (
                ticker    VARCHAR(20) NOT NULL,
                date      DATE        NOT NULL,
                open      NUMERIC(24,4),
                high      NUMERIC(24,4),
                low       NUMERIC(24,4),
                close     NUMERIC(24,4),
                adj_close NUMERIC(24,4),
                volume    NUMERIC(24,2),
                PRIMARY KEY (ticker, date)
            )
        """)

        # Insere/atualiza apenas registros novos (desde ultima data em ohlc_prices)
        if not DRY_RUN:
            rows = _execute(conn, """
                INSERT INTO ohlc_prices(ticker, date, open, high, low, close, adj_close, volume)
                SELECT
                    fc.ticker,
                    fc.data,
                    fc.preco_abertura,
                    fc.preco_maximo,
                    fc.preco_minimo,
                    fc.preco_fechamento,
                    fc.preco_fechamento_ajustado,
                    fc.volume_negociado
                FROM fintz_cotacoes fc
                WHERE fc.ticker != 'IBOV'
                  AND fc.data > COALESCE(
                      (SELECT MAX(op.date) FROM ohlc_prices op WHERE op.ticker = fc.ticker),
                      '2000-01-01'::date
                  )
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open      = EXCLUDED.open,
                    high      = EXCLUDED.high,
                    low       = EXCLUDED.low,
                    close     = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume    = EXCLUDED.volume
            """)
            conn.commit()
            step.rows_affected = rows

        # Verifica cobertura
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT ticker), COUNT(*) FROM ohlc_prices")
            tickers, total = cur.fetchone()
        step.message = f"ohlc_prices: {tickers} tickers, {total:,} total linhas, {step.rows_affected} novos"
        conn.close()
    except Exception as exc:
        step.ok = False
        step.message = str(exc)
        log.exception("maintenance.ohlc_prices.error", error=str(exc))
    finally:
        step.duration_s = time.perf_counter() - t0
        report.add(step)


# ── ETAPA 3: Refresh fintz_indicadores_dedup ─────────────────────────────────

def refresh_dedup_view(report: MaintenanceReport) -> None:
    t0 = time.perf_counter()
    step = StepResult(name="dedup_view_refresh")
    try:
        conn = _pg_conn()

        # Verifica se a view existe
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM pg_matviews
                WHERE matviewname = 'fintz_indicadores_dedup'
            """)
            exists = cur.fetchone()[0] > 0

        if not exists:
            # Cria a view se nao existir
            log.info("maintenance.dedup_view.creating")
            _execute(conn, """
                CREATE MATERIALIZED VIEW IF NOT EXISTS fintz_indicadores_dedup AS
                WITH ranked AS (
                    SELECT
                        ticker, indicador, valor, data_publicacao,
                        LAG(valor) OVER (
                            PARTITION BY ticker, indicador
                            ORDER BY data_publicacao
                        ) AS valor_anterior
                    FROM fintz_indicadores
                )
                SELECT ticker, indicador, valor, data_publicacao
                FROM ranked
                WHERE valor_anterior IS NULL
                   OR ABS(COALESCE(valor,0) - COALESCE(valor_anterior,0)) > 0.000001
            """)
            _execute(conn, """
                CREATE INDEX IF NOT EXISTS ix_fintz_ind_dedup_pit
                    ON fintz_indicadores_dedup (ticker, indicador, data_publicacao DESC)
            """)
            conn.commit()
            step.message = "View criada (primeira vez)"
        elif not DRY_RUN:
            # Refresh incremental
            _execute(conn, "REFRESH MATERIALIZED VIEW fintz_indicadores_dedup")
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM fintz_indicadores_dedup")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM fintz_indicadores")
                original = cur.fetchone()[0]

            reducao = round((1 - total / original) * 100, 1) if original > 0 else 0
            step.rows_affected = total
            step.message = f"Dedup: {total:,} linhas ({reducao}% reducao vs {original:,} originais)"

        conn.close()
    except Exception as exc:
        step.ok = False
        step.message = str(exc)
        log.exception("maintenance.dedup_view.error", error=str(exc))
    finally:
        step.duration_s = time.perf_counter() - t0
        report.add(step)


# ── ETAPA 4: Validacao de integridade ─────────────────────────────────────────

def validate_data_integrity(report: MaintenanceReport) -> None:
    t0 = time.perf_counter()
    step = StepResult(name="integrity_check")
    try:
        conn = _pg_conn()
        issues = []

        with conn.cursor() as cur:
            # 1. IBOV — dias sem dados nos ultimos 30 dias uteis
            cur.execute("""
                SELECT COUNT(*) FROM (
                    SELECT generate_series(
                        CURRENT_DATE - INTERVAL '30 days', CURRENT_DATE, '1 day'::interval
                    )::date AS d
                ) dias
                WHERE EXTRACT(DOW FROM d) NOT IN (0, 6)
                  AND NOT EXISTS (
                    SELECT 1 FROM fintz_cotacoes
                    WHERE ticker = 'IBOV' AND data = dias.d
                  )
            """)
            ibov_gaps = cur.fetchone()[0]
            if ibov_gaps > 3:
                issues.append(f"IBOV: {ibov_gaps} dias sem dados (ultimos 30 dias)")

            # 2. Tickers ML — cobertura de features
            for ticker in TICKERS_ML[:5]:  # amostra
                cur.execute("""
                    SELECT COUNT(*), COUNT(*) FILTER (WHERE beta_60d IS NOT NULL),
                           COUNT(*) FILTER (WHERE pe IS NOT NULL)
                    FROM ml_features WHERE ticker = %s
                """, (ticker,))
                total, com_beta, com_pe = cur.fetchone()
                if total == 0:
                    issues.append(f"{ticker}: sem features ML")
                elif com_beta == 0:
                    issues.append(f"{ticker}: beta_60d todos nulos")

            # 3. fintz_indicadores — ultima atualizacao
            cur.execute("""
                SELECT MAX(data_publicacao),
                       CURRENT_DATE - MAX(data_publicacao) AS dias_atraso
                FROM fintz_indicadores
                WHERE indicador = 'P_L'
            """)
            last_update, atraso = cur.fetchone()
            if atraso and atraso > 5:
                issues.append(f"fintz_indicadores P_L: {atraso} dias sem atualizacao")

            # 4. ohlc_prices — integridade
            cur.execute("""
                SELECT COUNT(*) FROM ohlc_prices
                WHERE close IS NOT NULL AND close <= 0
            """)
            precos_invalidos = cur.fetchone()[0]
            if precos_invalidos > 0:
                issues.append(f"ohlc_prices: {precos_invalidos} precos invalidos (close=NULL ou 0)")

        conn.close()

        if issues:
            step.warnings = issues
            step.message = f"{len(issues)} problemas encontrados: {'; '.join(issues[:3])}"
            log.warning("maintenance.integrity.issues", issues=issues)
        else:
            step.message = "Integridade OK — sem problemas detectados"

    except Exception as exc:
        step.ok = False
        step.message = str(exc)
        log.exception("maintenance.integrity.error", error=str(exc))
    finally:
        step.duration_s = time.perf_counter() - t0
        report.add(step)


# ── ETAPA 5: Re-computa ML features ──────────────────────────────────────────

async def recompute_ml_features(report: MaintenanceReport) -> None:
    t0 = time.perf_counter()
    step = StepResult(name="ml_features_recompute")
    try:
        if DRY_RUN:
            step.message = "DRY_RUN: pulando recomputo de features"
            report.add(step)
            return

        # Importa dependencias ML
        sys.path.insert(0, "/app/src")
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.infrastructure.ml.feature_repo import SqlFeatureRepository
        from finanalytics_ai.application.ml.feature_pipeline import build_features_from_ohlc

        factory = get_session_factory()
        total_ok = 0
        total_err = 0

        # Datas: ultimos 12 meses mensais
        today = date.today()
        dates_to_compute = []
        for i in range(12):
            d = (today.replace(day=1) - timedelta(days=1))
            for _ in range(i):
                d = (d.replace(day=1) - timedelta(days=1))
            dates_to_compute.append(d)
        dates_to_compute = sorted(set(dates_to_compute))

        async with factory() as session:
            repo = SqlFeatureRepository(session)
            ibov_rets_cache: dict[date, list[float]] = {}

            for ref in dates_to_compute:
                if ref not in ibov_rets_cache:
                    ibov_rets_cache[ref] = await repo.get_ibov_returns(ref)

                for ticker in TICKERS_ML:
                    try:
                        ohlc = await repo.get_ohlc_window(ticker, ref)
                        if len(ohlc) < 30:
                            continue
                        closes  = [float(r["close"])  for r in ohlc if r["close"]  is not None]
                        volumes = [float(r["volume"]) if r["volume"] is not None else None
                                   for r in ohlc]
                        fund    = await repo.get_fundamental_features(ticker, ref)
                        features = build_features_from_ohlc(
                            ticker=ticker,
                            date=datetime.combine(ref, datetime.min.time()).replace(tzinfo=timezone.utc),
                            closes=closes,
                            volumes=volumes,
                            ibov_rets=ibov_rets_cache[ref],
                            fundamental=fund,
                        )
                        await repo.upsert_features([features])
                        total_ok += 1
                    except Exception as e:
                        total_err += 1
                        log.warning("maintenance.ml.feature_error",
                                    ticker=ticker, date=ref, error=str(e))

        step.rows_affected = total_ok
        step.message = f"Features: {total_ok} ok, {total_err} erros"

    except Exception as exc:
        step.ok = False
        step.message = str(exc)
        log.exception("maintenance.ml_features.error", error=str(exc))
    finally:
        step.duration_s = time.perf_counter() - t0
        report.add(step)


# ── Orquestrador principal ────────────────────────────────────────────────────

async def run_maintenance(skip_ml: bool = False) -> MaintenanceReport:
    """
    Executa todas as etapas de manutencao em sequencia.
    Retorna o relatorio completo.
    """
    report = MaintenanceReport()
    log.info("maintenance.started", dry_run=DRY_RUN, skip_ml=skip_ml)

    # 1. IBOV (async — faz chamada HTTP)
    await sync_ibov(report)

    # 2. ohlc_prices (sync — SQL puro)
    sync_ohlc_prices(report)

    # 3. Refresh dedup view (sync — SQL)
    refresh_dedup_view(report)

    # 4. Validacao de integridade
    validate_data_integrity(report)

    # 5. ML features (async — pesado, opcional)
    if not skip_ml:
        await recompute_ml_features(report)

    summary = report.summary()
    log.info(
        "maintenance.completed",
        duration_s=summary["duration_s"],
        steps=summary["steps"],
        errors=summary["errors"],
    )

    if report.total_errors > 0:
        log.warning(
            "maintenance.completed_with_errors",
            errors=[s.name for s in report.steps if not s.ok],
        )

    return report


def main() -> None:
    """Ponto de entrada standalone."""
    import logging
    logging.basicConfig(level=logging.INFO)

    skip_ml = os.getenv("MAINTENANCE_SKIP_ML", "false").lower() == "true"

    report = asyncio.run(run_maintenance(skip_ml=skip_ml))
    summary = report.summary()

    print("\n" + "=" * 50)
    print("RELATORIO DE MANUTENCAO")
    print("=" * 50)
    for s in report.steps:
        status = "OK" if s.ok else "ERRO"
        print(f"  [{status}] {s.name}: {s.message} ({s.duration_s:.1f}s)")
        for w in s.warnings:
            print(f"       AVISO: {w}")
    print(f"\nTotal: {summary['steps']} etapas | "
          f"{summary['errors']} erros | "
          f"{summary['duration_s']}s")
    print("=" * 50)

    sys.exit(1 if report.total_errors > 0 else 0)


if __name__ == "__main__":
    main()
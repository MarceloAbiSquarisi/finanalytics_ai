"""
finanalytics_ai.infrastructure.adapters.cvm_client
────────────────────────────────────────────────────
Adapter para a API pública de Dados Abertos da CVM.
https://dados.cvm.gov.br/

Endpoints utilizados (todos gratuitos, sem autenticação):

  Informe Diário de Fundos (CVM/FundosNET):
    https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/
    CSV mensal com: CNPJ_FUNDO, DT_COMPTC, VL_QUOTA, VL_PATRIM_LIQ,
                    CAPTC_DIA, RESG_DIA, NR_COTST

  Cadastro de Fundos:
    https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv
    Mestre com: CNPJ_FUNDO, DENOM_SOCIAL, TP_FUNDO, SIT, CLASSE,
                DT_INI_ATIV, DT_CANCEL, ADMIN, GESTOR, AUDITOR

  Informes Mensais (Carteira):
    https://dados.cvm.gov.br/dados/FI/DOC/INF_MENSAL/DADOS/

Design decisions:

  CSV streaming com chunks:
    Os arquivos mensais de informe diário chegam a 150 MB.
    Usamos pandas read_csv com chunksize=50_000 para evitar OOM.
    O processamento é feito em asyncio.to_thread para não bloquear
    o event loop (pandas é síncrono).

  Cache por arquivo (TTL 12h para cadastro, 30min para informe):
    O cadastro de fundos muda raramente (novas CRIs/CRAs, cancelamentos).
    O informe diário do mês corrente é atualizado pela CVM ao longo do dia.
    Arquivos de meses passados são imutáveis — TTL de 24h.

  Formato de retorno normalizado:
    Retornamos dicts com chaves snake_case em português para manter
    consistência com o domínio financeiro brasileiro do projeto.
    Valores monetários como float (não Decimal) — suficiente para
    exibição e análise; persistência com Decimal é responsabilidade
    do repositório de destino.

  Encoding ISO-8859-1:
    Todos os CSVs da CVM usam ISO-8859-1 (Latin-1). Ler como UTF-8
    corromperia nomes com acentuação (ex: "AÇÃO", "GESTÃO").

  Sem tenacity aqui:
    A CVM não tem SLA garantido. Se falhar, retornamos lista vazia
    e logamos warning. O serviço de análise de fundos não deve
    derrubar a aplicação por indisponibilidade da CVM.
"""

from __future__ import annotations

import asyncio
import io
import time
from datetime import date, datetime
from typing import Any

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_CVM_BASE = "https://dados.cvm.gov.br/dados/FI"
_CAD_URL = f"{_CVM_BASE}/CAD/DADOS/cad_fi.csv"
_INF_DIARIO_BASE = f"{_CVM_BASE}/DOC/INF_DIARIO/DADOS"

_CACHE_CAD_TTL = 43_200   # 12h — cadastro muda raramente
_CACHE_INF_TTL = 1_800    # 30min — informe do mês atual atualizado ao longo do dia
_CACHE_HIST_TTL = 86_400  # 24h — meses passados são imutáveis

_HTTP_TIMEOUT = 120.0     # CSVs grandes (cadastro ~150MB) precisam de timeout generoso
_CHUNK_SIZE = 50_000      # linhas por chunk ao processar CSV grande
_MAX_RETRIES = 3          # tentativas para arquivos grandes

# Situações de fundo ativo na CVM — filtro robusto com strip e casefold
# Os valores reais no CSV podem ter espaços extras ou capitalização diferente
_SITUACOES_ATIVAS = {
    "EM FUNCIONAMENTO NORMAL",
    "FASE PRÉ-OPERACIONAL",
    "FASE PRE-OPERACIONAL",   # sem acento (encoding alternativo)
    "EM FUNCIONAMENTO",       # versão abreviada que alguns registros usam
    "NORMAL",
}


class CvmClient:
    """
    Adapter assíncrono para a API pública da CVM (dados.cvm.gov.br).

    Principais métodos:
      get_fund_register()     → cadastro completo de fundos
      get_daily_report()      → informe diário do mês atual
      get_daily_report_month()→ informe diário de um mês específico
      search_fund()           → busca por nome ou CNPJ
      get_fund_quota_series() → série histórica de cotas
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}

    # ── Cadastro de Fundos ────────────────────────────────────────────────────

    async def get_fund_register(self, only_active: bool = True) -> list[dict[str, Any]]:
        """
        Retorna o cadastro de fundos da CVM.

        Args:
            only_active: se True, filtra apenas fundos em funcionamento normal.

        Returns:
            Lista de dicts com: cnpj, nome, tipo, classe, situacao,
            data_inicio, data_cancelamento, administrador, gestor.
        """
        cache_key = f"cad_{only_active}"
        cached = self._get_cache(cache_key, _CACHE_CAD_TTL)
        if cached is not None:
            return cached  # type: ignore[return-value]

        logger.info("cvm.cadastro.fetching")
        try:
            csv_bytes = await self._download(_CAD_URL)
            result = await asyncio.to_thread(
                self._parse_cadastro, csv_bytes, only_active
            )
            self._set_cache(cache_key, result)
            logger.info("cvm.cadastro.ok", fundos=len(result), only_active=only_active)
            return result
        except Exception as exc:
            logger.warning("cvm.cadastro.failed", error=str(exc)[:120])
            return []

    def _parse_cadastro(
        self, csv_bytes: bytes, only_active: bool
    ) -> list[dict[str, Any]]:
        df = pd.read_csv(
            io.BytesIO(csv_bytes),
            sep=";",
            encoding="iso-8859-1",
            dtype=str,
            low_memory=False,
        )
        if only_active and "SIT" in df.columns:
            # strip() remove espaços extras que o CSV pode ter
            # Verificamos se algum dos termos de ativo está contido no valor
            def is_active(sit: str) -> bool:
                s = str(sit).strip().upper()
                return (
                    s in _SITUACOES_ATIVAS
                    or "FUNCIONAMENTO" in s
                    or "PRÉ-OPERACIONAL" in s
                    or "PRE-OPERACIONAL" in s
                )
            df = df[df["SIT"].apply(is_active)]

        result = []
        for _, row in df.iterrows():
            result.append({
                "cnpj":               row.get("CNPJ_FUNDO", ""),
                "nome":               row.get("DENOM_SOCIAL", ""),
                "tipo":               row.get("TP_FUNDO", ""),
                "classe":             row.get("CLASSE", ""),
                "situacao":           row.get("SIT", ""),
                "data_inicio":        row.get("DT_INI_ATIV", ""),
                "data_cancelamento":  row.get("DT_CANCEL", ""),
                "administrador":      row.get("ADMIN", ""),
                "gestor":             row.get("GESTOR", ""),
                "auditor":            row.get("AUDITOR", ""),
                "custodiante":        row.get("CUSTODIANTE", ""),
                "cnpj_admin":         row.get("CNPJ_ADMIN", ""),
                "cnpj_gestor":        row.get("CNPJ_GESTOR", ""),
                "taxa_adm":           _safe_float(row.get("TAXA_ADM")),
                "taxa_perfm":         _safe_float(row.get("TAXA_PERFM")),
            })
        return result

    # ── Informe Diário ────────────────────────────────────────────────────────

    async def get_daily_report(
        self,
        cnpj: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Retorna o informe diário do mês atual ou anterior.

        A CVM publica o arquivo do mês corrente com alguns dias de atraso
        e alguns servidores bloqueiam requests de data centers (Docker).
        Tenta o mês atual primeiro; se falhar com 403, tenta o mês anterior.

        Args:
            cnpj: filtra por CNPJ do fundo.
            limit: máximo de registros retornados.
        """
        today = date.today()
        # Tenta mês atual primeiro
        rows = await self.get_daily_report_month(today.year, today.month, cnpj, limit)
        if rows:
            return rows

        # Fallback: mês anterior (sempre publicado e mais estável)
        month = today.month - 1
        year = today.year
        if month == 0:
            month = 12
            year -= 1
        logger.info("cvm.informe_diario.fallback_previous_month", year=year, month=month)
        return await self.get_daily_report_month(year, month, cnpj, limit)

    async def get_daily_report_month(
        self,
        year: int,
        month: int,
        cnpj: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """
        Retorna informe diário de um mês específico.

        A CVM disponibiliza um arquivo por mês no formato:
          inf_diario_fi_YYYYMM.csv
        """
        ym = f"{year}{month:02d}"
        cache_key = f"inf_{ym}_{cnpj or 'all'}_{limit}"

        # Meses passados: TTL longo (imutáveis)
        today = date.today()
        is_current = (year == today.year and month == today.month)
        ttl = _CACHE_INF_TTL if is_current else _CACHE_HIST_TTL

        cached = self._get_cache(cache_key, ttl)
        if cached is not None:
            return cached  # type: ignore[return-value]

        url = f"{_INF_DIARIO_BASE}/inf_diario_fi_{ym}.csv"
        logger.info("cvm.informe_diario.fetching", ym=ym, cnpj=cnpj)

        try:
            csv_bytes = await self._download(url)
            cnpj_clean = _clean_cnpj(cnpj) if cnpj else None
            result = await asyncio.to_thread(
                self._parse_informe, csv_bytes, cnpj_clean, limit
            )
            self._set_cache(cache_key, result)
            logger.info("cvm.informe_diario.ok", ym=ym, rows=len(result))
            return result
        except Exception as exc:
            logger.warning("cvm.informe_diario.failed", ym=ym, error=str(exc)[:120])
            return []

    def _parse_informe(
        self,
        csv_bytes: bytes,
        cnpj_clean: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        chunks = pd.read_csv(
            io.BytesIO(csv_bytes),
            sep=";",
            encoding="iso-8859-1",
            dtype=str,
            chunksize=_CHUNK_SIZE,
        )
        rows: list[dict[str, Any]] = []

        for chunk in chunks:
            if cnpj_clean:
                # CNPJ na CVM: "XX.XXX.XXX/XXXX-XX" ou apenas dígitos
                mask = chunk["CNPJ_FUNDO"].str.replace(r"\D", "", regex=True) == cnpj_clean
                chunk = chunk[mask]

            for _, row in chunk.iterrows():
                rows.append({
                    "cnpj":           row.get("CNPJ_FUNDO", ""),
                    "data":           row.get("DT_COMPTC", ""),
                    "vl_quota":       _safe_float(row.get("VL_QUOTA")),
                    "vl_patrim_liq":  _safe_float(row.get("VL_PATRIM_LIQ")),
                    "captacao_dia":   _safe_float(row.get("CAPTC_DIA")),
                    "resgate_dia":    _safe_float(row.get("RESG_DIA")),
                    "nr_cotistas":    _safe_int(row.get("NR_COTST")),
                })
                if len(rows) >= limit:
                    return rows

        return rows

    # ── Série Histórica de Cotas ──────────────────────────────────────────────

    async def get_fund_quota_series(
        self,
        cnpj: str,
        months: int = 12,
    ) -> list[dict[str, Any]]:
        """
        Retorna série histórica de cotas de um fundo.

        Busca os últimos N meses de informes diários e concatena.
        Útil para calcular rentabilidade e comparar com benchmarks.

        Args:
            cnpj: CNPJ do fundo.
            months: número de meses para trás (padrão: 12).

        Returns:
            Lista de dicts {data, vl_quota, vl_patrim_liq, nr_cotistas}
            ordenada por data ASC.
        """
        today = date.today()
        all_rows: list[dict[str, Any]] = []

        for i in range(months):
            # Retrocede i meses
            month = today.month - i
            year = today.year
            while month <= 0:
                month += 12
                year -= 1

            rows = await self.get_daily_report_month(year, month, cnpj, limit=10_000)
            all_rows.extend(rows)

        # Ordena por data ASC e remove duplicatas
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for row in sorted(all_rows, key=lambda r: r.get("data", "")):
            key = row.get("data", "")
            if key not in seen:
                seen.add(key)
                unique.append(row)

        logger.info("cvm.quota_series.built", cnpj=cnpj, months=months, rows=len(unique))
        return unique

    # ── Busca de Fundos ───────────────────────────────────────────────────────

    async def search_fund(self, query: str) -> list[dict[str, Any]]:
        """
        Busca fundos por nome ou CNPJ (case-insensitive).

        Args:
            query: string de busca (mínimo 3 caracteres).

        Returns:
            Lista de até 50 fundos correspondentes.
        """
        if len(query) < 3:
            return []

        register = await self.get_fund_register(only_active=True)
        q = query.upper()
        results = []
        for fund in register:
            if q in fund.get("nome", "").upper() or q in fund.get("cnpj", ""):
                results.append(fund)
                if len(results) >= 50:
                    break
        return results

    # ── Utilitários ───────────────────────────────────────────────────────────

    async def _download(self, url: str) -> bytes:
        """Download de arquivo CSV com streaming para arquivos grandes.

        O cadastro de fundos (cad_fi.csv) tem ~150MB. Usar streaming
        evita timeout e truncamento em arquivos grandes.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=30.0,
                        read=_HTTP_TIMEOUT,
                        write=30.0,
                        pool=30.0,
                    ),
                    headers=headers,
                    follow_redirects=True,
                ) as client:
                    chunks: list[bytes] = []
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            chunks.append(chunk)
                    content = b"".join(chunks)
                    logger.debug(
                        "cvm.download.complete",
                        url=url.split("/")[-1],
                        size_kb=len(content) // 1024,
                    )
                    return content
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "cvm.download.retry",
                    url=url.split("/")[-1],
                    attempt=attempt,
                    error=str(exc)[:80],
                )
        raise last_exc or RuntimeError(f"Download falhou após {_MAX_RETRIES} tentativas")

    def _get_cache(self, key: str, ttl: float) -> Any | None:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    async def is_healthy(self) -> bool:
        """Health check: verifica se a CVM está acessível."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.head(_CAD_URL)
                return resp.status_code < 400
        except Exception:
            return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "nan", "NaN", "None"):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any) -> int | None:
    f = _safe_float(value)
    return int(f) if f is not None else None


def _clean_cnpj(cnpj: str) -> str:
    """Remove caracteres não-numéricos do CNPJ."""
    import re
    return re.sub(r"\D", "", cnpj)


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: CvmClient | None = None


def get_cvm_client() -> CvmClient:
    global _client
    if _client is None:
        _client = CvmClient()
    return _client

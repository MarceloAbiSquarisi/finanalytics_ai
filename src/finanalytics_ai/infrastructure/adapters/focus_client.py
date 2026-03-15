"""
finanalytics_ai.infrastructure.adapters.focus_client
──────────────────────────────────────────────────────
Adapter para o Boletim Focus do Banco Central do Brasil.
API: https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/

O Boletim Focus consolida as expectativas de mercado coletadas
semanalmente pelo BCB junto a ~140 instituições financeiras.
É o principal termômetro das projeções macro do mercado brasileiro.

Endpoints utilizados (gratuitos, sem autenticação):

  ExpectativaMercadoAnuais
    → Expectativas anuais: IPCA, IGP-M, PIB, Selic, câmbio, IPC-Fipe
    → Filtro: Indicador, Data, DataReferencia, suavizado, base

  ExpectativaMercadoMensais
    → Expectativas mensais: IPCA, IGP-M, câmbio
    → Útil para projeções mês a mês

  ExpectativasMercadoTop5Anuais
    → Mediana das 5 melhores instituições (mais acuradas historicamente)
    → Indicador de qualidade de consenso

Campos retornados pela API (OData):
  Indicador, Data, DataReferencia, Media, Mediana, DesvioPadrao,
  Minimo, Maximo, numeroRespondentes, baseCalculo

Design decisions:

  OData $filter e $select:
    A API Olinda usa OData v4. Usamos $filter para minimizar payload
    (apenas últimas N semanas) e $select para trazer só os campos necessários.
    Sem filtro, o endpoint pode retornar 100k+ registros históricos.

  Cache de 4 horas:
    O Focus é divulgado toda sexta-feira às 8h30. Reprocessar a cada
    chamada seria desperdício. 4h garante que sexta o dado novo apareça
    sem esperar até segunda.

  Sem tenacity aqui:
    A API Olinda é estável mas lenta (~2-5s por request). Uma falha
    retorna lista vazia com log warning — o macro snapshot continua
    funcionando com dados do cache ou estimativas.

  Encoding UTF-8:
    Diferente dos CSVs da CVM, a API Olinda retorna JSON em UTF-8.

  Indicadores suportados:
    IPCA, IGP-M, IGP-DI, IPC-Fipe, IPA-DI, PIB Total,
    Selic, Câmbio, Balança comercial, Conta corrente,
    Dívida líquida do setor público, Resultado primário.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_OLINDA_BASE = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata"

_ENDPOINTS = {
    "anual":       "ExpectativasMercadoAnuais",       # com 's' — nome correto da API BCB
    "mensal":      "ExpectativasMercadoMensais",
    "top5_anual":  "ExpectativasMercadoTop5Anuais",
}

# Indicadores relevantes para o FinAnalytics
# ATENÇÃO: A API Olinda rejeita "Câmbio" (com acento) com HTTP 400.
# Usar "Cambio" (sem acento) que é o nome aceito pelo endpoint.
_INDICADORES_FOCO = [
    "IPCA",
    "IGP-M",
    "PIB Total",
    "Selic",
    "Cambio",
    "IPC-Fipe",
]

# Normaliza nomes com acento para a forma aceita pela API Olinda
_INDICADOR_NORMALIZE: dict[str, str] = {
    "Câmbio": "Cambio",
    "câmbio": "Cambio",
}

_CACHE_TTL = 14_400   # 4 horas
_HTTP_TIMEOUT = 30.0

# Campos que queremos da API (reduz payload)
_SELECT_ANUAL = "Indicador,Data,DataReferencia,Media,Mediana,DesvioPadrao,Minimo,Maximo,numeroRespondentes"
_SELECT_TOP5  = "Indicador,Data,DataReferencia,Media,Mediana,tipoCalculo"


class FocusClient:
    """
    Adapter assíncrono para o Boletim Focus (BCB Olinda API).

    Principais métodos:
      get_latest_expectations()  → expectativas mais recentes (todos indicadores)
      get_indicator_history()    → histórico de um indicador específico
      get_top5_expectations()    → expectativas das top5 instituições
      get_focus_snapshot()       → snapshot resumido para o dashboard macro
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}

    # ── Interface principal ───────────────────────────────────────────────────

    async def get_latest_expectations(
        self,
        indicadores: list[str] | None = None,
        semanas: int = 4,
    ) -> list[dict[str, Any]]:
        """
        Retorna as expectativas mais recentes do Focus para os indicadores solicitados.

        Args:
            indicadores: lista de indicadores (default: _INDICADORES_FOCO).
            semanas: número de semanas de histórico recente (padrão: 4).

        Returns:
            Lista de dicts com: indicador, data, data_referencia,
            media, mediana, desvio_padrao, minimo, maximo, n_respondentes.
        """
        ind = indicadores or _INDICADORES_FOCO
        cache_key = f"exp_{','.join(sorted(ind))}_{semanas}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        all_rows: list[dict[str, Any]] = []
        for indicador in ind:
            rows = await self._fetch_anual(indicador, semanas)
            all_rows.extend(rows)

        self._set_cache(cache_key, all_rows)
        logger.info("focus.expectations.fetched", rows=len(all_rows), semanas=semanas)
        return all_rows

    async def get_indicator_history(
        self,
        indicador: str,
        semanas: int = 52,
    ) -> list[dict[str, Any]]:
        """
        Retorna histórico semanal de um indicador do Focus.

        Args:
            indicador: ex: "IPCA", "Selic", "PIB Total", "Câmbio".
            semanas: número de semanas de histórico (padrão: 52 = 1 ano).

        Returns:
            Lista de dicts ordenada por data ASC.
        """
        cache_key = f"hist_{indicador}_{semanas}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        rows = await self._fetch_anual(indicador, semanas)
        rows_sorted = sorted(rows, key=lambda r: r.get("data", ""))
        self._set_cache(cache_key, rows_sorted)
        return rows_sorted

    async def get_top5_expectations(
        self,
        indicadores: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retorna expectativas das 5 instituições historicamente mais acuradas.

        Útil para comparar consenso geral vs. melhores forecasters.
        """
        ind = indicadores or ["IPCA", "PIB Total", "Selic", "Cambio"]
        cache_key = f"top5_{','.join(sorted(ind))}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        all_rows: list[dict[str, Any]] = []
        for indicador in ind:
            rows = await self._fetch_top5(indicador)
            all_rows.extend(rows)

        self._set_cache(cache_key, all_rows)
        logger.info("focus.top5.fetched", rows=len(all_rows))
        return all_rows

    async def get_focus_snapshot(self) -> dict[str, Any]:
        """
        Retorna um snapshot resumido das expectativas mais recentes.

        Formato otimizado para o dashboard macro:
          {
            "ipca_ano_atual":   {"mediana": 4.5, "data": "2025-03-14"},
            "selic_ano_atual":  {"mediana": 13.0, "data": "2025-03-14"},
            "pib_ano_atual":    {"mediana": 2.1, "data": "2025-03-14"},
            "cambio_ano_atual": {"mediana": 5.25, "data": "2025-03-14"},
          }

        O "ano atual" é a projeção para o ano corrente (DataReferencia = ano).
        """
        cache_key = "snapshot"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        from datetime import date
        ano_atual = str(date.today().year)

        expectations = await self.get_latest_expectations(semanas=2)

        snapshot: dict[str, Any] = {}
        _key_map = {
            "IPCA":      "ipca",
            "Selic":     "selic",
            "PIB Total": "pib",
            "Cambio":    "cambio",   # sem acento — nome aceito pela API
            "Câmbio":    "cambio",   # compatibilidade com dados já coletados
            "IGP-M":     "igpm",
            "IPC-Fipe":  "ipc_fipe",
        }

        for row in expectations:
            indicador = row.get("indicador", "")
            ref = str(row.get("data_referencia", ""))
            key_base = _key_map.get(indicador)
            if not key_base:
                continue

            # Projections para o ano atual
            if ref == ano_atual:
                snap_key = f"{key_base}_ano_atual"
                if snap_key not in snapshot:
                    snapshot[snap_key] = {
                        "mediana":         row.get("mediana"),
                        "media":           row.get("media"),
                        "data":            row.get("data"),
                        "n_respondentes":  row.get("n_respondentes"),
                    }

            # Próximo ano
            elif ref == str(int(ano_atual) + 1):
                snap_key = f"{key_base}_ano_seguinte"
                if snap_key not in snapshot:
                    snapshot[snap_key] = {
                        "mediana":        row.get("mediana"),
                        "media":          row.get("media"),
                        "data":           row.get("data"),
                        "n_respondentes": row.get("n_respondentes"),
                    }

        self._set_cache(cache_key, snapshot)
        logger.info("focus.snapshot.built", keys=len(snapshot))
        return snapshot

    # ── Fetchers internos ─────────────────────────────────────────────────────

    async def _fetch_anual(
        self,
        indicador: str,
        semanas: int,
    ) -> list[dict[str, Any]]:
        """Busca expectativas anuais de um indicador via API Olinda."""
        from datetime import date, timedelta
        # Normaliza nome do indicador (remove acentos problemáticos)
        ind_api = _INDICADOR_NORMALIZE.get(indicador, indicador)
        data_inicio = (date.today() - timedelta(weeks=semanas)).isoformat()

        params = {
            "$filter":  f"Indicador eq '{ind_api}' and Data ge '{data_inicio}'",
            "$select":  _SELECT_ANUAL,
            "$orderby": "Data desc",
            "$top":     "500",
            "$format":  "json",
        }
        endpoint = _ENDPOINTS["anual"]
        raw = await self._request(endpoint, params)
        return [_parse_row_anual(item) for item in raw]

    async def _fetch_top5(self, indicador: str) -> list[dict[str, Any]]:
        """Busca expectativas Top5 de um indicador."""
        from datetime import date, timedelta
        ind_api = _INDICADOR_NORMALIZE.get(indicador, indicador)
        data_inicio = (date.today() - timedelta(weeks=8)).isoformat()

        params = {
            "$filter":  f"Indicador eq '{ind_api}' and Data ge '{data_inicio}'",
            "$select":  _SELECT_TOP5,
            "$orderby": "Data desc",
            "$top":     "100",
            "$format":  "json",
        }
        endpoint = _ENDPOINTS["top5_anual"]
        raw = await self._request(endpoint, params)
        return [_parse_row_top5(item) for item in raw]

    async def _request(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """HTTP GET na API Olinda com tratamento de erros.

        IMPORTANTE: A API OData do BCB exige que os parâmetros $filter,
        $select, $orderby etc. tenham o '$' LITERAL na URL — não encodado
        como %24. O httpx e urllib.parse.urlencode encodam o '$' por padrão,
        quebrando a requisição com HTTP 400.

        Solução: construir a query string manualmente encodando apenas os
        VALORES (não as chaves), preservando o '$' nos nomes dos parâmetros.
        """
        from urllib.parse import quote

        # Constrói query string com $ literal nas chaves, valores encodados
        # Exemplo: $filter=Indicador%20eq%20%27IPCA%27&$top=500&$format=json
        parts = []
        for key, value in params.items():
            # Encoda o valor mas preserva caracteres OData comuns no filter
            encoded_value = quote(str(value), safe="'")
            parts.append(f"{key}={encoded_value}")
        query_string = "&".join(parts)

        url = f"{_OLINDA_BASE}/{endpoint}?{query_string}"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "FinAnalyticsAI/1.0",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return data.get("value", [])  # type: ignore[return-value]
        except httpx.TimeoutException:
            logger.warning("focus.request.timeout", endpoint=endpoint)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "focus.request.http_error",
                endpoint=endpoint,
                status=exc.response.status_code,
                url=url[:120],
            )
            return []
        except Exception as exc:
            logger.warning("focus.request.error", endpoint=endpoint, error=str(exc)[:120])
            return []

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _get_cache(self, key: str) -> Any | None:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < _CACHE_TTL:
                return val
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    async def is_healthy(self) -> bool:
        """Health check: verifica se a API Olinda está acessível."""
        try:
            rows = await self._request(
                _ENDPOINTS["anual"],
                {
                    "$filter": "Indicador eq 'IPCA'",
                    "$top": "1",
                    "$format": "json",
                },
            )
            return len(rows) > 0
        except Exception:
            return False


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_row_anual(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "indicador":       item.get("Indicador", ""),
        "data":            item.get("Data", ""),
        "data_referencia": item.get("DataReferencia", ""),
        "media":           _safe_float(item.get("Media")),
        "mediana":         _safe_float(item.get("Mediana")),
        "desvio_padrao":   _safe_float(item.get("DesvioPadrao")),
        "minimo":          _safe_float(item.get("Minimo")),
        "maximo":          _safe_float(item.get("Maximo")),
        "n_respondentes":  _safe_int(item.get("numeroRespondentes")),
        "base_calculo":    item.get("baseCalculo"),
    }


def _parse_row_top5(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "indicador":       item.get("Indicador", ""),
        "data":            item.get("Data", ""),
        "data_referencia": item.get("DataReferencia", ""),
        "media":           _safe_float(item.get("Media")),
        "mediana":         _safe_float(item.get("Mediana")),
        "tipo_calculo":    item.get("tipoCalculo", ""),
        "fonte":           "top5",
    }


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


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: FocusClient | None = None


def get_focus_client() -> FocusClient:
    global _client
    if _client is None:
        _client = FocusClient()
    return _client

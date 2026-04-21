"""
finanalytics_ai.infrastructure.adapters.fintz_client
─────────────────────────────────────────────────────
Cliente assíncrono para a API Fintz.

Schema real dos parquets verificado em 2026-03-20:
  cotacoes      → 14 colunas snake_case
  item_contabil → 4 colunas: ticker, item, data, valor
  indicador     → 4 colunas: ticker, indicador, data, valor
"""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING, Any

import aiohttp
import pandas as pd
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from finanalytics_ai.exceptions import FintzAPIError, FintzParseError, TransientError

if TYPE_CHECKING:
    from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec

logger = structlog.get_logger(__name__)

_RETRYABLE = (aiohttp.ClientError, TransientError, TimeoutError)

# Colunas mínimas obrigatórias por tipo de dataset (schema real dos parquets)
_REQUIRED_COLS: dict[str, list[str]] = {
    "cotacoes": ["ticker", "data", "preco_fechamento"],
    "item_contabil": ["ticker", "item", "data", "valor"],
    "indicador": ["ticker", "indicador", "data", "valor"],
}


def _is_retryable_status(status: int) -> bool:
    return status >= 500 or status == 429


class FintzClient:
    """
    Cliente HTTP para a API Fintz.

    Usage:
        async with FintzClient(api_key="...") as client:
            df, sha256 = await client.fetch_dataset(spec)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.fintz.com.br",
        api_timeout_s: float = 30.0,
        link_timeout_s: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._api_timeout = aiohttp.ClientTimeout(total=api_timeout_s)
        self._link_timeout = aiohttp.ClientTimeout(total=link_timeout_s)
        self._max_retries = max_retries
        self._session: aiohttp.ClientSession | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> FintzClient:
        self._session = aiohttp.ClientSession(
            headers={"X-API-Key": self._api_key},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── API pública ───────────────────────────────────────────────────────────

    async def fetch_dataset(self, spec: FintzDatasetSpec) -> tuple[pd.DataFrame, str]:
        raw_bytes = await self._download_dataset(spec)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        df = self._parse_parquet(raw_bytes, spec)
        logger.info(
            "fintz_client.dataset_fetched",
            dataset_key=spec.key,
            rows=len(df),
            size_mb=round(len(raw_bytes) / 1_048_576, 2),
            sha256_prefix=sha256[:8],
        )
        return df, sha256

    async def is_healthy(self) -> bool:
        session = self._get_session()
        try:
            url = f"{self._base_url}/bolsa/b3/avista/busca"
            async with session.get(url, params={"q": "PETR4"}, timeout=self._api_timeout) as r:
                return r.status == 200
        except Exception:
            return False

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _download_dataset(self, spec: FintzDatasetSpec) -> bytes:
        link = await self._get_download_link(spec)
        return await self._download_file(link, spec.key)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _get_download_link(self, spec: FintzDatasetSpec) -> str:
        session = self._get_session()
        url = f"{self._base_url}{spec.endpoint}"
        logger.debug("fintz_client.get_link", dataset_key=spec.key, params=spec.params)

        async with session.get(url, params=spec.params, timeout=self._api_timeout) as resp:
            if _is_retryable_status(resp.status):
                raise TransientError(
                    message=f"Fintz API transitória: {resp.status}",
                    attempt=0,
                    context={"dataset_key": spec.key},
                )
            if resp.status != 200:
                raise FintzAPIError(
                    message=f"Fintz API retornou {resp.status} para {spec.key}",
                    status_code=resp.status,
                    dataset_key=spec.key,
                    context={"url": url, "params": spec.params},
                )
            data: dict[str, Any] = await resp.json()

        link: str | None = data.get("link")
        if not link:
            raise FintzAPIError(
                message=f"Resposta sem 'link' para {spec.key}",
                dataset_key=spec.key,
                context={"response": str(data)[:200]},
            )
        logger.debug("fintz_client.link_obtained", dataset_key=spec.key)
        return link

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        reraise=True,
    )
    async def _download_file(self, url: str, dataset_key: str) -> bytes:
        logger.info("fintz_client.download_start", dataset_key=dataset_key)
        # URL pré-assinada S3 — sem header de autenticação
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=self._link_timeout) as resp:
                if _is_retryable_status(resp.status):
                    raise TransientError(
                        message=f"Download transitório: {resp.status}",
                        context={"dataset_key": dataset_key},
                    )
                if resp.status != 200:
                    raise FintzAPIError(
                        message=f"Falha no download: {resp.status}",
                        status_code=resp.status,
                        dataset_key=dataset_key,
                    )
                content = await resp.read()

        logger.info(
            "fintz_client.download_done",
            dataset_key=dataset_key,
            size_mb=round(len(content) / 1_048_576, 2),
        )
        return content

    def _parse_parquet(self, raw: bytes, spec: FintzDatasetSpec) -> pd.DataFrame:
        try:
            df = pd.read_parquet(io.BytesIO(raw))
        except Exception as exc:
            raise FintzParseError(
                message=f"Falha ao ler parquet de {spec.key}: {exc}",
                dataset_key=spec.key,
                context={"error": str(exc)},
            ) from exc

        if df.empty:
            logger.warning("fintz_client.empty_parquet", dataset_key=spec.key)
            return df

        # Valida colunas mínimas com schema real
        required = _REQUIRED_COLS.get(spec.dataset_type, [])
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise FintzParseError(
                message=f"Colunas ausentes em {spec.key}: {missing}",
                dataset_key=spec.key,
                context={"columns": list(df.columns), "missing": missing},
            )
        return df

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError(
                "FintzClient deve ser usado como context manager: "
                "`async with FintzClient(...) as client:`"
            )
        return self._session


# ── Factory ───────────────────────────────────────────────────────────────────


def create_fintz_client() -> FintzClient:
    from finanalytics_ai.config import get_settings

    settings = get_settings()
    return FintzClient(
        api_key=settings.fintz_api_key,
        base_url=settings.fintz_base_url,
        api_timeout_s=settings.http_timeout_seconds,
        link_timeout_s=settings.fintz_download_timeout_s,
        max_retries=settings.http_retry_max_attempts,
    )

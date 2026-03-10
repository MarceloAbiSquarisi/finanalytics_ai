"""
finanalytics_ai.infrastructure.adapters.tesouro_client
────────────────────────────────────────────────────────
Adapter para a API pública do Tesouro Direto (B3).

Endpoint oficial (sem autenticação):
  https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/service/api/treasurybond.json

Retorna todos os títulos disponíveis para compra com:
  - Nome, tipo, vencimento
  - Taxa de compra/venda
  - Preço unitário
  - Investimento mínimo

Design decisions:
  Cache TTL de 15 minutos:
    Os preços do TD são atualizados a cada 30 minutos em dias úteis.
    Cache de 15min garante dados frescos sem sobrecarga na API da B3.

  Sem tenacity aqui:
    A API do TD é simples e confiável. Se falhar, retornamos lista vazia
    e o serviço usa apenas os títulos manuais cadastrados.

  Mapeamento de nomes:
    A API retorna nomes como "Tesouro Selic 2027" — mapeamos para
    nossos enums de BondType / Indexer para compatibilidade com o domínio.
"""

from __future__ import annotations

import contextlib
import time
from datetime import date
from typing import Any

import httpx
import structlog

from finanalytics_ai.domain.fixed_income.entities import Bond, BondType, Indexer, PaymentFrequency

logger = structlog.get_logger(__name__)

TD_API_URL = "https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/service/api/treasurybond.json"
CACHE_TTL = 900  # 15 minutos


class TesouroDiretoClient:
    def __init__(self) -> None:
        self._cache: list[Bond] = []
        self._cache_at: float = 0.0

    async def fetch_bonds(self) -> list[Bond]:
        """Retorna títulos disponíveis no Tesouro Direto."""
        if self._cache and (time.time() - self._cache_at) < CACHE_TTL:
            logger.debug("tesouro.cache_hit", count=len(self._cache))
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    TD_API_URL,
                    headers={
                        "User-Agent": "FinAnalyticsAI/1.0",
                        "Accept": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            bonds = self._parse(data)
            self._cache = bonds
            self._cache_at = time.time()
            logger.info("tesouro.fetched", count=len(bonds))
            return bonds

        except Exception as exc:
            logger.warning("tesouro.fetch_failed", error=str(exc))
            return self._cache  # retorna cache stale se existir

    def _parse(self, data: dict[str, Any]) -> list[Bond]:
        bonds: list[Bond] = []
        try:
            items = data["response"]["TrsrBdTradgList"]
        except (KeyError, TypeError):
            logger.warning("tesouro.parse_failed", keys=list(data.keys()))
            return []

        for item in items:
            try:
                td = item.get("TrsrBd", item)

                name = td.get("nm", "")
                bond_id = f"td_{td.get('cd', name).lower().replace(' ', '_')}"
                mat_str = td.get("mtrtyDt", "")
                min_inv = float(td.get("minInvstmtAmt", 0) or 0)

                # Taxa de compra (% a.a.)
                rate_str = td.get("anulInvstmtRate") or td.get("BuyAnulRate") or 0
                rate = float(rate_str) / 100 if rate_str else 0.0

                # Vencimento
                maturity: date | None = None
                if mat_str:
                    with contextlib.suppress(ValueError):
                        maturity = date.fromisoformat(mat_str[:10])

                bond_type, indexer, rate_pct, payment_freq = _classify_td(name)

                bonds.append(
                    Bond(
                        bond_id=bond_id,
                        name=name,
                        bond_type=bond_type,
                        indexer=indexer,
                        rate_annual=rate,
                        rate_pct_indexer=rate_pct,
                        maturity_date=maturity,
                        issuer="Tesouro Nacional",
                        min_investment=min_inv,
                        payment_freq=payment_freq,
                        ir_exempt=False,
                        liquidity="Diária (recompra garantida)",
                        source="tesouro_direto",
                        available=True,
                    )
                )
            except Exception as exc:
                logger.warning("tesouro.item_parse_error", error=str(exc))
                continue

        return bonds


def _classify_td(name: str) -> tuple[BondType, Indexer, bool, PaymentFrequency]:
    """
    Mapeia nome do título TD para (BondType, Indexer, rate_pct_indexer, PaymentFreq).

    Exemplos:
      "Tesouro Selic 2027"          → TESOURO_SELIC, SELIC, False, BULLET
      "Tesouro IPCA+ 2035"          → TESOURO_IPCA, IPCA, False, BULLET
      "Tesouro IPCA+ com Juros 2040" → TESOURO_IPCA, IPCA, False, SEMIANNUAL
      "Tesouro Prefixado 2027"       → TESOURO_PREFIXADO, PREFIXADO, False, BULLET
      "Tesouro Prefixado com Juros"  → TESOURO_PREFIXADO, PREFIXADO, False, SEMIANNUAL
    """
    n = name.lower()
    if "selic" in n:
        return BondType.TESOURO_SELIC, Indexer.SELIC, False, PaymentFrequency.BULLET
    if "ipca" in n:
        freq = PaymentFrequency.SEMIANNUAL if "juros" in n else PaymentFrequency.BULLET
        return BondType.TESOURO_IPCA, Indexer.IPCA, False, freq
    if "prefixado" in n or "ltn" in n:
        freq = PaymentFrequency.SEMIANNUAL if "juros" in n else PaymentFrequency.BULLET
        return BondType.TESOURO_PREFIXADO, Indexer.PREFIXADO, False, freq
    # Fallback
    return BondType.TESOURO_SELIC, Indexer.SELIC, False, PaymentFrequency.BULLET


# Singleton
_client: TesouroDiretoClient | None = None


def get_tesouro_client() -> TesouroDiretoClient:
    global _client
    if _client is None:
        _client = TesouroDiretoClient()
    return _client

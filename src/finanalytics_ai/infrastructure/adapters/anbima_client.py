"""
finanalytics_ai.infrastructure.adapters.anbima_client
───────────────────────────────────────────────────────
Adapter para curva de juros DI Futuro.

Fontes (em ordem de preferência):
  1. ANBIMA Data — API pública: https://data.anbima.com.br/
     Retorna taxas de mercado dos contratos DI Futuro (BM&FBovespa).
  2. Fallback sintético baseado em SELIC atual + prêmios históricos.

Curva sintética (fallback):
  Constrói uma curva razoável usando a SELIC como âncora de curto prazo
  e acrescenta prêmios por prazo baseados em padrões históricos do mercado
  brasileiro. Adequada para fins educativos e demonstração.

  Vértices sintéticos: 1, 3, 6, 12, 24, 36, 48, 60, 120 meses.
  Prêmios (sobre SELIC): estimados pela estrutura histórica da curva DI.

Design decisions:
  Cache de 60 minutos:
    A curva DI é publicada diariamente pela ANBIMA às 18h.
    Atualizações intraday são raras. 60min é suficiente para produção.

  Fallback transparente:
    Se a ANBIMA estiver indisponível, retornamos curva sintética marcada
    com source="synthetic". O frontend exibe aviso adequado.

  Sem tenacity aqui:
    Uma falha na curva não deve derrubar o serviço. Fallback imediato.
"""

from __future__ import annotations

from datetime import date, timedelta
import time
from typing import Any

import httpx
import structlog

from finanalytics_ai.domain.fixed_income.yield_curve import YieldCurve, YieldCurvePoint

logger = structlog.get_logger(__name__)

# Prêmios históricos sobre SELIC por prazo (estimativa conservadora)
# Estrutura típica da curva DI brasileira em ambiente de normalidade
_SYNTHETIC_PREMIUMS: list[tuple[int, float]] = [
    (21, -0.0010),  # 1 mês   — abaixo da SELIC (liquidez)
    (63, 0.0000),  # 3 meses — par com SELIC
    (126, 0.0015),  # 6 meses
    (252, 0.0035),  # 1 ano
    (504, 0.0075),  # 2 anos
    (756, 0.0120),  # 3 anos
    (1008, 0.0160),  # 4 anos
    (1260, 0.0195),  # 5 anos
    (2520, 0.0240),  # 10 anos
]


class AnbimaClient:
    """Adapter para curva DI Futuro da ANBIMA com fallback sintético."""

    ANBIMA_BASE = "https://data.anbima.com.br/indicadores/di-futuro"
    CACHE_TTL = 3600  # 60 minutos

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    async def get_yield_curve(
        self,
        selic: float,
        cdi: float,
        ipca: float,
    ) -> YieldCurve:
        """
        Retorna curva DI Futuro atual.
        Tenta ANBIMA; se falhar, usa curva sintética.
        """
        cache_key = "yield_curve"
        now = time.time()
        if cache_key in self._cache:
            cached, ts = self._cache[cache_key]
            if now - ts < self.CACHE_TTL:
                return cached

        curve = await self._fetch_anbima(selic, cdi, ipca)
        if curve is None:
            logger.info("anbima.fallback.synthetic")
            curve = self._build_synthetic(selic, cdi, ipca)

        self._cache[cache_key] = (curve, now)
        return curve

    async def _fetch_anbima(self, selic: float, cdi: float, ipca: float) -> YieldCurve | None:
        """
        Tenta buscar dados reais da ANBIMA Data API.
        Retorna None se indisponível (timeout, erro HTTP, etc.).
        """
        try:
            today = date.today().isoformat()
            url = f"{self.ANBIMA_BASE}/taxas?data={today}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers={"Accept": "application/json"})
                if resp.status_code != 200:
                    return None
                data = resp.json()
                points = self._parse_anbima_response(data)
                if not points:
                    return None
                logger.info("anbima.yield_curve.fetched", points=len(points))
                return YieldCurve(
                    reference_date=date.today(),
                    selic=selic,
                    cdi=cdi,
                    ipca=ipca,
                    points=sorted(points, key=lambda p: p.maturity_days),
                    source="anbima",
                )
        except Exception as exc:
            logger.warning("anbima.fetch.failed", error=str(exc))
            return None

    def _parse_anbima_response(self, data: Any) -> list[YieldCurvePoint]:
        """
        Interpreta resposta da ANBIMA.
        Formato esperado: lista de objetos com 'prazo', 'taxa', 'vencimento'.
        """
        points: list[YieldCurvePoint] = []
        if not isinstance(data, list):
            data = data.get("taxas", data.get("data", []))
        for item in data:
            try:
                days = int(item.get("prazo", item.get("diasCorridos", 0)))
                rate = float(item.get("taxa", item.get("taxaAnual", 0))) / 100
                venc = item.get("vencimento", item.get("dataVencimento"))
                mat = date.fromisoformat(venc) if venc else None
                code = item.get("codigo", item.get("contrato", ""))
                if days > 0 and rate > 0:
                    points.append(
                        YieldCurvePoint(
                            maturity_days=days,
                            rate_annual=rate,
                            maturity_date=mat,
                            contract=code,
                            source="anbima",
                        )
                    )
            except (KeyError, ValueError, TypeError):
                continue
        return points

    def _build_synthetic(self, selic: float, cdi: float, ipca: float) -> YieldCurve:
        """
        Constrói curva sintética baseada na SELIC atual + prêmios históricos.

        A curvatura varia com o nível da SELIC:
          - SELIC alta (>12%): curva flat ou ligeiramente invertida no longo prazo
          - SELIC baixa (<8%): inclinação positiva mais acentuada
        """
        today = date.today()
        points = []

        # Fator de ajuste: curva mais plana com SELIC alta
        slope_factor = 1.0 if selic < 0.10 else (0.7 if selic < 0.13 else 0.5)

        for days, premium in _SYNTHETIC_PREMIUMS:
            adjusted_rate = selic + (premium * slope_factor)
            mat_date = today + timedelta(days=days)
            months = days // 21
            code = f"DI1{'FGHJKMNQUVXZ'[months % 12]}{(today.year + months // 12) % 100:02d}"
            points.append(
                YieldCurvePoint(
                    maturity_days=days,
                    rate_annual=max(0.01, adjusted_rate),
                    maturity_date=mat_date,
                    contract=code,
                    source="synthetic",
                )
            )

        return YieldCurve(
            reference_date=today,
            selic=selic,
            cdi=cdi,
            ipca=ipca,
            points=points,
            source="synthetic",
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
_client: AnbimaClient | None = None


def get_anbima_client() -> AnbimaClient:
    global _client
    if _client is None:
        _client = AnbimaClient()
    return _client

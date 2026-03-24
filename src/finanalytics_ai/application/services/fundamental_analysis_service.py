"""
application/services/fundamental_analysis_service.py
Serviço de análise fundamentalista.

Responsabilidades:
  1. Buscar dados do Fintz (TimescaleDB) + BRAPI
  2. Montar estrutura de dados para o gerador de PDF
  3. Suportar empresa única e comparativo
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Nomes exatos do Fintz (verificados no banco)
INDICADORES_VALUATION     = ["P_L", "P_VP", "EV_EBITDA", "P_EBITDA", "P_SR"]
INDICADORES_RENTABILIDADE = ["ROE", "ROIC", "ROA", "MargemLiquida", "MargemEBITDA"]
INDICADORES_DIVIDENDOS    = ["DividendYield"]
INDICADORES_ENDIVIDAMENTO = ["DividaLiquida_EBITDA", "DividaLiquida_PatrimonioLiquido"]
ITENS_DRE = ["Receita Liquida", "EBITDA", "Lucro Liquido", "Divida Liquida"]

# Mapa Fintz -> nome legivel para o PDF
INDICADOR_LABELS = {
    "P_L":                           "P/L",
    "P_VP":                          "P/VP",
    "EV_EBITDA":                     "EV/EBITDA",
    "P_EBITDA":                      "P/EBITDA",
    "P_SR":                          "P/Receita",
    "ROE":                           "ROE",
    "ROIC":                          "ROIC",
    "ROA":                           "ROA",
    "MargemLiquida":                 "Margem Líquida",
    "MargemEBITDA":                  "Margem EBITDA",
    "MargemBruta":                   "Margem Bruta",
    "MargemEBIT":                    "Margem EBIT",
    "DividendYield":                 "DY",
    "DividaLiquida_EBITDA":          "Dívida/EBITDA",
    "DividaLiquida_PatrimonioLiquido": "Dívida/PL",
    "ValorDeMercado":                "Market Cap",
    "LPA":                           "LPA",
    "VPA":                           "VPA",
    "EV":                            "EV",
}


class FundamentalAnalysisService:
    """
    Orquestra busca de dados para relatorios fundamentalistas.
    Injecao: fintz_repo (FintzRepo — PostgreSQL), brapi_client (BrapiClient).
    """

    def __init__(self, fintz_repo: Any, brapi_client: Any) -> None:
        self._repo = fintz_repo
        self._brapi = brapi_client

    # ── Empresa única ─────────────────────────────────────────────────────────
    async def get_single_company_data(
        self,
        ticker: str,
        periodo_anos: int = 5,
    ) -> dict[str, Any]:
        """Coleta todos os dados necessários para relatório de empresa única."""
        ticker = ticker.upper()
        start = date.today() - timedelta(days=365 * periodo_anos)
        limit_dias = periodo_anos * 252
        limit_ind = periodo_anos * 52  # indicadores semanais/mensais — mais dados

        log.info("fundamental.single.start", ticker=ticker, anos=periodo_anos)

        # Busca em paralelo
        results = await asyncio.gather(
            self._repo.get_indicadores_latest(ticker),
            self._repo.get_indicadores(ticker, INDICADORES_VALUATION, start, limit_ind),
            self._repo.get_indicadores(ticker, INDICADORES_RENTABILIDADE, start, limit_ind),
            self._repo.get_indicadores(ticker, INDICADORES_DIVIDENDOS, start, limit_ind),
            self._repo.get_indicadores(ticker, INDICADORES_ENDIVIDAMENTO, start, limit_ind),
            self._repo.get_itens_contabeis(ticker, ITENS_DRE, "12M", start, 40),
            self._repo.get_cotacoes(ticker, start=start, limit=limit_dias),
            self._get_brapi_info(ticker),
            return_exceptions=True,
        )

        ind_latest, val_serie, rent_serie, div_serie, end_serie, dre_raw, cotacoes, brapi = results

        # Trata exceptions individuais graciosamente
        def safe(r: Any, default: Any) -> Any:
            return default if isinstance(r, Exception) else r

        ind_latest = safe(ind_latest, {})
        val_serie   = safe(val_serie, [])
        rent_serie  = safe(rent_serie, [])
        div_serie   = safe(div_serie, [])
        end_serie   = safe(end_serie, [])
        dre_raw     = safe(dre_raw, [])
        cotacoes    = safe(cotacoes, [])
        brapi       = safe(brapi, {})

        # Pivota DRE: {item: [rows]}
        dre: dict[str, list] = {}
        for row in dre_raw:
            item = row.get("item", "")
            dre.setdefault(item, []).append(row)

        # Enriquece com dados BRAPI
        nome = brapi.get("longName") or brapi.get("shortName") or ticker
        setor = brapi.get("sector") or brapi.get("industry") or "—"
        preco = brapi.get("regularMarketPrice")
        mcap = brapi.get("marketCap")

        log.info("fundamental.single.ready", ticker=ticker,
                 ind_count=len(ind_latest), cotacoes=len(cotacoes))

        return {
            "ticker": ticker,
            "nome": nome,
            "setor": setor,
            "preco": preco,
            "market_cap": mcap,
            "indicadores_latest": ind_latest,
            "valuation_serie": val_serie,
            "rentabilidade_serie": rent_serie,
            "dividendos_serie": div_serie,
            "endividamento_serie": end_serie,
            "dre": dre,
            "cotacoes": cotacoes,
            "periodo_anos": periodo_anos,
        }

    # ── Comparativo ───────────────────────────────────────────────────────────
    async def get_comparative_data(
        self,
        tickers: list[str],
        periodo_anos: int = 5,
    ) -> dict[str, Any]:
        """Coleta dados para relatório comparativo."""
        tickers = [t.upper() for t in tickers[:10]]  # máximo 10
        log.info("fundamental.comparative.start", tickers=tickers, anos=periodo_anos)

        tasks = [self.get_single_company_data(t, periodo_anos) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        empresas: dict[str, Any] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                log.warning("fundamental.comparative.ticker_failed",
                            ticker=ticker, error=str(result))
                empresas[ticker] = {"nome": ticker, "setor": "—",
                                    "indicadores_latest": {}}
            else:
                empresas[ticker] = result

        log.info("fundamental.comparative.ready", tickers=tickers)
        return {
            "tickers": tickers,
            "empresas": empresas,
            "periodo_anos": periodo_anos,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────
    async def _get_brapi_info(self, ticker: str) -> dict[str, Any]:
        """Busca informações gerais do ticker via BRAPI."""
        try:
            result = await self._brapi.get_quote(ticker)
            if isinstance(result, dict):
                results = result.get("results", [result])
                if results:
                    return results[0]
            return {}
        except Exception as exc:
            log.warning("fundamental.brapi_failed", ticker=ticker, error=str(exc))
            return {}

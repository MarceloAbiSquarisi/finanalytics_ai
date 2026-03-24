"""
application/services/fundamental_analysis_service.py
Serviço de análise fundamentalista — dados exclusivamente do Fintz (PostgreSQL).
BRAPI suspenso.
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
INDICADORES_ENDIVIDAMENTO = ["DividaLiquida_EBITDA", "DividaLiquida_PatrimonioLiquido",
                              "DividaBruta_PatrimonioLiquido"]
ITENS_DRE = ["Receita Liquida", "EBITDA", "Lucro Liquido", "Divida Liquida"]

# Mapa Fintz -> nome legível para o PDF
INDICADOR_LABELS: dict[str, str] = {
    "P_L":                              "Preço/Lucro (P/L)",
    "P_VP":                             "Preço/Valor Patrimonial (P/VP)",
    "EV_EBITDA":                        "EV/EBITDA",
    "P_EBITDA":                         "Preço/EBITDA",
    "P_SR":                             "Preço/Receita",
    "ROE":                              "Retorno sobre Patrimônio (ROE)",
    "ROIC":                             "Retorno sobre Capital Investido (ROIC)",
    "ROA":                              "Retorno sobre Ativos (ROA)",
    "MargemLiquida":                    "Margem Líquida",
    "MargemEBITDA":                     "Margem EBITDA",
    "MargemBruta":                      "Margem Bruta",
    "MargemEBIT":                       "Margem EBIT",
    "DividendYield":                    "Dividend Yield (DY)",
    "DividaLiquida_EBITDA":             "Dívida Líquida / EBITDA",
    "DividaLiquida_PatrimonioLiquido":  "Dívida Líquida / Patrimônio Líquido",
    "DividaBruta_PatrimonioLiquido":    "Dívida Bruta / Patrimônio Líquido",
    "ValorDeMercado":                   "Valor de Mercado",
    "LPA":                              "Lucro por Ação (LPA)",
    "VPA":                              "Valor Patrimonial por Ação (VPA)",
    "EV":                               "Enterprise Value (EV)",
    "EV_EBIT":                          "EV/EBIT",
    "LiquidezCorrente":                 "Liquidez Corrente",
    "GiroAtivos":                       "Giro dos Ativos",
}

# Indicadores armazenados em decimal no Fintz que devem ser exibidos como %
PCT_INDICATORS = {
    "ROE", "ROIC", "ROA", "MargemLiquida", "MargemEBITDA",
    "MargemBruta", "MargemEBIT", "DividendYield",
    "EBIT_Ativos", "GiroAtivos",
}


class FundamentalAnalysisService:
    """
    Orquestra busca de dados para relatórios fundamentalistas.
    Dados exclusivamente do Fintz (PostgreSQL). BRAPI suspenso.
    """

    def __init__(self, fintz_repo: Any, brapi_client: Any = None) -> None:
        self._repo = fintz_repo
        # brapi_client mantido na assinatura para compatibilidade mas não usado

    # ── Empresa única ─────────────────────────────────────────────────────────
    async def get_single_company_data(
        self,
        ticker: str,
        periodo_anos: int = 5,
    ) -> dict[str, Any]:
        ticker = ticker.upper()
        start = date.today() - timedelta(days=365 * periodo_anos)
        limit_cot = periodo_anos * 252
        limit_ind = periodo_anos * 260  # diarios: 5a*260=1300; trimestrais: so ~20 pontos reais

        log.info("fundamental.single.start", ticker=ticker, anos=periodo_anos)

        # Busca todos os indicadores relevantes em paralelo (série por indicador)
        all_indicators = (INDICADORES_VALUATION + INDICADORES_RENTABILIDADE +
                          INDICADORES_DIVIDENDOS + INDICADORES_ENDIVIDAMENTO)

        tasks = [
            self._repo.get_indicadores_latest(ticker),
            self._repo.get_cotacoes(ticker, start=start, limit=limit_cot),
            self._repo.get_itens_contabeis(ticker, ITENS_DRE, "12M", start, 40),
        ] + [
            self._repo.get_indicador_serie(ticker, ind, start, limit_ind)
            for ind in all_indicators
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        def safe(r: Any, default: Any) -> Any:
            return default if isinstance(r, Exception) else r

        ind_latest = safe(results[0], {})
        cotacoes    = safe(results[1], [])
        dre_raw     = safe(results[2], [])

        # Mapeia séries por indicador
        series_map: dict[str, list] = {}
        for i, ind_name in enumerate(all_indicators):
            series_map[ind_name] = safe(results[3 + i], [])

        # Converte indicadores percentuais (decimal → %)
        for key in list(ind_latest.keys()):
            if key in PCT_INDICATORS and ind_latest[key].get("valor") is not None:
                ind_latest[key]["valor"] = ind_latest[key]["valor"] * 100

        # Pivota DRE: {item: [rows]}
        dre: dict[str, list] = {}
        for row in dre_raw:
            dre.setdefault(row.get("item", ""), []).append(row)

        # Extrai preço e market cap do Fintz
        preco = None
        if cotacoes:
            preco = cotacoes[0].get("fechamento_ajustado") or cotacoes[0].get("fechamento")

        mcap_entry = ind_latest.get("ValorDeMercado", {})
        mcap = mcap_entry.get("valor") if isinstance(mcap_entry, dict) else None

        log.info("fundamental.single.ready", ticker=ticker,
                 ind_count=len(ind_latest), cotacoes=len(cotacoes))

        return {
            "ticker": ticker,
            "nome": ticker,      # Fintz não fornece nome — será buscado pela UI
            "setor": "—",        # Fintz não fornece setor
            "preco": preco,
            "market_cap": mcap,
            "indicadores_latest": ind_latest,
            "series_map": series_map,          # {indicador: [{data, valor}]}
            "valuation_serie": self._merge_series(series_map, INDICADORES_VALUATION),
            "rentabilidade_serie": self._merge_series(series_map, INDICADORES_RENTABILIDADE),
            "dividendos_serie": self._merge_series(series_map, INDICADORES_DIVIDENDOS),
            "endividamento_serie": self._merge_series(series_map, INDICADORES_ENDIVIDAMENTO),
            "dre": dre,
            "cotacoes": cotacoes,
            "periodo_anos": periodo_anos,
            "indicador_labels": INDICADOR_LABELS,
            "pct_indicators": list(PCT_INDICATORS),
        }

    def _merge_series(self, series_map: dict, indicadores: list) -> list[dict]:
        """Junta séries de múltiplos indicadores num único list com campo indicador."""
        result = []
        for ind in indicadores:
            for row in series_map.get(ind, []):
                result.append({**row, "indicador": ind})
        return result

    # ── Comparativo ───────────────────────────────────────────────────────────
    async def get_comparative_data(
        self,
        tickers: list[str],
        periodo_anos: int = 5,
    ) -> dict[str, Any]:
        tickers = [t.upper() for t in tickers[:10]]
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

        return {
            "tickers": tickers,
            "empresas": empresas,
            "periodo_anos": periodo_anos,
        }

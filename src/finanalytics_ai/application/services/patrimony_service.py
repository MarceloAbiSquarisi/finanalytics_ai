"""
finanalytics_ai.application.services.patrimony_service
────────────────────────────────────────────────────────
Agrega Ações, ETFs e Renda Fixa num snapshot consolidado.

Fluxo:
  1. Lista portfolios de ações do usuário → busca preços atuais em paralelo
  2. Identifica posições ETF vs Ações via ETF_CATALOG
  3. Lista carteiras RF → calcula valor atual com juros
  4. Soma tudo + caixa → ConsolidatedSnapshot

Por que não uma única query SQL?
  Os dados de mercado (preços atuais) vêm de API externa, não do banco.
  Qualquer abordagem "SQL puro" precisaria de preços stale.
  O custo de latência (~300ms) é aceitável para um dashboard de patrimônio
  que o usuário acessa esporadicamente, não em tempo real.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from finanalytics_ai.domain.patrimony.consolidated import (
    build_snapshot,
)

logger = structlog.get_logger(__name__)

DEFAULT_CDI = 0.1065
DEFAULT_SELIC = 0.1065
DEFAULT_IPCA = 0.0483

# Tickers que identificamos como ETF (subset do catálogo)
_ETF_TICKERS = {
    "BOVA11",
    "SMAL11",
    "DIVO11",
    "FIND11",
    "MATB11",
    "UTIP11",
    "ECOO11",
    "BOVV11",
    "XBOV11",
    "IVVB11",
    "SPXI11",
    "NASD11",
    "ACWI11",
    "EURP11",
    "HASH11",
    "QBTC11",
    "BITH11",
    "NTNB11",
    "IMAB11",
    "B5P211",
    "IRFM11",
    "GOLD11",
    "OGLD11",
    "XFIX11",
    "VISC11",
}


class PatrimonyService:
    def __init__(
        self,
        portfolio_repo,
        rf_repo,
        market_client,
    ) -> None:
        self._port_repo = portfolio_repo
        self._rf_repo = rf_repo
        self._market = market_client

    async def consolidated_snapshot(
        self,
        user_id: str,
        targets: dict[str, float] | None = None,
        cdi: float = DEFAULT_CDI,
        selic: float = DEFAULT_SELIC,
        ipca: float = DEFAULT_IPCA,
    ) -> dict[str, Any]:
        """
        Retorna patrimônio consolidado: ações + ETFs + RF + caixa.
        targets: {"Ações": 40, "ETFs": 20, "Renda Fixa": 35, "Caixa": 5}
        """
        from finanalytics_ai.domain.fixed_income.entities import (
            Bond,
            BondType,
            Indexer,
            PaymentFrequency,
            calculate_yield,
        )
        from finanalytics_ai.domain.value_objects.money import Ticker

        log = logger.bind(user_id=user_id)
        log.info("patrimony.consolidating")

        # ── 1. Portfólios de Ações/ETFs ──────────────────────────────────────
        eq_value = eq_invested = 0.0
        etf_value = etf_invested = 0.0
        cash_value = 0.0
        eq_positions = etf_positions = 0
        equity_details: list[dict] = []

        try:
            portfolios = await self._port_repo.find_by_user(user_id)
            all_tickers: set[str] = set()
            for p in portfolios:
                for pos in p.positions:
                    all_tickers.add(pos.ticker.value)
                cash_value += float(p.cash.amount)

            # Busca preços em paralelo
            prices: dict[str, float] = {}
            if all_tickers:
                sem = asyncio.Semaphore(5)

                async def _price(t: str) -> tuple[str, float]:
                    async with sem:
                        try:
                            bars = await self._market.get_ohlc_bars(Ticker(t), range_period="5d")
                            if bars:
                                return t, float(
                                    bars[-1].get("close") or bars[-1].get("regularMarketPrice") or 0
                                )
                        except Exception:
                            pass
                        return t, 0.0

                raw = await asyncio.gather(*[_price(t) for t in all_tickers])
                prices = dict(raw)

            for p in portfolios:
                for pos in p.positions:
                    t = pos.ticker.value
                    price = prices.get(t) or float(pos.average_price.amount)
                    val = price * float(pos.quantity.value)
                    inv = float(pos.average_price.amount) * float(pos.quantity.value)
                    pl = val - inv
                    detail = {
                        "ticker": t,
                        "quantity": float(pos.quantity.value),
                        "avg_price": float(pos.average_price.amount),
                        "current_price": price,
                        "current_value": round(val, 2),
                        "invested": round(inv, 2),
                        "pl": round(pl, 2),
                        "pl_pct": round(pl / inv * 100, 2) if inv else 0,
                        "asset_class": "ETF" if t in _ETF_TICKERS else "Ação",
                        "portfolio_name": p.name,
                    }
                    equity_details.append(detail)
                    if t in _ETF_TICKERS:
                        etf_value += val
                        etf_invested += inv
                        etf_positions += 1
                    else:
                        eq_value += val
                        eq_invested += inv
                        eq_positions += 1
        except Exception as e:
            log.warning("patrimony.equity_error", error=str(e))

        # ── 2. Renda Fixa ─────────────────────────────────────────────────────
        rf_value = rf_invested = 0.0
        rf_positions = 0
        rf_details: list[dict] = []

        try:
            rf_portfolios = await self._rf_repo.list_portfolios(user_id)
            for pf_summary in rf_portfolios:
                pf = await self._rf_repo.get_portfolio(pf_summary["portfolio_id"])
                if pf is None:
                    continue
                for h in pf.active_holdings:
                    try:
                        bt = BondType(h.bond_type)
                    except ValueError:
                        bt = BondType.CDB
                    try:
                        idx = Indexer(h.indexer)
                    except ValueError:
                        idx = Indexer.CDI
                    bond = Bond(
                        bond_id=h.bond_id,
                        name=h.bond_name,
                        bond_type=bt,
                        indexer=idx,
                        rate_annual=h.rate_annual,
                        rate_pct_indexer=h.rate_pct_indexer,
                        maturity_date=h.maturity_date,
                        issuer=h.issuer,
                        ir_exempt=h.ir_exempt,
                        payment_freq=PaymentFrequency.AT_MATURITY,
                    )
                    idx_rate = {"CDI": cdi, "SELIC": selic, "IPCA": ipca}.get(h.indexer, cdi)
                    yr = calculate_yield(
                        bond=bond,
                        principal=h.invested,
                        days=max(1, h.days_held),
                        indexer_rate=idx_rate,
                        inflation_rate=ipca,
                    )
                    rf_value += yr.net_amount
                    rf_invested += h.invested
                    rf_positions += 1
                    rf_details.append(
                        {
                            "holding_id": h.holding_id,
                            "bond_name": h.bond_name,
                            "bond_type": h.bond_type,
                            "issuer": h.issuer,
                            "invested": round(h.invested, 2),
                            "current_value": round(yr.net_amount, 2),
                            "net_return_pct": round(yr.net_return_pct, 4),
                            "portfolio_name": pf.name,
                        }
                    )
        except Exception as e:
            log.warning("patrimony.rf_error", error=str(e))

        # ── 3. Monta snapshot ─────────────────────────────────────────────────
        parsed_targets = None
        if targets:
            from finanalytics_ai.domain.patrimony.consolidated import AssetClass

            parsed_targets = {
                AssetClass.EQUITIES: targets.get("Ações", 40.0),
                AssetClass.ETFS: targets.get("ETFs", 20.0),
                AssetClass.FIXED_INC: targets.get("Renda Fixa", 35.0),
                AssetClass.CASH: targets.get("Caixa", 5.0),
            }

        snap = build_snapshot(
            user_id=user_id,
            equities_value=eq_value,
            equities_invested=eq_invested,
            equities_positions=eq_positions,
            etfs_value=etf_value,
            etfs_invested=etf_invested,
            etfs_positions=etf_positions,
            rf_value=rf_value,
            rf_invested=rf_invested,
            rf_positions=rf_positions,
            cash_value=cash_value,
            targets=parsed_targets,
        )

        result = snap.to_dict()
        result["equity_details"] = equity_details
        result["rf_details"] = rf_details

        log.info(
            "patrimony.done", total=round(snap.total_value, 2), pl_pct=round(snap.total_pl_pct, 2)
        )
        return result

    async def ir_planning(
        self,
        user_id: str,
        cdi: float = DEFAULT_CDI,
        selic: float = DEFAULT_SELIC,
        ipca: float = DEFAULT_IPCA,
    ) -> list[dict[str, Any]]:
        """
        Retorna análise de timing fiscal para todos os títulos RF ativos do usuário.
        """
        from finanalytics_ai.domain.fixed_income.ir_calculator import analyze_ir_timing

        results: list[dict] = []
        try:
            rf_portfolios = await self._rf_repo.list_portfolios(user_id)
            for pf_summary in rf_portfolios:
                pf = await self._rf_repo.get_portfolio(pf_summary["portfolio_id"])
                if pf is None:
                    continue
                for h in pf.active_holdings:
                    idx_rate = {"CDI": cdi, "SELIC": selic, "IPCA": ipca}.get(h.indexer, cdi)
                    advice = analyze_ir_timing(
                        holding_id=h.holding_id,
                        bond_name=h.bond_name,
                        bond_type=h.bond_type,
                        issuer=h.issuer,
                        invested=h.invested,
                        purchase_date=h.purchase_date,
                        maturity_date=h.maturity_date,
                        rate_annual=h.rate_annual,
                        rate_pct_indexer=h.rate_pct_indexer,
                        indexer=h.indexer,
                        indexer_rate=idx_rate,
                        inflation_rate=ipca,
                    )
                    results.append(
                        {
                            "holding_id": advice.holding_id,
                            "bond_name": advice.bond_name,
                            "bond_type": advice.bond_type,
                            "issuer": advice.issuer,
                            "invested": advice.invested,
                            "ir_exempt": advice.ir_exempt,
                            "max_saving": advice.max_saving,
                            "recommendation": advice.recommendation,
                            "portfolio_name": pf.name,
                            "today": _scenario_dict(advice.today_scenario),
                            "best": _scenario_dict(advice.best_scenario),
                            "scenarios": [_scenario_dict(s) for s in advice.scenarios],
                        }
                    )
        except Exception as e:
            logger.warning("patrimony.ir_error", error=str(e))

        # Ordena por economia potencial decrescente
        results.sort(key=lambda x: -x["max_saving"])
        return results


def _scenario_dict(s) -> dict:
    return {
        "label": s.label,
        "redemption_date": s.redemption_date.isoformat(),
        "days_held": s.days_held,
        "gross_value": s.gross_value,
        "gross_yield": s.gross_yield,
        "iof_rate_pct": round(s.iof_rate * 100, 1),
        "iof_amount": s.iof_amount,
        "ir_rate_pct": round(s.ir_rate * 100, 1),
        "ir_amount": s.ir_amount,
        "net_value": s.net_value,
        "net_yield": s.net_yield,
        "net_yield_pct": s.net_yield_pct,
        "net_annual_pct": s.net_annual_pct,
        "total_tax": s.total_tax,
        "effective_tax_pct": s.effective_tax_pct,
    }

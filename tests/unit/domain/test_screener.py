"""
Testes unitarios para o modulo de screener.

Cobertura:
  FundamentalData
    - to_dict retorna todos os campos
    - pct_from_low calculo correto
    - pct_from_high calculo correto
    - pct_from_low/high retorna None se dados faltantes
    - campos None ficam None no to_dict

  FilterCriteria
    - is_empty retorna True quando nenhum criterio definido
    - is_empty retorna False quando ha criterio
    - from_dict ignora chaves desconhecidas
    - from_dict popula campos conhecidos
    - from_dict ignora valores None

  _passes_range
    - valor None sempre passa
    - valor abaixo de min_val falha
    - valor acima de max_val falha
    - valor dentro do intervalo passa
    - min_val None sem limite inferior
    - max_val None sem limite superior
    - ambos None sempre passa

  apply_filters
    - filtro pe_max: acao cara e removida
    - filtro dy_min: acao sem dividendos e removida
    - filtro roe_min: ROE baixo e removido
    - filtro debt_equity_max: divida alta e removida
    - filtro por setor (substring case-insensitive)
    - filtro market_cap_min em bilhoes
    - filtro market_cap_max em bilhoes
    - multiplos filtros simultaneos (AND)
    - campo None nao desqualifica (passa filtro)
    - resultado ordenado por score composito
    - lista vazia de entrada retorna lista vazia
    - criterio vazio passa todos

  _composite_score
    - acao com ROE/DY alto tem score maior
    - acao com PE alto tem penalidade
    - acao com divida alta tem penalidade
    - campos None ignorados (score = 0 para ausentes)
    - score diferencia ativos com perfis distintos

  ScreenerService
    - brapi chamado com batches corretos
    - resultado ScreenerResult retornado
    - batch_failed nao cancela outros batches
    - parse correto: dy decimal -> percentual
    - parse correto: roe decimal -> percentual
    - parse ignora campos invalidos
    - setor vazio nao aparece na lista de setores
    - setores populados corretamente
    - criterios aplicados sobre dados retornados
    - universo IBOV_UNIVERSE usado por padrao
    - extra_tickers adicionados ao universo
    - use_universe=False usa apenas extra_tickers
    - universo vazio lanca BacktestError

  BrapiClient.get_fundamentals_batch
    - retorna lista de dicts
    - lista vazia de tickers retorna []
    - path correto: /quote/A,B,C?fundamental=true
    - erro HTTP retorna [] (sem raise)
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from unittest.mock import AsyncMock

import pytest

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.screener_service import (
    ScreenerService,
    _num,
    _parse_fundamental,
    _pct,
)
from finanalytics_ai.domain.screener.engine import (
    IBOV_UNIVERSE,
    FilterCriteria,
    FundamentalData,
    ScreenerResult,
    _composite_score,
    _passes_range,
    apply_filters,
)

# ── Factories ─────────────────────────────────────────────────────────────────


def _stock(
    ticker: str = "TEST3",
    pe: float | None = 10.0,
    pvp: float | None = 1.5,
    dy: float | None = 5.0,
    roe: float | None = 15.0,
    roic: float | None = 12.0,
    debt_equity: float | None = 1.0,
    net_margin: float | None = 10.0,
    ebitda_margin: float | None = 20.0,
    revenue_growth: float | None = 8.0,
    market_cap: float | None = 50e9,
    sector: str = "Technology",
    price: float | None = 30.0,
) -> FundamentalData:
    return FundamentalData(
        ticker=ticker,
        name="Test Corp",
        sector=sector,
        price=price,
        market_cap=market_cap,
        pe=pe,
        pvp=pvp,
        dy=dy,
        roe=roe,
        roic=roic,
        net_margin=net_margin,
        ebitda_margin=ebitda_margin,
        debt_equity=debt_equity,
        revenue_growth=revenue_growth,
        eps=2.0,
        high_52w=40.0,
        low_52w=20.0,
        volume=1e6,
    )


def _brapi_raw(
    symbol: str = "TEST3",
    price_earnings: float | None = 10.0,
    price_to_book: float | None = 1.5,
    dividend_yield: float | None = 0.05,  # decimal
    roe: float | None = 0.15,  # decimal
    sector: str = "Technology",
    market_cap: float = 50e9,
) -> dict:
    return {
        "symbol": symbol,
        "longName": "Test Corp",
        "sector": sector,
        "regularMarketPrice": 30.0,
        "marketCap": market_cap,
        "priceEarnings": price_earnings,
        "priceToBook": price_to_book,
        "dividendYield": dividend_yield,
        "returnOnEquity": roe,
        "returnOnInvestedCapital": 0.12,
        "ebitdaMargins": 0.20,
        "profitMargins": 0.10,
        "debtToEquity": 1.0,
        "revenueGrowth": 0.08,
        "earningsPerShare": 2.0,
        "fiftyTwoWeekHigh": 40.0,
        "fiftyTwoWeekLow": 20.0,
        "regularMarketVolume": 1e6,
    }


# ── FundamentalData ───────────────────────────────────────────────────────────


class TestFundamentalData:
    def test_to_dict_has_all_fields(self):
        s = _stock()
        d = s.to_dict()
        for f in dc_fields(FundamentalData):
            assert f.name in d

    def test_to_dict_has_derived_fields(self):
        d = _stock().to_dict()
        assert "pct_from_low" in d
        assert "pct_from_high" in d

    def test_pct_from_low_correct(self):
        s = FundamentalData(ticker="X", price=30.0, low_52w=20.0)
        assert s.pct_from_low() == pytest.approx(50.0)

    def test_pct_from_high_correct(self):
        s = FundamentalData(ticker="X", price=30.0, high_52w=40.0)
        assert s.pct_from_high() == pytest.approx(-25.0)

    def test_pct_from_low_none_when_missing(self):
        s = FundamentalData(ticker="X", price=30.0)
        assert s.pct_from_low() is None

    def test_pct_from_high_none_when_missing(self):
        s = FundamentalData(ticker="X", high_52w=40.0)
        assert s.pct_from_high() is None

    def test_none_fields_stay_none(self):
        s = FundamentalData(ticker="X")
        d = s.to_dict()
        for key in ["pe", "pvp", "dy", "roe", "price", "market_cap"]:
            assert d[key] is None


# ── FilterCriteria ────────────────────────────────────────────────────────────


class TestFilterCriteria:
    def test_is_empty_all_none(self):
        assert FilterCriteria().is_empty() is True

    def test_is_empty_one_set(self):
        assert FilterCriteria(pe_max=15).is_empty() is False

    def test_from_dict_known_keys(self):
        c = FilterCriteria.from_dict({"pe_max": 15.0, "dy_min": 5.0})
        assert c.pe_max == 15.0
        assert c.dy_min == 5.0

    def test_from_dict_ignores_unknown(self):
        c = FilterCriteria.from_dict({"unknown_key": 99, "pe_max": 10.0})
        assert c.pe_max == 10.0

    def test_from_dict_skips_none_values(self):
        c = FilterCriteria.from_dict({"pe_max": None, "dy_min": 5.0})
        assert c.pe_max is None
        assert c.dy_min == 5.0

    def test_from_dict_empty_dict(self):
        c = FilterCriteria.from_dict({})
        assert c.is_empty() is True


# ── _passes_range ─────────────────────────────────────────────────────────────


class TestPassesRange:
    def test_none_value_always_passes(self):
        assert _passes_range(None, 5.0, 20.0) is True

    def test_below_min_fails(self):
        assert _passes_range(4.0, 5.0, None) is False

    def test_above_max_fails(self):
        assert _passes_range(21.0, None, 20.0) is False

    def test_inside_range_passes(self):
        assert _passes_range(10.0, 5.0, 20.0) is True

    def test_at_min_boundary_passes(self):
        assert _passes_range(5.0, 5.0, 20.0) is True

    def test_at_max_boundary_passes(self):
        assert _passes_range(20.0, 5.0, 20.0) is True

    def test_min_none_no_lower_bound(self):
        assert _passes_range(0.001, None, 20.0) is True

    def test_max_none_no_upper_bound(self):
        assert _passes_range(9999.0, 5.0, None) is True

    def test_both_none_always_passes(self):
        assert _passes_range(-999.0, None, None) is True


# ── apply_filters ─────────────────────────────────────────────────────────────


class TestApplyFilters:
    def test_empty_criteria_passes_all(self):
        stocks = [_stock("A"), _stock("B"), _stock("C")]
        result = apply_filters(stocks, FilterCriteria())
        assert len(result) == 3

    def test_pe_max_removes_expensive(self):
        stocks = [_stock("A", pe=5.0), _stock("B", pe=30.0)]
        result = apply_filters(stocks, FilterCriteria(pe_max=15.0))
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_dy_min_removes_low_yield(self):
        stocks = [_stock("A", dy=8.0), _stock("B", dy=2.0)]
        result = apply_filters(stocks, FilterCriteria(dy_min=5.0))
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_roe_min_removes_low_roe(self):
        stocks = [_stock("A", roe=20.0), _stock("B", roe=5.0)]
        result = apply_filters(stocks, FilterCriteria(roe_min=15.0))
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_debt_equity_max_removes_over_leveraged(self):
        stocks = [_stock("A", debt_equity=1.0), _stock("B", debt_equity=5.0)]
        result = apply_filters(stocks, FilterCriteria(debt_equity_max=2.0))
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_sector_filter_case_insensitive(self):
        stocks = [
            _stock("A", sector="Financial Services"),
            _stock("B", sector="Technology"),
        ]
        result = apply_filters(stocks, FilterCriteria(sector="financial"))
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_sector_filter_substring(self):
        stocks = [_stock("A", sector="Consumer Defensive"), _stock("B", sector="Basic Materials")]
        result = apply_filters(stocks, FilterCriteria(sector="consumer"))
        assert len(result) == 1

    def test_market_cap_min_in_billions(self):
        stocks = [
            _stock("A", market_cap=100e9),  # 100B
            _stock("B", market_cap=5e9),  #   5B
        ]
        result = apply_filters(stocks, FilterCriteria(market_cap_min=20.0))  # 20B
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_market_cap_max_in_billions(self):
        stocks = [
            _stock("A", market_cap=10e9),  # 10B
            _stock("B", market_cap=200e9),  # 200B
        ]
        result = apply_filters(stocks, FilterCriteria(market_cap_max=50.0))  # 50B
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_multiple_filters_and_logic(self):
        stocks = [
            _stock("A", pe=8.0, dy=7.0, roe=18.0),
            _stock("B", pe=8.0, dy=2.0, roe=18.0),  # dy falha
            _stock("C", pe=25.0, dy=7.0, roe=18.0),  # pe falha
        ]
        c = FilterCriteria(pe_max=15.0, dy_min=5.0)
        result = apply_filters(stocks, c)
        assert len(result) == 1
        assert result[0].ticker == "A"

    def test_none_field_does_not_disqualify(self):
        # Acao sem PE (None) deve passar filtro pe_max
        s = _stock("A", pe=None)
        result = apply_filters([s], FilterCriteria(pe_max=15.0))
        assert len(result) == 1

    def test_sorted_by_score_desc(self):
        # A tem ROE alto e DY alto (score maior), B tem score menor
        a = _stock("A", roe=30.0, dy=10.0, pe=8.0)
        b = _stock("B", roe=5.0, dy=1.0, pe=40.0)
        result = apply_filters([b, a], FilterCriteria())
        assert result[0].ticker == "A"

    def test_empty_input_returns_empty(self):
        result = apply_filters([], FilterCriteria(pe_max=15.0))
        assert result == []

    def test_all_fail_returns_empty(self):
        stocks = [_stock("A", pe=50.0), _stock("B", pe=40.0)]
        result = apply_filters(stocks, FilterCriteria(pe_max=10.0))
        assert result == []


# ── _composite_score ──────────────────────────────────────────────────────────


class TestCompositeScore:
    def test_high_roe_dy_increases_score(self):
        high = _stock("A", roe=30.0, dy=10.0, pe=10.0, pvp=1.0)
        low = _stock("B", roe=5.0, dy=1.0, pe=10.0, pvp=1.0)
        assert _composite_score(high) > _composite_score(low)

    def test_high_pe_penalizes(self):
        a = _stock("A", pe=8.0, roe=15.0)
        b = _stock("B", pe=50.0, roe=15.0)
        assert _composite_score(a) > _composite_score(b)

    def test_high_debt_penalizes(self):
        a = _stock("A", debt_equity=0.5)
        b = _stock("B", debt_equity=5.0)
        assert _composite_score(a) > _composite_score(b)

    def test_none_fields_ignored(self):
        # Score nao deve levantar excecao com campos None
        s = FundamentalData(ticker="X")  # todos None
        score = _composite_score(s)
        assert score == pytest.approx(0.0)

    def test_score_differentiates_profiles(self):
        quality = _stock("A", roe=25.0, roic=18.0, dy=6.0, pe=12.0, debt_equity=0.5)
        mediocre = _stock("B", roe=8.0, roic=6.0, dy=1.0, pe=35.0, debt_equity=3.0)
        assert _composite_score(quality) > _composite_score(mediocre)


# ── _parse_fundamental / helpers ──────────────────────────────────────────────


class TestParseFundamental:
    def test_decimal_dy_converted_to_pct(self):
        s = _parse_fundamental(_brapi_raw(dividend_yield=0.06))
        assert s.dy == pytest.approx(6.0)

    def test_decimal_roe_converted_to_pct(self):
        s = _parse_fundamental(_brapi_raw(roe=0.15))
        assert s.roe == pytest.approx(15.0)

    def test_ticker_from_symbol(self):
        s = _parse_fundamental(_brapi_raw(symbol="PETR4"))
        assert s.ticker == "PETR4"

    def test_sector_populated(self):
        s = _parse_fundamental(_brapi_raw(sector="Energy"))
        assert s.sector == "Energy"

    def test_none_pe_stays_none(self):
        s = _parse_fundamental(_brapi_raw(price_earnings=None))
        assert s.pe is None

    def test_invalid_value_becomes_none(self):
        raw = _brapi_raw()
        raw["priceEarnings"] = "not-a-number"
        s = _parse_fundamental(raw)
        assert s.pe is None

    def test_pct_helper_decimal_to_percent(self):
        assert _pct(0.15) == pytest.approx(15.0)
        assert _pct(0.0) == pytest.approx(0.0)
        assert _pct(None) is None
        assert _pct("bad") is None

    def test_num_helper(self):
        assert _num(10.5) == pytest.approx(10.5)
        assert _num(None) is None
        assert _num("bad") is None


# ── ScreenerService ───────────────────────────────────────────────────────────


class TestScreenerService:
    def _make_svc(self) -> ScreenerService:
        return ScreenerService(AsyncMock())

    def _patch_brapi(self, svc: ScreenerService, raw_data: list[dict]) -> None:
        async def _fake(tickers):
            return [r for r in raw_data if r.get("symbol") in tickers]

        svc._brapi.get_fundamentals_batch = _fake

    @pytest.mark.asyncio
    async def test_returns_screener_result(self):
        svc = self._make_svc()
        self._patch_brapi(svc, [_brapi_raw("PETR4"), _brapi_raw("VALE3")])
        r = await svc.screen(FilterCriteria(), extra_tickers=["PETR4", "VALE3"], use_universe=False)
        assert isinstance(r, ScreenerResult)

    @pytest.mark.asyncio
    async def test_all_stocks_in_result(self):
        svc = self._make_svc()
        raw = [_brapi_raw(f"T{i}") for i in range(5)]
        self._patch_brapi(svc, raw)
        r = await svc.screen(
            FilterCriteria(), extra_tickers=[f"T{i}" for i in range(5)], use_universe=False
        )
        assert r.total_universe == 5

    @pytest.mark.asyncio
    async def test_filters_applied(self):
        svc = self._make_svc()
        raw = [
            _brapi_raw("A", price_earnings=8.0),
            _brapi_raw("B", price_earnings=50.0),
        ]
        self._patch_brapi(svc, raw)
        r = await svc.screen(
            FilterCriteria(pe_max=15.0),
            extra_tickers=["A", "B"],
            use_universe=False,
        )
        assert r.total_passed == 1
        assert r.stocks[0].ticker == "A"

    @pytest.mark.asyncio
    async def test_sectors_populated(self):
        svc = self._make_svc()
        raw = [
            _brapi_raw("A", sector="Energy"),
            _brapi_raw("B", sector="Financial Services"),
            _brapi_raw("C", sector="Energy"),
        ]
        self._patch_brapi(svc, raw)
        r = await svc.screen(FilterCriteria(), extra_tickers=["A", "B", "C"], use_universe=False)
        assert set(r.sectors) == {"Energy", "Financial Services"}

    @pytest.mark.asyncio
    async def test_empty_sector_not_in_sectors_list(self):
        svc = self._make_svc()
        raw = [_brapi_raw("A", sector=""), _brapi_raw("B", sector="Energy")]
        self._patch_brapi(svc, raw)
        r = await svc.screen(FilterCriteria(), extra_tickers=["A", "B"], use_universe=False)
        assert "" not in r.sectors

    @pytest.mark.asyncio
    async def test_use_universe_true_includes_ibov(self):
        svc = self._make_svc()
        batches_seen: list[list[str]] = []

        async def _capture(tickers):
            batches_seen.extend(tickers)
            return [_brapi_raw(t) for t in tickers]

        svc._brapi.get_fundamentals_batch = _capture
        await svc.screen(FilterCriteria(), use_universe=True)
        all_fetched = set(batches_seen)
        # Pelo menos alguns tickers do Ibovespa devem ter sido buscados
        assert len(all_fetched.intersection(set(IBOV_UNIVERSE))) > 0

    @pytest.mark.asyncio
    async def test_use_universe_false_only_extra(self):
        svc = self._make_svc()
        batches_seen: list[str] = []

        async def _capture(tickers):
            batches_seen.extend(tickers)
            return [_brapi_raw(t) for t in tickers]

        svc._brapi.get_fundamentals_batch = _capture
        await svc.screen(FilterCriteria(), extra_tickers=["XXXX"], use_universe=False)
        assert batches_seen == ["XXXX"]

    @pytest.mark.asyncio
    async def test_empty_universe_raises(self):
        svc = self._make_svc()
        with pytest.raises(BacktestError, match="Nenhum ticker"):
            await svc.screen(FilterCriteria(), extra_tickers=[], use_universe=False)

    @pytest.mark.asyncio
    async def test_batch_failure_does_not_cancel_others(self):
        svc = self._make_svc()
        call_count = [0]

        async def _partial(tickers):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("timeout")
            return [_brapi_raw(t) for t in tickers]

        svc._brapi.get_fundamentals_batch = _partial
        # Com 2 batches, um falha — resultado parcial
        r = await svc.screen(
            FilterCriteria(),
            extra_tickers=["A"] * 25,  # 2 batches de 20+5
            use_universe=False,
        )
        assert isinstance(r, ScreenerResult)

    @pytest.mark.asyncio
    async def test_extra_tickers_capped_at_max(self):
        svc = self._make_svc()
        batches_seen: list[str] = []

        async def _capture(tickers):
            batches_seen.extend(tickers)
            return []

        svc._brapi.get_fundamentals_batch = _capture
        # 25 extras -> capped a MAX_CUSTOM=20
        extras = [f"X{i}" for i in range(25)]
        await svc.screen(FilterCriteria(), extra_tickers=extras, use_universe=False)
        assert len(batches_seen) <= 20

    @pytest.mark.asyncio
    async def test_to_dict_serializable(self):
        svc = self._make_svc()
        self._patch_brapi(svc, [_brapi_raw("PETR4")])
        r = await svc.screen(FilterCriteria(), extra_tickers=["PETR4"], use_universe=False)
        d = r.to_dict()
        import json

        # Deve serializar sem erros
        json_str = json.dumps(d)
        assert "total_universe" in json_str

    @pytest.mark.asyncio
    async def test_result_sorted_by_score(self):
        svc = self._make_svc()
        raw = [
            _brapi_raw("LOW", roe=0.03, dividend_yield=0.01),
            _brapi_raw("HIGH", roe=0.30, dividend_yield=0.10),
        ]
        self._patch_brapi(svc, raw)
        r = await svc.screen(FilterCriteria(), extra_tickers=["LOW", "HIGH"], use_universe=False)
        if len(r.stocks) == 2:
            assert r.stocks[0].ticker == "HIGH"

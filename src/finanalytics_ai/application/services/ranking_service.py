"""
RankingService -- ranking/score de acoes com dados Fintz.

Metodologias implementadas:

  magic_formula (Greenblatt):
    ROIC alto + EV/EBIT baixo = empresa boa a preco bom
    Ranking: soma dos ranks de ROIC (desc) e EV_EBIT (asc)
    Referencia: "The Little Book That Beats The Market" (2005)

  barsi:
    Empresas geradoras de caixa com DY alto e divida controlada
    Score: DY * 2 + ROE - DividaLiquida_EBITDA
    Inspirado na filosofia de Luis Barsi Filho

  quality:
    Empresas de alta qualidade: ROE, ROIC, margens e liquidez
    Score ponderado de rentabilidade e solidez

  value:
    Empresas baratas: P/L, P/VP, EV/EBITDA baixos
    Score inverso de multiplos de valuation

  composite (padrao):
    Combinacao de quality (40%) + value (30%) + dy (30%)
    Equilibra qualidade, valuation e proventos

Design:
  - Um unico query SQL busca todos os indicadores necessarios
  - Ranking por percentil (0-100): posicao relativa no universo
  - Campos None = empresa sem dado = nao rankeia naquele criterio
  - Top N configuravel
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
import structlog

logger = structlog.get_logger(__name__)

# Indicadores necessarios por metodologia
_INDICATORS_NEEDED = [
    "ROE",
    "ROIC",
    "ROA",
    "DividendYield",
    "P_L",
    "P_VP",
    "EV_EBITDA",
    "EV_EBIT",
    "MargemEBITDA",
    "MargemLiquida",
    "MargemBruta",
    "DividaLiquida_EBITDA",
    "DividaLiquida_PatrimonioLiquido",
    "LiquidezCorrente",
    "ValorDeMercado",
    "GiroAtivos",
]

# Indicadores em decimal que precisam x100
_PCT_INDICATORS = {
    "ROE",
    "ROIC",
    "ROA",
    "DividendYield",
    "MargemEBITDA",
    "MargemLiquida",
    "MargemBruta",
}

METODOLOGIAS = {
    "magic_formula": "Greenblatt: ROIC alto + EV/EBIT baixo",
    "barsi": "Barsi: DY alto + ROE solido + divida controlada",
    "quality": "Qualidade: ROE, ROIC, margens e liquidez",
    "value": "Value: P/L, P/VP, EV/EBITDA baixos",
    "composite": "Composto: qualidade (40%) + value (30%) + proventos (30%)",
}


@dataclass
class RankedStock:
    ticker: str
    rank: int
    score: float  # 0-100 (percentil)
    metodologia: str
    indicadores: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "score": round(self.score, 2),
            "metodologia": self.metodologia,
            **{k: (round(v, 4) if v is not None else None) for k, v in self.indicadores.items()},
        }


@dataclass
class RankingResult:
    metodologia: str
    descricao: str
    total_universe: int
    top_n: int
    stocks: list[RankedStock]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metodologia": self.metodologia,
            "descricao": self.descricao,
            "total_universe": self.total_universe,
            "top_n": self.top_n,
            "stocks": [s.to_dict() for s in self.stocks],
        }


class RankingService:
    """
    Servico de ranking de acoes usando indicadores Fintz.

    session_factory: callable que retorna AsyncSession.
    """

    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def rank(
        self,
        metodologia: str = "composite",
        top_n: int = 20,
        min_market_cap_bi: float = 1.0,
        tickers_filter: list[str] | None = None,
    ) -> RankingResult:
        """
        Gera ranking de acoes.

        metodologia: magic_formula | barsi | quality | value | composite
        top_n: numero de acoes no ranking (max 100)
        min_market_cap_bi: market cap minimo em bilhoes R$ (filtra small caps)
        tickers_filter: lista de tickers para restringir o universo
        """
        if metodologia not in METODOLOGIAS:
            raise ValueError(
                f"Metodologia '{metodologia}' invalida. Use: {', '.join(METODOLOGIAS)}"
            )

        top_n = min(top_n, 100)
        log = logger.bind(metodologia=metodologia, top_n=top_n)
        log.info("ranking.starting")

        # Busca dados do banco
        raw = await self._fetch_indicators(tickers_filter)
        log.info("ranking.data_fetched", tickers=len(raw))

        # Filtra por market cap minimo
        if min_market_cap_bi > 0:
            raw = {
                t: v
                for t, v in raw.items()
                if (v.get("ValorDeMercado") or 0) >= min_market_cap_bi * 1e9
            }
            log.info("ranking.after_mcap_filter", tickers=len(raw))

        # Calcula score por metodologia
        scored = _score_all(raw, metodologia)

        # Ordena e pega top N
        scored.sort(key=lambda x: x[1], reverse=True)
        universe_size = len(scored)

        # Converte para percentil (0-100)
        result_stocks = []
        for i, (ticker, raw_score, indicators) in enumerate(scored[:top_n]):
            percentil = (1 - i / max(universe_size - 1, 1)) * 100
            result_stocks.append(
                RankedStock(
                    ticker=ticker,
                    rank=i + 1,
                    score=round(percentil, 2),
                    metodologia=metodologia,
                    indicadores=indicators,
                )
            )

        log.info("ranking.done", ranked=len(result_stocks), universe=universe_size)

        return RankingResult(
            metodologia=metodologia,
            descricao=METODOLOGIAS[metodologia],
            total_universe=universe_size,
            top_n=len(result_stocks),
            stocks=result_stocks,
        )

    async def _fetch_indicators(
        self,
        tickers_filter: list[str] | None = None,
    ) -> dict[str, dict[str, float | None]]:
        """Busca indicadores mais recentes por ticker."""
        ind_list = ", ".join(f"'{i}'" for i in _INDICATORS_NEEDED)
        ticker_clause = ""
        if tickers_filter:
            tickers_str = ", ".join(f"'{t}'" for t in tickers_filter)
            ticker_clause = f"AND ticker IN ({tickers_str})"

        query = text(f"""
            SELECT ticker, indicador, valor
            FROM (
                SELECT ticker, indicador, valor,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker, indicador
                           ORDER BY data_publicacao DESC
                       ) as rn
                FROM fintz_indicadores
                WHERE indicador IN ({ind_list})
                {ticker_clause}
            ) ranked
            WHERE rn = 1
        """)

        async with self._sf() as session:
            result = await session.execute(query)
            rows = result.fetchall()

        data: dict[str, dict[str, float | None]] = {}
        for row in rows:
            ticker, ind, valor = row
            if ticker not in data:
                data[ticker] = {}
            try:
                v = float(valor) if valor is not None else None
                if v is not None and ind in _PCT_INDICATORS:
                    v = round(v * 100, 4)
                data[ticker][ind] = v
            except (TypeError, ValueError):
                data[ticker][ind] = None

        return data


# ─────────────────────────────────────────────────────────────────────────────
# Funcoes de scoring por metodologia
# ─────────────────────────────────────────────────────────────────────────────


def _get(d: dict, key: str) -> float | None:
    v = d.get(key)
    if v is None or (isinstance(v, float) and v != v):
        return None
    return v


def _score_all(
    raw: dict[str, dict[str, float | None]],
    metodologia: str,
) -> list[tuple[str, float, dict[str, float | None]]]:
    """Calcula score de todos os tickers para a metodologia."""
    fn = {
        "magic_formula": _score_magic_formula,
        "barsi": _score_barsi,
        "quality": _score_quality,
        "value": _score_value,
        "composite": _score_composite,
    }[metodologia]

    # Para magic_formula: precisa de rank relativo (dois passes)
    if metodologia == "magic_formula":
        return _rank_magic_formula(raw)

    result = []
    for ticker, indicators in raw.items():
        score = fn(indicators)
        if score is not None:
            result.append((ticker, score, indicators))

    return result


def _score_magic_formula(d: dict) -> float | None:
    """Nao usado diretamente — veja _rank_magic_formula."""
    roic = _get(d, "ROIC")
    ev_ebit = _get(d, "EV_EBIT")
    if roic is None or ev_ebit is None or ev_ebit <= 0:
        return None
    return roic / ev_ebit  # proxy para rank combinado


def _rank_magic_formula(
    raw: dict[str, dict[str, float | None]],
) -> list[tuple[str, float, dict[str, float | None]]]:
    """
    Greenblatt Magic Formula:
      1. Rank por ROIC decrescente (empresa boa)
      2. Rank por EV/EBIT crescente (empresa barata)
      3. Score final = 200 - (rank_roic + rank_ev_ebit)
    """
    # Filtra empresas com ambos os indicadores positivos
    valid = [
        (t, v)
        for t, v in raw.items()
        if _get(v, "ROIC") is not None
        and _get(v, "EV_EBIT") is not None
        and _get(v, "EV_EBIT") > 0  # type: ignore
        and _get(v, "ROIC") > 0  # type: ignore
    ]

    if not valid:
        return []

    # Rank ROIC (maior = melhor = rank 1)
    by_roic = sorted(valid, key=lambda x: _get(x[1], "ROIC") or 0, reverse=True)
    rank_roic = {t: i + 1 for i, (t, _) in enumerate(by_roic)}

    # Rank EV/EBIT (menor = melhor = rank 1)
    by_ev = sorted(valid, key=lambda x: _get(x[1], "EV_EBIT") or 999)
    rank_ev = {t: i + 1 for i, (t, _) in enumerate(by_ev)}

    n = len(valid)
    result = []
    for ticker, indicators in valid:
        combined_rank = rank_roic[ticker] + rank_ev[ticker]
        # Score: menor rank combinado = melhor = score maior
        score = (2 * n - combined_rank) / (2 * n) * 100
        result.append((ticker, score, indicators))

    return result


def _score_barsi(d: dict) -> float | None:
    """
    Inspirado em Barsi: foco em proventos e qualidade.
    DY alto + ROE solido + divida controlada.
    """
    dy = _get(d, "DividendYield")
    roe = _get(d, "ROE")
    div_ebitda = _get(d, "DividaLiquida_EBITDA")

    if dy is None and roe is None:
        return None

    score = 0.0
    if dy is not None and dy > 0:
        score += dy * 3.0  # DY e o fator principal
    if roe is not None and roe > 0:
        score += roe * 1.0
    if div_ebitda is not None:
        if div_ebitda > 0:
            score -= div_ebitda * 2.0  # penalidade por alavancagem
        else:
            score += 5.0  # bonus: caixa liquido

    return score


def _score_quality(d: dict) -> float | None:
    """
    Qualidade: rentabilidade alta + solidez financeira.
    """
    roe = _get(d, "ROE")
    roic = _get(d, "ROIC")
    margem_liq = _get(d, "MargemLiquida")
    margem_ebitda = _get(d, "MargemEBITDA")
    liquidez = _get(d, "LiquidezCorrente")
    giro = _get(d, "GiroAtivos")

    if roe is None and roic is None:
        return None

    score = 0.0
    if roe is not None and roe > 0:
        score += roe * 0.35
    if roic is not None and roic > 0:
        score += roic * 0.35
    if margem_liq is not None and margem_liq > 0:
        score += margem_liq * 0.15
    if margem_ebitda is not None and margem_ebitda > 0:
        score += margem_ebitda * 0.10
    if liquidez is not None and liquidez > 1:
        score += min(liquidez - 1, 2) * 2.0
    if giro is not None and giro > 0:
        score += min(giro, 2) * 1.0

    return score


def _score_value(d: dict) -> float | None:
    """
    Value: multiplos de valuation baixos.
    Quanto menor o multiplo, maior o score.
    """
    pe = _get(d, "P_L")
    pvp = _get(d, "P_VP")
    ev_ebitda = _get(d, "EV_EBITDA")

    if pe is None and pvp is None and ev_ebitda is None:
        return None

    # So considera multiplos positivos (empresa lucrativa)
    score = 0.0
    count = 0

    if pe is not None and 0 < pe < 100:
        score += (1 / pe) * 20
        count += 1
    if pvp is not None and 0 < pvp < 20:
        score += (1 / pvp) * 10
        count += 1
    if ev_ebitda is not None and 0 < ev_ebitda < 50:
        score += (1 / ev_ebitda) * 15
        count += 1

    if count == 0:
        return None

    return score


def _score_composite(d: dict) -> float | None:
    """
    Score composto: qualidade (40%) + value (30%) + proventos (30%).
    """
    q = _score_quality(d)
    v = _score_value(d)
    dy = _get(d, "DividendYield")

    if q is None and v is None:
        return None

    score = 0.0
    weight_sum = 0.0

    if q is not None:
        score += q * 0.4
        weight_sum += 0.4
    if v is not None:
        score += v * 0.3
        weight_sum += 0.3
    if dy is not None and dy > 0:
        score += dy * 0.3
        weight_sum += 0.3

    if weight_sum == 0:
        return None

    return score / weight_sum * 100

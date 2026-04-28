"""
finanalytics_ai.interfaces.api.routes.crypto
--------------------------------------------
Rotas de criptoativos via CoinGecko.

GET  /api/v1/crypto/prices          -- precos em tempo real
GET  /api/v1/crypto/global          -- dados globais do mercado
GET  /api/v1/crypto/fear-greed      -- Fear & Greed Index
GET  /api/v1/crypto/technical/{sym} -- analise tecnica
POST /api/v1/crypto/portfolio       -- calcula P&L da carteira
POST /api/v1/crypto/import          -- importa CSV de posicoes
"""

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/crypto", tags=["Crypto"])

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT"]


def _svc(request: Request):
    from finanalytics_ai.application.services.crypto_service import CryptoService

    svc = getattr(request.app.state, "crypto_service", None)
    if svc is None:
        svc = CryptoService()
        request.app.state.crypto_service = svc
    return svc


class PositionInput(BaseModel):
    symbol: str
    quantity: float = Field(..., gt=0)
    avg_price: float = Field(..., gt=0)
    currency: str = "brl"


class PortfolioRequest(BaseModel):
    positions: list[PositionInput]
    vs_currency: str = "brl"


@router.get("/prices", summary="Precos em tempo real")
async def get_prices(
    request: Request,
    symbols: str = Query(
        ",".join(DEFAULT_SYMBOLS[:10]),
        description="Simbolos separados por virgula (ex: BTC,ETH,SOL)",
    ),
    vs_currency: str = Query("brl", description="Moeda de cotacao: brl ou usd"),
) -> dict[str, Any]:
    svc = _svc(request)
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(400, "Informe pelo menos 1 simbolo")
    try:
        prices = await svc.get_prices(symbol_list, vs_currency)
        return {"total": len(prices), "vs_currency": vs_currency, "coins": prices}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/global", summary="Dados globais do mercado cripto")
async def get_global(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    try:
        return await svc.get_global()
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/fear-greed", summary="Fear & Greed Index")
async def get_fear_greed(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    try:
        return await svc.get_fear_greed()
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/technical/{symbol}", summary="Analise tecnica de uma cripto")
async def get_technical(
    symbol: str,
    request: Request,
    days: int = Query(90, ge=7, le=365),
    vs_currency: str = Query("usd"),
) -> dict[str, Any]:
    svc = _svc(request)
    try:
        data = await svc.get_historical(symbol.upper(), days, vs_currency)
        if not data:
            raise HTTPException(404, f"Sem dados para {symbol.upper()}")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/signal/{symbol}", summary="Sinal BUY/SELL/HOLD agregado")
async def get_signal(
    symbol: str,
    request: Request,
    days: int = Query(180, ge=180, le=365, description="180+ pra CoinGecko gerar candles diários suficientes pros indicadores"),
    vs_currency: str = Query("usd"),
) -> dict[str, Any]:
    """Score weighted dos 4 indicadores técnicos → BUY/SELL/HOLD.

    Pesos:
      - RSI:        <30 +2  | 30-50 +1  | 50-70 -1  | >70 -2
      - MACD:       hist > 0 +1  | hist <= 0 -1
      - EMA cross:  ema9 > ema21 +1 | else -1
      - Bollinger:  price < lower +1 | price > upper -1 | else 0

    Total ≥ +3 → BUY · ≤ -3 → SELL · else HOLD
    """
    svc = _svc(request)
    try:
        data = await svc.get_historical(symbol.upper(), days, vs_currency)
        if not data or "technical" not in data:
            raise HTTPException(404, f"Sem dados técnicos para {symbol.upper()}")
        t = data["technical"]
        components: dict[str, Any] = {}
        score = 0

        rsi = t.get("rsi")
        if rsi is not None:
            if rsi < 30: r = 2
            elif rsi < 50: r = 1
            elif rsi < 70: r = -1
            else: r = -2
            components["rsi"] = {"value": rsi, "score": r}
            score += r

        macd_h = t.get("macd_hist")
        if macd_h is not None:
            m = 1 if macd_h > 0 else -1
            components["macd"] = {"hist": macd_h, "score": m}
            score += m

        ema9, ema21 = t.get("ema9"), t.get("ema21")
        if ema9 is not None and ema21 is not None:
            e = 1 if ema9 > ema21 else -1
            components["ema_cross"] = {"ema9": ema9, "ema21": ema21, "score": e}
            score += e

        bb_u, bb_l = t.get("bb_upper"), t.get("bb_lower")
        price = data.get("last_price") or data.get("current_price")
        if bb_u is not None and bb_l is not None and price is not None:
            if price < bb_l: b = 1
            elif price > bb_u: b = -1
            else: b = 0
            components["bollinger"] = {"price": price, "upper": bb_u, "lower": bb_l, "score": b}
            score += b

        if score >= 3:
            signal, label = "BUY", "Compra"
        elif score <= -3:
            signal, label = "SELL", "Venda"
        else:
            signal, label = "HOLD", "Aguardar"

        return {
            "symbol": symbol.upper(),
            "vs_currency": vs_currency,
            "current_price": price,
            "signal": signal,
            "label": label,
            "score": score,
            "components": components,
            "indicators": t,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/signal_history/{symbol}", summary="Historico de signals diarios")
async def get_signal_history(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    vs_currency: str = Query("usd"),
) -> dict[str, Any]:
    """N6 (28/abr): retorna ultimos N dias de snapshots de
    crypto_signals_history para o symbol. Util para analisar evolucao
    do score (multi-horizon natural via janela de dias).

    Pre-requisito: scheduler rodando snapshot_crypto_signals.py diario.
    """
    import os as _os
    import psycopg2

    dsn = (
        _os.environ.get("TIMESCALE_URL")
        or _os.environ.get("PROFIT_TIMESCALE_DSN")
        or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
    )
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    sym = symbol.upper()
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_date, signal, score, current_price,
                       rsi, macd_hist, ema9, ema21, bb_upper, bb_lower
                FROM crypto_signals_history
                WHERE symbol = %s AND vs_currency = %s
                  AND snapshot_date >= CURRENT_DATE - (%s::int * INTERVAL '1 day')
                ORDER BY snapshot_date ASC
                """,
                (sym, vs_currency, days),
            )
            rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    items = [
        {
            "snapshot_date": r[0].isoformat(),
            "signal": r[1],
            "score": r[2],
            "current_price": float(r[3]) if r[3] is not None else None,
            "rsi": float(r[4]) if r[4] is not None else None,
            "macd_hist": float(r[5]) if r[5] is not None else None,
            "ema9": float(r[6]) if r[6] is not None else None,
            "ema21": float(r[7]) if r[7] is not None else None,
            "bb_upper": float(r[8]) if r[8] is not None else None,
            "bb_lower": float(r[9]) if r[9] is not None else None,
        }
        for r in rows
    ]

    # Multi-horizon agregado: signal "predominante" em cada janela
    def _predominant(window_items: list[dict]) -> dict:
        if not window_items:
            return {"signal": None, "score_avg": None, "n": 0}
        scores = [i["score"] for i in window_items if i.get("score") is not None]
        avg = sum(scores) / len(scores) if scores else None
        from collections import Counter
        sigs = Counter(i["signal"] for i in window_items if i.get("signal"))
        return {
            "signal": sigs.most_common(1)[0][0] if sigs else None,
            "score_avg": round(avg, 2) if avg is not None else None,
            "n": len(window_items),
        }

    summary = {
        "h7d":  _predominant(items[-7:]) if len(items) >= 1 else {"signal": None, "score_avg": None, "n": 0},
        "h14d": _predominant(items[-14:]) if len(items) >= 1 else {"signal": None, "score_avg": None, "n": 0},
        "h30d": _predominant(items[-30:]) if len(items) >= 1 else {"signal": None, "score_avg": None, "n": 0},
    }

    return {
        "symbol": sym,
        "vs_currency": vs_currency,
        "days_requested": days,
        "count": len(items),
        "items": items,
        "horizons": summary,
    }


@router.post("/portfolio", summary="Calcula P&L da carteira de cripto")
async def calc_portfolio(body: PortfolioRequest, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    positions = [
        {
            "symbol": p.symbol,
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "currency": p.currency,
        }
        for p in body.positions
    ]
    try:
        return await svc.calc_portfolio(positions, body.vs_currency)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/import", summary="Importa CSV de posicoes de cripto")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    vs_currency: str = Query("brl"),
) -> dict[str, Any]:
    """
    Importa CSV com posicoes de cripto e calcula P&L.

    Formato do CSV:
      symbol,quantity,avg_price[,currency]

    Exemplo:
      BTC,0.5,280000,brl
      ETH,2.0,9500,brl
      SOL,10,350,brl
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "Arquivo deve ser .csv")

    try:
        content = (await file.read()).decode("utf-8")
        svc = _svc(request)
        positions = await svc.parse_csv(content)

        if not positions:
            raise HTTPException(400, "Nenhuma posicao valida encontrada no CSV")

        portfolio = await svc.calc_portfolio(positions, vs_currency)
        portfolio["imported_from"] = file.filename
        portfolio["rows_parsed"] = len(positions)
        return portfolio

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

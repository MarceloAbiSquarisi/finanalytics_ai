"""
finanalytics_ai.application.services.crypto_service
----------------------------------------------------
Servico de criptoativos via CoinGecko API (sem autenticacao).

Funcionalidades:
  - Precos em tempo real (USD e BRL)
  - Market cap, volume, variacao 24h/7d/30d
  - Dados OHLC historicos para analise tecnica
  - Fear & Greed Index (alternative.me)
  - Analise tecnica: RSI, MACD, EMA, Bollinger Bands
  - Gestao de carteira com P&L

Rate limit CoinGecko free: 30 req/min (sem key)
Cache Redis: 60s para precos, 300s para historico

Design:
  - Todos os calculos tecnicos em stdlib + math (sem pandas/numpy)
  - httpx async para todas as chamadas
  - Cache em memoria como fallback se Redis nao disponivel
"""

from __future__ import annotations

from datetime import UTC, datetime
import math
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/?limit=7"

# Mapa de simbolo -> id CoinGecko
COIN_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "NEAR": "near",
    "OP": "optimism",
    "ARB": "arbitrum",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
}

# Cache em memoria simples
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: int) -> Any | None:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < ttl:
            return val
    return None


def _cache_set(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


def symbol_to_id(symbol: str) -> str:
    return COIN_IDS.get(symbol.upper(), symbol.lower())


# ── Analise Tecnica (stdlib puro) ─────────────────────────────────────────────


def _sma(prices: list[float], period: int) -> list[float]:
    result = []
    for i in range(len(prices)):
        if i < period - 1:
            result.append(float("nan"))
        else:
            result.append(sum(prices[i - period + 1 : i + 1]) / period)
    return result


def _ema(prices: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    result = [float("nan")] * (period - 1)
    if len(prices) < period:
        return [float("nan")] * len(prices)
    result.append(sum(prices[:period]) / period)
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def _rsi(prices: list[float], period: int = 14) -> list[float]:
    if len(prices) < period + 1:
        return [float("nan")] * len(prices)
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    result = [float("nan")] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
    return result


def _macd(
    prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]]:
    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    macd_line = [
        (f - s) if not (math.isnan(f) or math.isnan(s)) else float("nan")
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if not math.isnan(v)]
    if len(valid) < signal:
        signal_line = [float("nan")] * len(macd_line)
    else:
        # EMA do MACD (apenas nos valores validos)
        pad = len(macd_line) - len(valid)
        ema_sig = _ema(valid, signal)
        signal_line = [float("nan")] * pad + ema_sig
    histogram = [
        (m - s) if not (math.isnan(m) or math.isnan(s)) else float("nan")
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def _bollinger(
    prices: list[float], period: int = 20, std_mult: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    mid = _sma(prices, period)
    upper, lower = [], []
    for i, m in enumerate(mid):
        if math.isnan(m):
            upper.append(float("nan"))
            lower.append(float("nan"))
        else:
            window = prices[i - period + 1 : i + 1]
            mean = m
            std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
            upper.append(m + std_mult * std)
            lower.append(m - std_mult * std)
    return upper, mid, lower


def _last_valid(lst: list[float]) -> float:
    for v in reversed(lst):
        if not math.isnan(v):
            return round(v, 4)
    return float("nan")


def _calc_technical(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 30:
        return {}
    rsi = _rsi(closes)
    macd_l, macd_s, macd_h = _macd(closes)
    bb_up, bb_mid, bb_low = _bollinger(closes)
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)

    rsi_val = _last_valid(rsi)
    price = closes[-1]

    # Sinal composto
    signals = []
    if rsi_val < 30:
        signals.append("RSI sobrevendido")
    elif rsi_val > 70:
        signals.append("RSI sobrecomprado")

    macd_val = _last_valid(macd_l)
    sig_val = _last_valid(macd_s)
    if not math.isnan(macd_val) and not math.isnan(sig_val):
        if macd_val > sig_val:
            signals.append("MACD bullish")
        else:
            signals.append("MACD bearish")

    e9 = _last_valid(ema9)
    e21 = _last_valid(ema21)
    if not math.isnan(e9) and not math.isnan(e21):
        if e9 > e21:
            signals.append("EMA9 acima EMA21 (alta)")
        else:
            signals.append("EMA9 abaixo EMA21 (baixa)")

    bb_u = _last_valid(bb_up)
    bb_l = _last_valid(bb_low)
    if not math.isnan(bb_u) and not math.isnan(bb_l):
        if price > bb_u:
            signals.append("Acima da Banda Superior")
        elif price < bb_l:
            signals.append("Abaixo da Banda Inferior")

    return {
        "rsi": round(rsi_val, 2) if not math.isnan(rsi_val) else None,
        "macd": round(macd_val, 4) if not math.isnan(macd_val) else None,
        "macd_signal": round(sig_val, 4) if not math.isnan(sig_val) else None,
        "macd_hist": round(_last_valid(macd_h), 4),
        "ema9": round(e9, 2) if not math.isnan(e9) else None,
        "ema21": round(e21, 2) if not math.isnan(e21) else None,
        "ema50": round(_last_valid(ema50), 2),
        "bb_upper": round(bb_u, 2) if not math.isnan(bb_u) else None,
        "bb_mid": round(_last_valid(bb_mid), 2),
        "bb_lower": round(bb_l, 2) if not math.isnan(bb_l) else None,
        "signals": signals,
        "bias": "ALTA"
        if signals.count("bullish") + signals.count("alta")
        > signals.count("bearish") + signals.count("baixa")
        else "BAIXA",
    }


# ── CryptoService ─────────────────────────────────────────────────────────────


class CryptoService:
    """Servico de criptoativos via CoinGecko + alternative.me."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=15,
            headers={"Accept": "application/json"},
        )

    async def get_prices(
        self,
        symbols: list[str],
        vs_currency: str = "brl",
    ) -> list[dict[str, Any]]:
        """Precos em tempo real com market cap e variacao 24h/7d."""
        cache_key = f"prices:{','.join(sorted(symbols))}:{vs_currency}"
        cached = _cache_get(cache_key, ttl=60)
        if cached:
            return cached

        ids = [symbol_to_id(s) for s in symbols]
        ids_str = ",".join(ids)

        try:
            r = await self._client.get(
                f"{COINGECKO_BASE}/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "ids": ids_str,
                    "order": "market_cap_desc",
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h,7d,30d",
                },
            )
            r.raise_for_status()
            data = r.json()

            result = []
            for coin in data:
                result.append(
                    {
                        "id": coin.get("id"),
                        "symbol": coin.get("symbol", "").upper(),
                        "name": coin.get("name"),
                        "image": coin.get("image"),
                        "price": coin.get("current_price", 0),
                        "market_cap": coin.get("market_cap", 0),
                        "volume_24h": coin.get("total_volume", 0),
                        "chg_1h": round(coin.get("price_change_percentage_1h_in_currency") or 0, 2),
                        "chg_24h": round(coin.get("price_change_percentage_24h") or 0, 2),
                        "chg_7d": round(coin.get("price_change_percentage_7d_in_currency") or 0, 2),
                        "chg_30d": round(
                            coin.get("price_change_percentage_30d_in_currency") or 0, 2
                        ),
                        "high_24h": coin.get("high_24h", 0),
                        "low_24h": coin.get("low_24h", 0),
                        "ath": coin.get("ath", 0),
                        "ath_change_pct": round(coin.get("ath_change_percentage") or 0, 2),
                        "rank": coin.get("market_cap_rank"),
                        "vs_currency": vs_currency,
                    }
                )

            _cache_set(cache_key, result)
            return result

        except Exception as exc:
            logger.error("crypto.prices.error", error=str(exc))
            return []

    async def get_historical(
        self,
        symbol: str,
        days: int = 90,
        vs_currency: str = "usd",
    ) -> dict[str, Any]:
        """OHLC historico para analise tecnica."""
        cache_key = f"hist:{symbol}:{days}:{vs_currency}"
        cached = _cache_get(cache_key, ttl=300)
        if cached:
            return cached

        coin_id = symbol_to_id(symbol)
        try:
            r = await self._client.get(
                f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
                params={"vs_currency": vs_currency, "days": days},
            )
            r.raise_for_status()
            ohlc = r.json()  # [[timestamp, open, high, low, close], ...]

            if not ohlc:
                return {}

            closes = [c[4] for c in ohlc]
            highs = [c[2] for c in ohlc]
            lows = [c[3] for c in ohlc]
            times = [c[0] for c in ohlc]

            technical = _calc_technical(closes)

            result = {
                "symbol": symbol.upper(),
                "coin_id": coin_id,
                "days": days,
                "vs_currency": vs_currency,
                "ohlc": ohlc[-60:],  # ultimos 60 candles para o grafico
                "closes": closes,
                "technical": technical,
                "last_price": closes[-1] if closes else 0,
                "last_updated": datetime.now(UTC).isoformat(),
            }
            _cache_set(cache_key, result)
            return result

        except Exception as exc:
            logger.error("crypto.historical.error", symbol=symbol, error=str(exc))
            return {}

    async def get_fear_greed(self) -> dict[str, Any]:
        """Fear & Greed Index dos ultimos 7 dias."""
        cached = _cache_get("fng", ttl=3600)
        if cached:
            return cached

        try:
            r = await self._client.get(FNG_URL)
            r.raise_for_status()
            data = r.json()
            items = data.get("data", [])

            result = {
                "current": {
                    "value": int(items[0]["value"]) if items else 50,
                    "classification": items[0]["value_classification"] if items else "Neutral",
                    "timestamp": items[0]["timestamp"] if items else "",
                },
                "history": [
                    {
                        "value": int(d["value"]),
                        "classification": d["value_classification"],
                        "date": datetime.fromtimestamp(int(d["timestamp"]), tz=UTC).strftime(
                            "%Y-%m-%d"
                        ),
                    }
                    for d in items
                ],
            }
            _cache_set("fng", result)
            return result

        except Exception as exc:
            logger.error("crypto.fng.error", error=str(exc))
            return {"current": {"value": 50, "classification": "Neutral"}, "history": []}

    async def get_global(self) -> dict[str, Any]:
        """Dados globais do mercado cripto."""
        cached = _cache_get("global", ttl=300)
        if cached:
            return cached

        try:
            r = await self._client.get(f"{COINGECKO_BASE}/global")
            r.raise_for_status()
            d = r.json().get("data", {})

            result = {
                "total_market_cap_usd": d.get("total_market_cap", {}).get("usd", 0),
                "total_market_cap_brl": d.get("total_market_cap", {}).get("brl", 0),
                "total_volume_24h_usd": d.get("total_volume", {}).get("usd", 0),
                "btc_dominance": round(d.get("market_cap_percentage", {}).get("btc", 0), 2),
                "eth_dominance": round(d.get("market_cap_percentage", {}).get("eth", 0), 2),
                "active_coins": d.get("active_cryptocurrencies", 0),
                "chg_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
            }
            _cache_set("global", result)
            return result

        except Exception as exc:
            logger.error("crypto.global.error", error=str(exc))
            return {}

    async def calc_portfolio(
        self,
        positions: list[dict],
        vs_currency: str = "brl",
    ) -> dict[str, Any]:
        """
        Calcula P&L da carteira de cripto.

        positions: [{"symbol": "BTC", "quantity": 0.5, "avg_price": 280000, "currency": "brl"}]
        """
        if not positions:
            return {
                "positions": [],
                "total_invested": 0,
                "total_current": 0,
                "pnl": 0,
                "pnl_pct": 0,
            }

        symbols = list({p["symbol"].upper() for p in positions})
        prices_data = await self.get_prices(symbols, vs_currency)
        price_map = {c["symbol"]: c["price"] for c in prices_data}

        result_positions = []
        total_invested = 0.0
        total_current = 0.0

        for pos in positions:
            sym = pos["symbol"].upper()
            qty = float(pos.get("quantity", 0))
            avg_price = float(pos.get("avg_price", 0))
            current_price = price_map.get(sym, 0)

            invested = qty * avg_price
            current = qty * current_price
            pnl = current - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0

            total_invested += invested
            total_current += current

            coin_data = next((c for c in prices_data if c["symbol"] == sym), {})

            result_positions.append(
                {
                    "symbol": sym,
                    "name": coin_data.get("name", sym),
                    "image": coin_data.get("image", ""),
                    "quantity": qty,
                    "avg_price": avg_price,
                    "current_price": current_price,
                    "invested": round(invested, 2),
                    "current_value": round(current, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "chg_24h": coin_data.get("chg_24h", 0),
                    "weight_pct": 0,  # calculado abaixo
                }
            )

        # Calcula peso
        for p in result_positions:
            p["weight_pct"] = (
                round(p["current_value"] / total_current * 100, 2) if total_current > 0 else 0
            )

        total_pnl = total_current - total_invested
        total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

        return {
            "positions": sorted(result_positions, key=lambda x: x["current_value"], reverse=True),
            "total_invested": round(total_invested, 2),
            "total_current": round(total_current, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl_pct, 2),
            "vs_currency": vs_currency,
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    async def parse_csv(self, content: str) -> list[dict]:
        """
        Parse de CSV de posicoes de cripto.

        Formatos suportados:
          symbol,quantity,avg_price           (preco em BRL)
          symbol,quantity,avg_price,currency  (USD ou BRL)
          coin,amount,buy_price               (Binance style)

        Exemplo:
          BTC,0.5,280000,brl
          ETH,2.0,9500,brl
          SOL,10,350,brl
        """
        positions = []
        lines = [l.strip() for l in content.strip().split("\n") if l.strip()]

        # Remove header se existir
        if lines and not lines[0][0].isdigit() and not lines[0][0].isalpha():
            lines = lines[1:]
        if lines and any(kw in lines[0].lower() for kw in ["symbol", "coin", "ticker", "moeda"]):
            lines = lines[1:]

        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                symbol = (
                    parts[0].upper().replace("-USDT", "").replace("-BRL", "").replace("-USD", "")
                )
                quantity = float(parts[1].replace(",", "."))
                avg_price = float(parts[2].replace(",", "."))
                currency = parts[3].lower() if len(parts) > 3 else "brl"
                positions.append(
                    {
                        "symbol": symbol,
                        "quantity": quantity,
                        "avg_price": avg_price,
                        "currency": currency,
                    }
                )
            except (ValueError, IndexError):
                continue

        return positions

    async def close(self) -> None:
        await self._client.aclose()

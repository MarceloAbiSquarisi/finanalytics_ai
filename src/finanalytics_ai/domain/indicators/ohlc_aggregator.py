"""
Agregacao de barras 1m para qualquer intervalo. Funcao pura, sem I/O.
Regras OHLC: open=primeiro, high=MAX, low=MIN, close=ultimo, volume=SUM
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any

INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "2m": 2,
    "3m": 3,
    "4m": 4,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "90m": 90,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
    "6h": 360,
    "1d": 1440,
    "2d": 2880,
    "3d": 4320,
    "5d": 7200,
}


def aggregate_bars(bars_1m: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    if not bars_1m:
        return []
    if interval == "1m":
        return sorted(bars_1m, key=lambda b: b["time"])
    minutes = INTERVAL_MINUTES.get(interval)
    if not minutes:
        raise ValueError(f"Intervalo desconhecido: {interval!r}")
    period_sec = minutes * 60
    buckets: dict[int, list] = defaultdict(list)
    for bar in bars_1m:
        ts = int(bar["time"])
        bucket = _day_bucket(ts) if minutes >= 1440 else _session_bucket(ts, period_sec)
        buckets[bucket].append(bar)
    result = []
    for bts in sorted(buckets):
        g = sorted(buckets[bts], key=lambda b: b["time"])
        result.append(
            {
                "time": bts,
                "open": g[0]["open"],
                "high": max(b["high"] for b in g),
                "low": min(b["low"] for b in g),
                "close": g[-1]["close"],
                "volume": sum(b.get("volume") or 0 for b in g),
            }
        )
    return result


def filter_by_range(bars: list[dict[str, Any]], range_period: str) -> list[dict[str, Any]]:
    import time as _t

    _M = {
        "1d": 86400,
        "5d": 432000,
        "1mo": 2592000,
        "3mo": 7776000,
        "6mo": 15552000,
        "1y": 31536000,
        "2y": 63072000,
    }
    d = _M.get(range_period)
    if not d:
        return bars
    c = int(_t.time()) - d
    return [b for b in bars if b["time"] >= c]


def _day_bucket(ts: int) -> int:
    d = datetime.datetime.utcfromtimestamp(ts)
    return int(datetime.datetime(d.year, d.month, d.day).timestamp())


def _session_bucket(ts: int, period_sec: int) -> int:
    dt = datetime.datetime.utcfromtimestamp(ts)
    so = dt.replace(hour=13, minute=0, second=0, microsecond=0)
    sots = int(so.timestamp())
    if ts < sots:
        sots -= 86400
    return sots + ((ts - sots) // period_sec) * period_sec

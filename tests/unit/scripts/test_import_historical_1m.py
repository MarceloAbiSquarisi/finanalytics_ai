"""Testes unitarios — import_historical_1m.normalize_row."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

from import_historical_1m import apply_column_map, normalize_row

BASE = {
    "ticker": "PETR4",
    "time": "2025-01-02 10:00:00",
    "open": 30.0,
    "high": 30.5,
    "low": 29.8,
    "close": 30.2,
    "volume": 1000,
    "trades": 45,
}


def test_ok_path():
    row, err = normalize_row(BASE, 0.01, "external_1m")
    assert err == ""
    assert row["ticker"] == "PETR4"
    assert row["open"] == 30.0 and row["close"] == 30.2
    assert row["source"] == "external_1m"
    assert isinstance(row["time"], datetime)


def test_rejects_ohlc_inconsistent_high_low():
    r = {**BASE, "high": 29.0}  # high < close
    row, err = normalize_row(r, 0.01, "external_1m")
    assert row is None and "inconsistente" in err


def test_rejects_price_below_min():
    r = {**BASE, "open": 0.001, "low": 0.001, "high": 0.005, "close": 0.003}
    row, err = normalize_row(r, 0.01, "external_1m")
    assert row is None and "abaixo min" in err


def test_rejects_missing_ohlc():
    r = {**BASE, "close": ""}
    row, err = normalize_row(r, 0.01, "external_1m")
    assert row is None and "OHLC invalido" in err


def test_rejects_negative_volume():
    r = {**BASE, "volume": -5}
    row, err = normalize_row(r, 0.01, "external_1m")
    assert row is None and "volume negativo" in err


def test_empty_ticker_rejected():
    r = {**BASE, "ticker": "  "}
    row, err = normalize_row(r, 0.01, "external_1m")
    assert row is None and "ticker" in err


def test_column_map_renames():
    raw = {
        "symbol": "VALE3",
        "dt": "2025-01-02 10:00",
        "o": 70.0,
        "h": 70.5,
        "l": 69.9,
        "c": 70.3,
        "vol": 500,
    }
    mapped = apply_column_map(
        raw,
        {
            "symbol": "ticker",
            "dt": "time",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "vol": "volume",
        },
    )
    row, err = normalize_row(mapped, 0.01, "external_1m")
    assert err == ""
    assert row["ticker"] == "VALE3" and row["close"] == 70.3


def test_time_formats():
    for t in ["2025-01-02 10:00:00", "2025-01-02T10:00:00", "02/01/2025 10:00"]:
        row, err = normalize_row({**BASE, "time": t}, 0.01, "external_1m")
        assert err == "", f"failed for {t}: {err}"


def test_timezone_default_utc():
    row, _ = normalize_row(BASE, 0.01, "external_1m")
    assert row["time"].tzinfo == UTC


def test_trades_defaults_zero():
    r = {k: v for k, v in BASE.items() if k != "trades"}
    row, _ = normalize_row(r, 0.01, "external_1m")
    assert row["trades"] == 0

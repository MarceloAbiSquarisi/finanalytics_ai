"""
DataStorageService — Parquet-based persistent storage on E: drive.

Layout (mounted as /data inside containers):
  /data/ohlcv/{TICKER}/{YEAR}.parquet      — daily bars, immutable per year
  /data/intraday/{DATE}/{TICKER}.parquet   — 1m/5m bars, rolling 90 days
  /data/macro/{SERIES}.parquet            — SELIC, IPCA, FX, IBOV, VIX
  /data/models/{TICKER}_{MODEL}.{ext}     — trained model artifacts

Design decisions:
  - Parquet via pyarrow: columnar, snappy-compressed, 5-10x smaller than CSV.
    A full B3 daily history (~500 tickers × 25 years) fits in ~4GB compressed.
  - Partitioning by year for OHLCV: allows fast range scans without reading
    the entire history. Append new year file; never rewrite old ones.
  - Partitioning by date for intraday: enables cheap cleanup of old data
    (just delete old date directories) without reading any content.
  - All reads return list[dict] to stay compatible with MarketDataProvider
    Protocol — no extra conversion layer needed in existing routes.
  - Thread-safe writes via filelock (multiple workers may write simultaneously).
  - DATA_DIR is configurable via env var DATA_DIR (default: /data).
    On Windows host: E:\finanalytics_data → mounted as /data in Docker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import os
from pathlib import Path
import threading
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Global lock per file path to avoid concurrent writes corrupting Parquet files
_write_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    with _locks_mutex:
        if path not in _write_locks:
            _write_locks[path] = threading.Lock()
        return _write_locks[path]


class DataStorageService:
    """
    Central service for reading and writing market data to persistent Parquet storage.

    All paths are relative to DATA_DIR (/data inside Docker, E:\\finanalytics_data on host).
    """

    def __init__(self, data_dir: str | None = None) -> None:
        self._root = Path(data_dir or os.environ.get("DATA_DIR", "/data"))
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for subdir in ["ohlcv", "intraday", "macro", "models"]:
            (self._root / subdir).mkdir(parents=True, exist_ok=True)

    # ── OHLCV Daily ─────────────────────────────────────────────────────────

    def ohlcv_path(self, ticker: str, year: int) -> Path:
        return self._root / "ohlcv" / ticker.upper() / f"{year}.parquet"

    def write_ohlcv(self, ticker: str, bars: list[dict[str, Any]]) -> int:
        """
        Persist daily OHLCV bars to Parquet, partitioned by ticker/year.
        Merges with existing data (deduplicates by date).
        Returns number of new rows written.
        """
        if not bars:
            return 0

        df = _bars_to_df(bars)
        df["date"] = pd.to_datetime(df["date"])  # garante Timestamp para .dt
        df["year"] = df["date"].dt.year
        written = 0

        for year, group in df.groupby("year"):
            path = self.ohlcv_path(ticker, int(year))
            path.parent.mkdir(parents=True, exist_ok=True)

            with _get_lock(str(path)):
                if path.exists():
                    existing = pd.read_parquet(path)
                    merged = pd.concat([existing, group.drop("year", axis=1)], ignore_index=True)
                    merged = (
                        merged.drop_duplicates("date").sort_values("date").reset_index(drop=True)
                    )
                    new_rows = len(merged) - len(existing)
                else:
                    merged = group.drop("year", axis=1).sort_values("date").reset_index(drop=True)
                    new_rows = len(merged)

                merged.to_parquet(path, index=False, compression="snappy")
                written += new_rows

        logger.info("storage.ohlcv.written", ticker=ticker, rows=written)
        return written

    def read_ohlcv(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict[str, Any]]:
        """Read daily bars from Parquet. Returns list[dict] compatible with MarketDataProvider."""
        ticker_dir = self._root / "ohlcv" / ticker.upper()
        if not ticker_dir.exists():
            return []

        files = sorted(ticker_dir.glob("*.parquet"))
        if not files:
            return []

        # Filter by year range to avoid reading unnecessary files
        if start:
            files = [f for f in files if int(f.stem) >= start.year]
        if end:
            files = [f for f in files if int(f.stem) <= end.year]

        if not files:
            return []

        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.sort_values("date").reset_index(drop=True)

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]

        return _df_to_bars(df)

    def ohlcv_available_tickers(self) -> list[str]:
        """Return list of tickers that have local OHLCV data."""
        ohlcv_dir = self._root / "ohlcv"
        if not ohlcv_dir.exists():
            return []
        return sorted(d.name for d in ohlcv_dir.iterdir() if d.is_dir())

    def ohlcv_date_range(self, ticker: str) -> tuple[date | None, date | None]:
        """Return (oldest_date, newest_date) for a ticker."""
        bars = self.read_ohlcv(ticker)
        if not bars:
            return None, None
        dates = [b.get("date") for b in bars if b.get("date")]
        if not dates:
            return None, None
        return min(dates), max(dates)

    # ── Intraday ────────────────────────────────────────────────────────────

    def intraday_path(self, ticker: str, day: date) -> Path:
        return self._root / "intraday" / day.isoformat() / f"{ticker.upper()}.parquet"

    def write_intraday(self, ticker: str, bars: list[dict[str, Any]], interval: str = "1m") -> int:
        """Persist intraday bars, partitioned by date/ticker."""
        if not bars:
            return 0

        df = _bars_to_df(bars, intraday=True)
        df["day"] = df["ts"].dt.date
        written = 0

        for day, group in df.groupby("day"):
            path = self.intraday_path(ticker, day)  # type: ignore[arg-type]
            path.parent.mkdir(parents=True, exist_ok=True)

            with _get_lock(str(path)):
                g = group.drop("day", axis=1)
                if path.exists():
                    existing = pd.read_parquet(path)
                    merged = pd.concat([existing, g], ignore_index=True)
                    merged = merged.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
                    new_rows = len(merged) - len(existing)
                else:
                    merged = g.sort_values("ts").reset_index(drop=True)
                    new_rows = len(merged)

                merged.to_parquet(path, index=False, compression="snappy")
                written += new_rows

        return written

    def read_intraday(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict[str, Any]]:
        """Read intraday bars for a ticker across date range."""
        intraday_dir = self._root / "intraday"
        if not intraday_dir.exists():
            return []

        start = start or (datetime.now(tz=UTC).date() - timedelta(days=90))
        end = end or datetime.now(tz=UTC).date()

        files = []
        current = start
        while current <= end:
            p = self.intraday_path(ticker, current)
            if p.exists():
                files.append(p)
            current += timedelta(days=1)

        if not files:
            return []

        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.sort_values("ts").reset_index(drop=True)
        return _df_to_bars(df, intraday=True)

    def cleanup_old_intraday(self, keep_days: int = 90) -> int:
        """Delete intraday partitions older than keep_days. Returns deleted count."""
        cutoff = datetime.now(tz=UTC).date() - timedelta(days=keep_days)
        intraday_dir = self._root / "intraday"
        if not intraday_dir.exists():
            return 0

        deleted = 0
        for day_dir in intraday_dir.iterdir():
            try:
                day = date.fromisoformat(day_dir.name)
                if day < cutoff:
                    import shutil

                    shutil.rmtree(day_dir)
                    deleted += 1
            except (ValueError, OSError):
                pass

        logger.info("storage.intraday.cleanup", deleted_dirs=deleted)
        return deleted

    # ── Macro ────────────────────────────────────────────────────────────────

    def macro_path(self, series: str) -> Path:
        return self._root / "macro" / f"{series.lower()}.parquet"

    def write_macro(self, series: str, df: pd.DataFrame) -> None:
        """Write macro timeseries (SELIC, IPCA, cambio, etc.)."""
        path = self.macro_path(series)
        path.parent.mkdir(parents=True, exist_ok=True)

        with _get_lock(str(path)):
            if path.exists():
                existing = pd.read_parquet(path)
                merged = pd.concat([existing, df], ignore_index=True)
                merged = merged.drop_duplicates("date").sort_values("date").reset_index(drop=True)
            else:
                merged = df.sort_values("date").reset_index(drop=True)
            merged.to_parquet(path, index=False, compression="snappy")

        logger.info("storage.macro.written", series=series, rows=len(df))

    def read_macro(self, series: str) -> pd.DataFrame:
        """Read macro timeseries. Returns empty DataFrame if not available."""
        path = self.macro_path(series)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path).sort_values("date").reset_index(drop=True)

    # ── Models ───────────────────────────────────────────────────────────────

    def model_path(self, ticker: str, model_name: str, ext: str = "pt") -> Path:
        return self._root / "models" / f"{ticker.upper()}_{model_name}.{ext}"

    def model_exists(self, ticker: str, model_name: str, ext: str = "pt") -> bool:
        return self.model_path(ticker, model_name, ext).exists()

    def model_age_hours(self, ticker: str, model_name: str, ext: str = "pt") -> float | None:
        """Return age in hours of a saved model, or None if not found."""
        path = self.model_path(ticker, model_name, ext)
        if not path.exists():
            return None
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return (datetime.now(tz=UTC) - mtime).total_seconds() / 3600

    # ── Storage stats ────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return storage usage statistics."""

        def _dir_size_gb(p: Path) -> float:
            if not p.exists():
                return 0.0
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return round(total / 1e9, 3)

        tickers = self.ohlcv_available_tickers()
        return {
            "root": str(self._root),
            "ohlcv_tickers": len(tickers),
            "ohlcv_size_gb": _dir_size_gb(self._root / "ohlcv"),
            "intraday_size_gb": _dir_size_gb(self._root / "intraday"),
            "macro_size_gb": _dir_size_gb(self._root / "macro"),
            "models_size_gb": _dir_size_gb(self._root / "models"),
            "total_size_gb": _dir_size_gb(self._root),
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _bars_to_df(bars: list[dict[str, Any]], intraday: bool = False) -> pd.DataFrame:
    rows = []
    for b in bars:
        raw = b.get("time") or b.get("date") or b.get("ds")
        if raw is None:
            continue
        try:
            if isinstance(raw, (int, float)):
                ts = datetime.fromtimestamp(raw, tz=UTC)
            else:
                ts = pd.to_datetime(str(raw), utc=True)
        except Exception:
            continue

        rows.append(
            {
                "ts" if intraday else "date": ts if intraday else ts.date(),
                "open": float(b.get("open", 0) or 0),
                "high": float(b.get("high", 0) or 0),
                "low": float(b.get("low", 0) or 0),
                "close": float(b.get("close", 0) or 0),
                "volume": int(b.get("volume", 0) or 0),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df[df["close"] > 0]
    return df


def _df_to_bars(df: pd.DataFrame, intraday: bool = False) -> list[dict[str, Any]]:
    time_col = "ts" if intraday else "date"
    bars = []
    for _, row in df.iterrows():
        ts = row[time_col]
        if hasattr(ts, "timestamp"):
            unix = int(ts.timestamp())
        else:
            unix = int(datetime.combine(ts, datetime.min.time(), tzinfo=UTC).timestamp())
        bars.append(
            {
                "time": unix,
                "date": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": int(row.get("volume", 0)),
            }
        )
    return bars


# Singleton factory
_instance: DataStorageService | None = None


def get_storage(data_dir: str | None = None) -> DataStorageService:
    global _instance
    if _instance is None:
        _instance = DataStorageService(data_dir)
    return _instance

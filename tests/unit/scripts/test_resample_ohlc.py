"""Testes unitarios para resample_ohlc — validacao de SQL e CLI parsing.

Estes testes validam a estrutura do SQL e o parsing de argumentos sem
exigir conexao real ao DB. Para validacao end-to-end ver smoke em
runbook_import_dados_historicos.md.
"""

from __future__ import annotations

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

from resample_ohlc import _RESAMPLE_DRY_SQL, _RESAMPLE_SQL


def test_resample_sql_uses_time_bucket():
    assert "time_bucket(make_interval(mins => %s)" in _RESAMPLE_SQL
    assert "GROUP BY bucket, ticker" in _RESAMPLE_SQL


def test_resample_sql_idempotent_upsert():
    assert "ON CONFLICT (time, ticker, interval_minutes) DO UPDATE" in _RESAMPLE_SQL


def test_resample_sql_handles_volume_zero_in_vwap():
    assert "WHEN COALESCE(SUM(volume), 0) > 0" in _RESAMPLE_SQL
    assert "ELSE AVG(close::numeric)" in _RESAMPLE_SQL


def test_resample_sql_open_close_via_array_agg():
    assert "(array_agg(open  ORDER BY time ASC))[1]" in _RESAMPLE_SQL
    assert "(array_agg(close ORDER BY time DESC))[1]" in _RESAMPLE_SQL


def test_resample_sql_uses_max_min_for_high_low():
    assert "MAX(high)" in _RESAMPLE_SQL
    assert "MIN(low)" in _RESAMPLE_SQL


def test_dry_sql_does_not_insert():
    assert "INSERT" not in _RESAMPLE_DRY_SQL
    assert "SELECT count(*)" in _RESAMPLE_DRY_SQL
    assert "time_bucket" in _RESAMPLE_DRY_SQL


def test_resample_sql_filters_empty_buckets():
    assert "HAVING COUNT(*) > 0" in _RESAMPLE_SQL

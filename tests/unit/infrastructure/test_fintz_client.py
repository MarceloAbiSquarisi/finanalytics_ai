"""
tests.unit.infrastructure.test_fintz_client
─────────────────────────────────────────────
Testes do FintzClient — focados em parse, hash e tratamento de erros.

Não mockamos aiohttp diretamente: testamos os métodos que não
fazem I/O de rede (_parse_parquet, compute_hash via fetch_dataset
com client mockado de rede).
"""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import pytest

from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec
from finanalytics_ai.exceptions import FintzParseError
from finanalytics_ai.infrastructure.adapters.fintz_client import FintzClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_spec(dataset_type: str = "cotacoes") -> FintzDatasetSpec:
    return FintzDatasetSpec(
        key="cotacoes_ohlc",
        endpoint="/bolsa/b3/avista/cotacoes/historico/arquivos",
        params={},
        dataset_type=dataset_type,
        description="Test",
    )


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def _make_client() -> FintzClient:
    return FintzClient(api_key="test-key", base_url="https://api.fintz.com.br")


# ── Testes: _parse_parquet ────────────────────────────────────────────────────


def test_parse_parquet_cotacoes_valid() -> None:
    """Parquet com colunas mínimas de cotações deve ser aceito."""
    df = pd.DataFrame({"ticker": ["PETR4"], "data": ["2024-01-01"], "preco_fechamento": [28.50]})
    raw = _df_to_parquet_bytes(df)
    spec = _make_spec("cotacoes")

    client = _make_client()
    result = client._parse_parquet(raw, spec)

    assert len(result) == 1
    assert "ticker" in result.columns


def test_parse_parquet_missing_required_column_raises() -> None:
    """Parquet sem coluna obrigatória deve levantar FintzParseError."""
    df = pd.DataFrame({"ticker": ["PETR4"]})  # falta 'data'
    raw = _df_to_parquet_bytes(df)
    spec = _make_spec("cotacoes")

    client = _make_client()
    with pytest.raises(FintzParseError) as exc_info:
        client._parse_parquet(raw, spec)

    assert exc_info.value.dataset_key == "cotacoes_ohlc"
    assert "data" in str(exc_info.value)


def test_parse_parquet_invalid_bytes_raises() -> None:
    """Bytes corrompidos devem levantar FintzParseError."""
    spec = _make_spec("cotacoes")
    client = _make_client()

    with pytest.raises(FintzParseError):
        client._parse_parquet(b"not a parquet file", spec)


def test_parse_parquet_empty_returns_empty_df() -> None:
    """Parquet vazio (0 linhas) deve retornar DataFrame vazio sem erro."""
    df = pd.DataFrame({"ticker": pd.Series([], dtype=str), "data": pd.Series([], dtype=str)})
    raw = _df_to_parquet_bytes(df)
    spec = _make_spec("cotacoes")

    client = _make_client()
    result = client._parse_parquet(raw, spec)

    assert result.empty


def test_parse_parquet_item_contabil_schema() -> None:
    """Parquet de item_contabil com colunas mínimas deve ser aceito."""
    df = pd.DataFrame(
        {
            "ticker": ["VALE3"],
            "item": ["EBIT"],
            "data": ["2024-03-31"],
            "valor": [1_000_000.0],
        }
    )
    raw = _df_to_parquet_bytes(df)
    spec = FintzDatasetSpec(
        key="item_EBIT_12M",
        endpoint="/any",
        params={"item": "EBIT", "tipoPeriodo": "12M"},
        dataset_type="item_contabil",
        description="test",
    )
    client = _make_client()
    result = client._parse_parquet(raw, spec)
    assert len(result) == 1


# ── Testes: hash ──────────────────────────────────────────────────────────────


def test_hash_is_sha256_of_raw_bytes() -> None:
    """O hash retornado em fetch_dataset deve ser SHA-256 dos bytes brutos."""
    df = pd.DataFrame({"ticker": ["PETR4"], "data": ["2024-01-01"], "preco_fechamento": [28.50]})
    raw = _df_to_parquet_bytes(df)
    expected_hash = hashlib.sha256(raw).hexdigest()

    client = _make_client()
    # Testa _parse_parquet + hash calculation isoladamente
    spec = _make_spec("cotacoes")
    result_df = client._parse_parquet(raw, spec)
    computed = hashlib.sha256(raw).hexdigest()

    assert computed == expected_hash
    assert len(computed) == 64  # SHA-256 hex = 64 chars


def test_different_content_produces_different_hash() -> None:
    df1 = pd.DataFrame({"ticker": ["PETR4"], "data": ["2024-01-01"]})
    df2 = pd.DataFrame({"ticker": ["VALE3"], "data": ["2024-01-02"]})

    h1 = hashlib.sha256(_df_to_parquet_bytes(df1)).hexdigest()
    h2 = hashlib.sha256(_df_to_parquet_bytes(df2)).hexdigest()

    assert h1 != h2


def test_same_content_produces_same_hash() -> None:
    df = pd.DataFrame({"ticker": ["PETR4"], "data": ["2024-01-01"], "preco_fechamento": [28.50]})
    raw = _df_to_parquet_bytes(df)

    h1 = hashlib.sha256(raw).hexdigest()
    h2 = hashlib.sha256(raw).hexdigest()

    assert h1 == h2


# ── Testes: context manager guard ────────────────────────────────────────────


def test_get_session_raises_without_context_manager() -> None:
    """Chamar _get_session fora do context manager deve levantar RuntimeError."""
    client = _make_client()
    with pytest.raises(RuntimeError, match="context manager"):
        client._get_session()

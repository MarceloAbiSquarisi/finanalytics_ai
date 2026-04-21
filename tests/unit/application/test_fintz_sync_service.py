"""
tests.unit.application.test_fintz_sync_service
────────────────────────────────────────────────
Testes unitários do FintzSyncService.

Cobre:
  - Sync bem-sucedido (hash novo → upsert → record)
  - Idempotência (hash igual → skip, sem upsert)
  - Isolamento de falhas (erro em 1 dataset não aborta os outros)
  - Despacho correto por dataset_type
  - Construção do sumário final
"""

from __future__ import annotations

import hashlib
import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
from finanalytics_ai.domain.fintz.entities import FintzDatasetSpec
from finanalytics_ai.exceptions import FintzAPIError

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_spec(
    key: str = "cotacoes_ohlc",
    dataset_type: str = "cotacoes",
    params: dict[str, str] | None = None,
) -> FintzDatasetSpec:
    return FintzDatasetSpec(
        key=key,
        endpoint="/bolsa/b3/avista/cotacoes/historico/arquivos",
        params=params or {},
        dataset_type=dataset_type,
        description="Test dataset",
    )


def _fake_df(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["PETR4"] * n,
            "data": ["2024-01-01"] * n,
            "valor": [1.0] * n,
        }
    )


def _sha(df: pd.DataFrame) -> str:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return hashlib.sha256(buf.getvalue()).hexdigest()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.fetch_dataset = AsyncMock()
    return client


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_last_hash = AsyncMock(return_value=None)
    repo.upsert_cotacoes = AsyncMock(return_value=10)
    repo.upsert_itens_contabeis = AsyncMock(return_value=20)
    repo.upsert_indicadores = AsyncMock(return_value=30)
    repo.record_sync = AsyncMock()
    return repo


# ── Testes: sync bem-sucedido ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_cotacoes_ok(mock_client: MagicMock, mock_repo: MagicMock) -> None:
    """Sync de cotações com hash novo deve chamar upsert_cotacoes e record_sync."""
    spec = _make_spec("cotacoes_ohlc", "cotacoes")
    df = _fake_df()
    sha = "abc123"

    mock_client.fetch_dataset.return_value = (df, sha)
    mock_repo.get_last_hash.return_value = None  # nunca sincronizado

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("cotacoes_ohlc")

    assert result["status"] == "ok"
    assert result["rows"] == 10
    mock_repo.upsert_cotacoes.assert_awaited_once_with(df)
    mock_repo.record_sync.assert_awaited_once()
    call_kwargs = mock_repo.record_sync.call_args.kwargs
    assert call_kwargs["status"] == "ok"
    assert call_kwargs["file_hash"] == sha
    assert call_kwargs["rows_upserted"] == 10


@pytest.mark.asyncio
async def test_sync_item_contabil_dispatches_correctly(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """Datasets de tipo item_contabil devem chamar upsert_itens_contabeis."""
    spec = _make_spec("item_EBIT_12M", "item_contabil", {"item": "EBIT", "tipoPeriodo": "12M"})
    mock_client.fetch_dataset.return_value = (_fake_df(), "sha_new")
    mock_repo.get_last_hash.return_value = None

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("item_EBIT_12M")

    assert result["status"] == "ok"
    mock_repo.upsert_itens_contabeis.assert_awaited_once()
    mock_repo.upsert_cotacoes.assert_not_awaited()
    mock_repo.upsert_indicadores.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_indicador_dispatches_correctly(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """Datasets de tipo indicador devem chamar upsert_indicadores."""
    spec = _make_spec("indicador_ROE", "indicador", {"indicador": "ROE"})
    mock_client.fetch_dataset.return_value = (_fake_df(), "sha_roe")
    mock_repo.get_last_hash.return_value = None

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("indicador_ROE")

    assert result["status"] == "ok"
    mock_repo.upsert_indicadores.assert_awaited_once()


# ── Testes: idempotência ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_skip_when_hash_unchanged(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """Hash idêntico ao anterior → status skip, sem upsert, sem record_sync."""
    spec = _make_spec()
    same_hash = "identical_sha256"

    mock_client.fetch_dataset.return_value = (_fake_df(), same_hash)
    mock_repo.get_last_hash.return_value = same_hash  # já processado

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("cotacoes_ohlc")

    assert result["status"] == "skip"
    assert result["rows"] == 0
    mock_repo.upsert_cotacoes.assert_not_awaited()
    mock_repo.record_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_ok_when_hash_changed(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """Hash diferente do anterior → deve fazer upsert normalmente."""
    spec = _make_spec()
    mock_client.fetch_dataset.return_value = (_fake_df(), "new_hash")
    mock_repo.get_last_hash.return_value = "old_hash"

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("cotacoes_ohlc")

    assert result["status"] == "ok"
    mock_repo.upsert_cotacoes.assert_awaited_once()


# ── Testes: isolamento de falhas ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_error_is_isolated(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """FintzAPIError em um dataset não aborta os demais."""
    spec_ok = _make_spec("cotacoes_ohlc", "cotacoes")
    spec_err = _make_spec("indicador_ROE", "indicador")

    async def _fetch_side_effect(spec: FintzDatasetSpec) -> Any:
        if spec.key == "indicador_ROE":
            raise FintzAPIError(
                message="API timeout",
                dataset_key="indicador_ROE",
                status_code=503,
            )
        return (_fake_df(), "good_hash")

    mock_client.fetch_dataset.side_effect = _fetch_side_effect
    mock_repo.get_last_hash.return_value = None

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec_ok, spec_err])
    summary = await svc.sync_all()

    assert summary["ok"] == 1
    assert summary["error"] == 1
    assert "indicador_ROE" in summary["failed_keys"]
    # cotacoes_ohlc deve ter sido processado normalmente
    mock_repo.upsert_cotacoes.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_error_is_isolated(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """Exceção genérica inesperada deve ser capturada e não propagar."""
    spec = _make_spec()
    mock_client.fetch_dataset.side_effect = RuntimeError("disk full")
    mock_repo.get_last_hash.return_value = None

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_dataset("cotacoes_ohlc")

    assert result["status"] == "error"
    assert "disk full" in result["error"]
    # record_sync deve ser chamado com status=error
    mock_repo.record_sync.assert_awaited_once()
    assert mock_repo.record_sync.call_args.kwargs["status"] == "error"


# ── Testes: sumário ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_all_summary_counts(
    mock_client: MagicMock,
    mock_repo: MagicMock,
) -> None:
    """sync_all deve retornar contagens corretas de ok/skip/error."""
    specs = [
        _make_spec("ds_ok1", "cotacoes"),
        _make_spec("ds_ok2", "cotacoes"),
        _make_spec("ds_skip", "cotacoes"),
        _make_spec("ds_err", "cotacoes"),
    ]

    async def _fetch(spec: FintzDatasetSpec) -> Any:
        if spec.key == "ds_err":
            raise FintzAPIError(message="err", dataset_key=spec.key, status_code=500)
        return (_fake_df(), spec.key + "_hash")

    async def _last_hash(key: str) -> str | None:
        return key + "_hash" if key == "ds_skip" else None

    mock_client.fetch_dataset.side_effect = _fetch
    mock_repo.get_last_hash.side_effect = _last_hash

    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=specs)
    summary = await svc.sync_all()

    assert summary["ok"] == 2
    assert summary["skip"] == 1
    assert summary["error"] == 1
    assert summary["total"] == 4
    assert "ds_err" in summary["failed_keys"]

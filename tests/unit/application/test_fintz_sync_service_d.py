"""
Testes Sprint D — FintzSyncService com EventPublisher + TimescaleWriter.

Cobre:
  - Ciclo completo: sync → timescale → evento publicado
  - FireAndForget: falha no TimescaleWriter não aborta sync
  - FireAndForget: falha no EventPublisher não aborta sync
  - Skip (hash idêntico): não grava TimescaleDB, não publica evento
  - Erro de API: publica FINTZ_SYNC_FAILED
  - Sem deps: comportamento idêntico ao original
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_spec(key: str = "cotacoes", dataset_type: str = "cotacoes") -> Any:
    spec = MagicMock()
    spec.key          = key
    spec.dataset_type = dataset_type
    return spec


def make_df() -> Any:
    df = MagicMock()
    df.empty = False
    return df


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.fetch_dataset.return_value = (make_df(), "sha256_novo")
    return client


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_last_hash.return_value = "sha256_antigo"
    repo.upsert_cotacoes.return_value = 100
    repo.upsert_itens_contabeis.return_value = 200
    repo.upsert_indicadores.return_value = 150
    repo.record_sync.return_value = None
    return repo


@pytest.fixture
def mock_publisher() -> AsyncMock:
    pub = AsyncMock()
    pub.publish_fintz_sync_completed.return_value = None
    pub.publish_fintz_sync_failed.return_value = None
    return pub


@pytest.fixture
def mock_ts_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.write.return_value = 100
    return writer


# ── Testes ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_sucesso_completo(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Ciclo completo: upsert → timescale → evento publicado."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService

    spec = make_spec()
    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    assert result["ok"] == 1
    assert result["error"] == 0
    mock_ts_writer.write.assert_called_once()
    mock_publisher.publish_fintz_sync_completed.assert_called_once()
    args = mock_publisher.publish_fintz_sync_completed.call_args.kwargs
    assert args["dataset"] == "cotacoes"
    assert args["rows_synced"] == 100


@pytest.mark.asyncio
async def test_sync_sem_deps_opcionais(mock_client, mock_repo):
    """Sem EventPublisher nem TimescaleWriter — comportamento idêntico ao original."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService

    spec = make_spec()
    svc = FintzSyncService(client=mock_client, repo=mock_repo, datasets=[spec])
    result = await svc.sync_all()

    assert result["ok"] == 1


@pytest.mark.asyncio
async def test_timescale_falha_nao_aborta_sync(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Falha no TimescaleWriter não aborta o sync."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService

    mock_ts_writer.write.side_effect = Exception("timescale down")
    spec = make_spec()
    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    # Sync completou com sucesso apesar da falha no TimescaleDB
    assert result["ok"] == 1
    assert result["error"] == 0
    # Evento ainda foi publicado
    mock_publisher.publish_fintz_sync_completed.assert_called_once()


@pytest.mark.asyncio
async def test_publisher_falha_nao_aborta_sync(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Falha no EventPublisher não aborta o sync."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService

    mock_publisher.publish_fintz_sync_completed.side_effect = Exception("kafka down")
    spec = make_spec()
    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    assert result["ok"] == 1
    assert result["error"] == 0
    # TimescaleDB ainda gravou
    mock_ts_writer.write.assert_called_once()


@pytest.mark.asyncio
async def test_skip_hash_identico_nao_grava_timescale(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Se hash idêntico, não grava no TimescaleDB nem publica evento."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService

    mock_repo.get_last_hash.return_value = "sha256_novo"  # mesmo hash
    spec = make_spec()
    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    assert result["skip"] == 1
    mock_ts_writer.write.assert_not_called()
    mock_publisher.publish_fintz_sync_completed.assert_not_called()
    mock_publisher.publish_fintz_sync_failed.assert_not_called()


@pytest.mark.asyncio
async def test_erro_api_publica_failed(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Erro de API publica FINTZ_SYNC_FAILED."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
    from finanalytics_ai.exceptions import FintzAPIError

    error = FintzAPIError("API timeout")
    error.code = "TIMEOUT"
    mock_client.fetch_dataset.side_effect = error

    spec = make_spec()
    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    assert result["error"] == 1
    mock_publisher.publish_fintz_sync_failed.assert_called_once()
    args = mock_publisher.publish_fintz_sync_failed.call_args.kwargs
    assert args["dataset"] == "cotacoes"
    assert "TIMEOUT" in args["error_type"] or "FintzAPIError" in args["error_type"]
    mock_ts_writer.write.assert_not_called()


@pytest.mark.asyncio
async def test_multiplos_datasets_isolados(
    mock_client, mock_repo, mock_publisher, mock_ts_writer
):
    """Erro em um dataset não afeta os outros."""
    from finanalytics_ai.application.services.fintz_sync_service import FintzSyncService
    from finanalytics_ai.exceptions import FintzAPIError

    spec_ok  = make_spec("cotacoes", "cotacoes")
    spec_err = make_spec("indicador_x", "indicador")

    error = FintzAPIError("Falha")
    error.code = "ERR"

    async def fetch_side_effect(spec):
        if spec.key == "indicador_x":
            raise error
        return make_df(), "sha256_novo"

    mock_client.fetch_dataset.side_effect = fetch_side_effect

    svc = FintzSyncService(
        client=mock_client,
        repo=mock_repo,
        datasets=[spec_ok, spec_err],
        event_publisher=mock_publisher,
        timescale_writer=mock_ts_writer,
    )

    result = await svc.sync_all()

    assert result["ok"] == 1
    assert result["error"] == 1
    mock_publisher.publish_fintz_sync_completed.assert_called_once()
    mock_publisher.publish_fintz_sync_failed.assert_called_once()

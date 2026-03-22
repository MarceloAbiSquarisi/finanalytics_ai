"""
Testes do PostSyncOrchestrator e handlers.

Cobre:
  - Cada handler individualmente (anomaly, integrity, cache, model flags)
  - Orquestrador executa todos em paralelo
  - Falha em um handler não afeta os demais
  - Resultado aggregado correto no metadata
  - Dataset desconhecido não quebra
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(dataset: str = "indicadores", rows: int = 1000) -> MagicMock:
    event = MagicMock()
    event.payload = {"dataset": dataset, "rows_synced": rows}
    return event


def make_ts_repo(
    indicadores_serie=None,
    indicadores_latest=None,
    itens_latest=None,
    cotacoes=None,
    tickers=None,
    coverage=None,
) -> AsyncMock:
    repo = AsyncMock()
    repo.get_indicadores_serie = AsyncMock(return_value=indicadores_serie or [])
    repo.get_indicadores_latest = AsyncMock(return_value=indicadores_latest or {})
    repo.get_itens_latest = AsyncMock(return_value=itens_latest or {})
    repo.get_cotacoes = AsyncMock(return_value=cotacoes or [])
    repo.list_tickers = AsyncMock(return_value=tickers or ["PETR4", "VALE3"])
    repo.get_coverage = AsyncMock(return_value=coverage or {})
    return repo


def make_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.set = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    return cache


def make_serie(n: int = 25, latest: float = 8.5) -> list[dict]:
    """Série com n pontos históricos + 1 ponto recente."""
    history = [{"data": f"2024-{i:02d}-01", "valor": 8.0} for i in range(1, n + 1)]
    return [{"data": "2025-01-02", "valor": latest}] + history


# ── AnomalyDetector ───────────────────────────────────────────────────────────

class TestFintzAnomalyDetector:
    @pytest.mark.asyncio
    async def test_nao_roda_para_cotacoes(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzAnomalyDetector
        repo = make_ts_repo()
        detector = FintzAnomalyDetector(repo)
        result = await detector.detect("cotacoes", ["PETR4"])
        assert result == 0
        repo.get_indicadores_serie.assert_not_called()

    @pytest.mark.asyncio
    async def test_sem_historico_suficiente_sem_anomalia(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzAnomalyDetector
        repo = make_ts_repo(indicadores_serie=[{"data": "2025-01-01", "valor": 8.0}])
        detector = FintzAnomalyDetector(repo)
        result = await detector.detect("indicador", ["PETR4"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_valor_normal_sem_anomalia(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzAnomalyDetector
        # Latest = 8.5, history = todos 8.0 → z_score < 3σ
        repo = make_ts_repo(indicadores_serie=make_serie(25, latest=8.5))
        detector = FintzAnomalyDetector(repo)
        result = await detector.detect("indicador", ["PETR4"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_valor_extremo_detecta_anomalia(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzAnomalyDetector
        # Latest = 500, history = todos 8.0 → z_score >> 3σ
        serie = make_serie(25, latest=500.0)
        from unittest.mock import AsyncMock as _AsyncMock
        repo = make_ts_repo()
        repo.get_indicadores_serie = _AsyncMock(return_value=serie)
        detector = FintzAnomalyDetector(repo)
        result = await detector.detect("indicador", ["PETR4"])
        assert result > 0

    @pytest.mark.asyncio
    async def test_falha_no_ticker_nao_propaga(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzAnomalyDetector
        repo = make_ts_repo()
        repo.get_indicadores_serie = AsyncMock(side_effect=Exception("db error"))
        detector = FintzAnomalyDetector(repo)
        result = await detector.detect("indicador", ["PETR4"])
        assert result == 0  # não levanta exceção


# ── IntegrityValidator ────────────────────────────────────────────────────────

class TestFintzIntegrityValidator:
    @pytest.mark.asyncio
    async def test_cotacoes_ok_sem_issues(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzIntegrityValidator
        cotacoes = [{"volume": 1000000}, {"volume": 900000}]
        repo = make_ts_repo(cotacoes=cotacoes, tickers=["PETR4"])
        validator = FintzIntegrityValidator(repo)
        ok, issues = await validator.validate("cotacoes")
        assert ok is True
        assert issues == []

    @pytest.mark.asyncio
    async def test_cotacoes_volume_zero_detecta_issue(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzIntegrityValidator
        cotacoes = [{"volume": 0}, {"volume": 0}]
        # 1 ticker de 1 = 100% com volume zero > 50%
        repo = make_ts_repo(cotacoes=cotacoes, tickers=["PETR4"])
        validator = FintzIntegrityValidator(repo)
        ok, issues = await validator.validate("cotacoes")
        assert ok is False
        assert len(issues) > 0

    @pytest.mark.asyncio
    async def test_indicadores_valores_normais(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzIntegrityValidator
        snapshot = {"P/L": {"valor": 8.5, "data_ref": "2025-01-02"}}
        repo = make_ts_repo(indicadores_latest=snapshot, tickers=["PETR4"])
        validator = FintzIntegrityValidator(repo)
        ok, issues = await validator.validate("indicador")
        assert ok is True

    @pytest.mark.asyncio
    async def test_falha_nao_propaga(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzIntegrityValidator
        repo = make_ts_repo()
        repo.list_tickers = AsyncMock(side_effect=Exception("db error"))
        validator = FintzIntegrityValidator(repo)
        ok, issues = await validator.validate("cotacoes")
        assert isinstance(ok, bool)


# ── CacheWarmer ───────────────────────────────────────────────────────────────

class TestFintzCacheWarmer:
    @pytest.mark.asyncio
    async def test_warm_atualiza_lista_tickers(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzCacheWarmer
        repo = make_ts_repo(tickers=["PETR4", "VALE3"])
        cache = make_cache()
        warmer = FintzCacheWarmer(repo, cache)
        keys = await warmer.warm("cotacoes")
        assert keys >= 1
        cache.set.assert_called()
        # Verifica que a chave de tickers foi setada
        call_keys = [c[0][0] for c in cache.set.call_args_list]
        assert any("tickers" in k for k in call_keys)

    @pytest.mark.asyncio
    async def test_warm_indicadores_aquece_snapshot(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzCacheWarmer
        repo = make_ts_repo(
            tickers=["PETR4"],
            indicadores_latest={"P/L": {"valor": 8.5, "data_ref": "2025-01-02"}},
        )
        cache = make_cache()
        warmer = FintzCacheWarmer(repo, cache)
        keys = await warmer.warm("indicadores")
        assert keys > 0

    @pytest.mark.asyncio
    async def test_falha_cache_retorna_zero(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import FintzCacheWarmer
        repo = make_ts_repo()
        cache = make_cache()
        cache.set = AsyncMock(side_effect=Exception("redis down"))
        warmer = FintzCacheWarmer(repo, cache)
        keys = await warmer.warm("cotacoes")
        assert keys == 0


# ── ModelStalenessFlagge ──────────────────────────────────────────────────────

class TestModelStalenessFlagge:
    @pytest.mark.asyncio
    async def test_cotacoes_sinaliza_modelos_corretos(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import ModelStalenessFlagge
        cache = make_cache()
        flagger = ModelStalenessFlagge(cache)
        flagged = await flagger.flag("cotacoes")
        assert "backtest" in flagged
        assert "correlation" in flagged

    @pytest.mark.asyncio
    async def test_indicadores_sinaliza_screener(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import ModelStalenessFlagge
        cache = make_cache()
        flagger = ModelStalenessFlagge(cache)
        flagged = await flagger.flag("indicadores")
        assert "screener" in flagged
        assert "valuation_model" in flagged

    @pytest.mark.asyncio
    async def test_dataset_desconhecido_sem_flags(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import ModelStalenessFlagge
        cache = make_cache()
        flagger = ModelStalenessFlagge(cache)
        flagged = await flagger.flag("desconhecido")
        assert flagged == []

    @pytest.mark.asyncio
    async def test_falha_cache_retorna_lista_vazia(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import ModelStalenessFlagge
        cache = make_cache()
        cache.set = AsyncMock(side_effect=Exception("redis down"))
        flagger = ModelStalenessFlagge(cache)
        flagged = await flagger.flag("cotacoes")
        assert flagged == []


# ── PostSyncOrchestrator ──────────────────────────────────────────────────────

class TestPostSyncOrchestrator:
    @pytest.mark.asyncio
    async def test_handles_fintz_sync_completed(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        from finanalytics_ai.domain.events.entities import EventType
        orch = PostSyncOrchestrator(make_ts_repo(), make_cache())
        assert EventType.FINTZ_SYNC_COMPLETED in orch.handles

    @pytest.mark.asyncio
    async def test_apply_retorna_metadata_completo(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        repo = make_ts_repo(tickers=["PETR4"])
        cache = make_cache()
        orch = PostSyncOrchestrator(repo, cache, tickers_sample=["PETR4"])
        result = await orch.apply(make_event("indicadores"))
        assert "post_sync" in result
        ps = result["post_sync"]
        assert "anomalies_found" in ps
        assert "integrity_ok" in ps
        assert "cache_keys_updated" in ps
        assert "model_stale_flags" in ps

    @pytest.mark.asyncio
    async def test_falha_em_handler_nao_propaga(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        repo = make_ts_repo()
        repo.list_tickers = AsyncMock(side_effect=Exception("db error"))
        cache = make_cache()
        cache.set = AsyncMock(side_effect=Exception("redis error"))
        orch = PostSyncOrchestrator(repo, cache)
        # Não deve lançar exceção mesmo com tudo falhando
        result = await orch.apply(make_event("cotacoes"))
        assert "post_sync" in result

    @pytest.mark.asyncio
    async def test_model_flags_para_cotacoes(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        repo = make_ts_repo(tickers=["PETR4"])
        cache = make_cache()
        orch = PostSyncOrchestrator(repo, cache, tickers_sample=["PETR4"])
        result = await orch.apply(make_event("cotacoes"))
        flags = result["post_sync"]["model_stale_flags"]
        assert "backtest" in flags

    @pytest.mark.asyncio
    async def test_dataset_vazio_nao_quebra(self):
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        orch = PostSyncOrchestrator(make_ts_repo(), make_cache())
        result = await orch.apply(make_event(""))
        assert "post_sync" in result

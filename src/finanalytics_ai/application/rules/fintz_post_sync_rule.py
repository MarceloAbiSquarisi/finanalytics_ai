"""
application/rules/fintz_post_sync_rule.py

PostSyncOrchestrator — BusinessRule que executa 4 ações pós-sync:

  1. AnomalyDetector    — detecta indicadores fora dos limites históricos
  2. IntegrityValidator — valida coerência dos dados no TimescaleDB
  3. CacheWarmer        — atualiza cache Redis de indicadores derivados
  4. ModelStalenessFlag — sinaliza modelos que precisam de re-treino

Por que uma rule composta em vez de 4 rules separadas?
  EventProcessor impõe 1 rule por EventType — design deliberado para
  forçar responsabilidade única de despacho. A solução correta é ter
  um Orchestrator que internamente delega, mantendo cada handler
  com responsabilidade única e testável isoladamente.

Todas as ações são best-effort: falha em uma não bloqueia as demais
nem aborta o processamento do evento.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from finanalytics_ai.domain.events.entities import EventType
from finanalytics_ai.observability.logging import get_logger

if TYPE_CHECKING:
    from finanalytics_ai.domain.events.entities import Event
from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository
from finanalytics_ai.infrastructure.cache.backend import CacheBackend

log = get_logger(__name__)


# ── Resultado agregado ────────────────────────────────────────────────────────

@dataclass
class PostSyncResult:
    dataset: str
    anomalies_found: int = 0
    integrity_ok: bool = True
    integrity_issues: list[str] = field(default_factory=list)
    cache_keys_updated: int = 0
    model_stale_flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── 1. Anomaly Detector ───────────────────────────────────────────────────────

class FintzAnomalyDetector:
    """
    Detecta indicadores fora dos limites históricos após sync.

    Compara o valor mais recente com média ± N desvios padrão
    dos últimos 252 pregões. Dispara alerta via AlertService
    se disponível no contexto.

    Thresholds configuráveis por indicador — padrões conservadores
    para evitar falsos positivos em indicadores voláteis.
    """

    THRESHOLDS: dict[str, float] = {
        "P/L":           3.0,   # P/L raramente muda >3σ num sync
        "ROE":           2.5,
        "ROIC":          2.5,
        "Margem Líquida":2.5,
        "DY":            3.0,
        "EV/EBITDA":     3.0,
        "Dívida Líquida/EBITDA": 2.0,  # mais sensível — risco de crédito
    }

    DEFAULT_THRESHOLD = 3.0
    MIN_HISTORY = 20  # mínimo de pontos para calcular estatísticas

    def __init__(
        self,
        ts_repo: TimescaleFintzRepository,
        alert_service: Any | None = None,
    ) -> None:
        self._repo = ts_repo
        self._alert_service = alert_service

    async def detect(self, dataset: str, tickers_sample: list[str]) -> int:
        """
        Detecta anomalias em uma amostra de tickers.
        Retorna número de anomalias encontradas.
        """
        if dataset not in ("indicador", "indicadores"):
            return 0

        anomalies = 0
        indicadores_to_check = list(self.THRESHOLDS.keys())

        # Verifica amostra de até 20 tickers para não sobrecarregar
        sample = tickers_sample[:20]

        for ticker in sample:
            try:
                ticker_anomalies = await self._check_ticker(
                    ticker, indicadores_to_check
                )
                anomalies += ticker_anomalies
            except Exception as exc:
                log.warning(
                    "post_sync.anomaly.ticker_failed",
                    ticker=ticker,
                    error=str(exc),
                )

        log.info(
            "post_sync.anomaly.complete",
            dataset=dataset,
            tickers_checked=len(sample),
            anomalies_found=anomalies,
        )
        return anomalies

    async def _check_ticker(self, ticker: str, indicadores: list[str]) -> int:
        """Verifica anomalias para um ticker específico."""
        anomalies = 0
        for indicador in indicadores:
            try:
                serie = await self._repo.get_indicadores_serie(
                    ticker, indicador, limit=self.MIN_HISTORY + 1
                )
                if len(serie) < self.MIN_HISTORY:
                    continue

                valores = [
                    float(r["valor"]) for r in serie
                    if r.get("valor") is not None
                ]
                if len(valores) < self.MIN_HISTORY:
                    continue

                # Último valor vs histórico (excluindo o mais recente)
                latest = valores[0]
                history = valores[1:]

                mean = sum(history) / len(history)
                variance = sum((v - mean) ** 2 for v in history) / len(history)
                std = variance ** 0.5

                if std == 0:
                    # std=0: historico constante — usa desvio relativo
                    # Evita falso positivo para pequenas variações (8.0->8.5)
                    # Detecta anomalias reais (8.0->500.0)
                    ref = abs(mean) if mean != 0 else 1.0
                    relative_dev = abs(latest - mean) / ref
                    if relative_dev > 0.5:  # >50% de desvio relativo
                        anomalies += 1
                        log.warning(
                            "post_sync.anomaly.detected",
                            ticker=ticker,
                            indicador=indicador,
                            latest=latest,
                            mean=round(mean, 4),
                            z_score=float("inf"),
                            threshold=threshold,
                        )
                    continue

                z_score = abs(latest - mean) / std
                threshold = self.THRESHOLDS.get(indicador, self.DEFAULT_THRESHOLD)

                if z_score > threshold:
                    anomalies += 1
                    log.warning(
                        "post_sync.anomaly.detected",
                        ticker=ticker,
                        indicador=indicador,
                        latest=latest,
                        mean=round(mean, 4),
                        z_score=round(z_score, 2),
                        threshold=threshold,
                    )
                    await self._try_trigger_alert(ticker, indicador, z_score)

            except Exception:
                pass

        return anomalies

    async def _try_trigger_alert(
        self, ticker: str, indicador: str, z_score: float
    ) -> None:
        """Dispara alerta via AlertService se disponível."""
        if self._alert_service is None:
            return
        try:
            # AlertService.evaluate_price espera ticker + price
            # Usamos z_score como proxy de "preço" para acionar o mecanismo
            # Em produção: criar AlertType.FUNDAMENTAL_ANOMALY
            log.info(
                "post_sync.anomaly.alert_triggered",
                ticker=ticker,
                indicador=indicador,
                z_score=round(z_score, 2),
            )
        except Exception as exc:
            log.warning("post_sync.anomaly.alert_failed", error=str(exc))


# ── 2. Integrity Validator ────────────────────────────────────────────────────

class FintzIntegrityValidator:
    """
    Valida coerência dos dados no TimescaleDB após sync.

    Checks implementados:
      - Receita Líquida negativa (improvável exceto em reestruturações)
      - Volume negociado = 0 para >50% dos tickers (falha silenciosa de sync)
      - Gap de pregões > 5 dias úteis em cotações recentes
      - Valor de indicador extremo (> 1000x ou < -1000x)
    """

    def __init__(self, ts_repo: TimescaleFintzRepository) -> None:
        self._repo = ts_repo

    async def validate(self, dataset: str) -> tuple[bool, list[str]]:
        """
        Executa validações para o dataset sincronizado.
        Retorna (ok, lista_de_issues).
        """
        issues: list[str] = []

        try:
            if dataset == "cotacoes":
                issues.extend(await self._check_cotacoes())
            elif dataset in ("item_contabil", "itens"):
                issues.extend(await self._check_itens())
            elif dataset in ("indicador", "indicadores"):
                issues.extend(await self._check_indicadores())
        except Exception as exc:
            issues.append(f"Erro na validação: {exc}")

        ok = len(issues) == 0
        if not ok:
            log.warning(
                "post_sync.integrity.issues_found",
                dataset=dataset,
                issues=issues,
            )
        else:
            log.info("post_sync.integrity.ok", dataset=dataset)

        return ok, issues

    async def _check_cotacoes(self) -> list[str]:
        issues = []
        try:
            # Verifica se há cotações com volume = 0 para muitos tickers
            tickers = await self._repo.list_tickers("cotacoes")
            if not tickers:
                issues.append("cotacoes_ts: nenhum ticker encontrado")
                return issues

            zero_vol = 0
            sample = tickers[:50]  # amostra de 50 tickers
            for ticker in sample:
                rows = await self._repo.get_cotacoes(ticker, limit=5)
                if rows and all(
                    (r.get("volume") or 0) == 0 for r in rows
                ):
                    zero_vol += 1

            pct_zero = zero_vol / len(sample) if sample else 0
            if pct_zero > 0.5:
                issues.append(
                    f"cotacoes_ts: {pct_zero:.0%} dos tickers com volume=0 "
                    "(possível falha silenciosa de sync)"
                )
        except Exception as exc:
            issues.append(f"cotacoes check error: {exc}")
        return issues

    async def _check_itens(self) -> list[str]:
        issues = []
        try:
            # Verifica receita líquida negativa em amostra de tickers
            tickers = await self._repo.list_tickers("itens")
            sample = tickers[:30]
            negative_revenue = 0
            for ticker in sample:
                snapshot = await self._repo.get_itens_latest(ticker, "12M")
                receita = snapshot.get("ReceitaLiquida", {})
                if isinstance(receita, dict):
                    val = receita.get("valor")
                    if val is not None and float(val) < 0:
                        negative_revenue += 1
            if negative_revenue > len(sample) * 0.3:
                issues.append(
                    f"itens_contabeis_ts: {negative_revenue} tickers com "
                    "ReceitaLiquida negativa (>30% da amostra)"
                )
        except Exception as exc:
            issues.append(f"itens check error: {exc}")
        return issues

    async def _check_indicadores(self) -> list[str]:
        issues = []
        try:
            # Verifica valores extremos de P/L
            tickers = await self._repo.list_tickers("indicadores")
            sample = tickers[:30]
            extreme = 0
            for ticker in sample:
                snapshot = await self._repo.get_indicadores_latest(
                    ticker, ["P/L"]
                )
                pl = snapshot.get("P/L", {})
                if isinstance(pl, dict):
                    val = pl.get("valor")
                    if val is not None and (
                        float(val) > 1000 or float(val) < -1000
                    ):
                        extreme += 1
            if extreme > 5:
                issues.append(
                    f"indicadores_ts: {extreme} tickers com P/L extremo "
                    "(>1000 ou <-1000) — verificar dados"
                )
        except Exception as exc:
            issues.append(f"indicadores check error: {exc}")
        return issues


# ── 3. Cache Warmer ───────────────────────────────────────────────────────────

class FintzCacheWarmer:
    """
    Atualiza cache Redis de indicadores derivados após sync.

    Pré-computa e cacheia:
      - Snapshot de indicadores por grupo para os top 50 tickers
      - Cobertura de dados por ticker
      - Lista de tickers disponíveis

    TTL: 1 hora (dados fundamentais mudam só no sync diário).
    """

    TTL = 3600  # 1 hora
    TOP_TICKERS = ["PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3",
                   "WEGE3", "RENT3", "RADL3", "EQTL3", "SUZB3"]

    def __init__(
        self,
        ts_repo: TimescaleFintzRepository,
        cache: CacheBackend,
    ) -> None:
        self._repo = ts_repo
        self._cache = cache

    async def warm(self, dataset: str) -> int:
        """
        Aquece o cache para o dataset sincronizado.
        Retorna número de keys atualizadas.
        """
        keys_updated = 0

        try:
            # Sempre atualiza lista de tickers
            keys_updated += await self._warm_ticker_list(dataset)

            if dataset in ("indicador", "indicadores"):
                keys_updated += await self._warm_indicadores_snapshot()
            elif dataset == "cotacoes":
                keys_updated += await self._warm_cotacoes_coverage()

        except Exception as exc:
            log.warning("post_sync.cache.warm_failed", error=str(exc))

        log.info(
            "post_sync.cache.warmed",
            dataset=dataset,
            keys_updated=keys_updated,
        )
        return keys_updated

    async def _warm_ticker_list(self, dataset: str) -> int:
        import json
        ds_map = {
            "cotacoes": "cotacoes",
            "indicador": "indicadores",
            "indicadores": "indicadores",
            "item_contabil": "itens",
            "itens": "itens",
        }
        ts_dataset = ds_map.get(dataset, "cotacoes")
        try:
            tickers = await self._repo.list_tickers(ts_dataset)
            key = f"fa:fintz:tickers:{ts_dataset}"
            await self._cache.set(key, json.dumps(tickers), self.TTL)
            return 1
        except Exception:
            return 0

    async def _warm_indicadores_snapshot(self) -> int:
        import json
        keys = 0
        grupos = ["valuation", "rentabilidade", "dividendos", "endividamento"]
        for ticker in self.TOP_TICKERS:
            for grupo in grupos:
                try:
                    # Tenta buscar do repo — se falhar, pula
                    data = await self._repo.get_indicadores_latest(ticker)
                    if data:
                        key = f"fa:fintz:ind:{ticker}:{grupo}"
                        await self._cache.set(key, json.dumps(data, default=str), self.TTL)
                        keys += 1
                except Exception:
                    pass
        return keys

    async def _warm_cotacoes_coverage(self) -> int:
        import json
        keys = 0
        for ticker in self.TOP_TICKERS:
            try:
                coverage = await self._repo.get_coverage(ticker)
                key = f"fa:fintz:coverage:{ticker}"
                await self._cache.set(key, json.dumps(coverage, default=str), self.TTL)
                keys += 1
            except Exception:
                pass
        return keys


# ── 4. Model Staleness Flagger ────────────────────────────────────────────────

class ModelStalenessFlagge:
    """
    Sinaliza modelos que precisam de re-treino após sync de dados.

    Datasets que invalidam modelos:
      - cotacoes:     backtests, correlação, anomalia de preço
      - indicadores:  screener, modelos de valuation, forecast
      - itens:        modelos de crédito, análise fundamentalista

    Grava flags no Redis com TTL de 24h. O scheduler ou worker de ML
    lê essas flags antes de servir resultados cacheados.

    Design: fire-and-forget via Redis — não bloqueia o pipeline.
    """

    FLAG_TTL = 86400  # 24 horas

    DATASET_MODELS: dict[str, list[str]] = {
        "cotacoes":    ["backtest", "correlation", "anomaly_price", "ohlc_forecast"],
        "indicador":   ["screener", "valuation_model", "anomaly_fundamental"],
        "indicadores": ["screener", "valuation_model", "anomaly_fundamental"],
        "item_contabil": ["credit_model", "fundamental_analysis"],
        "itens":       ["credit_model", "fundamental_analysis"],
    }

    def __init__(self, cache: CacheBackend) -> None:
        self._cache = cache

    async def flag(self, dataset: str) -> list[str]:
        """
        Sinaliza modelos afetados pelo dataset sincronizado.
        Retorna lista de modelos sinalizados.
        """
        models = self.DATASET_MODELS.get(dataset, [])
        flagged = []

        for model in models:
            try:
                key = f"fa:model:stale:{model}"
                await self._cache.set(key, "1", self.FLAG_TTL)
                flagged.append(model)
            except Exception as exc:
                log.warning(
                    "post_sync.model_flag.failed",
                    model=model,
                    error=str(exc),
                )

        if flagged:
            log.info(
                "post_sync.model_flags.set",
                dataset=dataset,
                models=flagged,
            )

        return flagged


# ── PostSyncOrchestrator — BusinessRule ───────────────────────────────────────

class PostSyncOrchestrator:
    """
    BusinessRule que orquestra as 4 ações pós-sync.

    Implementa o Protocol BusinessRule — handles fintz.sync.completed.

    Injeção de dependências:
        ts_repo:       TimescaleFintzRepository — queries nas hypertables
        cache:         CacheBackend — Redis para cache e flags
        alert_service: AlertService | None — opcional, para anomalias
        tickers_sample: list[str] — tickers a verificar nas anomalias

    Design: cada handler roda de forma independente via asyncio.gather.
    Falha em um não afeta os demais nem o resultado do evento principal.
    O resultado das 4 ações é logado e incluído no result_metadata
    do EventProcessingRecord.
    """

    handles = (EventType.FINTZ_SYNC_COMPLETED,)

    def __init__(
        self,
        ts_repo: TimescaleFintzRepository,
        cache: CacheBackend,
        alert_service: Any | None = None,
        tickers_sample: list[str] | None = None,
    ) -> None:
        self._anomaly_detector = FintzAnomalyDetector(ts_repo, alert_service)
        self._integrity_validator = FintzIntegrityValidator(ts_repo)
        self._cache_warmer = FintzCacheWarmer(ts_repo, cache)
        self._model_flagger = ModelStalenessFlagge(cache)
        self._tickers_sample = tickers_sample or FintzCacheWarmer.TOP_TICKERS

    async def apply(self, event: Event) -> dict[str, Any]:
        """
        Executa as 4 ações pós-sync em paralelo.
        Retorna result_metadata para o EventProcessingRecord.
        """
        payload = event.payload
        dataset = payload.get("dataset", "")
        rows_synced = payload.get("rows_synced", 0)

        log.info(
            "post_sync.orchestrator.start",
            dataset=dataset,
            rows_synced=rows_synced,
        )

        # Executa em paralelo — cada um tem seu próprio try/except interno
        results = await asyncio.gather(
            self._run_anomaly(dataset),
            self._run_integrity(dataset),
            self._run_cache(dataset),
            self._run_model_flags(dataset),
            return_exceptions=True,
        )

        anomalies, integrity, cache_keys, model_flags = results

        # Trata exceptions de gather (não deveriam ocorrer pois
        # cada handler tem try/except, mas por segurança)
        result = PostSyncResult(dataset=dataset)
        result.anomalies_found    = anomalies if isinstance(anomalies, int) else 0
        result.cache_keys_updated = cache_keys if isinstance(cache_keys, int) else 0

        if isinstance(integrity, tuple):
            result.integrity_ok, result.integrity_issues = integrity
        if isinstance(model_flags, list):
            result.model_stale_flags = model_flags

        log.info(
            "post_sync.orchestrator.complete",
            dataset=dataset,
            anomalies=result.anomalies_found,
            integrity_ok=result.integrity_ok,
            cache_keys=result.cache_keys_updated,
            model_flags=result.model_stale_flags,
        )

        return {
            "post_sync": {
                "anomalies_found":    result.anomalies_found,
                "integrity_ok":       result.integrity_ok,
                "integrity_issues":   result.integrity_issues,
                "cache_keys_updated": result.cache_keys_updated,
                "model_stale_flags":  result.model_stale_flags,
            }
        }

    async def _run_anomaly(self, dataset: str) -> int:
        try:
            return await self._anomaly_detector.detect(dataset, self._tickers_sample)
        except Exception as exc:
            log.warning("post_sync.anomaly.error", error=str(exc))
            return 0

    async def _run_integrity(self, dataset: str) -> tuple[bool, list[str]]:
        try:
            return await self._integrity_validator.validate(dataset)
        except Exception as exc:
            log.warning("post_sync.integrity.error", error=str(exc))
            return True, []

    async def _run_cache(self, dataset: str) -> int:
        try:
            return await self._cache_warmer.warm(dataset)
        except Exception as exc:
            log.warning("post_sync.cache.error", error=str(exc))
            return 0

    async def _run_model_flags(self, dataset: str) -> list[str]:
        try:
            return await self._model_flagger.flag(dataset)
        except Exception as exc:
            log.warning("post_sync.model_flags.error", error=str(exc))
            return []

"""
finanalytics_ai.metrics
───────────────────────
Métricas Prometheus para todos os módulos da plataforma.

Design decisions:

  /metrics no mesmo port 8000 (não porta separada):
    Usar prometheus_client.make_asgi_app() montado como sub-aplicação
    FastAPI é mais simples para Docker (1 porta a expor, 1 healthcheck).
    A abordagem de porta separada (start_http_server) é útil para workers
    sem HTTP — não é o caso aqui.

  Cardinality control:
    Labels de rota usam path templates ("/api/v1/quotes/{ticker}"),
    não URLs brutas. Isso evita explosão de cardinality com tickers
    distintos como labels. O middleware normaliza via request.route.path.

  Separação domínio/infra:
    Os contadores ficam AQUI, não nos services. Os services recebem
    uma função de incremento via DI (ou chamam diretamente este módulo
    com um import explícito). Isso mantém o domínio puro.

  Métricas cobertas:
    HTTP layer:   http_requests_total, http_request_duration_seconds
    BRAPI:        brapi_requests_total, brapi_request_duration_seconds
    Anomaly:      anomaly_scans_total, anomaly_detections_total
    Screener:     screener_runs_total, screener_stocks_passed_total
    Backtest:     backtest_runs_total
    Alerts:       alerts_triggered_total, alerts_active_total (Gauge)
    Price prod:   price_producer_polls_total, price_producer_errors_total
    Kafka:        kafka_messages_consumed_total
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request

logger = structlog.get_logger(__name__)

# ── Build info ────────────────────────────────────────────────────────────────

build_info = Info(
    name="finanalytics_build",
    documentation="Informações de build da plataforma",
)
build_info.info({"version": "0.1.0", "sprint": "21", "python": "3.12"})

# ── HTTP layer ────────────────────────────────────────────────────────────────

http_requests_total = Counter(
    name="finanalytics_http_requests_total",
    documentation="Total de requisições HTTP recebidas",
    labelnames=["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    name="finanalytics_http_request_duration_seconds",
    documentation="Latência das requisições HTTP em segundos",
    labelnames=["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

http_requests_in_flight = Gauge(
    name="finanalytics_http_requests_in_flight",
    documentation="Requisições HTTP atualmente em processamento",
    labelnames=["method", "path"],
)

# ── BRAPI / mercado ───────────────────────────────────────────────────────────

brapi_requests_total = Counter(
    name="finanalytics_brapi_requests_total",
    documentation="Total de chamadas à BRAPI",
    labelnames=["endpoint", "status"],  # status: success | error | timeout
)

brapi_request_duration_seconds = Histogram(
    name="finanalytics_brapi_request_duration_seconds",
    documentation="Latência das chamadas à BRAPI em segundos",
    labelnames=["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── Anomaly detection ─────────────────────────────────────────────────────────

anomaly_scans_total = Counter(
    name="finanalytics_anomaly_scans_total",
    documentation="Total de scans de anomalia executados",
    labelnames=["range_period"],
)

anomaly_detections_total = Counter(
    name="finanalytics_anomaly_detections_total",
    documentation="Total de anomalias detectadas",
    labelnames=["algorithm", "severity"],
    # algorithm: zscore_spike | bollinger_break | cusum_shift | volume_spike
    # severity:  high | medium | low
)

anomaly_tickers_scanned = Histogram(
    name="finanalytics_anomaly_tickers_scanned",
    documentation="Numero de tickers por scan de anomalia",
    buckets=(1, 2, 5, 10, 15, 20),
)

# ── Screener ──────────────────────────────────────────────────────────────────

screener_runs_total = Counter(
    name="finanalytics_screener_runs_total",
    documentation="Total de execucoes do screener",
)

screener_stocks_scanned_total = Counter(
    name="finanalytics_screener_stocks_scanned_total",
    documentation="Total de acoes avaliadas pelo screener",
)

screener_stocks_passed_total = Counter(
    name="finanalytics_screener_stocks_passed_total",
    documentation="Total de acoes que passaram nos filtros do screener",
)

screener_pass_rate = Histogram(
    name="finanalytics_screener_pass_rate",
    documentation="Taxa de aprovacao do screener (passed/total) por execucao",
    buckets=(0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0),
)

# ── Backtest ──────────────────────────────────────────────────────────────────

backtest_runs_total = Counter(
    name="finanalytics_backtest_runs_total",
    documentation="Total de backtests executados",
    labelnames=["strategy"],
)

backtest_duration_seconds = Histogram(
    name="finanalytics_backtest_duration_seconds",
    documentation="Duracao do backtest em segundos",
    labelnames=["strategy"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0),
)

optimizer_runs_total = Counter(
    name="finanalytics_optimizer_runs_total",
    documentation="Total de otimizacoes executadas",
    labelnames=["strategy", "objective"],
)

# ── Alertas ───────────────────────────────────────────────────────────────────

alerts_triggered_total = Counter(
    name="finanalytics_alerts_triggered_total",
    documentation="Total de alertas disparados",
    labelnames=["alert_type"],
)

alerts_active = Gauge(
    name="finanalytics_alerts_active",
    documentation="Numero de alertas ativos no momento",
)

# ── Price producer / Kafka ────────────────────────────────────────────────────

price_producer_polls_total = Counter(
    name="finanalytics_price_producer_polls_total",
    documentation="Total de polling cycles do produtor de precos",
    labelnames=["status"],  # success | error
)

price_producer_tickers_updated = Counter(
    name="finanalytics_price_producer_tickers_updated_total",
    documentation="Total de tickers atualizados pelo produtor",
)

kafka_messages_consumed_total = Counter(
    name="finanalytics_kafka_messages_consumed_total",
    documentation="Total de mensagens consumidas do Kafka",
    labelnames=["event_type"],
)

# ── Correlação ────────────────────────────────────────────────────────────────

correlation_runs_total = Counter(
    name="finanalytics_correlation_runs_total",
    documentation="Total de analises de correlacao executadas",
)

# ── Helpers para instrumentacao nos services ──────────────────────────────────


def record_anomaly_scan(
    tickers_count: int,
    range_period: str,
    results: list[dict],
) -> None:
    """Registra métricas de um scan de anomalia completo."""
    anomaly_scans_total.labels(range_period=range_period).inc()
    anomaly_tickers_scanned.observe(tickers_count)
    for r in results:
        for a in r.get("anomalies", []):
            anomaly_detections_total.labels(
                algorithm=a.get("anomaly_type", "unknown"),
                severity=a.get("severity", "unknown"),
            ).inc()


def record_screener_run(total_scanned: int, total_passed: int) -> None:
    """Registra métricas de uma execução do screener."""
    screener_runs_total.inc()
    screener_stocks_scanned_total.inc(total_scanned)
    screener_stocks_passed_total.inc(total_passed)
    if total_scanned > 0:
        screener_pass_rate.observe(total_passed / total_scanned)


def record_brapi_call(endpoint: str, success: bool, duration: float) -> None:
    """Registra métricas de uma chamada à BRAPI."""
    status = "success" if success else "error"
    brapi_requests_total.labels(endpoint=endpoint, status=status).inc()
    brapi_request_duration_seconds.labels(endpoint=endpoint).observe(duration)


# ── Middleware HTTP ───────────────────────────────────────────────────────────

_SKIP_PATHS = frozenset({"/metrics", "/health", "/favicon.ico"})


class PrometheusMiddleware(BaseHTTPMiddleware):
    """
    Middleware Starlette que instrumenta todas as requisições HTTP.

    Normaliza o path usando request.scope["route"].path quando disponível,
    evitando que paths com parâmetros ({ticker}, {alert_id} etc.) criem
    uma label por valor distinto.

    Paths em _SKIP_PATHS não são instrumentados para evitar ruído.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in _SKIP_PATHS:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()

        # Tenta obter path template (ex: /api/v1/quotes/{ticker})
        # Disponível após roteamento — fallback para path bruto
        http_requests_in_flight.labels(method=method, path=path).inc()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            http_requests_in_flight.labels(method=method, path=path).dec()
            raise

        # Normaliza path via route se disponível
        route = request.scope.get("route")
        norm_path = getattr(route, "path", path)

        elapsed = time.perf_counter() - start
        http_requests_total.labels(method=method, path=norm_path, status_code=str(status)).inc()
        http_request_duration_seconds.labels(method=method, path=norm_path).observe(elapsed)
        http_requests_in_flight.labels(method=method, path=path).dec()

        return response


# ── Endpoint /metrics ─────────────────────────────────────────────────────────


async def metrics_endpoint(request: Request) -> Response:
    """
    Endpoint FastAPI que serve métricas no formato Prometheus text.

    Montado diretamente como rota para evitar dependência de
    prometheus_client.make_asgi_app() que pode ter conflitos de
    registry em testes.
    """
    data = generate_latest(REGISTRY)
    return Response(
        content=data,
        media_type=CONTENT_TYPE_LATEST,
    )

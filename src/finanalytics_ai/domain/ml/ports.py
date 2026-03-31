"""
finanalytics_ai.domain.ml.ports

Ports (Protocols) da camada de ML.

Decisao: Protocol ao inves de ABC pelas mesmas razoes do restante do projeto —
duck typing estrutural, testavel com qualquer fake sem heranca.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime
from finanalytics_ai.domain.ml.entities import (
        ReturnForecast,
        RiskMetrics,
        TickerFeatures,
    )


@runtime_checkable
class FeatureStore(Protocol):
    """Porta de leitura/escrita de features."""

    async def get_features(
        self,
        tickers: list[str],
        date: datetime,
    ) -> list[TickerFeatures]:
        """Retorna features computadas para os tickers na data."""
        ...

    async def save_features(self, features: list[TickerFeatures]) -> None:
        """Persiste ou atualiza features (upsert por ticker+date)."""
        ...

    async def get_training_data(
        self,
        tickers: list[str],
        start: datetime,
        end: datetime,
    ) -> list[TickerFeatures]:
        """Retorna serie historica de features para treinamento."""
        ...


@runtime_checkable
class ForecastStore(Protocol):
    """Porta de persistencia de previsoes."""

    async def save_forecasts(self, forecasts: list[ReturnForecast]) -> None: ...

    async def get_latest_forecasts(
        self,
        tickers: list[str],
        horizon_days: int,
    ) -> list[ReturnForecast]:
        """Ultima previsao disponivel por ticker."""
        ...


@runtime_checkable
class RiskStore(Protocol):
    """Porta de persistencia de metricas de risco."""

    async def save_risk_metrics(self, metrics: list[RiskMetrics]) -> None: ...

    async def get_latest_risk(
        self,
        tickers: list[str],
    ) -> list[RiskMetrics]:
        ...

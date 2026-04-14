"""Pydantic v2 schemas for the Setup Scanner API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from datetime import date, datetime


class SetupDetectionSchema(BaseModel):
    ticker: str
    tipo: str
    setup_name: str
    descricao: str
    direcao: str
    timeframe: str
    strength: float
    date: date
    details: dict[str, float | None]
    entry_price: float | None = None
    stop_price: float | None = None


class ScanResultResponse(BaseModel):
    scanned_at: datetime
    total_tickers: int
    tickers_com_dados: int
    total_signals: int
    duracao_ms: int
    signals: list[SetupDetectionSchema]
    tickers_sem_dados: list[str]


class SetupInfoSchema(BaseModel):
    nome: str
    descricao: str
    direcao: str
    timeframe: str
    minimo_candles: int


class SetupListResponse(BaseModel):
    total: int
    setups: list[SetupInfoSchema]


class HistoryEntrySchema(BaseModel):
    setup_name: str
    descricao: str
    direcao: str
    timeframe: str
    strength: float
    date: date
    details: dict[str, float | None]
    entry_price: float | None = None
    stop_price: float | None = None


class HistoryResponse(BaseModel):
    ticker: str
    desde: date
    total: int
    detections: list[HistoryEntrySchema]

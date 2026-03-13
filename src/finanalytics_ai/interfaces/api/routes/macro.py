"""
Rotas de dados macroeconômicos — leitura do data lake Parquet.

Endpoints:
  GET /api/v1/macro/series          — lista séries disponíveis
  GET /api/v1/macro/{series}        — retorna dados históricos de uma série
  GET /api/v1/macro/snapshot        — valores mais recentes de todas as séries

Design:
  - Leitura direta do Parquet via DataStorageService (sem banco)
  - Cache em memória por 1h (dados macro mudam pouco)
  - Fallback para dados simulados se Parquet ainda não foi coletado
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/macro", tags=["Macro"])

# Cache simples em memória: {series: (timestamp, data)}
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 3600  # 1 hora

# Metadados das séries
_SERIES_META: dict[str, dict[str, str]] = {
    "selic":   {"label": "SELIC", "unit": "% a.a.", "source": "BCB", "freq": "diária"},
    "ipca":    {"label": "IPCA",  "unit": "% a.m.", "source": "BCB", "freq": "mensal"},
    "usd_brl": {"label": "USD/BRL","unit": "R$",   "source": "BCB", "freq": "diária"},
    "eur_brl": {"label": "EUR/BRL","unit": "R$",   "source": "BCB", "freq": "diária"},
    "igpm":    {"label": "IGP-M", "unit": "% a.m.", "source": "BCB", "freq": "mensal"},
    "ibov":    {"label": "IBOV",  "unit": "pts",   "source": "Yahoo","freq": "diária"},
    "vix":     {"label": "VIX",   "unit": "pts",   "source": "Yahoo","freq": "diária"},
    "sp500":   {"label": "S&P 500","unit": "pts",  "source": "Yahoo","freq": "diária"},
}


def _get_storage(request: Request) -> Any:
    from finanalytics_ai.config import get_settings
    from finanalytics_ai.infrastructure.storage.data_storage_service import get_storage
    settings = get_settings()
    return get_storage(settings.data_dir)


def _cached(key: str, fn: Any) -> Any:
    now = time.time()
    if key in _cache and now - _cache[key][0] < _CACHE_TTL:
        return _cache[key][1]
    result = fn()
    _cache[key] = (now, result)
    return result


@router.get("/series")
async def list_series() -> dict[str, Any]:
    """Lista séries macro disponíveis com metadados."""
    return {"series": _SERIES_META}


@router.get("/snapshot")
async def snapshot(request: Request) -> dict[str, Any]:
    """
    Valor mais recente de cada série macro.
    Usado pelos cards do dashboard.
    """
    storage = _get_storage(request)
    result: dict[str, Any] = {}

    for name, meta in _SERIES_META.items():
        try:
            def _read(n: str = name) -> Any:
                df = storage.read_macro(n)
                if df is None or df.empty:
                    return None
                row = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else row
                val = float(row["value"])
                prev_val = float(prev["value"])
                change = ((val - prev_val) / prev_val * 100) if prev_val else 0.0
                return {
                    "value": round(val, 4),
                    "change_pct": round(change, 2),
                    "date": str(row["date"])[:10],
                }

            data = _cached(f"snapshot_{name}", _read)
            if data:
                result[name] = {**meta, **data}
        except Exception as e:
            logger.warning("macro.snapshot.error", series=name, error=str(e)[:60])

    return {"snapshot": result, "cached": True}


@router.get("/{series}")
async def get_series(
    series: str,
    request: Request,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Retorna histórico de uma série macro.
    limit: número de pontos (padrão 500, ~2 anos para séries diárias)
    """
    if series not in _SERIES_META:
        raise HTTPException(
            status_code=404,
            detail=f"Série '{series}' não encontrada. Disponíveis: {list(_SERIES_META)}",
        )

    storage = _get_storage(request)

    def _read() -> list[dict[str, Any]]:
        df = storage.read_macro(series)
        if df is None or df.empty:
            return []
        import pandas as pd
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(limit)
        return [
            {"date": str(r["date"])[:10], "value": round(float(r["value"]), 4)}
            for _, r in df.iterrows()
        ]

    data = _cached(f"series_{series}_{limit}", _read)
    meta = _SERIES_META[series]

    return {
        "series": series,
        "label": meta["label"],
        "unit": meta["unit"],
        "source": meta["source"],
        "freq": meta["freq"],
        "count": len(data),
        "data": data,
    }

"""
Rotas de cotações e busca de ativos.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.domain.value_objects.money import Ticker
from finanalytics_ai.interfaces.api.dependencies import get_brapi_client

router = APIRouter()


@router.get("/{ticker}")
async def get_quote(
    ticker: str,
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    price = await brapi.get_quote(Ticker(ticker))
    return {"ticker": ticker.upper(), "price": str(price.amount), "currency": price.currency}


@router.get("")
async def search_assets(
    q: str = Query(..., min_length=1),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> list[dict]:
    return await brapi.search_assets(q)

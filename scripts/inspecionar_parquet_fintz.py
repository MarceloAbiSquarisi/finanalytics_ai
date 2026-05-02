"""
scripts/inspecionar_parquet_fintz.py
──────────────────────────────────────
Baixa uma amostra de cada tipo de parquet e imprime o schema real.
Uso: uv run python scripts/inspecionar_parquet_fintz.py
"""

from __future__ import annotations

import asyncio
import io
import os

import aiohttp
import pandas as pd

API_KEY = os.environ.get("FINTZ_API_KEY", "")
BASE_URL = "https://api.fintz.com.br"
HEADERS = {"X-API-Key": API_KEY}

AMOSTRAS = [
    ("cotacoes", f"{BASE_URL}/bolsa/b3/avista/cotacoes/historico/arquivos", {}),
    (
        "item_EBIT_12M",
        f"{BASE_URL}/bolsa/b3/avista/itens-contabeis/point-in-time/arquivos",
        {"item": "EBIT", "tipoPeriodo": "12M"},
    ),
    (
        "indicador_ROE",
        f"{BASE_URL}/bolsa/b3/avista/indicadores/point-in-time/arquivos",
        {"indicador": "ROE"},
    ),
]


async def inspecionar(nome: str, endpoint: str, params: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {nome}")
    print(f"{'=' * 60}")

    async with aiohttp.ClientSession(headers=HEADERS) as s:
        r = await s.get(endpoint, params=params)
        if r.status != 200:
            print(f"  ERRO HTTP {r.status}")
            return
        data = await r.json()

    link = data.get("link")
    if not link:
        print(f"  Sem link: {data}")
        return

    print("  Link obtido. Baixando...")
    async with aiohttp.ClientSession() as s:
        r = await s.get(link)
        raw = await r.read()

    df = pd.read_parquet(io.BytesIO(raw))

    print(f"\n  Shape: {df.shape[0]:,} linhas × {df.shape[1]} colunas")
    print("\n  Colunas e dtypes:")
    for col, dtype in df.dtypes.items():
        nulos = df[col].isna().sum()
        print(f"    {col:<45} {str(dtype):<12} nulos={nulos:,}")

    print("\n  Primeiras 3 linhas:")
    print(df.head(3).to_string(index=False))


async def main() -> None:
    if not API_KEY:
        print("ERRO: FINTZ_API_KEY não definida no ambiente")
        return

    for nome, endpoint, params in AMOSTRAS:
        await inspecionar(nome, endpoint, params)

    print("\nInspeção concluída.")


if __name__ == "__main__":
    asyncio.run(main())

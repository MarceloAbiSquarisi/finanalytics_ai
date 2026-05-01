"""
scripts/inspecionar_schema_fintz.py
Uso: uv run python scripts/inspecionar_schema_fintz.py
"""
import asyncio
import io
import os

import aiohttp
import pandas as pd

API_KEY = os.environ.get("FINTZ_API_KEY", "")
BASE    = "https://api.fintz.com.br"
HEADERS = {"X-API-Key": API_KEY}

ENDPOINTS = [
    ("cotacoes",
     f"{BASE}/bolsa/b3/avista/cotacoes/historico/arquivos",
     {}),
    ("item_EBIT_12M",
     f"{BASE}/bolsa/b3/avista/itens-contabeis/point-in-time/arquivos",
     {"item": "EBIT", "tipoPeriodo": "12M"}),
    ("indicador_ROE",
     f"{BASE}/bolsa/b3/avista/indicadores/point-in-time/arquivos",
     {"indicador": "ROE"}),
]


async def inspecionar(nome: str, url: str, params: dict) -> None:
    print(f"\n{'='*60}\n  {nome}\n{'='*60}")

    # 1. Pega o link
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        r = await s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30))
        if r.status != 200:
            print(f"  ERRO HTTP {r.status}")
            return
        body = await r.json()

    link: str = body.get("link", "")
    if not link:
        print(f"  Sem link na resposta: {body}")
        return

    print(f"  Link: {link[:80]}...")

    # 2. Baixa o parquet
    print("  Baixando arquivo (pode demorar)...")
    async with aiohttp.ClientSession() as s:
        r = await s.get(link, timeout=aiohttp.ClientTimeout(total=300))
        raw = await r.read()

    print(f"  Tamanho: {len(raw)/1_048_576:.1f} MB")

    # 3. Lê só as primeiras 5 linhas para não carregar tudo na memória
    df = pd.read_parquet(io.BytesIO(raw))

    print(f"\n  Shape: {df.shape[0]:,} linhas x {df.shape[1]} colunas")
    print("\n  Colunas e dtypes:")
    for col, dtype in df.dtypes.items():
        nulos = int(df[col].isna().sum())
        print(f"    {str(col):<45} {str(dtype):<15} nulos={nulos:,}")

    print("\n  Primeiras 3 linhas:")
    print(df.head(3).to_string(index=False))


async def main() -> None:
    if not API_KEY:
        print("ERRO: FINTZ_API_KEY nao encontrada no ambiente.")
        print("Execute: $env:FINTZ_API_KEY = 'sua_chave'")
        return

    for nome, url, params in ENDPOINTS:
        await inspecionar(nome, url, params)

    print("\n\nInspecao concluida.")


if __name__ == "__main__":
    asyncio.run(main())

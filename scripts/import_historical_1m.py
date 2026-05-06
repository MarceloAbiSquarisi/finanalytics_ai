"""import_historical_1m.py — CLI thin wrapper sobre
finanalytics_ai.application.services.ohlc_importer.

Toda a logica de parsing/normalizacao/upsert vive em ohlc_importer (refator
06/mai para que /admin → Backfill possa importar via FastAPI). Este script
mantem a interface CLI original sem mudar contrato.

Uso:
    python scripts/import_historical_1m.py --file bars.csv
    python scripts/import_historical_1m.py --file bars.parquet --batch-size 20000
    python scripts/import_historical_1m.py --file custom.csv \
        --column-map "ticker=ticker,dt=time,o=open,h=high,l=low,c=close,v=volume"
    python scripts/import_historical_1m.py --file a.csv --dry-run
    python scripts/import_historical_1m.py --file a.csv --only-tickers PETR4,VALE3
    python scripts/import_historical_1m.py --file a.csv --source nelogica_1m

Validacoes:
  - time em range plausivel (2010-01-01 ate hoje+1)
  - OHLC consistente: H >= max(O,C,L), L <= min(O,C,H), price > min_price
  - volume >= 0
  - ticker non-empty uppercase

Linhas invalidas sao logadas e puladas (nao abortam o batch).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Adiciona src/ ao path quando rodando standalone (fora do container).
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from finanalytics_ai.application.services.ohlc_importer import (  # noqa: E402
    DEFAULT_MIN_PRICE,
    DEFAULT_SOURCE,
    import_file,
    parse_column_map_str,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import de bars 1m historicos externos")
    p.add_argument("--file", required=True, help="CSV / Parquet / JSONL")
    p.add_argument("--column-map", default="", help='CSV "src=dst,src2=dst2"')
    p.add_argument("--only-tickers", default="", help="CSV de tickers a importar")
    p.add_argument(
        "--min-price",
        type=float,
        default=DEFAULT_MIN_PRICE,
        help="Rejeita rows com min(OHLC) < min (default 0.01)",
    )
    p.add_argument("--batch-size", type=int, default=10000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-rows", type=int, default=0, help="0 = sem limite")
    p.add_argument("--source", default=DEFAULT_SOURCE, help="Tag em ohlc_1m.source")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.file)
    if not src.exists():
        print(f"arquivo nao existe: {src}", file=sys.stderr)
        return 2

    col_map = parse_column_map_str(args.column_map)
    only = {t.strip().upper() for t in args.only_tickers.split(",") if t.strip()} or None

    print(
        f"import_historical_1m: file={src.name} format={src.suffix} "
        f"col_map={col_map} only={only} min_price={args.min_price} "
        f"source={args.source} batch={args.batch_size} dry_run={args.dry_run}"
    )

    stats = import_file(
        src,
        column_map=col_map,
        only_tickers=only,
        min_price=args.min_price,
        source=args.source,
        batch_size=args.batch_size,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
    )

    print("\n=== RESUMO ===")
    print(f"  read_total         = {stats.read_total}")
    print(f"  rejected_invalid   = {stats.rejected_invalid}")
    print(f"  rejected_ohlc_bad  = {stats.rejected_ohlc}")
    print(f"  rejected_filtered  = {stats.rejected_dedup_filtered}")
    print(f"  upserted           = {stats.upserted}")
    print(f"  tickers_unicos     = {len(stats.tickers_seen)}")
    rate = stats.read_total / max(stats.elapsed_s, 1)
    print(f"  elapsed            = {stats.elapsed_s:.1f}s ({rate:.0f} rows/s)")
    if stats.errors:
        print("\n  Primeiros erros:")
        for e in stats.errors[:10]:
            print(f"    {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

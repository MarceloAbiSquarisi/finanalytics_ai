#!/usr/bin/env bash
# Treina pickles h3 e h5 para top-20 tickers (por best_sharpe).
#
# Roda dentro do container finanalytics_api (que tem lightgbm/psycopg2).
# Pickles vão pra /tmp/models_out — fora do mount RO de /app/models.
#
# Output final: caller deve docker cp /tmp/models_out -> ./models/
#
# Uso (de host WSL):
#   wsl -- docker exec finanalytics_api bash /tmp/train_h3_h5.sh

set -e

DSN="${PROFIT_TIMESCALE_DSN:-postgresql://finanalytics:timescale_secret@timescale:5432/market_data}"

echo "=== Buscando top-20 por best_sharpe ==="
TICKERS=$(python -c "
import psycopg2
import os
dsn = os.environ.get('PROFIT_TIMESCALE_DSN','postgresql://finanalytics:timescale_secret@timescale:5432/market_data')
with psycopg2.connect(dsn) as c, c.cursor() as cur:
    cur.execute('SELECT ticker FROM ticker_ml_config WHERE best_sharpe IS NOT NULL ORDER BY best_sharpe DESC LIMIT 20')
    for r in cur.fetchall(): print(r[0])
")

echo "Top-20: $(echo "$TICKERS" | tr '\n' ' ')"
echo

mkdir -p /tmp/models_out

for HORIZON in 3 5; do
  echo "=== TREINANDO horizon=${HORIZON}d ==="
  for T in $TICKERS; do
    echo -n "  $T h${HORIZON} ... "
    PROFIT_TIMESCALE_DSN="$DSN" python -u /app/scripts/train_petr4_mvp_v2.py \
      --ticker "$T" \
      --horizon "$HORIZON" \
      --out-dir /tmp/models_out 2>&1 \
      | grep -E '^modelo:|^train <|Sem features' \
      | head -1
  done
  echo
done

echo "=== Pickles em /tmp/models_out ==="
ls -la /tmp/models_out/*.pkl 2>/dev/null | wc -l
echo
echo "=== DONE ==="

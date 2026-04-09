# FinAnalytics AI — Pendências e Backlog
> Atualizado: 2026-04-07 (pós-sessão 7 — DLL + bloqueadores)

---

## RESOLVIDOS HOJE (2026-04-07)

| # | Item | Status |
|---|------|--------|
| B1 | API rebuild limpo | ✅ |
| B2 | Migration 0014_import_tables — único head | ✅ |
| B3 | Pipeline Redis → TapeService → métricas | ✅ |
| F9 | Exportação DataFrame (parquet, botão dashboard) | 📝 anotado |

---

## DLL NELOGICA — STATUS ATUAL

Sequência correta identificada (baseada em diag_asyncio_dll.py que funcionou):
  1. DLLInitializeLogin (sem SetTradeCallbackV2 antes)
  2. Aguardar routing_connected (conn_type=1 r>=4) — ~1.5s
  3. SetTradeCallbackV2 UMA UNICA VEZ após routing
  4. Aguardar market_connected (conn_type=2 r>=4) — ~0.5s
  5. SubscribeTicker x8
  6. Ticks chegam via trade_cb → Redis → TapeService

Diagnósticos que funcionaram hoje:
  - diag_trade_raw_v2.py    13:25 → raw=1  tick price=49.0 ✅
  - diag_asyncio_dll.py     13:43 → raw=160 ticks ✅
  - diag_market_cb_check.py 15:29 → raw=57  ticks ✅

Worker v2 (profit_market_worker_v2.py) com ctypes direto:
  - Routing conecta corretamente (conn_type=1 r=4 em ~1.5s)
  - market_connected timeout após ~14:30 — rate limiting suspeito
  - Causa: 30+ tentativas de conexão no mesmo dia = throttling Nelogica

AÇÃO AMANHÃ ÀS 10H:
  1. uv run python diag_asyncio_dll.py → confirmar se market data volta
  2. Se OK: uv run python -m finanalytics_ai.workers.profit_market_worker_v2
  3. Validar ticks no tape: GET /api/v1/tape/metrics/WINFUT

Patches aplicados em client.py (estado atual):
  - patch_dll_polling_wait.py       ← wait_connected usa polling (sem event)
  - patch_dll_market_latch.py       ← market_connected = True se r>=4
  - patch_dll_remove_early_settrade ← sem SetTradeCallbackV2 em start()
  - patch_dll_log_market_cb.py      ← file log para conn_type=2 (debug)

Patches aplicados em profit_market_worker.py (estado atual):
  - patch_worker_subscribe_early    ← remove wait market_connected antigo
  - patch_worker_wait_routing       ← wait routing antes de subscribe
  - patch_worker_no_routing         ← "no_routing_account" label
  - patch_worker_restore_routing    ← restaura wait routing
  - patch_worker_single_settrade    ← SetTradeCallbackV2 único pós-routing
  - patch_worker_diag_sequence      ← sequência do diagnóstico
  - patch_worker_longer_market_wait ← timeout 120s

Worker alternativo recomendado: profit_market_worker_v2.py
  Usa ctypes direto (sem ProfitDLLClient), mesma abordagem do diagnóstico.

---

## DEPENDEM DE MERCADO ABERTO (amanhã)

| # | Item | Status |
|---|------|--------|
| M1 | Confirmar ticks ao vivo via worker v2 | ⏳ rate limiting hoje |
| M2 | TickAnomalyBridge ao vivo | Código pronto |
| M3 | Análise de anomalias loop 60s | Implementar |
| M4 | Sinais por confluência engine | Implementar |
| M6 | Dashboard tick live BusinessDay→Unix | Validar |

---

## FEATURES NOVAS (qualquer hora)

| # | Item | Prioridade |
|---|------|------------|
| F2 | Extrato bancário XLS/CSV/OFX → portfólio | Alta |
| F3 | Notas de corretagem PDF/XLS — XP, Clear, BTG, Inter | Alta |
| F5 | WhatsApp QR code — Evolution API | Média |
| F6 | Alertas Tape + WhatsApp | Média |
| F7 | Relatório PDF avançado | Baixa |
| F8 | Dias de Estresse — análise de risco | Média |
| F9 | Exportação Parquet (ticks, candles, anomalias) | Média |

---

## DÉBITO TÉCNICO / DASHBOARD

| # | Item |
|---|------|
| D1 | Linha vertical entre dias de pregão |
| D2 | Select duplicado refresh-sel |
| D3 | PETR4 5m — espaço vazio à esquerda |

---

## TIMESCALE (warninq no worker)

  profit_worker.timescale_unavailable
  error='invalid DSN: scheme is expected to be either "postgresql" or "postgres",
         got postgresql+asyncpg'

  Fix: converter TIMESCALE_URL de asyncpg para psycopg2/pg antes de criar pool asyncpg.
  Impacto: PriceUpdateRule não persiste candles no TimescaleDB (degraded mode).

---

## COMANDOS RÁPIDOS

  # Worker v2 (recomendado)
  $env:REDIS_URL = "redis://localhost:6379/0"
  $env:LOG_FORMAT = "text"
  uv run python -m finanalytics_ai.workers.profit_market_worker_v2

  # Diagnóstico básico (confirmar DLL ok)
  uv run python diag_asyncio_dll.py

  # Rebuild API
  docker-compose build --no-cache api && docker-compose up -d api

  # Migrations
  docker exec finanalytics_api alembic upgrade heads

  # Tick de teste manual
  docker exec finanalytics_redis redis-cli PUBLISH tape:ticks '{"ticker":"WINFUT","price":130500.0,"volume":5.0,"quantity":5,"trade_type":1,"buy_agent":1,"sell_agent":2,"ts":"now","trade_number":1}'

  # Git
  git add -A && git commit -m "mensagem" && git push origin master

---

## STACK

  Python 3.12 · FastAPI · PostgreSQL 16 · TimescaleDB · Redis
  SQLAlchemy 2.x async · uv · Docker Compose · Alembic
  ProfitDLL 4.0.0.35 · Fintz · Evolution API (WhatsApp)

  Containers:
    finanalytics_postgres  :5432 ✅
    finanalytics_timescale :5433 ✅
    finanalytics_redis     :6379 ✅
    finanalytics_api       :8000 ✅

# Contexto do Projeto — finanalytics_ai
> Atualizado em 2026-04-03. Cole este arquivo no início de uma nova conversa para retomar de onde paramos.

## Projeto
- **Nome:** finanalytics_ai_fresh
- **Caminho:** `D:\Projetos\finanalytics_ai_fresh`
- **Stack:** Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy 2.x async · asyncpg · uv · Docker Compose
- **Ambiente:** Windows 11 · PowerShell 7.5 · Python 64-bit · Docker rodando localmente
- **Package manager:** uv (PEP 735 — `[dependency-groups] dev`)
- **Interpreter:** `.venv` na raiz do projeto (Sources Root: `src/`)

## Infraestrutura Docker
| Container | Imagem | Porta |
|---|---|---|
| finanalytics_postgres | postgres:16-alpine | 5432 |
| finanalytics_timescale | timescaledb:latest-pg16 | 5433 |
| finanalytics_redis | redis:7.2-alpine | 6379 |
| finanalytics_kafka | cp-kafka:7.6.1 | 9092 |
| finanalytics_api | finanalytics-ai:latest | 8000 |
| finanalytics_worker | finanalytics-worker:latest | — |
| finanalytics_scheduler | finanalytics-worker:latest | — |

## Variáveis de ambiente
- **`.env`** — valores Docker/produção (`@postgres:5432`, `@timescale:5432`)
- **`.env.local`** — dev local, sobrescreve `.env` (`@localhost:5432`, `@localhost:5433`)
  - `.env.local` está no `.gitignore` — nunca commitado
  - Para carregar no terminal: `Get-Content .env.local | ForEach-Object { if ($_ -match "^([^#=\s][^=]*)=(.*)$") { [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process") } }`

## Arquitetura — Event Processor V2 (pipeline principal)

### Camadas
```
domain/events/
  models.py          -- DomainEvent, EventPayload, ProcessingResult (dataclasses)
  exceptions.py      -- TransientError, PermanentError, DatabaseError, etc.
  rules.py           -- Protocol BusinessRule (duck-typed, verificado pelo mypy)
  value_objects.py   -- EventType, CorrelationId

application/event_processor/
  service.py         -- EventProcessorService (orquestrador com OTEL tracing)
  ports.py           -- Protocols: EventRepository, IdempotencyStore, ObservabilityPort
  tracing.py         -- TracingPort + NullTracing + OtelTracing
  factory.py         -- create_event_processor_service() -- DI manual
  config.py          -- EventProcessorConfig (Pydantic BaseSettings, prefixo EVENT_PROCESSOR_)
  rules/
    price_validation.py  -- circuit breaker de preco
    price_update.py      -- persiste no TimescaleDB

infrastructure/event_processor/
  repository.py      -- SqlEventRepository (SQLAlchemy async, session.merge() -- portavel SQLite/PG)
  idempotency.py     -- RedisIdempotencyStore + InMemoryIdempotencyStore
  orm_models.py      -- EventRecord ORM (tabela: event_records)
  mapper.py          -- domain_to_record / record_to_domain
  observability.py   -- PrometheusObservability + NoOpObservability
  consumer.py        -- EventConsumerWorker (AsyncIterator + tenacity retry)

workers/
  event_worker_v2.py      -- loop com SELECT + asyncio.Semaphore(concurrency)
  profit_market_worker.py -- ProfitDLL -> EventConsumerWorker -> EventProcessorService
  fintz_sync_worker.py    -- sync Fintz diario as 22h05 BRT (migrado para container_v2)

observability/
  logging.py         -- structlog JSON/text configuravel
  correlation.py     -- CorrelationMiddleware ASGI + bind_correlation_id()
```

### Container de DI
- `container_v2.py` — wiring do V2: `build_engine_v2`, `build_session_factory_v2`, `build_event_processor_service_v2`, `build_idempotency_store`, `build_tracing`
- `container.py.v1.bak` — arquivado (V1 legado, sem callers ativos)

### Tabelas do Event Processor V2
- `event_records` — criada diretamente (migration 0011_event_records estava fantasma)

## ProfitDLL (Nelogica) — estado atual

### Configuracao
```
PROFIT_DLL_PATH=C:\Nelogica\profitdll.dll   -- DLL 64-bit OK
PROFIT_ACTIVATION_KEY=...                    -- no .env
PROFIT_USERNAME=marceloabisquarisi@gmail.com
PROFIT_TICKERS=PETR4,VALE3,ITUB4,BBDC4,ABEV3,WEGE3,WINFUT,WDOFUT
```

### Status
- DLL carregada e autenticada: `login_connected=True`, `market_login_valid=True`
- `wait_connected()` aceita `market_login_valid=True` como sucesso (fix para mercado fechado)
- 8 tickers subscritos com sucesso
- Pipeline: ProfitDLL → ProfitDLLMessageSource → EventConsumerWorker → EventProcessorService V2
- **Pendente:** testar ticks ao vivo na segunda-feira com mercado aberto

### Como rodar
```powershell
# Carrega .env.local (localhost)
Get-Content .env.local | ForEach-Object {
    if ($_ -match "^([^#=\s][^=]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
}
$env:LOG_FORMAT="text"
uv run python -m finanalytics_ai.workers.profit_market_worker
```

### Sequencia esperada nos logs
```
profit_market_worker.starting
profit_market_worker.connecting  (timeout=90s)
profit_dll.started
profit_market_worker.dll_connected
database.engine.created
profit_dll.subscribed x8
profit_market_worker.subscribed
consumer.started
profit_dll_source.started
```

### Arquivos relevantes
```
infrastructure/market_data/profit_dll/
  client.py         -- ProfitDLLClient (Windows-only, callbacks ctypes -> asyncio.Queue)
  noop_client.py    -- NoOpProfitClient (Linux/Docker/testes)
  message_source.py -- ProfitDLLMessageSource (AsyncIterator[dict])
  types.py          -- estruturas ctypes
workers/profit_agent.py  -- agent standalone (zero imports projeto, psycopg2 puro)
```

## Fintz — estado atual

### Dados no banco
| Tabela | Linhas |
|--------|--------|
| fintz_cotacoes | 1.320.059 |
| fintz_itens_contabeis | 121.971.684 |
| fintz_indicadores | 46.301.031 |
| fintz_sync_log (ok) | 80/80 |

### Sync automatico
- Worker: `workers/fintz_sync_worker.py` — loop diario as 22h05 BRT
- API trigger manual: `POST /api/v1/fintz/sync/trigger` ou `POST /api/v1/fintz/sync/trigger/{key}`
- Status: `GET /api/v1/fintz/sync/status`

## Testes

### Comandos
```powershell
# Suite completa (exceto test_market_data_client.py -- 4 falhas pre-existentes)
uv run pytest tests/unit/ tests/integration_sqlite/ --ignore=tests/unit/infrastructure/test_market_data_client.py --tb=no -q

# So event processor V2
uv run pytest tests/unit/application/event_processor/ -v

# Integracao SQLite (sem banco real)
uv run pytest tests/integration_sqlite/ -v
```

### Estado
- **1221 passando** (excluindo test_market_data_client.py)
- **6 integration SQLite** passando (idempotencia, retry, sucesso)
- **4 falhas pre-existentes** em test_market_data_client.py: CompositeMarketDataClient passou a usar Fintz como fonte primaria mas os mocks ainda cobrem apenas BrapiClient/Yahoo

## pyproject.toml — estrutura de deps
```toml
[dependency-groups]
dev = [
    "pytest>=8.2.0", "pytest-asyncio>=0.23.0", "pytest-cov>=7.0.0",
    "httpx>=0.27.0", "aiosqlite>=0.22.1",
    "mypy>=1.10.0", "ruff>=0.15.0", "black>=24.4.0",
    "opentelemetry-sdk>=1.20.0", "pyarrow>=16.0.0", "tenacity>=9.1.4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"  # obrigatorio para pytest-asyncio 1.x
```

## Migracao V1 -> V2 — estado

### Migrados
| Arquivo | O que mudou |
|---------|-------------|
| `admin_events.py` | `EventId.from_str()` -> `uuid.UUID()` |
| `app.py` | `container` -> `container_v2` |
| `fintz_sync_worker.py` | `container` -> `container_v2` |
| `container.py` | Arquivado como `container.py.v1.bak` |

### Mantidos em V1 (migracao futura)
| Arquivo | Motivo |
|---------|--------|
| `domain/events/entities.py` | Necessario para as regras Fintz (EventType StrEnum vs value object) |
| `application/rules/fintz_*.py` | Usam EventType V1 StrEnum — migrar junto com o pipeline Fintz |
| `application/services/event_processor.py` | EventProcessor V1 para market events com BrapiClient — servico diferente do V2 |
| `application/services/event_publisher.py` | Usado pelo EventProcessor V1 |
| `infrastructure/database/repositories/event_repository.py` | Repositorio V1 (tabelas events + event_processing_records) |

### Proxima sprint de consolidacao V1
1. Migrar `fintz_*.py` rules de `entities.EventType` (StrEnum) para `value_objects.EventType` (value object)
2. Migrar `dependencies.py` `get_event_processor` para V2
3. Remover `entities.py` apos migracao completa

## Falhas conhecidas / pendencias

| Item | Status | Acao |
|------|--------|------|
| `test_market_data_client.py` (4 falhas) | Pre-existente | Atualizar mocks para cobrir Fintz como fonte primaria |
| ProfitDLL ticks ao vivo | Pendente | Testar segunda-feira com mercado aberto |
| `PROFIT_TIMESCALE_DSN` no .env.local | Corrigido | `@localhost:5433` -- fix ja aplicado |
| Regras Fintz em V1 | Decisao arquitetural | Manter ate sprint de migracao coordenada |

## Run configs PyCharm (.run/)
| Config | Comando |
|--------|---------|
| API (uvicorn debug) | `uvicorn finanalytics_ai.interfaces.api.app:create_app --reload` |
| Event Worker V2 | `python -m finanalytics_ai.workers.event_worker_v2` |
| Profit Market Worker | `python -m finanalytics_ai.workers.profit_market_worker` |
| pytest unit | `pytest tests/unit/ -v --tb=short -q` |
| pytest integration v2 | `pytest tests/integration_sqlite/ -v --tb=short` |
| pytest coverage | `pytest --cov` |
| mypy | `mypy src/` |
| ruff check | `ruff check src/ tests/` |
| ruff fix | `ruff check src/ tests/ --fix` |

## Proximos passos sugeridos
1. **Segunda-feira:** validar ticks ProfitDLL ao vivo (mercado aberto)
2. **Sprint Fintz rules V2:** migrar `fintz_*.py` para `value_objects.EventType`
3. **Sprint test_market_data_client:** atualizar mocks para CompositeMarketDataClient com Fintz
4. **Sprint features:** ML/Forecast wiring, screener atualizado, alertas em tempo real

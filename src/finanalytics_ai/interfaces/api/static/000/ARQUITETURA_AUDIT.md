# finanalytics_ai — Auditoria Arquitetural e Plano de Sprints
> Gerado em 2026-04-03 | Stack: Python 3.12 · FastAPI · SQLAlchemy 2.x async · uv

---

## 1. Estado Atual — O que já existe (e está bom)

O projeto está substancialmente implementado. Abaixo o inventário honesto:

### ✅ Infraestrutura de Projeto (A)

| Item | Status | Detalhe |
|------|--------|---------|
| uv como package manager | ✅ Completo | `pyproject.toml` + `uv.lock` corretos |
| pytest configurado | ✅ Completo | `asyncio_mode = "auto"`, markers, `--strict-markers` |
| mypy strict | ✅ Completo | `strict = true`, `pydantic.mypy` plugin, overrides corretos |
| ruff + black | ✅ Completo | `line-length = 100`, `ANN`, `TCH`, `UP` rules |
| .env.example | ✅ Completo | Documentado por seção, PyCharm EnvFile mencionado |
| PyCharm .run/ | ✅ Completo | 7 run configs (pytest, uvicorn, mypy, ruff, coverage) |
| Sources Root | ✅ Completo | `src/` layout com `hatchling`, `PYTHONPATH=$PROJECT_DIR$/src` |

### ✅ Arquitetura de Domínio (B + C)

A separação de camadas está correta e bem fundamentada:

```
src/finanalytics_ai/
├── domain/events/         # Entidades puras — sem imports de infra
│   ├── models.py          # DomainEvent, EventPayload, ProcessingResult (dataclasses)
│   ├── exceptions.py      # Hierarquia TransientError / PermanentError
│   ├── rules.py           # Protocol BusinessRule (duck-typing verificado pelo mypy)
│   └── value_objects.py   # EventType, CorrelationId
├── application/event_processor/
│   ├── service.py         # EventProcessorService — orquestrador
│   ├── ports.py           # Protocols: EventRepository, IdempotencyStore, ObservabilityPort
│   ├── tracing.py         # TracingPort + NullTracing + OtelTracing
│   ├── factory.py         # create_event_processor_service() — DI manual
│   ├── config.py          # EventProcessorConfig (Pydantic BaseSettings)
│   └── rules/
│       ├── price_update.py     # PriceUpdateRule — persiste no TimescaleDB
│       └── price_validation.py # PriceValidationRule — circuit breaker
├── infrastructure/event_processor/
│   ├── repository.py      # SqlEventRepository (SQLAlchemy async + merge/upsert)
│   ├── idempotency.py     # RedisIdempotencyStore + InMemoryIdempotencyStore
│   ├── orm_models.py      # EventRecord ORM
│   ├── mapper.py          # domain_to_record / record_to_domain
│   └── observability.py   # PrometheusObservability
└── workers/
    └── event_worker.py    # Loop com SELECT FOR UPDATE SKIP LOCKED
```

---

## 2. Problema Crítico Identificado — Dual Event System

### Diagnóstico

Existe **coexistência de dois sistemas de eventos incompatíveis**:

| Sistema | Arquivos | Usado por |
|---------|----------|-----------|
| **V1 (Legacy)** | `application/services/event_processor.py` + `domain/events/entities.py` + `domain/events/ports.py` | `container.py` (root), `event_worker.py`, `application/rules/`, `infrastructure/database/repositories/event_repository.py` |
| **V2 (Novo)** | `application/event_processor/service.py` + `domain/events/models.py` + `domain/events/rules.py` | `infrastructure/event_processor/`, testes unitários do event_processor |

### Impacto

```
container.py (root)
  └── build_event_processor()
        └── EventProcessor (V1)           ← usa Event/EventType de entities.py
              └── PostgresEventRepository ← usa infraestrutura antiga

workers/event_worker.py
  └── build_event_processor()             ← importa container.py (V1)
        └── NÃO usa EventProcessorService ← o sistema com OTEL, TracingPort, etc.
```

**Consequência**: Todos os testes em `tests/unit/application/event_processor/` testam o V2, mas o que roda em produção é o V1. OTEL tracing, `IdempotencyStore` como port separado, `NullTracing` — nada disso está no path de produção.

---

## 3. Plano de Sprints

### Sprint 1 — Consolidação (CRÍTICO)
**Objetivo**: Fazer o worker de produção usar o V2 `EventProcessorService`.

Entregável: `finanalytics_sprint1_consolidacao.ps1`

- [ ] Novo `src/finanalytics_ai/container_v2.py` — wiring do V2
- [ ] Atualizar `workers/event_worker.py` para usar factory V2
- [ ] Adicionar `asyncio.Semaphore` para controle de concorrência
- [ ] Remover import circulares residuais

### Sprint 2 — Hardening do Worker
**Objetivo**: Worker production-grade com concorrência, graceful shutdown e métricas.

Entregável: `finanalytics_sprint2_worker.ps1`

- [ ] `asyncio.Semaphore(concurrency)` no loop principal
- [ ] Graceful drain: aguardar tasks ativas antes de exit
- [ ] Prometheus metrics para o worker loop (lag, throughput)
- [ ] Health check endpoint do worker

### Sprint 3 — Testes de Integração
**Objetivo**: Testes com banco real usando pytest-asyncio + aiosqlite.

Entregável: `finanalytics_sprint3_tests.ps1`

- [ ] `conftest.py` com SQLite async para SqlEventRepository
- [ ] Teste de idempotência end-to-end
- [ ] Teste de retry com TransientError
- [ ] Teste de dead-letter com MaxRetriesExceeded

### Sprint 4 — Observabilidade Completa
**Objetivo**: OtelTracing wiring no container + Prometheus metrics no worker.

Entregável: `finanalytics_sprint4_otel.ps1`

- [ ] `OtelTracing` injetado via container quando `TRACING_ENABLED=true`
- [ ] `PrometheusObservability` com labels `event_type`
- [ ] Structured logging com `correlation_id` propagado nos spans

---

## 4. Decisões Arquiteturais — Defesa em Code Review

### 4.1 Por que `Protocol` em vez de `ABC` para `BusinessRule` e ports?

**ABC** obriga herança explícita — viola Dependency Inversion porque a infra passaria a depender do domínio para herdar. **Protocol** é estruturalmente tipado: qualquer classe que implemente os métodos satisfaz o contrato, verificado pelo mypy em tempo de análise. Isso significa que `FakeEventRepository` nos testes não precisa importar nada do domínio além dos types que usa.

**Trade-off**: Protocol não lança `TypeError` em runtime se a interface não for satisfeita — mitiga-se com `runtime_checkable` + testes que instanciam o fake diretamente.

### 4.2 Por que `dataclasses` no domínio e `Pydantic` na borda?

`DomainEvent` usa `@dataclass` porque o domínio não deve saber nada sobre serialização JSON, validação HTTP, ou formatos de banco. Pydantic é uma dependência de infraestrutura. Já `EventProcessorConfig` usa `BaseSettings` porque está na borda do sistema — é o ponto onde variáveis de ambiente entram.

**Trade-off**: Duas formas de definir "modelos de dados" no mesmo codebase pode confundir. A regra é simples: se o objeto cruza uma fronteira de I/O (HTTP, DB, .env), usa Pydantic. Se fica no domínio, usa dataclass.

### 4.3 Por que `SELECT FOR UPDATE SKIP LOCKED` em vez de filas (Kafka/Redis)?

Para o volume atual (<1k eventos/min), o lock otimista no Postgres elimina um componente de infraestrutura inteiro (broker). Dois workers concorrentes nunca pegam o mesmo evento. A sessão do SKIP LOCKED é deliberadamente curtíssima — commit imediato libera o lock antes do processamento, evitando contention.

**Trade-off**: Não escala para >10k eventos/min. Se chegar lá, migrar para ARQ (Redis-backed) ou aiokafka é trivial porque o contrato `BusinessRule` não muda — só o `consumer.py` na infra.

### 4.4 Por que DI manual em vez de um container como `dependency-injector`?

Com <10 dependências no service principal, um `container_v2.py` com funções puras `build_*()` é mais legível e debugável do que uma DSL proprietária. O PyCharm consegue rastrear "go to definition" de qualquer dependência. Em code review, o reviewer vê o grafo completo de dependências em ~50 linhas.

**Trade-off**: Verboso se o grafo crescer muito. Threshold para adotar um container: quando `build_*()` tiver mais de 3 níveis de dependências aninhadas.

### 4.5 Por que `InMemoryIdempotencyStore` além de `RedisIdempotencyStore`?

Redis é um ponto de falha em dev. Com `InMemory`, o pipeline de testes unitários roda sem infraestrutura externa. Em produção, Redis é preferido pela atomicidade do `SET NX EX`. O ponto importante: a escolha é feita no container/DI, não no domínio — a lógica de negócio nunca sabe qual backend está sendo usado.

---

## 5. Trade-offs e Alternativas Consideradas

| Decisão | Alternativa rejeitada | Motivo da rejeição |
|---------|----------------------|-------------------|
| `asyncio.Semaphore` para concorrência | `asyncio.gather` sem limite | Sem backpressure — estouraria conexões do pool |
| `structlog` para logging | `logging` stdlib | Sem suporte nativo a JSON structured logging |
| `tenacity` para retry (config) | Retry manual com `for i in range(max)` | Mais propenso a bugs de off-by-one e falta de jitter |
| `Pydantic BaseSettings` para config | `os.getenv()` manual | Sem validação de tipos, sem documentação automática |
| `hatchling` como build backend | `setuptools` | Mais rápido, zero-config com `src/` layout |

---

## 6. Instruções PyCharm

### Configurar o interpreter (uv)
```
Settings → Project → Python Interpreter → Add → Existing Environment
→ Aponte para: .venv/Scripts/python.exe  (gerado por: uv sync --dev)
```

### Marcar Sources Root
```
Clique com botão direito em src/ → Mark Directory as → Sources Root
Clique com botão direito em tests/ → Mark Directory as → Test Sources Root
```

### .env no PyCharm (dois métodos)

**Método 1 — Plugin EnvFile** (recomendado):
```
Marketplace → instale "EnvFile" → Em cada Run Config → "EnvFile" tab → Enable → + → selecione .env
```

**Método 2 — Nativo (Environment Variables)**:
```
Run Config → Environment Variables → Load from file → .env
```

### Debugar o Event Worker
Use a run config `.run/Event Worker.run.xml` já configurada.
Para breakpoints em código async, certifique-se que está em:
```
Settings → Build → Python Debugger → Gevent compatible = OFF
```

### Rodar testes unitários
```
Run Config: "pytest unit"
ou via terminal: uv run pytest tests/unit/ -v --tb=short
```

### Rodar mypy
```
Run Config: "mypy"
ou via terminal: uv run mypy src/
```

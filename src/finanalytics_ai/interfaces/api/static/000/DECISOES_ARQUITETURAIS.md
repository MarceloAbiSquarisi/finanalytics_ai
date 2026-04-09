# DECISOES_ARQUITETURAIS.md
# Referência rápida para code review — finanalytics_ai Event Processor V2

## Por que cada decisão foi tomada

### 1. `Protocol` em vez de `ABC` para `BusinessRule` e ports

ABC obriga herança explícita — viola Dependency Inversion porque a infra passaria a depender do domínio para herdar. `Protocol` é estruturalmente tipado: qualquer classe que implemente os métodos satisfaz o contrato, verificado pelo mypy sem import do domínio.

Resultado prático: `FakeEventRepository` nos testes não importa nada da infra. `InMemoryIdempotencyStore` implementa o mesmo contrato de `RedisIdempotencyStore` sem herança compartilhada.

**Trade-off aceitado**: Protocol não lança `TypeError` em runtime se a interface não for satisfeita. Mitigado com `runtime_checkable` + testes que instanciam fakes diretamente.

---

### 2. `dataclasses` no domínio, `Pydantic` na borda

`DomainEvent` usa `@dataclass` porque o domínio não deve saber nada sobre serialização JSON, validação HTTP, ou formatos de banco. Pydantic é infraestrutura.

`EventProcessorConfig` usa `BaseSettings` porque está na borda — é o ponto onde variáveis de ambiente entram no sistema.

**Regra de bolso**: se o objeto cruza uma fronteira de I/O (HTTP, banco, .env), usa Pydantic. Se fica no domínio, usa dataclass.

---

### 3. `session.merge()` em vez de `INSERT ON CONFLICT`

`session.merge()` é portável entre PostgreSQL e SQLite. Isso permite rodar testes de integração com `aiosqlite` sem banco real, eliminando dependência de infraestrutura no CI.

**Trade-off aceitado**: Em PostgreSQL com alto volume, `INSERT ... ON CONFLICT DO UPDATE` seria ~15% mais eficiente. Threshold para trocar: >50k eventos/min. Abaixo disso, a diferença não justifica a perda de portabilidade.

---

### 4. `SELECT FOR UPDATE SKIP LOCKED` em vez de filas (Kafka/Redis)

Para o volume atual (<1k eventos/min), elimina um componente de infraestrutura inteiro. Dois workers concorrentes nunca pegam o mesmo evento. A sessão do lock é deliberadamente curtíssima — commit imediato libera o lock antes do processamento.

**Trade-off aceitado**: Não escala para >10k eventos/min. Migrar para ARQ (Redis-backed) ou aiokafka é trivial porque o contrato `BusinessRule` não muda — só o consumer na infra.

---

### 5. `asyncio.Semaphore` para concorrência no worker

Sem backpressure, `asyncio.gather` sem limite estouraria o pool de conexões do banco com N eventos simultâneos. `Semaphore(concurrency)` garante que no máximo `EVENT_PROCESSOR_CONCURRENCY` coroutines processem eventos ao mesmo tempo.

**Dimensionamento**: `concurrency <= database_pool_size`. O valor padrão é 10, que combina com `DATABASE_POOL_SIZE=10` no .env.example.

---

### 6. DI manual em vez de container como `dependency-injector`

Com <10 dependências no service principal, funções puras `build_*()` em `container_v2.py` são mais legíveis e debugáveis do que uma DSL proprietária. O PyCharm consegue rastrear "go to definition" de qualquer dependência.

**Threshold para mudar**: quando `build_*()` tiver mais de 3 níveis de dependências aninhadas, ou quando >15 serviços precisarem de injeção.

---

### 7. `InMemoryIdempotencyStore` para dev/test, `RedisIdempotencyStore` para prod

Redis é ponto de falha em dev. Com `InMemory`, o pipeline de testes unitários roda sem infraestrutura. A escolha é feita no container/DI — a lógica de negócio nunca sabe qual backend está sendo usado.

**Limitação do InMemory**: sem TTL real — eventos antigos nunca expiram na memória. Aceitável em dev onde o processo é reiniciado frequentemente.

---

### 8. `structlog` + `contextvars` para correlation_id

Em uma call chain async longa (middleware → route → service → rule → infra), passar `correlation_id` por parâmetro em cada função viola o princípio de que é um cross-cutting concern. `structlog.contextvars.bind_contextvars()` resolve de forma async-safe: cada task asyncio tem seu próprio contexto.

**Cuidado**: sempre chamar `clear_correlation_id()` no finally após processar um evento/request. O `CorrelationMiddleware` faz isso automaticamente para requests HTTP.

---

### 9. Dual system V1/V2 — migração incremental

O sistema V1 (`EventProcessor` + `entities.py`) continua funcionando em produção enquanto o V2 é validado. Isso evita big-bang migrations. O worker V2 é um serviço separado que pode ser desligado sem afetar o V1.

**Plano de remoção do V1**: após 1 semana de operação estável do V2 em produção, remover:
- `application/services/event_processor.py`
- `domain/events/entities.py` (consolidar em `models.py`)
- `domain/events/ports.py` (old-style)
- `container.py` (root level)
- `workers/event_worker.py` (renomear v2 para o nome original)

---

## Checklist de Code Review

Antes de aprovar um PR que toca o event processor, verificar:

- [ ] Novas regras de negócio implementam o `Protocol BusinessRule` (mypy verifica)
- [ ] Regras que dependem de I/O recebem dependencies pelo `__init__` (não importam globals)
- [ ] Erros de negócio esperados retornam `ProcessingResult.failure()` (não exceções)
- [ ] Erros de infraestrutura levantam `TransientError` ou `PermanentError` (não `Exception`)
- [ ] Novos campos de configuração estão em `EventProcessorConfig` com `Field(description=...)`
- [ ] Testes unitários usam `FakeEventRepository` e `FakeIdempotencyStore` (não mocks)
- [ ] Testes de integração usam `aiosqlite` (não banco real)
- [ ] `correlation_id` é propagado no `bind_correlation_id()` antes do processamento

# FinAnalytics AI

> Framework de Análise e Busca de Investimentos com IA

![FinAnalytics AI](docs/logo.png)

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Runtime | Python 3.12+ |
| Async DB | SQLAlchemy (asyncio) + asyncpg |
| Validação | Pydantic v2 |
| Logging | structlog (JSON) |
| Resiliência | tenacity |
| Observabilidade | OpenTelemetry + Prometheus |
| Linting | ruff |
| Type check | mypy (strict) |
| Testes | pytest + pytest-asyncio |

---

## Setup Rápido

### 1. Usando `uv` (recomendado — mais rápido)

```bash
# Instalar uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Criar venv e instalar dependências
uv venv .venv --python 3.12
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

uv pip install -e ".[dev]"
```

### 2. Usando `poetry`

```bash
poetry install --with dev
poetry shell
```

### 3. Configurar ambiente

```bash
cp .env.example .env
# Edite .env com suas credenciais
```

---

## Rodar

```bash
# Aplicação principal
python -m finanalytics_ai.main

# Testes
pytest

# Testes sem I/O (apenas unit)
pytest -m unit

# Type check
mypy src/

# Linting
ruff check src/ tests/
ruff format src/ tests/
```

---

## PyCharm — Configuração Específica

### Sources Root
Clique com botão direito em `src/` → **Mark Directory as → Sources Root**

Isso garante que `finanalytics_ai` seja resolvido corretamente nos imports.

### EnvFile Plugin
1. Instale o plugin **EnvFile** (Settings → Plugins)
2. Em cada Run Configuration → aba **EnvFile** → adicione `.env`

### Run Configuration (main.py)
- **Script path**: `src/finanalytics_ai/main.py`
- **Working directory**: `<raiz do projeto>`
- **Python interpreter**: `.venv/bin/python`
- **EnvFile**: `.env`

### Run Configuration (pytest)
- **Target**: `tests/`
- **Additional args**: `-v --tb=short`
- **Working directory**: `<raiz do projeto>`
- **EnvFile**: `.env`

### mypy no PyCharm
Settings → Editor → Inspections → Python → Mypy:
- Habilite "Use mypy"
- Mypy executable: `.venv/bin/mypy`

---

## Arquitetura

```
src/finanalytics_ai/
├── domain/          # Regras de negócio puras. Sem I/O.
│   ├── entities/    # Aggregate roots (Portfolio, MarketEvent)
│   ├── value_objects/ # Imutáveis: Money, Ticker, Quantity
│   ├── rules/       # Regras desacopladas: StopLoss, RuleChain
│   └── ports/       # Protocols: MarketDataProvider, EventStore
│
├── application/     # Casos de uso. Orquestra domínio + ports.
│   ├── commands/    # DTOs de entrada (imutáveis)
│   ├── handlers/    # Handlers de comando
│   └── services/    # EventProcessorService, PortfolioService
│
├── infrastructure/  # Implementações concretas dos ports.
│   ├── database/    # SQLAlchemy + repositories
│   ├── adapters/    # BrapiClient, XPClient, BTGClient
│   └── queue/       # InMemoryQueue, RedisQueue
│
└── interfaces/      # Entrypoints: API REST, CLI
```

### Decisões Arquiteturais

**Por que Clean Architecture?**
O domínio financeiro muda por regulação, não por tecnologia.
Separar domínio de infra permite trocar o banco, a API de mercado
ou o broker sem tocar nas regras de negócio.

**Por que Protocol ao invés de ABC?**
Structural subtyping do Python: qualquer classe que implemente
os métodos é compatível. Sem herança forçada = menos acoplamento.
O mypy valida em tempo de checagem estática.

**Por que Decimal ao invés de float?**
`0.1 + 0.2 != 0.3` em float. Para cálculos financeiros,
Decimal com quantize() garante precisão e arredondamento correto.

**Por que tenacity ao invés de retry manual?**
Backoff exponencial, jitter, configuração declarativa e
integração com async. Código de retry manual é verboso e propenso a bugs.

**Trade-offs:**
- `InMemoryEventQueue`: simples para dev, sem persistência. Em prod: Redis Streams
- `structlog` vs `logging`: mais verboso para configurar, mas JSON out-of-box
- `SQLAlchemy async` vs `asyncpg raw`: mais overhead, mas migrations + type safety

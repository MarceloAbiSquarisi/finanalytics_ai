# ──────────────────────────────────────────────────────────────────────────────
# Makefile — finanalytics_ai
#
# Uso:
#   make install       instala dependências (dev)
#   make test          roda testes unitários
#   make test-all      roda unit + integration (precisa de banco)
#   make lint          ruff + black check
#   make fmt           formata código com black
#   make typecheck     mypy
#   make check         lint + typecheck + test (pre-commit / CI)
#   make worker        sobe event_worker localmente
#   make sync          roda fintz_sync_worker em modo RUN_ONCE
#   make migration     cria nova migration (NAME=nome_da_migration)
#   make migrate       aplica todas as migrations pendentes
#   make clean         remove arquivos temporários
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: install test test-all lint fmt typecheck check worker sync \
        migration migrate clean publish-test-events

PYTHON   := uv run python
PYTEST   := uv run python -m pytest
MYPY     := uv run mypy
RUFF     := uv run ruff
BLACK    := uv run black
ALEMBIC  := uv run alembic

# Variáveis de ambiente mínimas para comandos que não precisam de banco
UNIT_ENV := \
	DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" \
	APP_SECRET_KEY="dev-secret-key-local" \
	LOG_LEVEL="ERROR" \
	METRICS_ENABLED="false"

# ── Instalação ─────────────────────────────────────────────────────────────────

install:
	uv pip install -e ".[dev]"

# ── Testes ─────────────────────────────────────────────────────────────────────

test:
	$(UNIT_ENV) $(PYTEST) tests/unit/ -v --tb=short

test-cov:
	$(UNIT_ENV) $(PYTEST) tests/unit/ \
		--cov=src/finanalytics_ai \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=80

test-all:
	$(PYTEST) tests/ -v --tb=short -m "not slow"

test-integration:
	$(PYTEST) tests/integration/ -v --tb=short -m integration

# ── Qualidade de código ────────────────────────────────────────────────────────

lint:
	$(RUFF) check src/ tests/
	$(BLACK) --check src/ tests/

fmt:
	$(RUFF) check --fix src/ tests/
	$(BLACK) src/ tests/

typecheck:
	$(MYPY) src/

# Roda lint + typecheck + testes unit — para usar antes de push
check: lint typecheck test
	@echo "✓ Tudo OK — pronto para commit"

# ── Workers ────────────────────────────────────────────────────────────────────

worker:
	$(PYTHON) -m finanalytics_ai.workers.event_worker

sync:
	RUN_ONCE=true $(PYTHON) -m finanalytics_ai.workers.fintz_sync_worker

# ── API ────────────────────────────────────────────────────────────────────────

api:
	uv run uvicorn finanalytics_ai.app:app --reload --host 0.0.0.0 --port 8000

# ── Database ───────────────────────────────────────────────────────────────────

migrate:
	$(ALEMBIC) upgrade head

# Uso: make migration NAME=add_alert_table
migration:
	$(ALEMBIC) revision --autogenerate -m "$(NAME)"

db-current:
	$(ALEMBIC) current

db-history:
	$(ALEMBIC) history --verbose

# ── Debug scripts ──────────────────────────────────────────────────────────────

# Uso: make publish-test-events TYPE=completed DATASET=cotacoes COUNT=5
publish-test-events:
	$(PYTHON) scripts/publish_test_events.py \
		--type $(or $(TYPE),completed) \
		--dataset $(or $(DATASET),cotacoes) \
		--count $(or $(COUNT),3)

validate-pipeline:
	$(PYTHON) scripts/validate_event_pipeline.py

# ── Docker ─────────────────────────────────────────────────────────────────────

docker-build:
	docker build -f Dockerfile.event_worker -t finanalytics-event-worker:latest .

docker-worker:
	docker compose -f docker-compose.yml -f docker-compose.event_worker.yml \
		up -d event_worker

docker-logs:
	docker logs finanalytics_event_worker --follow --tail 50

# ── Limpeza ────────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov .coverage .mypy_cache .ruff_cache .pytest_cache
	@echo "✓ Limpo"

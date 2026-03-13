# ──────────────────────────────────────────────────────────────────────────────
# FinAnalytics AI — Makefile
#
# Stacks disponíveis:
#   make up          → API + Postgres + Redis (dev, hot reload, ~10s)
#   make up-full     → stack completa: + Kafka + TimescaleDB
#   make up-obs      → + Prometheus + Grafana (sobre a stack atual)
#   make up-prod     → produção (sem override dev)
#   make down        → para e remove containers (mantém volumes)
#   make clean       → para E remove volumes (limpa dados)
#
# Build:
#   make build       → reconstrói imagem da API
#   make build-worker → reconstrói imagem do worker
#   make build-all   → reconstrói API + worker
#
# Dev:
#   make logs        → tail logs da API
#   make logs-worker → tail logs do worker
#   make shell       → bash na API
#   make psql        → psql no PostgreSQL
#   make test        → pytest dentro do container
#   make lint        → ruff + mypy
#   make health      → health check manual
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: up up-full up-obs up-prod down clean \
        build build-worker build-all \
        logs logs-worker logs-all shell psql kafka-topics \
        test lint health status help

# ── Setup automático de .env ───────────────────────────────────────────────────
.env:
	@echo "⚠️  .env não encontrado — copiando .env.docker como base"
	cp .env.docker .env
	@echo "✅ .env criado. Edite BRAPI_TOKEN e APP_SECRET_KEY antes de subir."
	@echo ""

# ── Stacks ────────────────────────────────────────────────────────────────────

## [Stack] API + Postgres + Redis (dev rápido, hot reload)
up: .env
	docker compose up -d postgres redis api worker
	@echo ""
	@echo "✅ Stack dev subindo... aguarde ~15s para os healthchecks."
	@echo "   API:  http://localhost:${API_PORT:-8000}"
	@echo "   Docs: http://localhost:${API_PORT:-8000}/docs"
	@echo "   Logs: make logs"

## [Stack] Stack completa: + Kafka + TimescaleDB
up-full: .env
	docker compose up -d
	@echo ""
	@echo "✅ Stack completa subindo... aguarde ~60s (Kafka demora)."
	@echo "   API:  http://localhost:${API_PORT:-8000}"
	@echo "   Kafka: localhost:${KAFKA_PORT:-9092}"

## [Stack] + Prometheus (9090) + Grafana (3000)
up-obs: .env
	docker compose -f docker-compose.yml \
	               -f docker-compose.override.yml \
	               -f docker-compose.observability.yml up -d
	@echo ""
	@echo "✅ Stack com observabilidade:"
	@echo "   Prometheus: http://localhost:9090"
	@echo "   Grafana:    http://localhost:3000  (admin/admin)"

## [Stack] Modo produção (sem overrides de dev)
up-prod: .env
	docker compose -f docker-compose.yml up -d
	@echo "✅ Produção subindo (sem hot reload)."

## Para containers (mantém volumes de dados)
down:
	docker compose down

## Para e remove TODOS os volumes (⚠️ dados apagados)
clean:
	docker compose down -v
	@echo "⚠️  Volumes removidos — dados apagados."

# ── Build ──────────────────────────────────────────────────────────────────────

## Reconstrói imagem da API sem cache
build:
	docker compose build --no-cache api

## Reconstrói imagem do worker sem cache
build-worker:
	docker build --target worker -t finanalytics-worker:latest --no-cache .

## Reconstrói API + worker
build-all:
	docker compose build --no-cache api
	docker build --target worker -t finanalytics-worker:latest --no-cache .

# ── Logs ──────────────────────────────────────────────────────────────────────

## Logs da API em tempo real
logs:
	docker compose logs -f api

## Logs do worker em tempo real
logs-worker:
	docker compose logs -f worker

## Logs de todos os serviços
logs-all:
	docker compose logs -f

# ── Dev ───────────────────────────────────────────────────────────────────────

## Shell bash na API
shell:
	docker compose exec api bash

## psql no PostgreSQL
psql:
	docker compose exec postgres psql -U finanalytics -d finanalytics

## Lista tópicos Kafka (requer up-full)
kafka-topics:
	docker compose exec kafka kafka-topics \
	    --bootstrap-server localhost:9092 --list

## Roda pytest dentro do container da API
test:
	docker compose run --rm api \
	    python -m pytest tests/ -v --tb=short

## Lint: ruff + mypy
lint:
	docker compose run --rm api \
	    sh -c "ruff check src/ && mypy src/ --no-error-summary"

## Status de todos os containers
status:
	docker compose ps

## Health check manual da API
health:
	@curl -sf http://localhost:${API_PORT:-8000}/health | python -m json.tool \
	    || echo "❌ API não respondeu em http://localhost:${API_PORT:-8000}/health"

## Mostra este help
help:
	@echo "FinAnalytics AI — comandos disponíveis:"
	@echo ""
	@grep -E '^## ' Makefile | sed 's/## /  /'

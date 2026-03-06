# ──────────────────────────────────────────────────────────────────────────────
# FinAnalytics AI — Makefile
#
# Comandos:
#   make up          → sobe a stack completa (modo dev com hot reload)
#   make up-prod     → sobe em modo produção (sem override dev)
#   make up-platform → sobe usando infra do finanalytics-platform
#   make down        → para e remove containers (mantém volumes)
#   make clean       → para, remove containers E volumes
#   make logs        → tail dos logs da API
#   make shell       → bash dentro do container da API
#   make psql        → psql no PostgreSQL
#   make kafka-topics → lista tópicos Kafka
#   make build       → reconstrói a imagem
#   make test        → roda pytest dentro do container
#   make lint        → ruff + mypy
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: up up-prod up-platform down clean logs shell psql kafka-topics build test lint help

# Copia .env.docker se .env não existir
.env:
	@echo "⚠️  .env não encontrado — copiando .env.docker"
	cp .env.docker .env
	@echo "✅ .env criado. Edite BRAPI_TOKEN e APP_SECRET_KEY antes de subir."

## Sobe a stack completa (dev: hot reload ativo via override)
up: .env
	docker compose up -d
	@echo ""
	@echo "✅ Stack subindo... aguarde ~30s para os healthchecks passarem."
	@echo "   Dashboard: http://localhost:8000"
	@echo "   Docs:      http://localhost:8000/docs"
	@echo "   Logs:      make logs"

## Sobe em modo produção (sem docker-compose.override.yml)
up-prod: .env
	docker compose -f docker-compose.yml up -d

## Sobe usando a infra existente do finanalytics-platform
up-platform: .env
	docker compose -f docker-compose.platform.yml up -d

## Para os containers (mantém volumes de dados)
down:
	docker compose down

## Para e remove TODOS os volumes (limpa dados)
clean:
	docker compose down -v
	@echo "⚠️  Volumes removidos — dados apagados."

## Logs da API em tempo real
logs:
	docker compose logs -f api

## Logs de todos os serviços
logs-all:
	docker compose logs -f

## Shell bash dentro da API
shell:
	docker compose exec api bash

## psql no PostgreSQL
psql:
	docker compose exec postgres psql -U finanalytics -d finanalytics

## Lista tópicos Kafka
kafka-topics:
	docker compose exec kafka kafka-topics --bootstrap-server localhost:9092 --list

## Reconstrói a imagem sem cache
build:
	docker compose build --no-cache api

## Roda testes dentro do container
test:
	docker compose run --rm api python -m pytest tests/ -v

## Lint: ruff + mypy
lint:
	docker compose run --rm api sh -c "ruff check src/ && mypy src/"

## Status de todos os containers
status:
	docker compose ps

## Healthcheck manual
health:
	curl -s http://localhost:8000/health | python -m json.tool

help:
	@grep -E '^##' Makefile | sed 's/## //'

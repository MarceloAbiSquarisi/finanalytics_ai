.PHONY: setup up down migrate api test lint typecheck

setup:
	uv venv .venv --python 3.12
	. .venv/bin/activate && uv pip install -e ".[dev]"
	cp .env.example .env

up:
	docker compose up -d db redis
	@echo "Aguardando PostgreSQL..."
	@sleep 3
	@$(MAKE) migrate

down:
	docker compose down

migrate:
	. .venv/bin/activate && alembic upgrade head

api:
	. .venv/bin/activate && python -m finanalytics_ai.interfaces.api.run

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check src/ tests/ && ruff format --check src/ tests/

typecheck:
	. .venv/bin/activate && mypy src/

logs:
	docker compose logs -f db

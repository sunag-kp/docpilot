SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
INFRA := postgres redis redpanda minio

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install Python deps (incl. dev) into .venv
	$(UV) sync --all-groups

.PHONY: up
up: ## Build and start the full stack (API, worker, infra, observability)
	$(COMPOSE) up --build -d --wait
	@echo "API:  http://localhost:8000/docs"

.PHONY: down
down: ## Stop the stack and remove volumes
	$(COMPOSE) down -v --remove-orphans

.PHONY: infra
infra: ## Start only the infrastructure needed by integration tests
	$(COMPOSE) up -d --wait $(INFRA)

.PHONY: logs
logs: ## Tail application logs
	$(COMPOSE) logs -f api

.PHONY: lint
lint: ## Ruff lint + format check, mypy, architecture contracts
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy
	$(UV) run lint-imports

.PHONY: fmt
fmt: ## Auto-format and fix lint
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

.PHONY: test
test: ## Unit + integration tests with coverage (starts infra if needed)
	$(MAKE) infra
	$(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: test-unit
test-unit: ## Fast unit tests only (no Docker needed)
	$(UV) run pytest -m "not integration"

.PHONY: smoke
smoke: ## End-to-end smoke test against the running stack
	$(UV) run python scripts/smoke_test.py

.PHONY: migrate
migrate: ## Apply database migrations against DATABASE_URL
	$(UV) run alembic upgrade head

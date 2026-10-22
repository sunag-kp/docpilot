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
	@echo "UI + API:   http://localhost:8000      (OpenAPI docs: /docs)"
	@echo "Grafana:    http://localhost:3000      Jaeger: http://localhost:16686"
	@echo "Prometheus: http://localhost:9090      MinIO:  http://localhost:9001"

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

LOAD_RUN ?= $(shell date -u +%Y-%m-%d)-local
LOAD_DIR := benchmarks/results/$(LOAD_RUN)
LOAD_ENV := RATE_LIMIT_CAPACITY=1000000 RATE_LIMIT_REFILL_PER_SEC=100000 OTEL_TRACES_SAMPLER=parentbased_traceidratio OTEL_TRACES_SAMPLER_ARG=0.05
LOCUST := $(UV) run --group load locust --host http://localhost:8000 --headless --only-summary

.PHONY: load-test
load-test: ## Locust: query API p50/p95 (32 users + 1 user) and ingestion docs/min; results in benchmarks/results/
	$(LOAD_ENV) $(COMPOSE) up --build -d --wait
	mkdir -p $(LOAD_DIR)
	$(LOCUST) -f benchmarks/locustfile_query.py -u 32 -r 8 -t 2m --csv $(LOAD_DIR)/query
	$(UV) run python benchmarks/prom_snapshot.py query $(LOAD_DIR)/query_stages.json --window 2m
	$(LOCUST) -f benchmarks/locustfile_query.py -u 1 -r 1 -t 1m --csv $(LOAD_DIR)/query_single_user
	INGEST_SUMMARY=$(LOAD_DIR)/ingest_throughput.json \
		$(LOCUST) -f benchmarks/locustfile_ingest.py -u 8 -r 8 -t 1m --csv $(LOAD_DIR)/ingest
	$(UV) run python benchmarks/prom_snapshot.py ingest $(LOAD_DIR)/ingest_stages.json --window 6m
	$(UV) run python benchmarks/summarize.py $(LOAD_DIR)

.PHONY: smoke
smoke: ## End-to-end smoke test against the running stack
	$(UV) run python scripts/smoke_test.py

.PHONY: migrate
migrate: ## Apply database migrations against DATABASE_URL
	$(UV) run alembic upgrade head

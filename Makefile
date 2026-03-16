.PHONY: up up-gpu down down-gpu logs logs-gpu cli compile fmt lint test test-integration

COMPOSE := docker compose --env-file .env -f deploy/docker-compose.yml
COMPOSE_GPU := docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml

up:
	@$(COMPOSE) up --build -d

up-gpu:
	@$(COMPOSE_GPU) up --build -d

down:
	@$(COMPOSE) down -v

down-gpu:
	@$(COMPOSE_GPU) down -v

logs:
	@$(COMPOSE) logs -f --tail=200

logs-gpu:
	@$(COMPOSE_GPU) logs -f --tail=200

cli:
	@python3 -m venv .venv && . .venv/bin/activate && pip install -r cli/requirements.txt

compile:
	@python3 -m compileall -q control_plane agent cli tests

fmt:
	@echo "Formatting not configured yet (Milestone 10)."

lint:
	@python3 -m ruff check control_plane agent cli tests

test:
	@python3 -m pytest tests/unit

test-integration:
	@RUN_INTEGRATION=1 python3 -m pytest tests/integration

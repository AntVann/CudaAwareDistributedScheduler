.PHONY: up up-gpu down down-gpu logs logs-gpu cli compile fmt lint test test-integration

up:
	@docker compose -f deploy/docker-compose.yml up --build -d

up-gpu:
	@docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml up --build -d

down:
	@docker compose -f deploy/docker-compose.yml down -v

down-gpu:
	@docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml down -v

logs:
	@docker compose -f deploy/docker-compose.yml logs -f --tail=200

logs-gpu:
	@docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml logs -f --tail=200

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

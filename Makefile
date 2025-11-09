.PHONY: up down logs cli fmt lint

up:
	@docker compose -f deploy/docker-compose.yml up --build -d

down:
	@docker compose -f deploy/docker-compose.yml down -v

logs:
	@docker compose -f deploy/docker-compose.yml logs -f --tail=200

cli:
	@python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

fmt:
	@echo "Formatting not configured yet (Milestone 10)."

lint:
	@echo "Linting not configured yet (Milestone 10)."

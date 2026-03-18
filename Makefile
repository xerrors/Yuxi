
.PHONY: up up-lite down logs lint format

PYTEST_ARGS ?=

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose up -d

down:
	docker compose down

up-lite:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	LITE_MODE=true docker compose up -d postgres redis minio api worker web

logs:
	@docker logs --tail=50 api-dev
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

######################
# LINTING AND FORMATTING
######################

lint:
	uv run python -m ruff check backend/package
	uv run python -m ruff format --check backend/package
	uv run python -m ruff check --select I backend/package

format:
	uv run python -m ruff format backend/package
	uv run python -m ruff check backend/package --fix
	uv run python -m ruff check --select I backend/package --fix
	docker compose exec -T web pnpm run format

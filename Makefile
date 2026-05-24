
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
	LITE_MODE=true VITE_USE_RUNS_API=false docker compose up -d postgres redis minio api web worker

logs:
	@docker logs --tail=50 api-dev
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

######################
# LINTING AND FORMATTING
######################

format:
	cd backend && uv run ruff format package
	cd backend && uv run ruff check package --fix
	cd backend && uv run ruff check --select I package --fix
	cd web && pnpm run format
	cd web && pnpm run lint

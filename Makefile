
.PHONY: up down logs lint format format_diff router-tests

PYTEST_ARGS ?=

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose up -d

down:
	docker compose down

logs:
	@docker logs --tail=50 api-dev
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

######################
# LINTING AND FORMATTING
######################

lint:
	cd backend && uv run ruff check package
	cd backend && uv run ruff format --check package
	cd backend && uv run ruff check --select I package

format:
	cd backend && uv run ruff format package
	cd backend && uv run ruff check package --fix
	cd backend && uv run ruff check --select I package --fix
	docker compose exec -T web pnpm run format

router-tests:
	docker compose exec -T api uv run --group test pytest test/api $(PYTEST_ARGS)

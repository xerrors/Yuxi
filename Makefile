.PHONY: up down logs format lint test audit-dependencies audit-licenses

COMPOSE ?= docker compose --profile container
BACKEND_PYTHON ?= $(shell cat backend/.python-version)

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	@$(COMPOSE) logs --tail=50 api
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

format:
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff format yuxi
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check yuxi --fix
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check --select I yuxi --fix
	cd frontend && pnpm run format
	cd frontend && pnpm run lint

# 只检查不修改，供提交前与 CI 使用（与 ruff.yml 的命令保持一致）
lint:
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check yuxi
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff format yuxi --check
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check --select I yuxi
	cd frontend && pnpm run lint:check

# 后端单元测试（不依赖 docker 服务）；integration/e2e 需在容器环境运行
test:
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run pytest test/unit -m "not slow" $(PYTEST_ARGS)

audit-dependencies:
	cd backend && uv audit --locked --no-dev
	cd packages/yuxi-cli && npm audit --audit-level=high --omit=dev
	cd frontend && pnpm audit --audit-level=high --prod
	cd docs && pnpm audit --audit-level=high --prod
	@if uv audit --script scripts/ci/dependency-audit-fixtures/vulnerable.py > /tmp/yuxi-python-audit-negative.log 2>&1; then echo "Expected the vulnerable Python fixture to fail"; exit 1; fi
	grep -q "aiohttp 3.14.1 has" /tmp/yuxi-python-audit-negative.log
	grep -q "GHSA-cq5v-8q36-5273" /tmp/yuxi-python-audit-negative.log
	bash scripts/ci/dependency-audit-fixtures/run-node-negative-control.sh

audit-licenses:
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run --isolated --no-dev --with pip-licenses pip-licenses --from mixed --format markdown

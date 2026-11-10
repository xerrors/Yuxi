
.PHONY: up down logs lint format seed reset test verify-trust audit-dependencies audit-licenses

COMPOSE ?= docker compose --profile container

PYTEST_ARGS ?=
BACKEND_PYTHON ?= $(shell cat backend/.python-version)

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

reset:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	@if [ -n "$$YUXI_STATE_DIR" ] || grep -Eq '^[[:space:]]*YUXI_STATE_DIR[[:space:]]*=[[:space:]]*[^[:space:]#]' .env; then \
		echo "Refusing to delete an external YUXI_STATE_DIR; stop the slot and remove its exact state directory explicitly." >&2; \
		exit 1; \
	fi
	$(COMPOSE) down
	rm -rf docker/volumes
	$(COMPOSE) up -d
	@echo "Waiting for api to be ready..."
	@until $(COMPOSE) exec -T api true >/dev/null 2>&1; do sleep 2; done
	$(MAKE) seed

logs:
	@$(COMPOSE) logs --tail=50 api
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

seed:
	$(COMPOSE) exec api uv run python scripts/seed_initial_users.py

######################
# LINTING AND FORMATTING
######################

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

verify-trust:
	python3 scripts/ci/verify_engineering_contracts.py
	python3 -m unittest scripts.ci.test_verify_engineering_contracts scripts.ci.test_bump_version

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

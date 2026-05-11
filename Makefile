.PHONY: setup quality quality-full pre-pr lint-fix lint typecheck test-fast test-full
.PHONY: test-stack-up test-stack-down test-all test-cov test-entity openapi
.PHONY: test-int test-bdd test-e2e

setup:
	uv run python scripts/setup-dev.py

# Regenerate the static Tenant Management API OpenAPI spec at
# docs/api/tenant-management-openapi.{json,yaml}. Run this whenever
# you add/change an endpoint, request schema, or response schema in
# src/admin/tenant_management_api.py — the in-sync drift test
# (tests/unit/test_openapi_export_in_sync.py) fails CI otherwise.
openapi:
	uv run python scripts/export_openapi.py

quality:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/ --config-file=mypy.ini
	uv run python .pre-commit-hooks/check_code_duplication.py
	uv run pytest tests/unit/ -x

quality-full:
	$(MAKE) quality
	./run_all_tests.sh ci

pre-pr: quality-full
	@echo ""
	@echo "✅ All CI checks passed — safe to push and create PR"

lint-fix:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src/ --config-file=mypy.ini

test-fast:
	uv run pytest tests/unit/ -x

test-full:
	./run_all_tests.sh ci

# ─── tox-based test targets ──────────────────────────────────────

test-stack-up:
	@echo "Starting Docker test stack..."
	@./scripts/test-stack.sh up

test-stack-down:
	@echo "Stopping Docker test stack..."
	@./scripts/test-stack.sh down

test-all: test-stack-up
	tox -p; rc=$$?; $(MAKE) test-stack-down; exit $$rc

test-cov:
	@echo "Opening coverage report..."
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "Open htmlcov/index.html in your browser"

# ─── Single-suite convenience targets ──────────────────────────
# Usage:
#   make test-int TARGET=tests/integration/test_products.py
#   make test-int TARGET=tests/integration/test_products.py ARGS="-k test_brand -v"
#   make test-bdd TARGET=tests/bdd/ ARGS="-k uc004"
#   make test-e2e TARGET=tests/e2e/test_mcp.py

test-int:
ifndef TARGET
	$(error TARGET is required. Usage: make test-int TARGET=tests/integration/test_file.py)
endif
	scripts/run-test.sh --db $(TARGET) $(ARGS)

test-bdd:
ifndef TARGET
	scripts/run-test.sh --db tests/bdd/ $(ARGS)
else
	scripts/run-test.sh --db $(TARGET) $(ARGS)
endif

test-e2e:
ifndef TARGET
	$(error TARGET is required. Usage: make test-e2e TARGET=tests/e2e/test_file.py)
endif
	scripts/run-test.sh --stack $(TARGET) $(ARGS)

# ─── Docker dev stack rebuild ──────────────────────────────────
# Bypasses a BuildKit cache-mount edge case where ``compose build``
# can reuse a stale install layer after ``uv.lock`` changes — passes
# the lockfile content hash as a build arg so the install step's
# layer key changes whenever lockfile content changes. Use after any
# dependency bump (``uv lock`` / ``uv add`` / ``uv sync``).
# Build args passed to docker compose:
#   LOCKFILE_HASH — invalidates uv install layer on uv.lock change
#   GIT_SHA / GIT_BRANCH — baked into image, surfaced in admin UI footer
COMPOSE_BUILD_ARGS = LOCKFILE_HASH=$$(shasum -a 256 uv.lock | awk '{print $$1}') \
	GIT_SHA=$$(git rev-parse --short=7 HEAD 2>/dev/null || echo unknown) \
	GIT_BRANCH=$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)

compose-build:
	$(COMPOSE_BUILD_ARGS) docker compose build adcp-server

compose-up: compose-build
	$(COMPOSE_BUILD_ARGS) docker compose up -d --force-recreate adcp-server

# ─── Entity-scoped test runs ────────────────────────────────────
# Usage: make test-entity ENTITY=delivery
#        make test-entity ENTITY="creative and unit"
ENTITY ?= ""
test-entity:
	uv run pytest tests/unit/ tests/integration/ tests/e2e/ tests/admin/ -m "$(ENTITY)" -x -v

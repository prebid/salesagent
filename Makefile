.PHONY: setup quality quality-full pre-pr lint-fix lint typecheck test-fast test-full
.PHONY: test-stack-up test-stack-down test-all test-cov test-int test-e2e

setup:
	uv run python scripts/setup-dev.py

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

# ─── single-test convenience targets ────────────────────────────
# Usage:
#   make test-int TARGET=tests/integration/test_products.py
#   make test-int TARGET=tests/integration/test_products.py ARGS="-k test_brand -v"
#   make test-e2e TARGET=tests/e2e/test_mcp.py

test-int:
ifndef TARGET
	$(error TARGET is required. Usage: make test-int TARGET=tests/integration/test_file.py)
endif
	scripts/run-test.sh $(TARGET) $(ARGS)

test-e2e:
ifndef TARGET
	$(error TARGET is required. Usage: make test-e2e TARGET=tests/e2e/test_file.py)
endif
	scripts/run-test.sh $(TARGET) $(ARGS)

.PHONY: setup quality quality-ci quality-full pre-pr lint-fix lint typecheck test-fast test-full
.PHONY: test-stack-up test-stack-down test-all test-cov test-entity
.PHONY: test-int test-bdd test-e2e creative-formats-refresh

setup:
	uv run python scripts/setup-dev.py

# Recapture the reference format fixture from the pinned creative agent. Run only when
# the pin or the agent's catalog changes; the reviewed fixture diff is the drift gate.
# Brings up the pinned agent if needed (idempotent). See issue #1418.
creative-formats-refresh:
	@./scripts/creative-agent-stack.sh up
	uv run python scripts/refresh-reference-formats.py --url $$(./scripts/creative-agent-stack.sh url)

quality-ci:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/ --config-file=mypy.ini
	uv run mypy tests/bdd/steps/_outcome_helpers.py --config-file=mypy.ini --follow-imports=silent --cache-dir=.mypy_cache_tests_gate
	uv run python .pre-commit-hooks/check_code_duplication.py
	uv run python .pre-commit-hooks/check-gam-auth-support.py
	uv run python scripts/hooks/check_response_attribute_access.py $$(find src -name '*.py')
	uv run python .pre-commit-hooks/check_roundtrip_tests.py
	uv run python scripts/verify_feature_error_codes.py --uc UC-002 UC-003
	uv run python scripts/verify_feature_error_codes.py --casing-only
	uv run python .pre-commit-hooks/check_route_conflicts.py
	uv run python .pre-commit-hooks/check_type_ignore_count.py
	uv run python .pre-commit-hooks/check_ruff_complexity_count.py
	uv run python .pre-commit-hooks/check_mypy_untyped_defs_count.py
	uv run python .pre-commit-hooks/check_docs_links.py
	uv run python .pre-commit-hooks/check_hardcoded_urls.py $$(find templates static -type f \( -name '*.html' -o -name '*.js' \) 2>/dev/null)

quality:
	$(MAKE) quality-ci
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
	# tests/ accessor-layer gate (GH #1728 fallout): the ctx accessors in
	# _outcome_helpers feed every BDD step module, so a wrong type there
	# propagates suite-wide. Hard gate (0 errors today). Dedicated cache dir:
	# sharing .mypy_cache with the src/ run reports stale phantom errors;
	# --follow-imports=silent keeps followed-import errors outside the gated
	# file from leaking into this gate. Widening to tests/harness/ (43 errors)
	# and a tests/-wide count ratchet (baseline 427) are tracked follow-ups.
	uv run mypy tests/bdd/steps/_outcome_helpers.py --config-file=mypy.ini --follow-imports=silent --cache-dir=.mypy_cache_tests_gate

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

# ─── Entity-scoped test runs ────────────────────────────────────
# Usage: make test-entity ENTITY=delivery
#        make test-entity ENTITY="creative and unit"
ENTITY ?= ""
test-entity:
	uv run pytest tests/unit/ tests/integration/ tests/e2e/ tests/admin/ -m "$(ENTITY)" -x -v

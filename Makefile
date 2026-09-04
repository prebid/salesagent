.PHONY: setup quality quality-ci quality-full pre-pr lint-fix lint typecheck test-fast test-full
.PHONY: test-stack-up test-stack-down test-all test-cov test-entity
.PHONY: test-int test-bdd test-e2e creative-formats-refresh mutation-check-breaker

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
	uv run ruff check --config ruff-egress.toml --ignore-noqa --no-respect-gitignore src/ scripts/
	uv run mypy src/ --config-file=mypy.ini
	uv run python .pre-commit-hooks/check_code_duplication.py
	uv run python .pre-commit-hooks/check-gam-auth-support.py
	uv run python scripts/hooks/check_response_attribute_access.py $$(find src -name '*.py')
	uv run python .pre-commit-hooks/check_roundtrip_tests.py
	uv run python scripts/verify_feature_error_codes.py --uc UC-002 UC-003
	uv run python .pre-commit-hooks/check_route_conflicts.py
	uv run python .pre-commit-hooks/check_type_ignore_count.py
	uv run python .pre-commit-hooks/check_ruff_complexity_count.py
	uv run python .pre-commit-hooks/check_mypy_untyped_defs_count.py
	uv run python .pre-commit-hooks/check_docs_links.py
	uv run python .pre-commit-hooks/check_hardcoded_urls.py $$(find templates static -type f \( -name '*.html' -o -name '*.js' \) 2>/dev/null)

# tests/harness/ is NOT optional here: tox's `unit` env runs
# `pytest tests/unit/ tests/harness/`, so anything under tests/harness/ was
# graded ONLY by a full-suite run and was invisible to the per-change gate.
# That gap is not theoretical -- tests/harness/test_forward_compat_acceptance.py
# went red on 2026-08-13 and stayed red across 47 commits and 4+ days, because
# every per-bead `make quality` ran a path that did not contain it. Keep this
# target's scope identical to tox's unit env.
quality:
	$(MAKE) quality-ci
	uv run pytest tests/unit/ tests/harness/ -x

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
	uv run mypy src/ .claude/scripts/ scripts/graduate_pending.py --config-file=mypy.ini

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

# ─── Mutation gate (explicitly invoked, never part of a suite) ──
# Proves the e2e_rest circuit-breaker scenario actually grades the DEPLOYED
# server's breaker: deletes circuit_breaker.record_failure() from the server,
# rebuilds its image, and requires the scenario to REDDEN. Deliberately not in
# `quality`, `quality-full` or run_all_tests.sh — it runs the in-network bdd_e2e
# suite twice against two builds. See the script header for why it is a script.
mutation-check-breaker:
	scripts/mutation-check-webhook-breaker.sh

# ─── Entity-scoped test runs ────────────────────────────────────
# Usage: make test-entity ENTITY=delivery
#        make test-entity ENTITY="creative and unit"
ENTITY ?= ""
test-entity:
	uv run pytest tests/unit/ tests/integration/ tests/e2e/ tests/admin/ -m "$(ENTITY)" -x -v

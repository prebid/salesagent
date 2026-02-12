.PHONY: quality quality-full pre-pr lint-fix lint typecheck test-fast test-full

quality:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/ --config-file=mypy.ini
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

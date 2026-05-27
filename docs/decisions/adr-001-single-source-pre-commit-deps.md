# ADR-001 — uv.lock as single source of truth for pre-commit deps

**Status:** Accepted — partially implemented. PR 1 establishes the rule and SHA-freezes all hooks. The `mirrors-mypy` hook still uses `additional_dependencies:` (see `.pre-commit-config.yaml` FIXME comment) because full migration requires restructuring `[project.optional-dependencies]` and wiring the `pydantic.mypy` plugin — scoped to PR 2 of issue #1234.
**Date:** 2026-05
**Deciders:** @chrishuie, @mkostromin-sigma

## Context

Pre-commit hooks that run Python tools (mypy, pylint, ruff, pytest) can resolve
their dependencies from two places:

1. `additional_dependencies:` in `.pre-commit-config.yaml` — a separate, manually
   pinned list maintained inside the pre-commit config itself.
2. The project's own virtual environment (`language: system`) — driven by `uv.lock`.

The original setup used `mirrors-mypy` with `additional_dependencies: [adcp==3.2.0]`.
When the project upgraded adcp from 3.2 to 4.3, the hook's pin was not updated,
causing `mypy` to report ~262 false errors from the old type stubs. This silently
diverged for multiple PRs before discovery.

## Decision

All Python pre-commit hooks that invoke tools installed in the project venv MUST use
`language: system` (or `language: python` with `entry: uv run ...`) so that they
resolve dependencies from `uv.lock`, never from `additional_dependencies:`.

`additional_dependencies:` is permitted only for hooks that install truly
isolated, non-project-overlapping tools (e.g., `actionlint`, `gitleaks`).

## Consequences

**Good:**
- One lock file governs both CI and pre-commit — no divergence possible.
- `adcp` version upgrades automatically propagate to hooks without manual edits.
- `make quality` and pre-commit run the same mypy binary and the same adcp stubs.

**Bad / tradeoffs:**
- Requires contributors to have `uv sync --group dev` completed before running
  `pre-commit run`; cold-clone setup has one extra step.
- Hooks using `language: system` skip pre-commit's own venv isolation — a bad
  tool install in the project venv breaks the hook.

## Alternatives considered

**Keep `additional_dependencies:`** — rejected because it creates a second source
of truth that diverges silently (demonstrated by the adcp 3.2 → 4.3 incident).

**Use `language: python` (pre-commit-managed venv)** — considered, but creates a
third resolution path (pre-commit's own venv, separate from `uv.lock`). `language: system`
is simpler and more transparent.

## Review trigger

Revisit if pre-commit drops `language: system` support or if uv's venv management
changes the `uv run` entry-point resolution.

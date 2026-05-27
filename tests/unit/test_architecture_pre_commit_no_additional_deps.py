"""Guard: .pre-commit-config.yaml must not contain additional_dependencies.

Per ADR-001, all Python pre-commit hooks must resolve dependencies from uv.lock
via `language: system` / `entry: uv run ...`. Using additional_dependencies
creates an isolated venv that diverges from uv.lock, reintroducing the
dependency drift that caused the adcp 3.2->3.10 type-check regression.

This guard prevents regression: adding additional_dependencies back to any hook
immediately fails `make quality`.

Fixed in PR 2 of issue #1234.
"""

from pathlib import Path

PRE_COMMIT_CONFIG = Path(".pre-commit-config.yaml")


class TestPreCommitNoAdditionalDeps:
    """Enforce ADR-001: no additional_dependencies in .pre-commit-config.yaml."""

    def test_no_additional_dependencies(self):
        """Fail if any hook uses additional_dependencies.

        additional_dependencies creates an isolated pre-commit venv that cannot
        stay in sync with uv.lock. Every Python hook that needs project deps must
        use language: system with entry: uv run <tool> instead.

        To fix: replace the offending hook with a local repo hook:

            - repo: local
              hooks:
                - id: mypy
                  name: mypy
                  entry: uv run mypy
                  language: system
                  ...
        """
        assert PRE_COMMIT_CONFIG.exists(), f"{PRE_COMMIT_CONFIG} not found — are you running from the repo root?"

        content = PRE_COMMIT_CONFIG.read_text()
        lines_with_additional_deps = [
            (i + 1, line.rstrip())
            for i, line in enumerate(content.splitlines())
            if "additional_dependencies:" in line and not line.lstrip().startswith("#")
        ]

        if lines_with_additional_deps:
            offending = "\n".join(f"  line {lineno}: {text}" for lineno, text in lines_with_additional_deps)
            raise AssertionError(
                "Found additional_dependencies: in .pre-commit-config.yaml.\n"
                "This violates ADR-001 (uv.lock as single source of truth).\n\n"
                f"Offending lines:\n{offending}\n\n"
                "Fix: replace the hook with a local repo hook using language: system\n"
                "     and entry: uv run <tool>. See .pre-commit-config.yaml for examples."
            )

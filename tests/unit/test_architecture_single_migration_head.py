"""Guard: alembic migration graph must have exactly one head.

Multiple heads mean the migration DAG has forked — `alembic upgrade head`
fails, `alembic downgrade -1` is ambiguous, and `alembic revision` errors
without `--head`. This happens when two PRs each create a migration from the
same parent and both merge to main.

Fix: run `uv run alembic merge -m "Merge migration heads" heads` to create
a merge migration that joins the branches.

No allowlist — zero tolerance. Multiple heads must be resolved before merge.
"""

import pytest

from tests.unit._migration_helpers import (
    extract_revision_info,
    get_migration_files,
    get_migration_heads,
    resolve_roundtrip_downgrade_target,
)


class TestSingleMigrationHead:
    """The alembic migration graph must have exactly one head revision."""

    def test_single_migration_head(self):
        """Assert that the migration DAG has exactly one head.

        A head is a revision that no other revision lists as its down_revision.
        Multiple heads indicate a forked migration graph that must be resolved
        with a merge migration.
        """
        heads = get_migration_heads()

        assert len(heads) == 1, (
            f"Expected exactly 1 migration head, found {len(heads)}: {sorted(heads)}\n\n"
            f"This means the migration graph has forked. Fix by running:\n"
            f'  uv run alembic merge -m "Merge migration heads" heads\n\n'
            f"Then commit the generated merge migration file."
        )


class TestRoundtripDowngradeTarget:
    """CI migration roundtrip must resolve explicit downgrade targets."""

    def test_merge_head_downgrade_target_uses_first_parent(self):
        """Merge head downgrade uses one parent revision (Alembic restores all branch tips)."""
        heads = get_migration_heads()
        assert len(heads) == 1
        head = next(iter(heads))

        down_revisions: list[str] = []
        for path in get_migration_files():
            revision, downs = extract_revision_info(path)
            if revision == head:
                down_revisions = downs
                break

        assert len(down_revisions) > 1, "Expected current head to be a merge migration."

        target = resolve_roundtrip_downgrade_target()
        assert target == down_revisions[0]

    def test_non_merge_revision_downgrade_target_is_single_parent(self):
        """Single-parent revisions downgrade to their explicit down_revision."""
        for path in get_migration_files():
            revision, downs = extract_revision_info(path)
            if revision and len(downs) == 1:
                assert resolve_roundtrip_downgrade_target(revision) == downs[0]
                return
        pytest.fail("No single-parent migration found in graph.")

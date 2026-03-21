"""Guard: alembic migration graph must have exactly one head.

Multiple heads mean the migration DAG has forked — `alembic upgrade head`
fails, `alembic downgrade -1` is ambiguous, and `alembic revision` errors
without `--head`. This happens when two PRs each create a migration from the
same parent and both merge to main.

Fix: run `uv run alembic merge -m "Merge migration heads" heads` to create
a merge migration that joins the branches.

No allowlist — zero tolerance. Multiple heads must be resolved before merge.
"""

from tests.unit._migration_helpers import get_migration_heads


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

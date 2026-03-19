"""Tests for database migrations - ensure migrations work correctly."""

from pathlib import Path

import pytest

from tests.unit._migration_helpers import get_migration_heads


class TestMigrationVersioning:
    """Test migration version tracking."""

    @pytest.mark.smoke
    def test_migrations_directory_exists(self):
        """Test that migrations directory and files exist."""
        migrations_dir = Path("alembic")
        assert migrations_dir.exists(), "Migrations directory does not exist"

        # Check for alembic.ini
        alembic_ini = Path("alembic.ini")
        assert alembic_ini.exists(), "alembic.ini not found"

        # Check for versions directory
        versions_dir = migrations_dir / "versions"
        assert versions_dir.exists(), "Migrations versions directory does not exist"

        # Check that at least one migration exists
        migration_files = list(versions_dir.glob("*.py"))
        assert len(migration_files) > 0, "No migration files found"

    @pytest.mark.smoke
    def test_single_migration_head(self):
        """Smoke test: migration graph must have exactly one head."""
        heads = get_migration_heads()
        assert len(heads) == 1, (
            f"Multiple migration heads detected: {sorted(heads)}. "
            f"Run: uv run alembic merge -m 'Merge migration heads' heads"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "smoke"])

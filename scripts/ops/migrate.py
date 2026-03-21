#!/usr/bin/env python3
"""Run database migrations using Alembic."""

import sys
from pathlib import Path

from alembic.config import Config

from alembic import command

# Project root (two levels up from scripts/ops/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


def _get_alembic_cfg() -> Config:
    """Return an Alembic Config pointing at the project's alembic.ini."""
    return Config(str(_ALEMBIC_INI))


def _ensure_default_tenant() -> None:
    """In single-tenant mode, ensure the default tenant exists."""
    try:
        from src.core.config_loader import ensure_default_tenant_exists

        tenant = ensure_default_tenant_exists()
        if tenant:
            print(f"✅ Default tenant ready: {tenant.get('name', 'Unknown')}")
    except Exception as e:
        print(f"⚠️ Could not ensure default tenant: {e}")


def run_migrations(exit_on_error=True):
    """Run all pending database migrations.

    Args:
        exit_on_error: If True, exit the process on error. If False, raise exception.
    """
    alembic_cfg = _get_alembic_cfg()

    # Run migrations
    try:
        print("Running database migrations...")
        # Use 'head' (singular) — the migration graph must have exactly one head.
        # Structural guard test_architecture_single_migration_head.py enforces this.
        # If this fails with "multiple heads", create a merge migration first:
        #   uv run alembic merge -m "Merge migration heads" heads
        command.upgrade(alembic_cfg, "head")
        print("✅ Database migrations completed successfully!")
        _ensure_default_tenant()
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error running migrations: {error_msg}")

        # Handle race condition where another container already created alembic_version
        if "pg_type_typname_nsp_index" in error_msg and "alembic_version" in error_msg:
            print("⚠️ alembic_version table already exists (race condition with another container)")
            print("🔄 Retrying migration...")
            try:
                command.upgrade(alembic_cfg, "head")
                print("✅ Database migrations completed successfully on retry!")
                _ensure_default_tenant()
                return
            except Exception as retry_error:
                print(f"❌ Migration retry also failed: {retry_error}")
                # Fall through to other error handlers

        if exit_on_error:
            sys.exit(1)
        else:
            raise


def check_migration_status():
    """Check current migration status."""
    alembic_cfg = _get_alembic_cfg()

    try:
        print("Checking migration status...")
        command.current(alembic_cfg)
    except Exception as e:
        print(f"Error checking status: {e}")


def create_migration(message: str):
    """Create a new migration."""
    alembic_cfg = _get_alembic_cfg()

    try:
        print(f"Creating migration: {message}")
        command.revision(alembic_cfg, message=message, autogenerate=True)
        print("✅ Migration created successfully!")
    except Exception as e:
        print(f"❌ Error creating migration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "status":
            check_migration_status()
        elif sys.argv[1] == "create" and len(sys.argv) > 2:
            create_migration(" ".join(sys.argv[2:]))
        elif sys.argv[1] == "upgrade":
            run_migrations()
        else:
            print(
                """Usage:
    python migrate.py               # Run all pending migrations
    python migrate.py upgrade       # Run all pending migrations
    python migrate.py status        # Check current migration status
    python migrate.py create <msg>  # Create a new migration
            """
            )
    else:
        # Default action is to run migrations
        run_migrations()

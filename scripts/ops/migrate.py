#!/usr/bin/env python3
"""Run database migrations using Alembic."""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations(exit_on_error=True):
    """Run all pending database migrations.

    Args:
        exit_on_error: If True, exit the process on error. If False, raise exception.
    """
    # Get the project root directory (two levels up from scripts/ops/)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent

    # Path to alembic.ini in project root
    alembic_ini_path = project_root / "alembic.ini"

    # Create Alembic configuration
    alembic_cfg = Config(str(alembic_ini_path))

    # Run migrations
    try:
        print("Running database migrations...")
        # Use 'head' (singular) — the migration graph must have exactly one head.
        # Structural guard test_architecture_single_migration_head.py enforces this.
        # If this fails with "multiple heads", create a merge migration first:
        #   uv run alembic merge -m "Merge migration heads" heads
        command.upgrade(alembic_cfg, "head")
        print("✅ Database migrations completed successfully!")

        # In single-tenant mode, ensure default tenant exists
        try:
            from src.core.config_loader import ensure_default_tenant_exists

            tenant = ensure_default_tenant_exists()
            if tenant:
                print(f"✅ Default tenant ready: {tenant.get('name', 'Unknown')}")
        except Exception as e:
            print(f"⚠️ Could not ensure default tenant: {e}")
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

                # In single-tenant mode, ensure default tenant exists
                try:
                    from src.core.config_loader import ensure_default_tenant_exists

                    tenant = ensure_default_tenant_exists()
                    if tenant:
                        print(f"✅ Default tenant ready: {tenant.get('name', 'Unknown')}")
                except Exception as tenant_error:
                    print(f"⚠️ Could not ensure default tenant: {tenant_error}")
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
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    alembic_ini_path = project_root / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))

    try:
        print("Checking migration status...")
        command.current(alembic_cfg)
    except Exception as e:
        print(f"Error checking status: {e}")


def create_migration(message: str):
    """Create a new migration."""
    script_dir = Path(__file__).parent
    alembic_ini_path = script_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))

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

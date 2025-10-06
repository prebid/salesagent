"""Database health check utility for diagnosing schema issues.

This module provides utilities to check database schema consistency,
identify missing tables, and validate that migrations have been applied correctly.
"""

import logging

from sqlalchemy import inspect, text

from src.core.database.database_session import get_db_session

logger = logging.getLogger(__name__)


def check_database_health() -> dict[str, any]:
    """Perform comprehensive database health check.

    Returns:
        Dictionary with health check results including:
        - missing_tables: List of expected tables that don't exist
        - extra_tables: List of unexpected tables
        - migration_status: Current migration status
        - schema_issues: List of detected schema problems
    """
    health_report = {
        "status": "unknown",
        "missing_tables": [],
        "extra_tables": [],
        "migration_status": None,
        "schema_issues": [],
        "recommendations": [],
    }

    try:
        with get_db_session() as db_session:
            engine = db_session.get_bind()
            inspector = inspect(engine)

            # Get current tables
            existing_tables = set(inspector.get_table_names())

            # Expected tables based on models.py (core application tables)
            expected_tables = {
                "tenants",
                "creative_formats",
                "products",
                "principals",
                "users",
                "creatives",
                "creative_assignments",
                "media_buys",
                "audit_logs",
                "superadmin_config",
                "adapter_config",
                "gam_inventory",
                "product_inventory_mappings",
                "gam_orders",
                "gam_line_items",
                "sync_jobs",
                "contexts",
                "workflow_steps",
                "object_workflow_mapping",
                "strategies",
                "strategy_states",
                "authorized_properties",
                "property_tags",
                "format_performance_metrics",
                "push_notification_configs",
            }

            # Deprecated tables that may still exist but are not used
            deprecated_tables = {"tasks", "human_tasks", "creative_associations", "media_packages"}

            # Check for missing tables
            missing = expected_tables - existing_tables
            health_report["missing_tables"] = sorted(missing)

            # Check for unexpected tables (excluding system and deprecated tables)
            system_tables = {"alembic_version", "sqlite_sequence"}
            extra = existing_tables - expected_tables - system_tables - deprecated_tables
            health_report["extra_tables"] = sorted(extra)

            # Check migration status (optional for in-memory databases)
            try:
                result = db_session.execute(text("SELECT version_num FROM alembic_version"))
                current_version = result.scalar()
                health_report["migration_status"] = current_version or "unknown"
            except Exception as e:
                # Not having alembic_version is normal for in-memory test databases
                # that use Base.metadata.create_all() instead of migrations
                if "alembic_version" not in existing_tables:
                    health_report["migration_status"] = "no_migrations_table"
                else:
                    health_report["schema_issues"].append(f"Cannot read alembic_version: {e}")

            # Specific checks for problematic tables
            problematic_tables = ["workflow_steps", "object_workflow_mapping"]
            for table in problematic_tables:
                if table in missing:
                    health_report["schema_issues"].append(
                        f"Critical table '{table}' is missing - this causes dashboard crashes"
                    )

            # Check for deprecated but still existing tables
            for table in deprecated_tables:
                if table in existing_tables:
                    health_report["schema_issues"].append(
                        f"Deprecated table '{table}' still exists - safe to ignore but may cause confusion"
                    )

            # Generate recommendations
            if missing:
                health_report["recommendations"].append("Run migrations to create missing tables: python migrate.py")

            if "workflow_steps" in missing or "object_workflow_mapping" in missing:
                health_report["recommendations"].append("Migration 020 may have failed - check migration logs")

            # Overall health status - distinguish between critical issues and warnings
            critical_issues = []
            warning_issues = []

            # Missing tables are critical
            if missing:
                critical_issues.extend([f"Missing table: {table}" for table in missing])

            # Categorize schema issues
            for issue in health_report["schema_issues"]:
                if "Critical table" in issue or "Cannot read alembic_version" in issue:
                    critical_issues.append(issue)
                elif "Deprecated table" in issue and "safe to ignore" in issue:
                    warning_issues.append(issue)
                else:
                    critical_issues.append(issue)

            # Determine overall status
            if critical_issues:
                health_report["status"] = "unhealthy"
            elif warning_issues:
                health_report["status"] = "warning"
            else:
                health_report["status"] = "healthy"

    except Exception as e:
        health_report["status"] = "error"
        health_report["schema_issues"].append(f"Health check failed: {e}")
        logger.error(f"Database health check failed: {e}")

    return health_report


def print_health_report(health_report: dict[str, any]) -> None:
    """Print a formatted health report."""
    status = health_report["status"]
    status_emoji = {"healthy": "✅", "unhealthy": "❌", "error": "💥", "unknown": "❓"}

    print(f"\n{status_emoji.get(status, '❓')} Database Health Status: {status.upper()}")

    if health_report["missing_tables"]:
        print(f"\n❌ Missing Tables ({len(health_report['missing_tables'])}):")
        for table in health_report["missing_tables"]:
            print(f"  - {table}")

    if health_report["extra_tables"]:
        print(f"\n⚠️  Unexpected Tables ({len(health_report['extra_tables'])}):")
        for table in health_report["extra_tables"]:
            print(f"  - {table}")

    if health_report["schema_issues"]:
        print(f"\n🚨 Schema Issues ({len(health_report['schema_issues'])}):")
        for issue in health_report["schema_issues"]:
            print(f"  - {issue}")

    if health_report["migration_status"]:
        print(f"\n📊 Current Migration: {health_report['migration_status']}")

    if health_report["recommendations"]:
        print("\n💡 Recommendations:")
        for rec in health_report["recommendations"]:
            print(f"  - {rec}")

    print()


def check_table_exists(table_name: str) -> bool:
    """Check if a specific table exists."""
    try:
        with get_db_session() as db_session:
            engine = db_session.get_bind()
            inspector = inspect(engine)
            return table_name in inspector.get_table_names()
    except Exception as e:
        logger.error(f"Error checking if table {table_name} exists: {e}")
        return False


def get_table_info(table_name: str) -> dict[str, any]:
    """Get detailed information about a table."""
    try:
        with get_db_session() as db_session:
            engine = db_session.get_bind()
            inspector = inspect(engine)

            if table_name not in inspector.get_table_names():
                return {"exists": False}

            return {
                "exists": True,
                "columns": [col["name"] for col in inspector.get_columns(table_name)],
                "indexes": [idx["name"] for idx in inspector.get_indexes(table_name)],
                "foreign_keys": [fk["name"] for fk in inspector.get_foreign_keys(table_name)],
            }
    except Exception as e:
        logger.error(f"Error getting table info for {table_name}: {e}")
        return {"exists": False, "error": str(e)}


if __name__ == "__main__":
    # Run health check when script is executed directly
    report = check_database_health()
    print_health_report(report)

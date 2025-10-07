"""
Audit logging module for AdCP Sales Agent platform.

Implements security-compliant logging with:
- Timestamps
- Principal context
- Operation tracking
- Success/failure status
- Database-based audit trail with optional file backup
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog

# Create logs directory if it doesn't exist (for backup)
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Configure logging format
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Set up file handler for backup
audit_handler = logging.FileHandler(LOG_DIR / "audit.log")
audit_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

# Set up error handler
error_handler = logging.FileHandler(LOG_DIR / "error.log")
error_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
error_handler.setLevel(logging.ERROR)

# Create audit logger
audit_logger = logging.getLogger("adcp.audit")
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(audit_handler)
audit_logger.addHandler(error_handler)

# Also log to console for debugging
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
audit_logger.addHandler(console_handler)


class AuditLogger:
    """Provides security-compliant audit logging for AdCP operations."""

    def __init__(self, adapter_name: str, tenant_id: str | None = None):
        self.adapter_name = adapter_name
        self.tenant_id = tenant_id

    def log_operation(
        self,
        operation: str,
        principal_name: str,
        principal_id: str,
        adapter_id: str,
        success: bool = True,
        details: dict[str, Any] | None = None,
        error: str | None = None,
        tenant_id: str | None = None,
    ):
        """Log an adapter operation with full audit context.

        Args:
            operation: The operation being performed (e.g., "create_media_buy")
            principal_name: Human-readable principal name
            principal_id: Internal principal ID
            adapter_id: Platform-specific advertiser ID
            success: Whether the operation succeeded
            details: Additional operation details
            error: Error message if operation failed
            tenant_id: Override tenant ID (uses instance tenant_id if not provided)
        """
        # Use provided tenant_id or fall back to instance tenant_id
        tenant_id = tenant_id or self.tenant_id

        # Build log message in security documentation format
        message = f"{self.adapter_name}.{operation} for principal '{principal_name}' ({self.adapter_name} advertiser ID: {adapter_id})"

        if success:
            audit_logger.info(message)
            if details:
                for key, value in details.items():
                    audit_logger.info(f"  {key}: {value}")
        else:
            audit_logger.error(f"{message} - FAILED")
            if error:
                audit_logger.error(f"  Error: {error}")

        # Write to database
        try:
            with get_db_session() as db_session:
                audit_log = AuditLog(
                    tenant_id=tenant_id,
                    timestamp=datetime.now(UTC),
                    operation=f"{self.adapter_name}.{operation}",
                    principal_name=principal_name,
                    principal_id=principal_id,
                    adapter_id=adapter_id,
                    success=success,
                    error_message=error if not success else None,
                    details=json.dumps(details) if details else None,
                )
                db_session.add(audit_log)
                db_session.commit()
        except Exception as e:
            audit_logger.error(f"Failed to write audit log to database: {e}")
            # Continue with file logging as fallback

        # Also write structured JSON log for machine processing (backup)
        self._write_structured_log(
            operation=operation,
            principal_name=principal_name,
            principal_id=principal_id,
            adapter_id=adapter_id,
            success=success,
            details=details,
            error=error,
            tenant_id=tenant_id,
        )

        # Send to Slack audit channel if configured
        try:
            # Lazy import to avoid circular dependency

            # Get tenant name and config for context
            tenant_name = None
            if tenant_id:
                try:
                    with get_db_session() as db_session:
                        from src.core.database.models import Tenant

                        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                        tenant = db_session.scalars(stmt).first()
                        if tenant:
                            tenant_name = tenant.name
                except:
                    pass

            # Send notification based on criteria
            should_notify = False
            security_alert = False

            # Always notify on failures
            if not success:
                should_notify = True

            # Notify on sensitive operations
            sensitive_ops = [
                "create_media_buy",
                "update_media_buy",
                "delete_media_buy",
                "approve_creative",
                "reject_creative",
                "manual_approval",
            ]
            if operation in sensitive_ops:
                should_notify = True

            # Check for high-value operations
            if details and isinstance(details, dict):
                if "budget" in details and isinstance(details["budget"], int | float) and details["budget"] > 10000:
                    should_notify = True
                if (
                    "total_budget" in details
                    and isinstance(details["total_budget"], int | float)
                    and details["total_budget"] > 10000
                ):
                    should_notify = True

            if should_notify:
                slack_notifier.notify_audit_log(
                    operation=operation,
                    principal_name=principal_name,
                    success=success,
                    adapter_id=adapter_id,
                    tenant_name=tenant_name,
                    error_message=error,
                    details=details,
                    security_alert=security_alert,
                )
        except Exception:
            # Don't let Slack failures affect core functionality
            pass

    def log_security_violation(
        self, operation: str, principal_id: str, resource_id: str, reason: str, tenant_id: str | None = None
    ):
        """Log a security violation attempt."""
        # Use provided tenant_id or fall back to instance tenant_id
        tenant_id = tenant_id or self.tenant_id

        message = (
            f"SECURITY VIOLATION: {self.adapter_name}.{operation} "
            f"Principal '{principal_id}' attempted to access resource '{resource_id}' - {reason}"
        )
        audit_logger.error(message)

        # Write to database
        try:
            with get_db_session() as db_session:
                audit_log = AuditLog(
                    tenant_id=tenant_id,
                    timestamp=datetime.now(UTC),
                    operation=f"SECURITY_VIOLATION:{self.adapter_name}.{operation}",
                    principal_name=None,  # principal_name not available
                    principal_id=principal_id,
                    adapter_id=None,  # adapter_id not applicable
                    success=False,  # Security violations are failures
                    error_message=f"Attempted to access resource '{resource_id}' - {reason}",
                    details=json.dumps({"resource_id": resource_id, "reason": reason}),
                )
                db_session.add(audit_log)
                db_session.commit()
        except Exception as e:
            audit_logger.error(f"Failed to write security violation to database: {e}")

        # Write to security log (backup)
        self._write_security_log(
            operation=operation, principal_id=principal_id, resource_id=resource_id, reason=reason, tenant_id=tenant_id
        )

        # Send security alert to Slack
        try:
            # Lazy import to avoid circular dependency
            from slack_notifier import slack_notifier

            # Get tenant name
            tenant_name = None
            if tenant_id:
                try:
                    with get_db_session() as db_session:
                        from src.core.database.models import Tenant

                        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                        tenant = db_session.scalars(stmt).first()
                        if tenant:
                            tenant_name = tenant.name
                except:
                    pass

            slack_notifier.notify_audit_log(
                operation=operation,
                principal_name=f"UNAUTHORIZED: {principal_id}",
                success=False,
                adapter_id=self.adapter_name,
                tenant_name=tenant_name,
                error_message=f"Security violation: {reason}",
                details={"resource_id": resource_id, "violation_type": "unauthorized_access"},
                security_alert=True,
            )
        except Exception:
            # Don't let Slack failures affect core functionality
            pass

    def log_success(self, message: str):
        """Log a success message with checkmark."""
        audit_logger.info(f"✓ {message}")

    def log_warning(self, message: str):
        """Log a warning message."""
        audit_logger.warning(f"⚠️  {message}")

    def log_info(self, message: str):
        """Log an informational message."""
        audit_logger.info(message)

    def _write_structured_log(self, **kwargs):
        """Write structured JSON log for machine processing (backup)."""
        log_entry = {"timestamp": datetime.now(UTC).isoformat(), "adapter": self.adapter_name, **kwargs}

        try:
            with open(LOG_DIR / "structured.jsonl", "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            audit_logger.error(f"Failed to write structured log: {e}")

    def _write_security_log(self, **kwargs):
        """Write security-specific log entry (backup)."""
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "adapter": self.adapter_name,
            "type": "security_violation",
            **kwargs,
        }

        try:
            with open(LOG_DIR / "security.jsonl", "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            audit_logger.error(f"Failed to write security log: {e}")


# Convenience function for getting logger
def get_audit_logger(adapter_name: str, tenant_id: str | None = None) -> AuditLogger:
    """Get an audit logger instance for the specified adapter."""
    return AuditLogger(adapter_name, tenant_id)

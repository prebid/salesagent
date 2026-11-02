"""
Audit logging module for Prebid Sales Agent platform.

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
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.core.config import RuntimeSettings
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog

# Directory holding the FILE BACKUP of the audit trail. The database is the
# audit authority (see the module docstring); these files are a secondary sink.
#
# Importing this module must not touch the filesystem. The path is CWD-relative,
# so creating it at import time makes the module's importability depend on who
# owns a directory the importer never chose: under Docker, two containers share
# /app as a bind mount, whichever one reached logs/audit.log first owned it at
# umask-derived mode 644, and every other uid then died at pytest COLLECTION
# with `PermissionError: [Errno 13] Permission denied: '/app/logs/audit.log'`.
# Creation is therefore deferred to the first WRITE — the first moment a log
# path is genuinely needed.
#
# ADCP_LOG_DIR exists so a process that SHARES its working directory with another
# process running as a different uid can be pointed somewhere private, instead of
# the two contending over the same files. Default is unchanged.
#
# A log directory is a process fact, read once here; the handlers below are built
# from it at import. Read off RuntimeSettings alone, the way the schema modules read
# their extra mode: importing this module must not compose the whole Settings.
_runtime = RuntimeSettings()
LOG_DIR = _runtime.adcp_log_dir


def _ensure_log_dir(directory: Path) -> Path:
    """Create a log directory on demand and return it.

    The single creation point for every sink in this module — both file handlers
    and both .jsonl writers — so no sink can ever assume a directory that
    another sink happened to create.
    """
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _log_path(filename: str) -> Path:
    """Return the path to a backup log file, creating its directory first."""
    return _ensure_log_dir(LOG_DIR) / filename


class _LazyDirFileHandler(logging.FileHandler):
    """A ``FileHandler`` that creates its directory when it opens the file.

    ``logging.FileHandler`` opens its file in ``__init__`` unless ``delay=True``.
    Pairing ``delay=True`` with this ``_open()`` moves the directory creation and
    the file open together to the first emitted record, so constructing the
    handler — which happens at import — performs no I/O at all.
    """

    def _open(self):
        _ensure_log_dir(Path(self.baseFilename).parent)
        return super()._open()

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a record, refusing to let a failed BACKUP write reach the caller.

        ``FileHandler.emit`` calls ``_open()`` outside the ``try`` that guards
        the write, so an unopenable path propagates ``OSError`` straight into
        whichever business logic called ``audit_logger.info(...)``. Deferring the
        open with ``delay=True`` alone would therefore only move that failure
        from import time to first-emit time, not remove it.

        Losing this sink must not take the operation down with it, so the error
        is routed to ``handleError``, which writes to stderr (``logging``'s
        ``raiseExceptions`` is never set in ``src/``). The trade is a
        fail-CLOSED import-time failure becoming a fail-OPEN per-record stderr
        note.

        What that costs, stated precisely rather than as "it's only a backup":
        for ``log_operation`` and ``log_security_violation`` the database row is
        the authority and this file duplicates it, so nothing is lost. For
        ``log_success``/``log_warning``/``log_info`` -- 19 production call sites,
        including workflow-step creation and ``sync_accounts`` completion -- this
        file is the ONLY durable sink, and a record dropped here is gone. Giving
        those three a database counterpart is the real fix; it is not this
        change, which only stops an unwritable log directory from failing an
        import.
        """
        try:
            super().emit(record)
        except OSError:
            self.handleError(record)


# Configure logging format
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Set up file handler for backup
audit_handler = _LazyDirFileHandler(LOG_DIR / "audit.log", delay=True)
audit_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

# Set up error handler
error_handler = _LazyDirFileHandler(LOG_DIR / "error.log", delay=True)
error_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
error_handler.setLevel(logging.ERROR)

# Create audit logger
audit_logger = logging.getLogger("adcp.audit")
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(audit_handler)
audit_logger.addHandler(error_handler)

# In development, also log to console for debugging
# In production, the root logger already handles console output with JSON formatting
if not _runtime.structured_logging:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    audit_logger.addHandler(console_handler)


# Budget above (strictly greater than) this value triggers a high-value Slack alert
# to the Seller — matches the strict ``>`` gate in _is_high_value_budget and the
# @T-UC-002-nfr-highvalue BDD contract (">$10k"); exactly $10,000 does NOT alert.
_HIGH_VALUE_BUDGET_THRESHOLD = 10000


def _decimal_to_float(value: Any) -> Any:
    """The single Decimal->float money rule shared by every audit sink.

    Money is ``Decimal`` (``get_total_budget()`` — float is wrong for money), but
    JSON has no Decimal type, so both audit sinks must emit it as a number.
    Non-Decimal values pass through unchanged (#1417)."""
    return float(value) if isinstance(value, Decimal) else value


def _normalize_audit_details(value: Any) -> Any:
    """Recursively apply the Decimal->float money rule to an audit payload.

    Produces a JSON-native structure — every ``Decimal`` becomes a float number,
    every other leaf (int/bool/None/str) and nested dict/list is preserved — so the
    DB ``details`` JSONType column and the ``.jsonl`` structured log serialize a
    budget IDENTICALLY as a number. Without this, the DB path (engine-wide
    ``pydantic_core.to_json(fallback=str)``) stringifies a bare Decimal while the
    ``.jsonl`` path emits a number, diverging the two sinks and breaking admin
    readers that format the budget with ``:,.0f`` (#1417). Single source
    of the money rule (reused by ``_audit_json_default``)."""
    if isinstance(value, dict):
        return {k: _normalize_audit_details(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_audit_details(v) for v in value]
    return _decimal_to_float(value)


def _is_high_value_budget(details: dict[str, Any]) -> bool:
    """True if ``details`` carries a budget above the high-value alert threshold.

    Accepts int | float | Decimal. ``get_total_budget()`` returns ``Decimal``
    (money — float is wrong for money), which the old ``isinstance(.,(int,float))``
    gate silently rejected, disabling the >$10k alert on the create path
    (#1417). One helper covers both the ``budget`` and ``total_budget``
    detail keys.
    """
    for key in ("budget", "total_budget"):
        value = details.get(key)
        if isinstance(value, (int, float, Decimal)) and value > _HIGH_VALUE_BUDGET_THRESHOLD:
            return True
    return False


def _audit_json_default(value: Any) -> Any:
    """``json.dumps`` fallback for audit payloads.

    Safety net for the ``.jsonl`` sink: ``_normalize_audit_details`` already
    converts Decimals before serialization, but keep the shared Decimal->float
    rule here too; any other non-serializable leaf falls back to ``str``
    (#1417).
    """
    if isinstance(value, Decimal):
        return _decimal_to_float(value)
    return str(value)


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

        # Normalize the payload ONCE so every sink (DB details column + .jsonl log)
        # serializes a Decimal budget identically as a JSON number (#1417).
        details = _normalize_audit_details(details) if details else details

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
                    # Pass dict directly - JSONType column handles serialization
                    details=details or {},
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
                except Exception:
                    audit_logger.debug("Could not load tenant name for Slack context", exc_info=True)

            # Send notification based on criteria
            should_notify = False
            security_alert = False

            # Always notify on failures
            if not success:
                should_notify = True

            # Notify on sensitive operations
            sensitive_ops = [
                # Media buy operations (business critical)
                "create_media_buy",
                "update_media_buy",
                "delete_media_buy",
                "approve_creative",
                "reject_creative",
                "manual_approval",
                # Admin UI - User management (security critical)
                "add_user",
                "toggle_user",
                "update_user_role",
                "update_role",
                # Admin UI - Tenant management (security critical)
                "create_tenant",
                "deactivate_tenant",
                "reactivate_tenant",
                "update",  # General tenant settings update
                "update_general_settings",
                "add_authorized_domain",
                "remove_authorized_domain",
                "add_authorized_email",
                "remove_authorized_email",
                # Admin UI - Adapter configuration (infrastructure critical)
                "update_adapter",
                "setup_adapter",
                "configure_gam",
                "detect_gam_network",
                # Admin UI - Principal management (access control)
                "create_principal",
                "update_mappings",
                "update_principal_mappings",
                "register_webhook",
                "delete_webhook",
                # Admin UI - Policy changes (business critical)
                "update_policy",
                "update_business_rules",
                "review_policy_task",
            ]
            if operation in sensitive_ops:
                should_notify = True

            # Check for high-value operations
            if details and isinstance(details, dict) and _is_high_value_budget(details):
                should_notify = True

            if should_notify:
                from src.core.utils.tenant_utils import serialize_tenant_to_dict
                from src.services.slack_notifier import get_slack_notifier

                # Get tenant config for Slack notifier
                tenant_config = None
                if tenant_id:
                    try:
                        with get_db_session() as db_session:
                            from src.core.database.models import Tenant

                            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                            tenant = db_session.scalars(stmt).first()
                            if tenant:
                                tenant_config = serialize_tenant_to_dict(tenant)
                    except Exception:
                        audit_logger.debug("Could not load tenant config for Slack context", exc_info=True)

                slack_notifier = get_slack_notifier(tenant_config=tenant_config)
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
            audit_logger.warning("Slack audit notification failed", exc_info=True)

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
                    details={"resource_id": resource_id, "reason": reason},
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
            from src.core.utils.tenant_utils import serialize_tenant_to_dict
            from src.services.slack_notifier import get_slack_notifier

            # Get tenant name and config
            tenant_name = None
            tenant_config = None
            if tenant_id:
                try:
                    with get_db_session() as db_session:
                        from src.core.database.models import Tenant

                        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                        tenant = db_session.scalars(stmt).first()
                        if tenant:
                            tenant_name = tenant.name
                            tenant_config = serialize_tenant_to_dict(tenant)
                except Exception:
                    audit_logger.debug("Could not load tenant for security violation Slack context", exc_info=True)

            slack_notifier = get_slack_notifier(tenant_config=tenant_config)
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
            audit_logger.warning("Slack security violation notification failed", exc_info=True)

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
            with open(_log_path("structured.jsonl"), "a") as f:
                f.write(json.dumps(log_entry, default=_audit_json_default) + "\n")
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
            with open(_log_path("security.jsonl"), "a") as f:
                f.write(json.dumps(log_entry, default=_audit_json_default) + "\n")
        except Exception as e:
            audit_logger.error(f"Failed to write security log: {e}")


# Convenience function for getting logger
def get_audit_logger(adapter_name: str, tenant_id: str | None = None) -> AuditLogger:
    """Get an audit logger instance for the specified adapter."""
    return AuditLogger(adapter_name, tenant_id)

"""Creative Sync and Listing tool implementations.

Handles creative operations including:
- Creative synchronization from buyer creative agents
- Creative asset validation and format conversion
- Creative library management
- Creative discovery and filtering

This package re-exports all public functions for backward compatibility.
Existing imports like ``from src.core.tools.creatives import _sync_creatives_impl``
continue to work.
"""

from src.core.config_loader import get_current_tenant
from src.core.helpers import get_principal_id_from_context, log_tool_activity

from ._assets import _build_creative_data, _extract_url_from_assets
from ._assignments import _process_assignments
from ._processing import _create_new_creative, _update_existing_creative
from ._sync import _sync_creatives_impl
from ._validation import _get_field, _validate_creative_input
from ._workflow import _audit_log_sync, _create_sync_workflow_steps, _send_creative_notifications
from .listing import _list_creatives_impl, list_creatives, list_creatives_raw
from .sync_wrappers import sync_creatives, sync_creatives_raw

__all__ = [
    # Re-exported dependencies (for mock.patch compatibility)
    "get_current_tenant",
    "get_principal_id_from_context",
    "log_tool_activity",
    # Sync orchestrator
    "_sync_creatives_impl",
    # Listing
    "_list_creatives_impl",
    "list_creatives",
    "list_creatives_raw",
    # Sync wrappers (MCP + A2A)
    "sync_creatives",
    "sync_creatives_raw",
    # Validation
    "_get_field",
    "_validate_creative_input",
    # Assets
    "_extract_url_from_assets",
    "_build_creative_data",
    # Processing
    "_update_existing_creative",
    "_create_new_creative",
    # Assignments
    "_process_assignments",
    # Workflow
    "_create_sync_workflow_steps",
    "_send_creative_notifications",
    "_audit_log_sync",
]

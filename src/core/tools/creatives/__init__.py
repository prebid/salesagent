"""Creative Sync and Listing tool implementations.

Handles creative operations including:
- Creative synchronization from buyer creative agents
- Creative asset validation and format conversion
- Creative library management
- Creative discovery and filtering

This package re-exports the package's helpers. It does NOT re-export a tool ``_impl``:
a tool is reached through ``invoke_tool`` / ``TOOLS[name].impl``, and a package-level
alias widens the surface a caller can bypass the boundary from. Import the defining
module (``._sync`` / ``.listing``) when you genuinely need the function object.
"""

from src.core.helpers import log_tool_activity

from ._assets import (
    _build_creative_data,
    _extract_message_from_assets,
    _extract_text_from_asset_value,
    _extract_url_from_assets,
)
from ._assignments import _process_assignments
from ._processing import _create_new_creative, _update_existing_creative
from ._sync import sync_creatives
from ._validation import _get_field, _validate_creative_input
from ._workflow import _audit_log_sync, _create_sync_workflow_steps, _send_creative_notifications

__all__ = [
    # Re-exported dependencies (for mock.patch compatibility)
    "log_tool_activity",
    "list_creatives",
    "build_sync_creatives_request",
    "sync_creatives",
    # Validation
    "_get_field",
    "_validate_creative_input",
    # Assets
    "_extract_url_from_assets",
    "_extract_text_from_asset_value",
    "_extract_message_from_assets",
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

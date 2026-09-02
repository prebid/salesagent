"""Get products tool implementation.

This module contains the get_products tool implementation following the MCP/A2A
shared implementation pattern from CLAUDE.md.
"""

import logging
import os
import time
from typing import Annotated, Any

# FIXME(#1388): FormatId, ProductFilters have local subclasses; import from src.core.schemas (Pattern #7/#4).
from adcp import FormatId, ProductFilters
from adcp import GetProductsRequest as GetProductsRequestGenerated
from adcp import Product as LibraryProduct
from adcp.types import BrandReference, ContextObject, PropertyListReference
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from src.adapters import get_adapter_default_channels
from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_object, require_identity, require_tenant
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPError,
    AdCPPolicyViolationError,
    AdCPValidationError,
)
from src.core.helpers import enum_value
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import create_get_products_request
from src.core.schemas import (
    GetProductsResponse,
    Product,  # Extends library Product
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context
from src.core.validation_helpers import adcp_validation_boundary, safe_parse_json_field
from src.services.policy_check_service import PolicyCheckService, PolicyStatus

logger = logging.getLogger(__name__)

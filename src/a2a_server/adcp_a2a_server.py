#!/usr/bin/env python3
"""
AdCP Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import contextvars
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any

# Fix import order to avoid local a2a directory conflict
# Import official a2a-sdk first before adding local paths

original_path = sys.path.copy()

# Temporarily remove current directory to avoid local a2a conflict
if "" in sys.path:
    sys.path.remove("")
if "." in sys.path:
    sys.path.remove(".")

# Official a2a-sdk imports (must be before adding local paths)
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    Artifact,
    DataPart,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    Message,
    MessageSendParams,
    MethodNotFoundError,
    Part,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import ServerError

# Restore paths and add parent directories for local imports
sys.path = original_path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import core functions for direct calls (raw functions without FastMCP decorators)
from datetime import UTC, datetime

from sqlalchemy import select

from src.core.audit_logger import get_audit_logger
from src.core.auth_utils import get_principal_from_token
from src.core.config_loader import get_current_tenant
from src.core.schemas import (
    GetSignalsRequest,
    ListAuthorizedPropertiesRequest,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.tools import (
    create_media_buy_raw as core_create_media_buy_tool,
)
from src.core.tools import (
    get_media_buy_delivery_raw as core_get_media_buy_delivery_tool,
)
from src.core.tools import (
    get_products_raw as core_get_products_tool,
)
from src.core.tools import (
    get_signals_raw as core_get_signals_tool,
)
from src.core.tools import (
    list_authorized_properties_raw as core_list_authorized_properties_tool,
)
from src.core.tools import (
    list_creative_formats_raw as core_list_creative_formats_tool,
)
from src.core.tools import (
    list_creatives_raw as core_list_creatives_tool,
)
from src.core.tools import (
    sync_creatives_raw as core_sync_creatives_tool,
)
from src.core.tools import (
    update_media_buy_raw as core_update_media_buy_tool,
)
from src.core.tools import (
    update_performance_index_raw as core_update_performance_index_tool,
)
from src.services.protocol_webhook_service import get_protocol_webhook_service

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Context variables for current request (works with async code, unlike threading.local())
_request_auth_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_auth_token", default=None)
_request_headers: contextvars.ContextVar[dict | None] = contextvars.ContextVar("request_headers", default=None)


class AdCPRequestHandler(RequestHandler):
    """Request handler for AdCP A2A operations supporting JSON-RPC 2.0."""

    def __init__(self):
        """Initialize the AdCP A2A request handler."""
        self.tasks = {}  # In-memory task storage
        logger.info("AdCP Request Handler initialized for direct function calls")

    def _get_auth_token(self) -> str | None:
        """Extract Bearer token from current request context."""
        return _request_auth_token.get()

    def _create_tool_context_from_a2a(
        self, auth_token: str | None, tool_name: str, context_id: str | None = None
    ) -> ToolContext:
        """Create a ToolContext from A2A authentication information.

        Args:
            auth_token: Bearer token from Authorization header (optional)
            tool_name: Name of the tool being called
            context_id: Optional context ID for conversation tracking

        Returns:
            ToolContext for calling core functions

        Raises:
            ValueError: If authentication fails
        """
        # Import tenant resolution functions
        from src.core.config_loader import (
            get_tenant_by_id,
            get_tenant_by_subdomain,
            get_tenant_by_virtual_host,
            set_current_tenant,
        )

        # Get request headers for debugging (case-insensitive lookup)
        headers = _request_headers.get() or {}

        # Helper to get header case-insensitively
        def get_header(header_name: str) -> str | None:
            for key, value in headers.items():
                if key.lower() == header_name.lower():
                    return value
            return None

        apx_host = get_header("apx-incoming-host") or "NOT_PRESENT"
        host = get_header("host")
        tenant_header = get_header("x-adcp-tenant")

        logger.info("[A2A AUTH] Resolving tenant from headers:")
        logger.info(f"  Host: {host}")
        logger.info(f"  Apx-Incoming-Host: {apx_host}")
        logger.info(f"  x-adcp-tenant: {tenant_header}")

        # CRITICAL: Resolve tenant from headers FIRST (before authentication)
        # This matches the MCP pattern in main.py::get_principal_from_context()
        requested_tenant_id = None
        tenant_context = None
        detection_method = None

        # 1. Check Host header for subdomain
        if not requested_tenant_id and host:
            subdomain = host.split(".")[0] if "." in host else None
            if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "admin"]:
                logger.info(f"[A2A AUTH] Looking up tenant by subdomain: {subdomain}")
                tenant_context = get_tenant_by_subdomain(subdomain)
                if tenant_context:
                    requested_tenant_id = tenant_context["tenant_id"]
                    detection_method = "subdomain"
                    set_current_tenant(tenant_context)
                    logger.info(f"[A2A AUTH] ✅ Tenant detected from subdomain: {subdomain} → {requested_tenant_id}")
                else:
                    # Try virtual host lookup
                    logger.info(f"[A2A AUTH] Trying virtual host lookup for: {host}")
                    tenant_context = get_tenant_by_virtual_host(host)
                    if tenant_context:
                        requested_tenant_id = tenant_context["tenant_id"]
                        detection_method = "host header (virtual host)"
                        set_current_tenant(tenant_context)
                        logger.info(f"[A2A AUTH] ✅ Tenant detected from Host header: {host} → {requested_tenant_id}")

        # 2. Check x-adcp-tenant header
        if not requested_tenant_id and tenant_header:
            logger.info(f"[A2A AUTH] Looking up tenant from x-adcp-tenant: {tenant_header}")
            tenant_context = get_tenant_by_subdomain(tenant_header)
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "x-adcp-tenant (subdomain)"
                set_current_tenant(tenant_context)
                logger.info(
                    f"[A2A AUTH] ✅ Tenant detected from x-adcp-tenant: {tenant_header} → {requested_tenant_id}"
                )
            else:
                # Fallback: assume it's a tenant_id
                tenant_context = get_tenant_by_id(tenant_header)
                if tenant_context:
                    requested_tenant_id = tenant_context["tenant_id"]
                    detection_method = "x-adcp-tenant (direct)"
                    set_current_tenant(tenant_context)
                    logger.info(f"[A2A AUTH] ✅ Tenant detected from x-adcp-tenant: {requested_tenant_id}")

        # 3. Check Apx-Incoming-Host header (for Approximated.app routing)
        if not requested_tenant_id and apx_host and apx_host != "NOT_PRESENT":
            logger.info(f"[A2A AUTH] Looking up tenant by Apx-Incoming-Host: {apx_host}")
            tenant_context = get_tenant_by_virtual_host(apx_host)
            if tenant_context:
                requested_tenant_id = tenant_context["tenant_id"]
                detection_method = "apx-incoming-host"
                set_current_tenant(tenant_context)
                logger.info(f"[A2A AUTH] ✅ Tenant detected from Apx-Incoming-Host: {apx_host} → {requested_tenant_id}")

        if requested_tenant_id:
            logger.info(f"[A2A AUTH] Final tenant_id: {requested_tenant_id} (via {detection_method})")
        else:
            logger.warning("[A2A AUTH] ⚠️  No tenant detected from headers - will use global token lookup")

        # NOW authenticate with tenant context (if we have one)
        principal_id = get_principal_from_token(auth_token, requested_tenant_id)
        if not principal_id:
            raise ServerError(
                InvalidRequestError(
                    message=f"Invalid authentication token (not found in database). "
                    f"Token: {auth_token[:20]}..., "
                    f"Tenant: {requested_tenant_id or 'any'}, "
                    f"Apx-Incoming-Host: {apx_host}"
                )
            )

        # Get tenant info (either from header detection or from token lookup)
        if not tenant_context:
            tenant_context = get_current_tenant()
        if not tenant_context:
            raise ServerError(
                InvalidRequestError(
                    message=f"Unable to determine tenant from authentication. "
                    f"Principal: {principal_id}, "
                    f"Apx-Incoming-Host: {apx_host}"
                )
            )

        # Generate context ID if not provided
        if not context_id:
            context_id = f"a2a_{datetime.now(UTC).timestamp()}"

        logger.info(
            f"[A2A AUTH] ✅ Authentication successful: tenant={tenant_context['tenant_id']}, principal={principal_id}"
        )

        # Create ToolContext
        return ToolContext(
            context_id=context_id,
            tenant_id=tenant_context["tenant_id"],
            principal_id=principal_id,
            tool_name=tool_name,
            request_timestamp=datetime.now(UTC),
            metadata={"source": "a2a_server", "protocol": "a2a_jsonrpc"},
            testing_context=AdCPTestContext().model_dump(),  # Default testing context for A2A requests
        )

    def _log_a2a_operation(
        self,
        operation: str,
        tenant_id: str,
        principal_id: str,
        success: bool = True,
        details: dict = None,
        error: str = None,
    ):
        """Log A2A operations to audit system for visibility in activity feed."""
        try:
            if not tenant_id:
                return

            audit_logger = get_audit_logger("A2A", tenant_id)
            audit_logger.log_operation(
                operation=operation,
                principal_name=f"A2A_Client_{principal_id}",
                principal_id=principal_id,
                adapter_id="a2a_client",
                success=success,
                details=details,
                error=error,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.warning(f"Failed to log A2A operation: {e}")

    async def _send_protocol_webhook(
        self,
        task: Task,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ):
        """Send protocol-level push notification if configured."""
        try:
            # Check if task has push notification config in metadata
            if not task.metadata or "push_notification_config" not in task.metadata:
                return

            webhook_config = task.metadata["push_notification_config"]
            webhook_service = get_protocol_webhook_service()

            await webhook_service.send_notification(
                webhook_config=webhook_config,
                task_id=task.id,
                status=status,
                result=result,
                error=error,
            )
        except Exception as e:
            # Don't fail the task if webhook fails
            logger.warning(f"Failed to send protocol-level webhook for task {task.id}: {e}")

    def _reconstruct_response_object(self, skill_name: str, data: dict) -> Any:
        """Reconstruct a response object from skill result data to call __str__().

        Args:
            skill_name: Name of the skill that produced the result
            data: Dictionary containing the response data

        Returns:
            Reconstructed response object, or None if reconstruction fails
        """
        try:
            # Map skill names to response classes
            from src.core.schema_adapters import (
                CreateMediaBuyResponse,
                GetMediaBuyDeliveryResponse,
                GetProductsResponse,
                ListAuthorizedPropertiesResponse,
                ListCreativeFormatsResponse,
                ListCreativesResponse,
                SyncCreativesResponse,
                UpdateMediaBuyResponse,
            )

            response_map = {
                "create_media_buy": CreateMediaBuyResponse,
                "get_media_buy_delivery": GetMediaBuyDeliveryResponse,
                "get_products": GetProductsResponse,
                "list_authorized_properties": ListAuthorizedPropertiesResponse,
                "list_creative_formats": ListCreativeFormatsResponse,
                "list_creatives": ListCreativesResponse,
                "sync_creatives": SyncCreativesResponse,
                "update_media_buy": UpdateMediaBuyResponse,
            }

            response_class = response_map.get(skill_name)
            if response_class:
                return response_class(**data)
        except Exception as e:
            logger.debug(f"Could not reconstruct response object for {skill_name}: {e}")
        return None

    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Task | Message:
        """Handle 'message/send' method for non-streaming requests.

        Supports both invocation patterns from AdCP PR #48:
        1. Natural Language: parts[{kind: "text", text: "..."}]
        2. Explicit Skill: parts[{kind: "data", data: {skill: "...", parameters: {...}}}]

        Args:
            params: Parameters including the message and configuration
            context: Server call context

        Returns:
            Task object or Message response
        """
        logger.info(f"Handling message/send request: {params}")

        # Parse message for both text and structured data parts
        message = params.message
        text_parts = []
        skill_invocations = []

        if hasattr(message, "parts") and message.parts:
            for part in message.parts:
                # Handle text parts (natural language invocation)
                if hasattr(part, "text"):
                    text_parts.append(part.text)
                elif hasattr(part, "root") and hasattr(part.root, "text"):
                    text_parts.append(part.root.text)

                # Handle structured data parts (explicit skill invocation)
                elif hasattr(part, "data") and isinstance(part.data, dict):
                    # Support both "input" (A2A spec) and "parameters" (legacy) for skill params
                    if "skill" in part.data:
                        params_data = part.data.get("input") or part.data.get("parameters", {})
                        skill_invocations.append({"skill": part.data["skill"], "parameters": params_data})
                        logger.info(
                            f"Found explicit skill invocation: {part.data['skill']} with params: {list(params_data.keys())}"
                        )

                # Handle nested data structure (some A2A clients use this format)
                elif hasattr(part, "root") and hasattr(part.root, "data"):
                    data = part.root.data
                    if isinstance(data, dict) and "skill" in data:
                        # Support both "input" (A2A spec) and "parameters" (legacy) for skill params
                        params_data = data.get("input") or data.get("parameters", {})
                        skill_invocations.append({"skill": data["skill"], "parameters": params_data})
                        logger.info(
                            f"Found explicit skill invocation (nested): {data['skill']} with params: {list(params_data.keys())}"
                        )

        # Combine text for natural language fallback
        combined_text = " ".join(text_parts).strip().lower()

        # Create task for tracking
        task_id = f"task_{len(self.tasks) + 1}"
        # Handle message_id being a number or string
        msg_id = str(params.message.message_id) if hasattr(params.message, "message_id") else None
        context_id = params.message.context_id or msg_id or f"ctx_{task_id}"

        # Extract push notification config from protocol layer (A2A MessageSendConfiguration)
        push_notification_config = None
        if hasattr(params, "configuration") and params.configuration:
            if hasattr(params.configuration, "pushNotificationConfig"):
                push_notification_config = params.configuration.pushNotificationConfig
                logger.info(
                    f"Protocol-level push notification config provided for task {task_id}: {push_notification_config.url}"
                )

        # Prepare task metadata with both invocation types
        task_metadata: dict[str, Any] = {
            "request_text": combined_text,
            "invocation_type": "explicit_skill" if skill_invocations else "natural_language",
        }
        if skill_invocations:
            task_metadata["skills_requested"] = [inv["skill"] for inv in skill_invocations]

        # Store push notification config in metadata if provided
        if push_notification_config:
            task_metadata["push_notification_config"] = {
                "url": push_notification_config.url,
                "authentication": (
                    {
                        "schemes": (
                            push_notification_config.authentication.schemes
                            if push_notification_config.authentication
                            else []
                        ),
                        "credentials": (
                            push_notification_config.authentication.credentials
                            if push_notification_config.authentication
                            else None
                        ),
                    }
                    if push_notification_config.authentication
                    else None
                ),
            }

        task = Task(
            id=task_id,
            context_id=context_id,
            kind="task",
            status=TaskStatus(state=TaskState.working),
            metadata=task_metadata,
        )
        self.tasks[task_id] = task

        try:
            # Get authentication token
            auth_token = self._get_auth_token()

            # List of skills that don't require authentication (public discovery endpoints)
            public_skills = {"list_authorized_properties"}

            # Check if any requested skills require authentication
            requires_auth = True
            if skill_invocations:
                # If ALL skills are public, don't require auth
                requested_skills = {inv["skill"] for inv in skill_invocations}
                if requested_skills.issubset(public_skills):
                    requires_auth = False

            # Require authentication for non-public skills
            if requires_auth and not auth_token:
                raise ServerError(
                    InvalidRequestError(
                        message="Missing authentication token - Bearer token required in Authorization header"
                    )
                )

            # Route: Handle explicit skill invocations first, then natural language fallback
            if skill_invocations:
                # Process explicit skill invocations
                results = []
                for invocation in skill_invocations:
                    skill_name = invocation["skill"]
                    parameters = invocation["parameters"]
                    logger.info(f"Processing explicit skill: {skill_name} with parameters: {parameters}")

                    try:
                        result = await self._handle_explicit_skill(skill_name, parameters, auth_token)
                        results.append({"skill": skill_name, "result": result, "success": True})
                    except ServerError:
                        # ServerError should bubble up immediately (JSON-RPC error)
                        raise
                    except Exception as e:
                        logger.error(f"Error in explicit skill {skill_name}: {e}")
                        results.append({"skill": skill_name, "error": str(e), "success": False})

                # Create artifacts for all skill results with human-readable descriptions
                for i, res in enumerate(results):
                    artifact_data = res["result"] if res["success"] else {"error": res["error"]}

                    # Generate human-readable description from response __str__()
                    description = None
                    if res["success"] and isinstance(artifact_data, dict):
                        try:
                            response_obj = self._reconstruct_response_object(res["skill"], artifact_data)
                            if response_obj and hasattr(response_obj, "__str__"):
                                description = str(response_obj)
                        except Exception:
                            pass  # If reconstruction fails, skip description

                    task.artifacts = task.artifacts or []
                    task.artifacts.append(
                        Artifact(
                            artifact_id=f"skill_result_{i + 1}",
                            name=f"{'error' if not res['success'] else res['skill']}_result",
                            description=description,  # Human-readable message
                            parts=[Part(root=DataPart(data=artifact_data))],
                        )
                    )

                # Check if any skills failed and determine task status
                failed_skills = [res["skill"] for res in results if not res["success"]]
                successful_skills = [res["skill"] for res in results if res["success"]]

                if failed_skills and not successful_skills:
                    # All skills failed - mark task as failed
                    task.status = TaskStatus(state=TaskState.failed)

                    # Send protocol-level webhook notification for failure
                    error_messages = [res.get("error", "Unknown error") for res in results if not res["success"]]
                    await self._send_protocol_webhook(task, status="failed", error="; ".join(error_messages))

                    return task
                elif successful_skills:
                    # Log successful skill invocations with rich context
                    try:
                        tool_context = self._create_tool_context_from_a2a(auth_token, successful_skills[0])

                        # Extract meaningful details from results
                        log_details = {"skills": successful_skills, "count": len(successful_skills)}

                        # Add context from the first successful skill
                        first_result = next((r for r in results if r["success"]), None)
                        if first_result and "result" in first_result:
                            result_data = first_result["result"]

                            # Extract budget and package info for create_media_buy
                            if "create_media_buy" in first_result["skill"]:
                                if isinstance(result_data, dict):
                                    if "total_budget" in result_data:
                                        log_details["total_budget"] = result_data["total_budget"]
                                    if "packages" in result_data:
                                        log_details["package_count"] = len(result_data["packages"])
                                    if "media_buy_id" in result_data:
                                        log_details["media_buy_id"] = result_data["media_buy_id"]

                            # Extract product count for get_products
                            elif "get_products" in first_result["skill"]:
                                if isinstance(result_data, dict) and "products" in result_data:
                                    log_details["product_count"] = len(result_data["products"])

                            # Extract creative count for sync_creatives
                            elif "sync_creatives" in first_result["skill"]:
                                if isinstance(result_data, dict) and "creatives" in result_data:
                                    log_details["creative_count"] = len(result_data["creatives"])

                        self._log_a2a_operation(
                            "explicit_skill_invocation",
                            tool_context.tenant_id,
                            tool_context.principal_id,
                            True,
                            log_details,
                        )
                    except Exception as e:
                        logger.warning(f"Could not log skill invocations: {e}")

            # Natural language fallback (existing keyword-based routing)
            elif any(word in combined_text for word in ["product", "inventory", "available", "catalog"]):
                result = await self._get_products(combined_text, auth_token)
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_products")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_products",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "product_count": len(result.get("products", [])) if isinstance(result, dict) else 0,
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifact_id="product_catalog_1",
                        name="product_catalog",
                        parts=[Part(root=DataPart(data=result))],
                    )
                ]
            elif any(word in combined_text for word in ["price", "pricing", "cost", "cpm", "budget"]):
                result = self._get_pricing()
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_pricing")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_pricing",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "pricing_models": len(result.get("pricing_models", [])) if isinstance(result, dict) else 0,
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifact_id="pricing_info_1",
                        name="pricing_information",
                        parts=[Part(root=DataPart(data=result))],
                    )
                ]
            elif any(word in combined_text for word in ["target", "audience"]):
                result = self._get_targeting()
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_targeting")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_targeting",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "targeting_categories": (
                            len(result.get("targeting_options", {})) if isinstance(result, dict) else 0
                        ),
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifact_id="targeting_opts_1",
                        name="targeting_options",
                        parts=[Part(root=DataPart(data=result))],
                    )
                ]
            elif any(word in combined_text for word in ["create", "buy", "campaign", "media"]):
                result = await self._create_media_buy(combined_text, auth_token)
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "create_media_buy")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "create_media_buy",
                    tenant_id,
                    principal_id,
                    result.get("success", False),
                    {"query": combined_text[:100], "success": result.get("success", False)},
                    result.get("message") if not result.get("success") else None,
                )
                if result.get("success"):
                    task.artifacts = [
                        Artifact(
                            artifact_id="media_buy_1",
                            name="media_buy_created",
                            parts=[Part(root=DataPart(data=result))],
                        )
                    ]
                else:
                    task.artifacts = [
                        Artifact(
                            artifact_id="media_buy_error_1",
                            name="media_buy_error",
                            parts=[Part(root=DataPart(data=result))],
                        )
                    ]
            else:
                # General help response
                capabilities = {
                    "supported_queries": [
                        "product_catalog",
                        "targeting_options",
                        "pricing_information",
                        "campaign_creation",
                    ],
                    "example_queries": [
                        "What video ad products do you have available?",
                        "Show me targeting options",
                        "What are your pricing models?",
                        "How do I create a media buy?",
                    ],
                }
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_capabilities")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_capabilities",
                    tenant_id,
                    principal_id,
                    True,
                    {"query": combined_text[:100], "response_type": "capabilities"},
                )
                task.artifacts = [
                    Artifact(
                        artifact_id="capabilities_1",
                        name="capabilities",
                        parts=[Part(root=DataPart(data=capabilities))],
                    )
                ]

            # Mark task as completed
            task.status = TaskStatus(state=TaskState.completed)

            # Send protocol-level webhook notification if configured
            result_data = {}
            if task.artifacts:
                # Extract result from artifacts
                for artifact in task.artifacts:
                    if hasattr(artifact, "parts") and artifact.parts:
                        for part in artifact.parts:
                            if hasattr(part, "data") and part.data:
                                result_data[artifact.name] = part.data

            await self._send_protocol_webhook(task, status="completed", result=result_data)

        except ServerError:
            # Re-raise ServerError as-is (will be caught by JSON-RPC handler)
            raise
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Try to get context for error logging
            try:
                auth_token = self._get_auth_token()
                if auth_token:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "error_handler")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                else:
                    tenant_id = "unknown"
                    principal_id = "unknown"
            except:
                tenant_id = "unknown"
                principal_id = "unknown"

            self._log_a2a_operation(
                "message_processing",
                tenant_id,
                principal_id,
                False,
                {"error_type": type(e).__name__},
                str(e),
            )

            # Send protocol-level webhook notification for failure if configured
            task.status = TaskStatus(state=TaskState.failed)
            await self._send_protocol_webhook(task, status="failed", error=str(e))

            # Raise ServerError instead of creating failed task
            raise ServerError(InternalError(message=f"Message processing failed: {str(e)}"))

        self.tasks[task_id] = task
        return task

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Handle 'message/stream' method for streaming requests.

        Args:
            params: Parameters including the message and configuration
            context: Server call context

        Yields:
            Event objects from the agent's execution
        """
        # For now, implement non-streaming behavior
        # In production, this would yield events as they occur
        task = await self.on_message_send(params, context)

        # Yield a single event with the complete task
        yield Event(type="task_update", data=task.model_dump())

    async def on_get_task(
        self,
        params: TaskQueryParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handle 'tasks/get' method to retrieve task status.

        Args:
            params: Parameters specifying the task ID
            context: Server call context

        Returns:
            Task object if found, otherwise None
        """
        task_id = params.task_id
        return self.tasks.get(task_id)

    async def on_cancel_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handle 'tasks/cancel' method to cancel a task.

        Args:
            params: Parameters specifying the task ID
            context: Server call context

        Returns:
            Task object with canceled status, or None if not found
        """
        task_id = params.task_id
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus(state=TaskState.canceled)
            self.tasks[task_id] = task
        return task

    async def on_resubscribe_to_task(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle task resubscription requests."""
        # Not implemented for now
        from a2a.types import UnsupportedOperationError

        raise UnsupportedOperationError("Task resubscription not supported")

    async def on_get_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle get push notification config requests.

        Retrieves the push notification configuration for a specific config ID.
        """
        from a2a.types import InvalidParamsError, NotFoundError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "get_push_notification_config")

            # Extract config_id from params
            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise ServerError(InvalidParamsError(message="Missing required parameter: id"))

            # Query database for config
            with get_db_session() as db:
                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id,
                    tenant_id=tool_context.tenant_id,
                    principal_id=tool_context.principal_id,
                    is_active=True,
                )
                config = db.scalars(stmt).first()

                if not config:
                    raise ServerError(NotFoundError(message=f"Push notification config not found: {config_id}"))

                # Return A2A PushNotificationConfig format
                return {
                    "id": config.id,
                    "url": config.url,
                    "authentication": (
                        {"type": config.authentication_type or "none", "token": config.authentication_token}
                        if config.authentication_type
                        else None
                    ),
                    "token": config.validation_token,
                }

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error getting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to get push notification config: {str(e)}"))

    async def on_set_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle set push notification config requests.

        Creates or updates a push notification configuration for async operation callbacks.
        Buyers use this to register webhook URLs where they want to receive status updates.
        """
        import uuid
        from datetime import UTC, datetime

        from a2a.types import InvalidParamsError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "set_push_notification_config")

            # Extract parameters (A2A spec format)
            # Params structure: {task_id, push_notification_config: {url, authentication}}
            # Note: params comes as Pydantic object with snake_case attributes
            logger.info(f"[DEBUG] Received params type: {type(params)}, value: {params}")

            task_id = getattr(params, "task_id", None)
            push_config = getattr(params, "push_notification_config", None)

            logger.info(f"[DEBUG] task_id: {task_id}, push_config: {push_config}, type: {type(push_config)}")

            # Extract URL and authentication from push_config object
            url = getattr(push_config, "url", None) if push_config else None
            authentication = getattr(push_config, "authentication", None) if push_config else None
            config_id = getattr(push_config, "id", None) if push_config else None
            config_id = config_id or f"pnc_{uuid.uuid4().hex[:16]}"
            validation_token = getattr(push_config, "token", None) if push_config else None
            session_id = None  # Not in A2A spec

            if not url:
                raise ServerError(InvalidParamsError(message="Missing required parameter: url"))

            # Extract authentication details (A2A spec format: schemes, credentials)
            auth_type = None
            auth_token_value = None
            if authentication:
                if isinstance(authentication, dict):
                    # A2A spec uses "schemes" (array) and "credentials" (string)
                    schemes = authentication.get("schemes", [])
                    auth_type = schemes[0] if schemes else None
                    auth_token_value = authentication.get("credentials")
                else:
                    schemes = getattr(authentication, "schemes", [])
                    auth_type = schemes[0] if schemes else None
                    auth_token_value = getattr(authentication, "credentials", None)

            # Create or update configuration
            with get_db_session() as db:
                # Check if config exists
                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id
                )
                existing_config = db.scalars(stmt).first()

                if existing_config:
                    # Update existing config
                    existing_config.url = url
                    existing_config.authentication_type = auth_type
                    existing_config.authentication_token = auth_token_value
                    existing_config.validation_token = validation_token
                    existing_config.session_id = session_id
                    existing_config.updated_at = datetime.now(UTC)
                    existing_config.is_active = True
                else:
                    # Create new config
                    new_config = DBPushNotificationConfig(
                        id=config_id,
                        tenant_id=tool_context.tenant_id,
                        principal_id=tool_context.principal_id,
                        session_id=session_id,
                        url=url,
                        authentication_type=auth_type,
                        authentication_token=auth_token_value,
                        validation_token=validation_token,
                        is_active=True,
                    )
                    db.add(new_config)

                db.commit()

                logger.info(
                    f"Push notification config {'updated' if existing_config else 'created'}: {config_id} for tenant {tool_context.tenant_id}"
                )

                # Return A2A response (TaskPushNotificationConfig format)
                from a2a.types import (
                    PushNotificationAuthenticationInfo,
                    PushNotificationConfig,
                    TaskPushNotificationConfig,
                )

                # Build authentication info if present
                auth_info = None
                if auth_type and auth_token_value:
                    auth_info = PushNotificationAuthenticationInfo(schemes=[auth_type], credentials=auth_token_value)

                # Build push notification config
                pnc = PushNotificationConfig(url=url, authentication=auth_info, id=config_id, token=validation_token)

                # Return TaskPushNotificationConfig
                return TaskPushNotificationConfig(task_id=task_id or "*", push_notification_config=pnc)

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error setting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to set push notification config: {str(e)}"))

    async def on_list_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle list push notification config requests.

        Returns all active push notification configurations for the authenticated principal.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "list_push_notification_configs")

            # Query database for all active configs
            with get_db_session() as db:
                stmt = select(DBPushNotificationConfig).filter_by(
                    tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id, is_active=True
                )
                configs = db.scalars(stmt).all()

                # Convert to A2A format
                configs_list = []
                for config in configs:
                    configs_list.append(
                        {
                            "id": config.id,
                            "url": config.url,
                            "authentication": (
                                {"type": config.authentication_type or "none", "token": config.authentication_token}
                                if config.authentication_type
                                else None
                            ),
                            "token": config.validation_token,
                            "created_at": config.created_at.isoformat() if config.created_at else None,
                        }
                    )

                logger.info(f"Listed {len(configs_list)} push notification configs for tenant {tool_context.tenant_id}")

                return {"configs": configs_list, "total_count": len(configs_list)}

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error listing push notification configs: {e}")
            raise ServerError(InternalError(message=f"Failed to list push notification configs: {str(e)}"))

    async def on_delete_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle delete push notification config requests.

        Marks a push notification configuration as inactive (soft delete).
        """
        from datetime import UTC, datetime

        from a2a.types import InvalidParamsError, NotFoundError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "delete_push_notification_config")

            # Extract config_id from params
            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise ServerError(InvalidParamsError(message="Missing required parameter: id"))

            # Query database and mark as inactive
            with get_db_session() as db:
                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id
                )
                config = db.scalars(stmt).first()

                if not config:
                    raise ServerError(NotFoundError(message=f"Push notification config not found: {config_id}"))

                # Soft delete by marking as inactive
                config.is_active = False
                config.updated_at = datetime.now(UTC)
                db.commit()

                logger.info(f"Deleted push notification config: {config_id} for tenant {tool_context.tenant_id}")

                return {
                    "id": config_id,
                    "status": "deleted",
                    "message": "Push notification configuration deleted successfully",
                }

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error deleting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to delete push notification config: {str(e)}"))

    async def _handle_explicit_skill(self, skill_name: str, parameters: dict, auth_token: str | None) -> dict:
        """Handle explicit AdCP skill invocations.

        Maps skill names to appropriate handlers and validates parameters.

        Args:
            skill_name: The AdCP skill name (e.g., "get_products")
            parameters: Dictionary of skill-specific parameters
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing the skill result

        Raises:
            ValueError: For unknown skills or invalid parameters
        """
        logger.info(f"Handling explicit skill: {skill_name} with parameters: {list(parameters.keys())}")

        # Map skill names to handlers
        skill_handlers = {
            # Core AdCP Media Buy Skills
            "get_products": self._handle_get_products_skill,
            "create_media_buy": self._handle_create_media_buy_skill,
            # ✅ NEW: Missing AdCP Discovery Skills (CRITICAL for protocol compliance)
            "list_creative_formats": self._handle_list_creative_formats_skill,
            "list_authorized_properties": self._handle_list_authorized_properties_skill,
            # ✅ NEW: Missing Media Buy Management Skills (CRITICAL for campaign lifecycle)
            "update_media_buy": self._handle_update_media_buy_skill,
            "get_media_buy_delivery": self._handle_get_media_buy_delivery_skill,
            "update_performance_index": self._handle_update_performance_index_skill,
            # AdCP Spec Creative Management (centralized library approach)
            "sync_creatives": self._handle_sync_creatives_skill,
            "list_creatives": self._handle_list_creatives_skill,
            # Creative Management & Approval
            "approve_creative": self._handle_approve_creative_skill,
            "get_media_buy_status": self._handle_get_media_buy_status_skill,
            "optimize_media_buy": self._handle_optimize_media_buy_skill,
            # Core AdCP Signals Skills
            "get_signals": self._handle_get_signals_skill,
            "search_signals": self._handle_search_signals_skill,
            # Legacy skill names (for backward compatibility)
            "get_pricing": lambda params, token: self._get_pricing(),
            "get_targeting": lambda params, token: self._get_targeting(),
        }

        if skill_name not in skill_handlers:
            available_skills = list(skill_handlers.keys())
            raise ServerError(
                MethodNotFoundError(message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}")
            )

        try:
            handler = skill_handlers[skill_name]
            if skill_name in ["get_pricing", "get_targeting"]:
                # These are simple handlers without async
                return handler(parameters, auth_token)
            else:
                # These are async handlers that call core tools
                return await handler(parameters, auth_token)
        except ServerError:
            # Re-raise ServerError as-is (already properly formatted)
            raise
        except Exception as e:
            logger.error(f"Error in skill handler {skill_name}: {e}")
            raise ServerError(InternalError(message=f"Skill {skill_name} failed: {str(e)}"))

    async def _handle_get_products_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_products skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_products",
            )

            # Map A2A parameters to GetProductsRequest
            brief = parameters.get("brief", "")
            promoted_offering = parameters.get("promoted_offering", "")

            if not brief and not promoted_offering:
                raise ServerError(
                    InvalidParamsError(message="Either 'brief' or 'promoted_offering' parameter is required")
                )

            # Use brief as promoted_offering if not provided
            if not promoted_offering and brief:
                promoted_offering = f"Business seeking to advertise: {brief}"

            # Call core function directly with individual parameters, not request object
            response = await core_get_products_tool(
                brief=brief, promoted_offering=promoted_offering, context=tool_context
            )

            # Convert response to dict
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump()

            # Add A2A protocol fields (message for human readability)
            # Use __str__() method which all response types implement
            response_data["message"] = (
                str(response) if not isinstance(response, dict) else "Products retrieved successfully"
            )

            return response_data

        except Exception as e:
            logger.error(f"Error in get_products skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve products: {str(e)}"))

    async def _handle_create_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit create_media_buy skill invocation.

        IMPORTANT: This handler ONLY accepts AdCP spec-compliant format:
        - packages[] (required)
        - budget{} (required)
        - start_time (required)
        - end_time (required)
        - promoted_offering (required)

        Legacy format (product_ids, total_budget, start_date, end_date) is NOT supported.
        """
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_media_buy",
            )

            # Validate AdCP spec required parameters
            required_params = [
                "brand_manifest",
                "packages",
                "budget",
                "start_time",
                "end_time",
            ]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required AdCP parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                    "errors": [
                        {
                            "code": "validation_error",
                            "message": f"Missing required AdCP parameters: {missing_params}",
                            "details": {
                                "required": required_params,
                                "received": list(parameters.keys()),
                            },
                        }
                    ],
                }

            # Call core function with AdCP spec-compliant parameters
            response = await core_create_media_buy_tool(
                brand_manifest=parameters["brand_manifest"],
                po_number=parameters.get("po_number", f"A2A-{uuid.uuid4().hex[:8]}"),
                buyer_ref=parameters.get("buyer_ref", f"A2A-{tool_context.principal_id}"),
                packages=parameters["packages"],
                start_time=parameters["start_time"],
                end_time=parameters["end_time"],
                budget=parameters["budget"],
                targeting_overlay=parameters.get("custom_targeting", {}),
                push_notification_config=parameters.get("push_notification_config"),
                context=tool_context,
            )

            # Convert response to dict and add A2A success wrapper
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump()

            # Check if response contains errors (domain errors from validation/ad server)
            has_errors = response_data.get("errors") and len(response_data.get("errors", [])) > 0

            # A2A wrapper adds success field and message
            # Success is False if there are domain errors, even if no exception was raised
            response_data["success"] = not has_errors
            if has_errors:
                response_data["message"] = "Media buy creation failed with validation errors"
            else:
                response_data["message"] = "Media buy created successfully"

            return response_data

        except Exception as e:
            logger.error(f"Error in create_media_buy skill: {e}")
            # Return error response instead of raising ServerError
            # This allows tests to check error structure in artifacts
            return {
                "success": False,
                "message": f"Failed to create media buy: {str(e)}",
                "errors": [
                    {
                        "code": (
                            "authentication_error"
                            if "foreign key" in str(e).lower() and "principal" in str(e).lower()
                            else "internal_error"
                        ),
                        "message": str(e),
                    }
                ],
            }

    async def _handle_sync_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit sync_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="sync_creatives",
            )

            # Map A2A parameters - creatives is required
            if "creatives" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'creatives'",
                    "required_parameters": ["creatives"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with spec-compliant parameters (AdCP v2.4)
            response = core_sync_creatives_tool(
                creatives=parameters["creatives"],
                patch=parameters.get("patch", False),
                assignments=parameters.get("assignments"),
                delete_missing=parameters.get("delete_missing", False),
                dry_run=parameters.get("dry_run", False),
                validation_mode=parameters.get("validation_mode", "strict"),
                push_notification_config=parameters.get("push_notification_config"),
                context=tool_context,
            )

            # Convert response to dict
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump()

            # Add A2A protocol fields
            # Check for errors in response
            has_errors = response_data.get("errors") and len(response_data.get("errors", [])) > 0

            response_data["success"] = not has_errors
            response_data["message"] = (
                "Creatives synced with errors"
                if has_errors
                else str(response) if not isinstance(response, dict) else "Creatives synced successfully"
            )

            return response_data

        except Exception as e:
            logger.error(f"Error in sync_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to sync creatives: {str(e)}"))

    async def _handle_list_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit list_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="list_creatives",
            )

            # Call core function with optional parameters (fixing original validation bug)
            response = core_list_creatives_tool(
                media_buy_id=parameters.get("media_buy_id"),
                buyer_ref=parameters.get("buyer_ref"),
                status=parameters.get("status"),
                format=parameters.get("format"),
                tags=parameters.get("tags", []),
                created_after=parameters.get("created_after"),
                created_before=parameters.get("created_before"),
                search=parameters.get("search"),
                page=parameters.get("page", 1),
                limit=parameters.get("limit", 50),
                sort_by=parameters.get("sort_by", "created_date"),
                sort_order=parameters.get("sort_order", "desc"),
                context=tool_context,
            )

            # Convert response to dict
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump()

            # Add A2A protocol fields (message for human readability)
            # Use __str__() method which all response types implement
            response_data["message"] = (
                str(response) if not isinstance(response, dict) else "Creatives listed successfully"
            )

            return response_data

        except Exception as e:
            logger.error(f"Error in list_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to list creatives: {str(e)}"))

    async def _handle_create_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit create_creative skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_creative",
            )

            # Map A2A parameters - format_id, content_uri, and name are required
            required_params = ["format_id", "content_uri", "name"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with individual parameters
            response = core_create_creative_tool(
                format_id=parameters["format_id"],
                content_uri=parameters["content_uri"],
                name=parameters["name"],
                group_id=parameters.get("group_id"),
                click_through_url=parameters.get("click_through_url"),
                metadata=parameters.get("metadata", {}),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "creative_id": response.creative_id,
                "message": str(response),  # Use __str__ method for human-readable message
                "status": response.status if hasattr(response, "status") else "created",
            }

        except Exception as e:
            logger.error(f"Error in create_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to create creative: {str(e)}"))

    async def _handle_get_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_creatives skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_creatives",
            )

            # Call core function with optional parameters
            response = core_get_creatives_tool(
                group_id=parameters.get("group_id"),
                media_buy_id=parameters.get("media_buy_id"),
                status=parameters.get("status"),
                tags=parameters.get("tags", []),
                include_assignments=parameters.get("include_assignments", False),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "creatives": [creative.model_dump() for creative in response.creatives],
                "message": str(response),  # Use __str__ method for human-readable message
                "total_count": len(response.creatives),
            }

        except Exception as e:
            logger.error(f"Error in get_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to get creatives: {str(e)}"))

    async def _handle_assign_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit assign_creative skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="assign_creative",
            )

            # Map A2A parameters - media_buy_id, package_id, and creative_id are required
            required_params = ["media_buy_id", "package_id", "creative_id"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with individual parameters
            response = core_assign_creative_tool(
                media_buy_id=parameters["media_buy_id"],
                package_id=parameters["package_id"],
                creative_id=parameters["creative_id"],
                weight=parameters.get("weight", 100),
                percentage_goal=parameters.get("percentage_goal"),
                rotation_type=parameters.get("rotation_type", "weighted"),
                override_click_url=parameters.get("override_click_url"),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "assignment_id": response.assignment_id,
                "message": str(response),  # Use __str__ method for human-readable message
                "status": response.status if hasattr(response, "status") else "assigned",
            }

        except Exception as e:
            logger.error(f"Error in assign_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to assign creative: {str(e)}"))

    async def _handle_approve_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit approve_creative skill invocation."""
        # TODO: Implement full approve_creative skill handler
        return {
            "success": False,
            "message": "approve_creative skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_get_signals_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_signals skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_signals",
            )

            # Map A2A parameters to GetSignalsRequest (per AdCP spec: signal_spec and deliver_to required)
            if "signal_spec" not in parameters or "deliver_to" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameters: 'signal_spec' and 'deliver_to'",
                    "required_parameters": ["signal_spec", "deliver_to"],
                    "received_parameters": list(parameters.keys()),
                }

            request = GetSignalsRequest(
                signal_spec=parameters["signal_spec"],
                deliver_to=parameters["deliver_to"],
                filters=parameters.get("filters"),
                max_results=parameters.get("max_results"),
            )

            # Call core function directly
            response = await core_get_signals_tool(request, tool_context)

            # Handle both dict and object responses (defensive pattern)
            if isinstance(response, dict):
                signals = response.get("signals", [])
                signals_list = signals
            else:
                signals = response.signals
                signals_list = [signal.model_dump() for signal in signals]

            # Convert response to A2A format
            return {
                "signals": signals_list,
                "message": "Signals retrieved successfully",
                "total_count": len(signals_list),
            }

        except Exception as e:
            logger.error(f"Error in get_signals skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve signals: {str(e)}"))

    async def _handle_search_signals_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit search_signals skill invocation."""
        # TODO: Implement full search_signals skill handler
        return {
            "signals": [],
            "message": "search_signals skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_get_media_buy_status_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_media_buy_status skill invocation."""
        # TODO: Implement full get_media_buy_status skill handler
        return {
            "success": False,
            "message": "get_media_buy_status skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_optimize_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit optimize_media_buy skill invocation."""
        # TODO: Implement full optimize_media_buy skill handler
        return {
            "success": False,
            "message": "optimize_media_buy skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_list_creative_formats_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit list_creative_formats skill invocation (CRITICAL AdCP endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="list_creative_formats",
            )

            # Build request from parameters (all optional)
            from src.core.schemas import ListCreativeFormatsRequest

            req = ListCreativeFormatsRequest(
                adcp_version=parameters.get("adcp_version", "1.0.0"),
                type=parameters.get("type"),
                standard_only=parameters.get("standard_only"),
                category=parameters.get("category"),
                format_ids=parameters.get("format_ids"),
            )

            # Call core function with request
            response = core_list_creative_formats_tool(req=req, context=tool_context)

            # Convert response to dict
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump()

            # Add A2A protocol fields (message for human readability)
            # Use __str__() method which all response types implement
            response_data["message"] = (
                str(response) if not isinstance(response, dict) else "Creative formats retrieved successfully"
            )

            return response_data

        except Exception as e:
            logger.error(f"Error in list_creative_formats skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve creative formats: {str(e)}"))

    async def _handle_list_authorized_properties_skill(self, parameters: dict, auth_token: str | None) -> dict:
        """Handle explicit list_authorized_properties skill invocation (CRITICAL AdCP endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        If no auth token provided, uses headers for tenant detection.

        Per AdCP v2.4 spec, returns publisher_domains (not properties/tags).
        """
        try:
            # Create ToolContext from A2A auth info (which sets tenant context as side effect)
            tool_context = None

            if auth_token:
                try:
                    tool_context = self._create_tool_context_from_a2a(
                        auth_token=auth_token,
                        tool_name="list_authorized_properties",
                    )
                except Exception as e:
                    logger.warning(f"Failed to create authenticated context (continuing without auth): {e}")
                    tool_context = None
            else:
                # No auth token - create minimal Context-like object with headers for tenant detection
                # This allows tenant detection via Apx-Incoming-Host, Host, or x-adcp-tenant headers
                headers = _request_headers.get() or {}

                # Create a simple object that quacks like a FastMCP Context
                # get_principal_from_context() needs: context.meta["headers"] or context.headers
                class MinimalContext:
                    def __init__(self, headers):
                        self.meta = {"headers": headers}
                        self.headers = headers

                tool_context = MinimalContext(headers)

            # Map A2A parameters to ListAuthorizedPropertiesRequest
            request = ListAuthorizedPropertiesRequest(tags=parameters.get("tags", []))

            # Call core function directly
            # Context can be None for unauthenticated calls - tenant will be detected from headers
            response = core_list_authorized_properties_tool(req=request, context=tool_context)

            # Return spec-compliant response (no extra fields)
            # Per AdCP v2.4 spec: only publisher_domains, primary_channels, primary_countries,
            # portfolio_description, advertising_policies, last_updated, and errors
            if isinstance(response, dict):
                return response
            else:
                return response.model_dump()

        except Exception as e:
            logger.error(f"Error in list_authorized_properties skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve authorized properties: {str(e)}"))

    async def _handle_update_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit update_media_buy skill invocation (CRITICAL for campaign management)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="update_media_buy",
            )

            # Validate required parameters (per AdCP v2.0+ spec: media_buy_id + optional packages)
            if "media_buy_id" not in parameters and "buyer_ref" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: one of 'media_buy_id' or 'buyer_ref' is required",
                    "required_parameters": ["media_buy_id (or buyer_ref)"],
                    "received_parameters": list(parameters.keys()),
                }

            # Extract update parameters (AdCP v2.0+ uses individual fields, not 'updates' wrapper)
            # Support both 'packages' (AdCP v2.0+) and legacy 'updates' field for backward compatibility
            packages = parameters.get("packages")
            if packages is None and "updates" in parameters:
                # Legacy format: extract packages from updates object
                packages = parameters["updates"].get("packages")

            # Call core function directly with AdCP v2.0+ parameter names
            response = core_update_media_buy_tool(
                media_buy_id=parameters.get("media_buy_id"),
                buyer_ref=parameters.get("buyer_ref"),
                active=parameters.get("active"),
                start_time=parameters.get("start_time"),
                end_time=parameters.get("end_time"),
                budget=parameters.get("budget"),
                packages=packages,
                push_notification_config=parameters.get("push_notification_config"),
                context=tool_context,
            )

            # Return spec-compliant response (no extra fields)
            # Per AdCP spec: all fields from UpdateMediaBuyResponse
            if isinstance(response, dict):
                return response
            else:
                return response.model_dump()

        except Exception as e:
            logger.error(f"Error in update_media_buy skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update media buy: {str(e)}"))

    async def _handle_get_media_buy_delivery_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_media_buy_delivery skill invocation (CRITICAL for monitoring).

        Accepts media_buy_ids (plural, per AdCP v1.6.0 spec) or media_buy_id (singular, legacy).
        """
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_media_buy_delivery",
            )

            # Extract media_buy_ids - support both plural (spec) and singular (legacy)
            media_buy_ids = parameters.get("media_buy_ids")
            if not media_buy_ids:
                # Fallback to singular form for backward compatibility
                media_buy_id = parameters.get("media_buy_id")
                if media_buy_id:
                    media_buy_ids = [media_buy_id]

            # Validate that we have at least one ID
            if not media_buy_ids:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'media_buy_ids' (or 'media_buy_id' for single buy)",
                    "required_parameters": ["media_buy_ids"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function directly with spec-compliant plural parameter
            response = core_get_media_buy_delivery_tool(
                media_buy_ids=media_buy_ids,
                context=tool_context,
            )

            # Convert response to dict for A2A format
            return response.model_dump() if hasattr(response, "model_dump") else response

        except Exception as e:
            logger.error(f"Error in get_media_buy_delivery skill: {e}")
            raise ServerError(InternalError(message=f"Unable to get media buy delivery: {str(e)}"))

    async def _handle_update_performance_index_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit update_performance_index skill invocation (CRITICAL for optimization)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="update_performance_index",
            )

            # Validate required parameters
            required_params = ["media_buy_id", "performance_data"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function directly
            response = core_update_performance_index_tool(
                media_buy_id=parameters["media_buy_id"],
                performance_data=parameters["performance_data"],
                context=tool_context,
            )

            # Return spec-compliant response (no extra fields)
            # Per AdCP spec: all fields from ProvidePerformanceFeedbackResponse
            if isinstance(response, dict):
                return response
            else:
                return response.model_dump()

        except Exception as e:
            logger.error(f"Error in update_performance_index skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update performance index: {str(e)}"))

    async def _get_products(self, query: str, auth_token: str | None) -> dict:
        """Get available advertising products by calling core functions directly.

        Args:
            query: User's product query
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing product information
        """
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_products",
            )

            # Extract promoted offering from the query or use a reasonable default
            promoted_offering = self._extract_promoted_offering_from_query(query)

            # Call core function directly using the underlying function
            response = await core_get_products_tool(
                brief=query, promoted_offering=promoted_offering, context=tool_context
            )

            # Convert to A2A response format
            return {
                "products": [product.model_dump() for product in response.products],
                "message": str(response),  # Use __str__ method for human-readable message
            }

        except Exception as e:
            logger.error(f"Error getting products: {e}")
            # Return empty products list instead of fallback data
            return {"products": [], "message": f"Unable to retrieve products: {str(e)}"}

    def _extract_promoted_offering_from_query(self, query: str) -> str:
        """Extract or infer promoted_offering from the user query.

        AdCP requires promoted_offering to be provided. We'll try to extract
        it from the query or provide a reasonable default.
        """
        # Look for common patterns that might indicate the promoted offering
        query_lower = query.lower()

        # If the query mentions specific brands or products, use those
        if "advertise" in query_lower or "promote" in query_lower:
            # Try to extract what they're promoting
            parts = query.split()
            for i, word in enumerate(parts):
                if word.lower() in ["advertise", "promote", "advertising", "promoting"]:
                    if i + 1 < len(parts):
                        # Take the next few words as the offering
                        offering_parts = parts[i + 1 : i + 4]  # Take up to 3 words
                        offering = " ".join(offering_parts).strip(".,!?")
                        if len(offering) > 5:  # Make sure it's substantial
                            return f"Business promoting {offering}"

        # Default offering based on query type
        if any(word in query_lower for word in ["video", "display", "banner", "ad"]):
            return "Brand advertising products and services"
        elif any(word in query_lower for word in ["coffee", "beverage", "food"]):
            return "Food and beverage company"
        elif any(word in query_lower for word in ["tech", "software", "app", "digital"]):
            return "Technology company digital products"
        else:
            # Generic fallback that should pass AdCP validation
            return "Business advertising products and services"

    def _get_pricing(self) -> dict:
        """Get pricing information.

        Returns:
            Dictionary containing pricing models and information
        """
        return {
            "pricing_models": [
                {
                    "type": "CPM",
                    "description": "Cost per thousand impressions",
                    "ranges": {
                        "video": {"min": 15, "max": 50},
                        "display": {"min": 2, "max": 10},
                        "native": {"min": 5, "max": 20},
                    },
                },
                {
                    "type": "CPC",
                    "description": "Cost per click",
                    "ranges": {"min": 0.50, "max": 5.00},
                },
                {
                    "type": "Guaranteed",
                    "description": "Fixed price for guaranteed delivery",
                    "minimum_commitment": 10000,
                },
            ],
            "volume_discounts": [
                {"threshold": 50000, "discount": "5%"},
                {"threshold": 100000, "discount": "10%"},
                {"threshold": 500000, "discount": "15%"},
            ],
        }

    def _get_targeting(self) -> dict:
        """Get available targeting options.

        Returns:
            Dictionary containing targeting capabilities
        """
        return {
            "targeting_options": {
                "demographics": {
                    "age_ranges": ["18-24", "25-34", "35-44", "45-54", "55+"],
                    "gender": ["male", "female", "unknown"],
                    "household_income": ["0-50k", "50-100k", "100-150k", "150k+"],
                },
                "geography": {
                    "levels": ["country", "state", "dma", "city", "zip"],
                    "available_countries": ["US", "CA", "UK", "AU"],
                },
                "interests": {
                    "categories": [
                        "Technology",
                        "Sports",
                        "Entertainment",
                        "Travel",
                        "Food & Dining",
                        "Health & Fitness",
                    ]
                },
                "contextual": {
                    "content_categories": ["News", "Sports", "Entertainment", "Business"],
                    "keywords": "Custom keyword targeting available",
                },
                "devices": {
                    "types": ["desktop", "mobile", "tablet", "ctv"],
                    "operating_systems": ["ios", "android", "windows", "macos"],
                },
            }
        }

    async def _create_media_buy(self, request: str, auth_token: str | None) -> dict:
        """Create a media buy based on the request.

        Args:
            request: User's media buy request
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing media buy creation result
        """
        # For now, return a mock response indicating authentication is working
        # but media buy creation needs more implementation
        try:
            # Verify authentication works
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_media_buy",
            )

            return {
                "success": False,
                "message": f"Authentication successful for {tool_context.principal_id}. To create a media buy, use explicit skill invocation with AdCP v2.4 spec-compliant format.",
                "required_fields": ["promoted_offering", "packages", "budget", "start_time", "end_time"],
                "authenticated_tenant": tool_context.tenant_id,
                "authenticated_principal": tool_context.principal_id,
                "example": {
                    "promoted_offering": "https://example.com/product",
                    "packages": [
                        {
                            "buyer_ref": "pkg_1",
                            "products": ["video_premium"],
                            "budget": {"total": 10000, "currency": "USD"},
                        }
                    ],
                    "budget": {"total": 10000, "currency": "USD"},
                    "start_time": "2025-02-01T00:00:00Z",
                    "end_time": "2025-02-28T23:59:59Z",
                },
                "documentation": "https://adcontextprotocol.org/docs/",
            }
        except Exception as e:
            logger.error(f"Error in media buy creation: {e}")
            raise ServerError(InternalError(message=f"Authentication failed: {str(e)}"))


def create_agent_card() -> AgentCard:
    """Create the agent card describing capabilities.

    Returns:
        AgentCard with AdCP Sales Agent capabilities
    """
    # Use new production domain for agent card
    # Note: This will be overridden dynamically in the endpoint handlers
    server_url = "https://sales-agent.scope3.com/a2a"

    from a2a.types import AgentCapabilities, AgentSkill

    # Create the agent card with minimal required fields
    agent_card = AgentCard(
        name="AdCP Sales Agent",
        description="AI agent for programmatic advertising campaigns via AdCP protocol",
        version="1.0.0",
        protocol_version="1.0",
        capabilities=AgentCapabilities(pushNotifications=True),
        default_input_modes=["message"],
        default_output_modes=["message"],
        skills=[
            # Core AdCP Media Buy Skills
            AgentSkill(
                id="get_products",
                name="get_products",
                description="Browse available advertising products and inventory",
                tags=["products", "inventory", "catalog", "adcp"],
            ),
            AgentSkill(
                id="create_media_buy",
                name="create_media_buy",
                description="Create advertising campaigns with products, targeting, and budget",
                tags=["campaign", "media", "buy", "adcp"],
            ),
            # ✅ NEW: Critical AdCP Discovery Endpoints (REQUIRED for protocol compliance)
            AgentSkill(
                id="list_creative_formats",
                name="list_creative_formats",
                description="List all available creative formats and specifications",
                tags=["creative", "formats", "specs", "discovery", "adcp"],
            ),
            AgentSkill(
                id="list_authorized_properties",
                name="list_authorized_properties",
                description="List authorized properties this agent can sell advertising for",
                tags=["properties", "authorization", "publisher", "adcp"],
            ),
            # ✅ NEW: Media Buy Management Skills (CRITICAL for campaign lifecycle)
            AgentSkill(
                id="update_media_buy",
                name="update_media_buy",
                description="Update existing media buy configuration and settings",
                tags=["campaign", "update", "management", "adcp"],
            ),
            AgentSkill(
                id="get_media_buy_delivery",
                name="get_media_buy_delivery",
                description="Get delivery metrics and performance data for media buys",
                tags=["delivery", "metrics", "performance", "monitoring", "adcp"],
            ),
            AgentSkill(
                id="update_performance_index",
                name="update_performance_index",
                description="Update performance data and optimization metrics",
                tags=["performance", "optimization", "metrics", "adcp"],
            ),
            # AdCP Spec Creative Management (centralized library approach)
            AgentSkill(
                id="sync_creatives",
                name="sync_creatives",
                description="Upload and manage creative assets to centralized library (AdCP spec)",
                tags=["creative", "sync", "library", "adcp", "spec"],
            ),
            AgentSkill(
                id="list_creatives",
                name="list_creatives",
                description="Search and query creative library with advanced filtering (AdCP spec)",
                tags=["creative", "library", "search", "adcp", "spec"],
            ),
            # Creative Management & Approval
            AgentSkill(
                id="approve_creative",
                name="approve_creative",
                description="Review and approve/reject creative assets (admin only)",
                tags=["creative", "approval", "review", "adcp"],
            ),
            AgentSkill(
                id="get_media_buy_status",
                name="get_media_buy_status",
                description="Check status and performance of media buys",
                tags=["status", "performance", "tracking", "adcp"],
            ),
            AgentSkill(
                id="optimize_media_buy",
                name="optimize_media_buy",
                description="Optimize media buy performance and targeting",
                tags=["optimization", "performance", "targeting", "adcp"],
            ),
            # Core AdCP Signals Skills (2 total)
            AgentSkill(
                id="get_signals",
                name="get_signals",
                description="Discover available targeting signals (audiences, contextual, etc.)",
                tags=["signals", "targeting", "discovery", "adcp"],
            ),
            AgentSkill(
                id="search_signals",
                name="search_signals",
                description="Search and filter targeting signals by criteria",
                tags=["signals", "search", "targeting", "adcp"],
            ),
            # Legacy Skills (for backward compatibility)
            AgentSkill(
                id="get_pricing",
                name="get_pricing",
                description="Get pricing information and rate cards",
                tags=["pricing", "cost", "budget", "legacy"],
            ),
            AgentSkill(
                id="get_targeting",
                name="get_targeting",
                description="Explore available targeting options",
                tags=["targeting", "audience", "demographics", "legacy"],
            ),
        ],
        url=server_url,
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


def main():
    """Main entry point for the A2A server."""
    host = os.getenv("A2A_HOST", "0.0.0.0")
    port = int(os.getenv("A2A_PORT", "8091"))

    # Initialize components
    agent_card = create_agent_card()
    request_handler = AdCPRequestHandler()

    logger.info(f"Starting AdCP A2A Agent on {host}:{port}")
    logger.info("Using official a2a-sdk with A2AStarletteApplication")

    # Create Starlette application
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Build the Starlette app with standard A2A specification endpoints
    app = a2a_app.build(
        agent_card_url="/.well-known/agent-card.json",  # Primary A2A discovery endpoint
        rpc_url="/a2a",  # Standard JSON-RPC endpoint
        extended_agent_card_url="/agent.json",
    )

    # Add CORS middleware for browser compatibility (must be added early to wrap all responses)
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for A2A protocol
        allow_credentials=True,
        allow_methods=["*"],  # Allow all HTTP methods
        allow_headers=["*"],  # Allow all headers
    )
    logger.info("CORS middleware enabled for browser compatibility")

    # Override the agent card endpoints to support tenant-specific URLs
    def create_dynamic_agent_card(request) -> AgentCard:
        """Create agent card with tenant-specific URL from request headers."""
        # Debug logging
        logger.info(f"Agent card request headers: {dict(request.headers)}")

        # Helper to get header case-insensitively
        def get_header_case_insensitive(headers, header_name: str) -> str | None:
            """Get header value with case-insensitive lookup."""
            for key, value in headers.items():
                if key.lower() == header_name.lower():
                    return value
            return None

        # Determine protocol based on host (localhost = HTTP, others = HTTPS)
        def get_protocol(hostname: str) -> str:
            """Return HTTP for localhost, HTTPS for production domains."""
            return "http" if hostname.startswith("localhost") or hostname.startswith("127.0.0.1") else "https"

        # Check for Approximated routing first (takes priority)
        apx_incoming_host = get_header_case_insensitive(request.headers, "Apx-Incoming-Host")
        if apx_incoming_host:
            # Use the original host from Approximated - preserve the exact domain
            protocol = get_protocol(apx_incoming_host)
            server_url = f"{protocol}://{apx_incoming_host}/a2a"
            logger.info(f"Using Apx-Incoming-Host: {apx_incoming_host} -> {server_url}")
        else:
            # Fallback to Host header
            host = get_header_case_insensitive(request.headers, "Host") or ""
            if host and host != "sales-agent.scope3.com":
                # For external domains or localhost, use appropriate protocol
                protocol = get_protocol(host)
                server_url = f"{protocol}://{host}/a2a"
                logger.info(f"Using Host header: {host} -> {server_url}")
            else:
                # Default fallback - production HTTPS
                server_url = "https://sales-agent.scope3.com/a2a"
                logger.info(f"Using default URL: {server_url}")

        # Create a copy of the static agent card with dynamic URL
        dynamic_card = agent_card.model_copy()
        dynamic_card.url = server_url
        return dynamic_card

    # Replace the library's agent card endpoints with our dynamic ones
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def dynamic_agent_discovery(request):
        """Override for /.well-known/agent.json with tenant-specific URL."""
        from starlette.responses import Response

        # Handle OPTIONS preflight requests (CORS middleware will add headers)
        if request.method == "OPTIONS":
            return Response(status_code=204)

        dynamic_card = create_dynamic_agent_card(request)
        # CORS middleware automatically adds CORS headers
        return JSONResponse(dynamic_card.model_dump())

    async def dynamic_agent_card_endpoint(request):
        """Override for /agent.json with tenant-specific URL."""
        from starlette.responses import Response

        # Handle OPTIONS preflight requests (CORS middleware will add headers)
        if request.method == "OPTIONS":
            return Response(status_code=204)

        dynamic_card = create_dynamic_agent_card(request)
        # CORS middleware automatically adds CORS headers
        return JSONResponse(dynamic_card.model_dump())

    # Find and replace the existing routes to ensure proper A2A specification compliance
    new_routes = []
    for route in app.routes:
        if hasattr(route, "path"):
            if route.path == "/.well-known/agent.json":
                # Replace with our dynamic endpoint (legacy compatibility)
                new_routes.append(Route("/.well-known/agent.json", dynamic_agent_discovery, methods=["GET", "OPTIONS"]))
                logger.info("Replaced /.well-known/agent.json with dynamic version")
            elif route.path == "/.well-known/agent-card.json":
                # Replace with our dynamic endpoint (primary A2A discovery)
                new_routes.append(
                    Route("/.well-known/agent-card.json", dynamic_agent_discovery, methods=["GET", "OPTIONS"])
                )
                logger.info("Replaced /.well-known/agent-card.json with dynamic version")
            elif route.path == "/agent.json":
                # Replace with our dynamic endpoint
                new_routes.append(Route("/agent.json", dynamic_agent_card_endpoint, methods=["GET", "OPTIONS"]))
                logger.info("Replaced /agent.json with dynamic version")
            else:
                new_routes.append(route)
        else:
            new_routes.append(route)

    # Update the app's router with new routes
    app.router.routes = new_routes

    # Add debug endpoint for tenant detection
    from starlette.routing import Route

    from src.core.config_loader import get_tenant_by_virtual_host

    async def debug_tenant_endpoint(request):
        """Debug endpoint to check tenant detection from headers."""

        # Helper to get header case-insensitively
        def get_header_case_insensitive(headers, header_name: str) -> str | None:
            """Get header value with case-insensitive lookup."""
            for key, value in headers.items():
                if key.lower() == header_name.lower():
                    return value
            return None

        # Check for Apx-Incoming-Host header (case-insensitive)
        apx_host = get_header_case_insensitive(request.headers, "Apx-Incoming-Host")
        host_header = get_header_case_insensitive(request.headers, "Host")

        # Resolve tenant using same logic as auth
        tenant_id = None
        tenant_name = None
        detection_method = None

        # Try Apx-Incoming-Host first
        if apx_host:
            tenant = get_tenant_by_virtual_host(apx_host)
            if tenant:
                tenant_id = tenant.get("tenant_id")
                tenant_name = tenant.get("name")
                detection_method = "apx-incoming-host"

        # Try Host header subdomain
        if not tenant_id and host_header:
            subdomain = host_header.split(".")[0] if "." in host_header else None
            if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "sales-agent"]:
                tenant_id = subdomain
                detection_method = "host-subdomain"

        response_data = {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "detection_method": detection_method,
            "apx_incoming_host": apx_host,
            "host": host_header,
            "service": "a2a",
        }

        # Add X-Tenant-Id header to response
        response = JSONResponse(response_data)
        if tenant_id:
            response.headers["X-Tenant-Id"] = tenant_id

        return response

    # Add debug route
    app.router.routes.append(Route("/debug/tenant", debug_tenant_endpoint, methods=["GET"]))

    # Add middleware for backward compatibility with numeric messageId
    @app.middleware("http")
    async def messageId_compatibility_middleware(request, call_next):
        """Middleware to handle both numeric and string messageId for backward compatibility."""
        import json

        # Only process JSON-RPC requests to /a2a
        if request.url.path == "/a2a" and request.method == "POST":
            # Read the body
            body = await request.body()
            try:
                data = json.loads(body)

                # Check if this is a JSON-RPC request with numeric messageId
                if isinstance(data, dict) and "params" in data:
                    params = data.get("params", {})
                    if "message" in params and isinstance(params["message"], dict):
                        message = params["message"]
                        # Convert numeric messageId to string if needed
                        if "messageId" in message and isinstance(message["messageId"], int | float):
                            logger.warning(
                                f"Converting numeric messageId {message['messageId']} to string for compatibility"
                            )
                            message["messageId"] = str(message["messageId"])
                            # Update the request body
                            body = json.dumps(data).encode()

                # Also handle the outer id field for JSON-RPC
                if "id" in data and isinstance(data["id"], int | float):
                    logger.warning(f"Converting numeric JSON-RPC id {data['id']} to string for compatibility")
                    data["id"] = str(data["id"])
                    body = json.dumps(data).encode()

            except (json.JSONDecodeError, KeyError):
                # Not JSON or doesn't have expected structure, pass through
                pass

            # Create new request with potentially modified body
            from starlette.requests import Request

            request = Request(request.scope, receive=lambda: {"type": "http.request", "body": body})

        response = await call_next(request)
        return response

    # Add authentication middleware for Bearer token extraction
    @app.middleware("http")
    async def auth_middleware(request, call_next):
        """Extract Bearer token and set authentication context for A2A requests.

        Accepts authentication via either:
        - Authorization: Bearer <token> (standard A2A/HTTP)
        - x-adcp-auth: <token> (AdCP convention, for compatibility with MCP)
        """
        # Only process A2A endpoint requests (handle both /a2a and /a2a/)
        if request.url.path in ["/a2a", "/a2a/"] and request.method == "POST":
            # Try Authorization header first (standard)
            token = None
            auth_source = None

            for key, value in request.headers.items():
                if key.lower() == "authorization":
                    auth_header = value.strip()
                    if auth_header.startswith("Bearer "):
                        token = auth_header[7:]  # Remove "Bearer " prefix
                        auth_source = "Authorization"
                        break
                elif key.lower() == "x-adcp-auth":
                    # Also accept x-adcp-auth for compatibility with MCP clients
                    token = value.strip()
                    auth_source = "x-adcp-auth"
                    # Don't break - prefer Authorization if both present

            if token:
                _request_auth_token.set(token)
                _request_headers.set(dict(request.headers))
                logger.info(f"Extracted token from {auth_source} header for A2A request: {token[:10]}...")
            else:
                logger.warning(
                    f"A2A request to {request.url.path} missing authentication (checked Authorization and x-adcp-auth headers)"
                )
                _request_auth_token.set(None)
                _request_headers.set(dict(request.headers))

        response = await call_next(request)

        # Clean up context variables (ContextVars automatically clean up at context boundary,
        # but explicit cleanup ensures no leakage between requests)
        _request_auth_token.set(None)
        _request_headers.set(None)

        return response

    # Run with uvicorn
    import uvicorn

    logger.info("Standard A2A endpoints: /.well-known/agent.json, /a2a, /agent.json")
    logger.info("JSON-RPC 2.0 support enabled at /a2a")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

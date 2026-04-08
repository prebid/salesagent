#!/usr/bin/env python3
"""
Prebid Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import logging
import uuid
from collections.abc import AsyncGenerator

# Import core functions for direct calls (raw functions without FastMCP decorators)
from datetime import UTC, datetime
from typing import Any

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    AgentExtension,
    Artifact,
    DataPart,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    Message,
    MessageSendParams,
    MethodNotFoundError,
    Part,
    PushNotificationConfig,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError
from adcp import create_a2a_webhook_payload
from adcp.types import GeneratedTaskStatus
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from sqlalchemy import select

from src.core.audit_logger import get_audit_logger
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.domain_config import get_a2a_server_url
from src.core.exceptions import (
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPBudgetExhaustedError,
    AdCPConflictError,
    AdCPError,
    AdCPValidationError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreativeStatusEnum
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
    list_accounts_raw as core_list_accounts_tool,
)

# Signals tools removed - should come from dedicated signals agents, not sales agent
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
    sync_accounts_raw as core_sync_accounts_tool,
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
from src.core.version import get_version
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)


def _adcp_to_a2a_error(exc: AdCPError) -> InvalidParamsError | InvalidRequestError | InternalError:
    """Translate AdCPError to an A2A SDK error type preserving semantics.

    The recovery classification, error_code, and details are forwarded in the
    ``data`` field so that buyer agents (and test harness unwrapping) can
    reconstruct the original AdCPError.
    """
    data: dict[str, Any] = {"recovery": exc.recovery, "error_code": exc.error_code}
    if exc.details:
        data["details"] = exc.details
    if isinstance(exc, (AdCPValidationError, AdCPConflictError, AdCPBudgetExhaustedError)):
        return InvalidParamsError(message=exc.message, data=data)
    elif isinstance(exc, (AdCPAuthenticationError, AdCPAuthorizationError)):
        return InvalidRequestError(message=exc.message, data=data)
    else:
        return InternalError(message=exc.message, data=data)


# ADCP Discovery Skills: Skills that don't require authentication
# Per AdCP spec section 3.2, these endpoints allow optional authentication for public discovery.
# IMPORTANT: This is the single source of truth for auth-optional skills in A2A.
# Add new skills here ONLY if they meet AdCP discovery endpoint requirements:
#   1. Return only public/non-sensitive data
#   2. Support tenant-level access control (e.g., brand_manifest_policy)
#   3. Never expose user-specific or transactional data
#   4. Must be safe to call without authentication
DISCOVERY_SKILLS = frozenset(
    {
        "get_adcp_capabilities",  # Agent capabilities (always public per AdCP spec)
        "list_accounts",  # Account discovery (public, returns empty for unauthed per BR-RULE-055)
        "list_creative_formats",  # Creative specifications (always public)
        "list_authorized_properties",  # Property catalog (always public)
        "get_products",  # Conditional: depends on tenant brand_manifest_policy setting
    }
)


class AdCPRequestHandler(RequestHandler):
    """Request handler for AdCP A2A operations supporting JSON-RPC 2.0."""

    def __init__(self):
        """Initialize the AdCP A2A request handler."""
        self.tasks = {}  # In-memory task storage
        logger.info("AdCP Request Handler initialized for direct function calls")

    def _get_auth_token(self, context: ServerCallContext | None = None) -> str | None:
        """Extract Bearer token from ServerCallContext.

        Args:
            context: ServerCallContext from SDK (None when called directly in tests).
        """
        if context is None:
            return None
        auth_ctx = context.state.get(AUTH_CONTEXT_STATE_KEY)
        return auth_ctx.auth_token if auth_ctx else None

    def _resolve_a2a_identity(
        self,
        auth_token: str | None,
        require_valid_token: bool = True,
        context: ServerCallContext | None = None,
    ) -> ResolvedIdentity:
        """Resolve identity at the A2A transport boundary — called ONCE per request.

        This is the A2A equivalent of REST's _resolve_auth(). It calls
        resolve_identity() once and returns the result. All downstream handlers
        receive the pre-resolved identity instead of re-resolving from auth_token.

        Args:
            auth_token: Bearer token from Authorization header (None for unauthenticated)
            require_valid_token: If True, auth failures raise ServerError
            context: ServerCallContext from SDK (None when called directly in tests).

        Returns:
            ResolvedIdentity with tenant and (optionally) principal info

        Raises:
            ServerError: If require_valid_token=True and authentication fails
        """
        from src.core.resolved_identity import resolve_identity
        from src.core.testing_hooks import AdCPTestContext

        auth_ctx = context.state.get(AUTH_CONTEXT_STATE_KEY) if context is not None else None
        headers = auth_ctx.headers if auth_ctx else {}

        if require_valid_token and not auth_token:
            raise ServerError(InvalidRequestError(message="Missing authentication token"))

        # Extract testing context from A2A request headers (same as MCP does)
        testing_context = AdCPTestContext.from_headers(headers)

        try:
            identity = resolve_identity(
                headers=headers,
                auth_token=auth_token,
                require_valid_token=require_valid_token,
                protocol="a2a",
                testing_context=testing_context,
            )
        except AdCPAuthenticationError as e:
            raise ServerError(InvalidRequestError(message=str(e))) from e

        if require_valid_token:
            if not identity.principal_id:
                raise ServerError(InvalidRequestError(message="Authentication token is invalid or expired."))

            if not identity.tenant:
                raise ServerError(
                    InvalidRequestError(
                        message=f"Unable to determine tenant from authentication. Principal: {identity.principal_id}"
                    )
                )

            tenant_id = identity.tenant_id or identity.tenant.get("tenant_id", "unknown")
            logger.info(
                f"[A2A AUTH] ✅ Authentication successful: tenant={tenant_id}, principal={identity.principal_id}"
            )

        # Set tenant ContextVar at the A2A transport boundary
        if identity.tenant:
            from src.core.config_loader import set_current_tenant

            set_current_tenant(identity.tenant)

        return identity

    def _make_tool_context(
        self, identity: ResolvedIdentity, tool_name: str, context_id: str | None = None
    ) -> ToolContext:
        """Build ToolContext from a pre-resolved identity — NO database calls.

        Args:
            identity: Pre-resolved identity from _resolve_a2a_identity
            tool_name: Name of the tool being called
            context_id: Optional context ID for conversation tracking

        Returns:
            ToolContext for calling core functions
        """
        if not context_id:
            context_id = f"a2a_{datetime.now(UTC).timestamp()}"

        tenant_id = identity.tenant_id or (
            identity.tenant.get("tenant_id", "unknown") if identity.tenant else "unknown"
        )

        return ToolContext(
            context_id=context_id,
            tenant_id=tenant_id,
            principal_id=identity.principal_id,
            tool_name=tool_name,
            request_timestamp=datetime.now(UTC),
            metadata={"source": "a2a_server", "protocol": "a2a_jsonrpc"},
            testing_context=identity.testing_context,
        )

    def _log_a2a_operation(
        self,
        operation: str,
        tenant_id: str,
        principal_id: str,
        success: bool = True,
        details: dict[str, Any] | None = None,
        error: str | None = None,
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
        """Send protocol-level push notification if configured.

        Per AdCP A2A spec (https://docs.adcontextprotocol.org/docs/protocols/a2a-guide#push-notifications-a2a-specific):
        - Final states (completed, failed, canceled): Send full Task object with artifacts
        - Intermediate states (working, input-required, submitted): Send TaskStatusUpdateEvent

        Uses create_a2a_webhook_payload from adcp library to automatically select correct type.
        """
        try:
            # Check if task has push notification config in metadata
            if not task.metadata or "push_notification_config" not in task.metadata:
                return

            webhook_config: PushNotificationConfig = task.metadata["push_notification_config"]
            push_notification_service = get_protocol_webhook_service()

            from uuid import uuid4

            url = webhook_config.url
            if not url:
                logger.info("[red]No push notification URL present; skipping webhook[/red]")
                return

            auth = webhook_config.authentication
            schemes = auth.schemes if auth else []
            auth_type = schemes[0] if isinstance(schemes, list) and schemes else None
            auth_token = auth.credentials if auth else None

            push_notification_config = DBPushNotificationConfig(
                id=webhook_config.id or f"pnc_{uuid4().hex[:16]}",
                tenant_id="",
                principal_id="",
                url=url,
                authentication_type=auth_type,
                authentication_token=auth_token,
                is_active=True,
            )

            # Convert status string to GeneratedTaskStatus enum
            try:
                status_enum = GeneratedTaskStatus(status)
            except ValueError:
                # Fallback for unknown status values
                logger.warning(f"Unknown status '{status}', defaulting to 'working'")
                status_enum = GeneratedTaskStatus.working

            # Build result data for the webhook payload
            # Include error information in result if status is failed
            result_data: dict[str, Any] = result or {}
            if error and status == "failed":
                result_data["error"] = error

            # Use create_a2a_webhook_payload to get the correct payload type:
            # - Task for final states (completed, failed, canceled)
            # - TaskStatusUpdateEvent for intermediate states (working, input-required, submitted)
            payload = create_a2a_webhook_payload(
                task_id=task.id,
                status=status_enum,
                context_id=task.context_id or "",
                result=result_data,
            )

            metadata = {
                "task_type": (
                    task.metadata["skills_requested"][0] if len(task.metadata["skills_requested"]) > 0 else "unknown"
                ),
            }

            await push_notification_service.send_notification(
                push_notification_config=push_notification_config, payload=payload, metadata=metadata
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
            # Import response classes - for union types, import the concrete variants
            from src.core.schemas import (
                CreateMediaBuyError,
                CreateMediaBuySuccess,
                GetMediaBuyDeliveryResponse,
                GetMediaBuysResponse,
                GetProductsResponse,
                ListAccountsResponse,
                ListAuthorizedPropertiesResponse,
                ListCreativeFormatsResponse,
                ListCreativesResponse,
                SyncAccountsResponse,
                SyncCreativesResponse,
                UpdateMediaBuyError,
                UpdateMediaBuySuccess,
            )

            # For union types (CreateMediaBuyResponse, UpdateMediaBuyResponse),
            # determine which concrete class based on data content
            if skill_name == "create_media_buy":
                # Success responses have media_buy_id, error responses have errors
                if "media_buy_id" in data:
                    return CreateMediaBuySuccess(**data)
                else:
                    return CreateMediaBuyError(**data)
            elif skill_name == "update_media_buy":
                # Success responses have media_buy_id, error responses have errors
                if "media_buy_id" in data:
                    return UpdateMediaBuySuccess(**data)
                else:
                    return UpdateMediaBuyError(**data)

            # Non-union response types - use the concrete class directly
            response_map: dict[str, type] = {
                "get_media_buy_delivery": GetMediaBuyDeliveryResponse,
                "get_media_buys": GetMediaBuysResponse,
                "get_products": GetProductsResponse,
                "list_accounts": ListAccountsResponse,
                "sync_accounts": SyncAccountsResponse,
                "list_authorized_properties": ListAuthorizedPropertiesResponse,
                "list_creative_formats": ListCreativeFormatsResponse,
                "list_creatives": ListCreativesResponse,
                "sync_creatives": SyncCreativesResponse,
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
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        # Handle message_id being a number or string
        msg_id = str(params.message.message_id) if hasattr(params.message, "message_id") else None
        context_id = params.message.context_id or msg_id or f"ctx_{task_id}"

        # Extract push notification config from protocol layer (A2A MessageSendConfiguration)
        push_notification_config = None
        if hasattr(params, "configuration") and params.configuration:
            if hasattr(params.configuration, "push_notification_config"):
                push_notification_config = params.configuration.push_notification_config
                if push_notification_config:
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

        # Store push notification config model directly in metadata (no destructuring)
        if push_notification_config:
            task_metadata["push_notification_config"] = push_notification_config

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
            auth_token = self._get_auth_token(context)

            # Check if any requested skills require authentication
            # Default to not requiring auth - only require if we have non-discovery skills
            requires_auth = False
            if skill_invocations:
                # If ANY skill requires auth (not in discovery set), then require auth
                requested_skills = {inv["skill"] for inv in skill_invocations}
                non_discovery_skills = requested_skills - DISCOVERY_SKILLS
                if non_discovery_skills:
                    requires_auth = True

            # Require authentication for non-public skills
            if requires_auth and not auth_token:
                raise ServerError(
                    InvalidRequestError(
                        message="Missing authentication token - Bearer token required in Authorization header"
                    )
                )

            # ── Transport boundary: resolve identity ONCE ──
            # Like REST's _resolve_auth(), identity is resolved here and passed
            # to all downstream handlers. No handler should call resolve_identity().
            identity: ResolvedIdentity | None = None
            if auth_token:
                identity = self._resolve_a2a_identity(auth_token, require_valid_token=requires_auth, context=context)
            elif not requires_auth:
                # Unauthenticated discovery request — resolve tenant from headers only
                identity = self._resolve_a2a_identity(None, require_valid_token=False, context=context)

            # Route: Handle explicit skill invocations first, then natural language fallback
            if skill_invocations:
                # Process explicit skill invocations
                results = []
                for invocation in skill_invocations:
                    skill_name = invocation["skill"]
                    parameters = invocation["parameters"]
                    logger.info(f"Processing explicit skill: {skill_name} with parameters: {parameters}")

                    try:
                        result = await self._handle_explicit_skill(
                            skill_name,
                            parameters,
                            identity,
                            push_notification_config=task_metadata.get("push_notification_config"),
                        )
                        results.append({"skill": skill_name, "result": result, "success": True})
                    except ServerError:
                        # ServerError should bubble up immediately (JSON-RPC error)
                        raise
                    except Exception as e:
                        logger.error(f"Error in explicit skill {skill_name}: {e}")
                        results.append({"skill": skill_name, "error": str(e), "success": False})

                # Check for submitted status (manual approval required) - return early without artifacts
                # Per AdCP spec, async operations should return Task with status=submitted and no artifacts
                for res in results:
                    if res["success"] and isinstance(res["result"], dict):
                        result_status = res["result"].get("status")
                        if result_status == "submitted":
                            task.status = TaskStatus(state=TaskState.submitted)
                            task.artifacts = None  # No artifacts for pending tasks
                            logger.info(
                                f"Task {task_id} requires manual approval, returning status=submitted with no artifacts"
                            )
                            # Send protocol-level webhook notification
                            await self._send_protocol_webhook(task, status="submitted")
                            self.tasks[task_id] = task
                            return task

                # Create artifacts for all skill results with human-readable text
                for i, res in enumerate(results):
                    artifact_data = res["result"] if res["success"] else {"error": res["error"]}

                    # Generate human-readable text from response __str__()
                    # Per A2A spec, use TextPart + DataPart pattern (not description field)
                    text_message = None
                    if res["success"] and isinstance(artifact_data, dict):
                        try:
                            response_obj = self._reconstruct_response_object(res["skill"], artifact_data)
                            if response_obj and hasattr(response_obj, "__str__"):
                                text_message = str(response_obj)
                        except Exception:
                            pass  # If reconstruction fails, skip text part

                    # Build parts list per A2A spec: optional TextPart + required DataPart
                    parts = []
                    if text_message:
                        parts.append(Part(root=TextPart(text=text_message)))
                    parts.append(Part(root=DataPart(data=artifact_data)))

                    task.artifacts = task.artifacts or []
                    task.artifacts.append(
                        Artifact(
                            artifact_id=f"skill_result_{i + 1}",
                            name=f"{'error' if not res['success'] else res['skill']}_result",
                            parts=parts,
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
                        tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                        principal_id = (identity.principal_id or "unknown") if identity else "unknown"

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
                            tenant_id,
                            principal_id,
                            True,
                            log_details,
                        )
                    except Exception as e:
                        logger.warning(f"Could not log skill invocations: {e}")

            # Natural language fallback (existing keyword-based routing)
            elif any(word in combined_text for word in ["product", "inventory", "available", "catalog"]):
                result = await self._get_products(combined_text, identity)
                tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                principal_id = (identity.principal_id or "unknown") if identity else "unknown"

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
                # Redirect pricing queries to get_products which has real price_guidance
                result = await self._handle_get_products_skill(
                    {"brief": combined_text},
                    identity,
                )
                tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                principal_id = (identity.principal_id or "unknown") if identity else "unknown"

                self._log_a2a_operation(
                    "get_products",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "query_type": "pricing",
                        "products_count": len(result.get("products", [])) if isinstance(result, dict) else 0,
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
                # Redirect targeting queries to get_adcp_capabilities which has real targeting info
                result = await self._handle_get_adcp_capabilities_skill({}, identity)
                tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                principal_id = (identity.principal_id or "unknown") if identity else "unknown"

                self._log_a2a_operation(
                    "get_adcp_capabilities",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "query_type": "targeting",
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
                result = await self._create_media_buy(combined_text, identity)
                tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                principal_id = (identity.principal_id or "unknown") if identity else "unknown"

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
                tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
                principal_id = (identity.principal_id or "unknown") if identity else "unknown"

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

            # Determine task status based on operation result
            # For sync_creatives, check if any creatives are pending review
            task_state = TaskState.completed
            task_status_str = "completed"

            result_data = {}
            if task.artifacts:
                # Extract result from artifacts
                for artifact in task.artifacts:
                    if hasattr(artifact, "parts") and artifact.parts:
                        for part in artifact.parts:
                            if hasattr(part, "data") and part.data:
                                result_data[artifact.name] = part.data

                                # Check if this is a sync_creatives response with pending creatives
                                if artifact.name == "result" and isinstance(part.data, dict):
                                    creatives = part.data.get("creatives", [])
                                    if any(
                                        c.get("status") == CreativeStatusEnum.pending_review.value
                                        for c in creatives
                                        if isinstance(c, dict)
                                    ):
                                        task_state = TaskState.submitted
                                        task_status_str = "submitted"

                                    # Check for explicit status field (e.g., create_media_buy returns this)
                                    result_status = part.data.get("status")
                                    if result_status == "submitted":
                                        task_state = TaskState.submitted
                                        task_status_str = "submitted"

            # Mark task with appropriate status
            task.status = TaskStatus(state=task_state)

            # Send protocol-level webhook notification if configured
            await self._send_protocol_webhook(task, status=task_status_str)

        except ServerError:
            # Re-raise ServerError as-is (will be caught by JSON-RPC handler)
            raise
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Use identity resolved at transport boundary (if available)
            err_tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
            err_principal_id = (identity.principal_id or "unknown") if identity else "unknown"

            self._log_a2a_operation(
                "message_processing",
                err_tenant_id,
                err_principal_id,
                False,
                {"error_type": type(e).__name__},
                str(e),
            )

            # Send protocol-level webhook notification for failure if configured
            task.status = TaskStatus(state=TaskState.failed)
            # Attach error to task artifacts
            task.artifacts = [
                Artifact(
                    artifact_id="error_1",
                    name="processing_error",
                    parts=[Part(root=DataPart(data={"error": str(e), "error_type": type(e).__name__}))],
                )
            ]

            await self._send_protocol_webhook(task, status="failed")

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
        result = await self.on_message_send(params, context)

        # Yield a single event with the complete task
        # result can be Task, Message, or other A2A types - all have model_dump()
        # mypy doesn't understand that union members all have model_dump()
        yield Event(type="task_update", data=result.model_dump(mode="json"))  # type: ignore[operator]

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
        task_id = params.id
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
        task_id = params.id
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus(state=TaskState.canceled)
            self.tasks[task_id] = task
        return task

    async def on_resubscribe_to_task(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Handle task resubscription requests."""
        # Not implemented for now
        from a2a.types import UnsupportedOperationError
        from a2a.utils.errors import ServerError

        raise ServerError(UnsupportedOperationError(message="Task resubscription not supported"))
        yield  # Make this a generator (unreachable but satisfies type checker)

    async def on_get_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle get push notification config requests.

        Retrieves the push notification configuration for a specific config ID.
        """
        from a2a.types import InvalidParamsError, TaskNotFoundError

        from src.core.database.database_session import get_db_session

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "get_push_notification_config")

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
                    raise ServerError(TaskNotFoundError(message=f"Push notification config not found: {config_id}"))

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
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "set_push_notification_config")

            # Extract parameters (A2A spec format)
            # Params structure: {task_id, push_notification_config: {url, authentication}}
            # Note: params comes as Pydantic object with snake_case attributes
            task_id = getattr(params, "task_id", None)
            push_config = getattr(params, "push_notification_config", None)

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
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "list_push_notification_configs")

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

        from a2a.types import InvalidParamsError, TaskNotFoundError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "delete_push_notification_config")

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
                    raise ServerError(TaskNotFoundError(message=f"Push notification config not found: {config_id}"))

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

    @staticmethod
    def _serialize_for_a2a(response: Any) -> dict:
        """Serialize a handler response for A2A protocol at the framework boundary.

        This is the single serialization point for all A2A skill responses.
        Handlers return raw Pydantic models; this method converts them to
        A2A-compatible dicts with protocol fields (message, success).

        - Pydantic models: serialized via model_dump(mode="json"), protocol fields added
        - Dicts: passed through as-is (early-return error/stub responses from handlers)

        Protocol fields added:
        - message: human-readable string from response.__str__()
        - success: derived from absence of errors field (for responses that have one)

        Args:
            response: Pydantic model or dict from a skill handler

        Returns:
            Dict ready for A2A DataPart
        """
        if isinstance(response, dict):
            return response

        response_data = response.model_dump(mode="json")
        response_data["message"] = str(response)

        # Derive success from errors field if present, default True otherwise
        if "errors" in response_data:
            response_data["success"] = not bool(response_data["errors"])
        else:
            response_data.setdefault("success", True)

        return response_data

    async def _handle_explicit_skill(
        self,
        skill_name: str,
        parameters: dict,
        identity: ResolvedIdentity | None,
        push_notification_config: PushNotificationConfig | None = None,
    ) -> dict:
        """Handle explicit AdCP skill invocations.

        Maps skill names to appropriate handlers and validates parameters.
        Handlers return raw Pydantic models; serialization happens here at the boundary.

        Args:
            skill_name: The AdCP skill name (e.g., "get_products")
            parameters: Dictionary of skill-specific parameters
            identity: Pre-resolved identity from transport boundary
            push_notification_config: Push notification config from A2A protocol layer

        Returns:
            Dictionary containing the skill result

        Raises:
            ValueError: For unknown skills or invalid parameters
        """
        # Inject push_notification_config into parameters for skills that need it
        # Serialize to dict at the transport boundary — _impl accepts dict, not BaseModel
        if push_notification_config and skill_name in ("create_media_buy", "sync_creatives"):
            pnc_dict = (
                push_notification_config.model_dump(mode="json")
                if hasattr(push_notification_config, "model_dump")
                else push_notification_config
            )
            parameters = {**parameters, "push_notification_config": pnc_dict}
        # Normalize deprecated fields before any handler sees the parameters
        from src.core.request_compat import normalize_request_params

        compat_result = normalize_request_params(skill_name, parameters)
        parameters = compat_result.params

        logger.info(f"Handling explicit skill: {skill_name} with parameters: {list(parameters.keys())}")

        # Validate identity for non-discovery skills
        if skill_name not in DISCOVERY_SKILLS and (identity is None or not identity.principal_id):
            raise ServerError(InvalidRequestError(message="Authentication required for skill invocation"))

        # Map skill names to handlers
        skill_handlers = {
            # Core AdCP Discovery Skills
            "get_adcp_capabilities": self._handle_get_adcp_capabilities_skill,
            # Core AdCP Media Buy Skills
            "get_products": self._handle_get_products_skill,
            "create_media_buy": self._handle_create_media_buy_skill,
            # ✅ NEW: Missing AdCP Discovery Skills (CRITICAL for protocol compliance)
            "list_creative_formats": self._handle_list_creative_formats_skill,
            "list_accounts": self._handle_list_accounts_skill,
            "sync_accounts": self._handle_sync_accounts_skill,
            "list_authorized_properties": self._handle_list_authorized_properties_skill,
            # ✅ NEW: Missing Media Buy Management Skills (CRITICAL for campaign lifecycle)
            "update_media_buy": self._handle_update_media_buy_skill,
            "get_media_buys": self._handle_get_media_buys_skill,
            "get_media_buy_delivery": self._handle_get_media_buy_delivery_skill,
            "update_performance_index": self._handle_update_performance_index_skill,
            # AdCP Spec Creative Management (centralized library approach)
            "sync_creatives": self._handle_sync_creatives_skill,
            "list_creatives": self._handle_list_creatives_skill,
            # Creative Management & Approval
            "approve_creative": self._handle_approve_creative_skill,
            "get_media_buy_status": self._handle_get_media_buy_status_skill,
            "optimize_media_buy": self._handle_optimize_media_buy_skill,
            # Note: signals skills removed - should come from dedicated signals agents
            # Note: legacy get_pricing/get_targeting removed - use get_products and get_adcp_capabilities instead
        }

        if skill_name not in skill_handlers:
            available_skills = list(skill_handlers.keys())
            raise ServerError(
                MethodNotFoundError(message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}")
            )

        try:
            handler = skill_handlers[skill_name]
            # Handlers return raw Pydantic models (or dicts for early-return errors)
            result = await handler(parameters, identity)  # type: ignore[arg-type]
            # Serialize at the boundary — models become dicts with protocol fields
            return self._serialize_for_a2a(result)
        except ServerError:
            # Re-raise ServerError as-is (already properly formatted)
            raise
        except AdCPError as e:
            # Translate AdCPError to protocol-specific A2A error
            logger.error(f"AdCPError in skill handler {skill_name}: {e.error_code} - {e.message}")
            raise ServerError(_adcp_to_a2a_error(e))
        except ValueError as e:
            # Same translation as MCP: ValueError → VALIDATION_ERROR
            logger.error(f"ValueError in skill handler {skill_name}: {e}")
            raise ServerError(InvalidParamsError(message=str(e)))
        except Exception as e:
            logger.error(f"Error in skill handler {skill_name}: {e}")
            raise ServerError(InternalError(message=f"Skill {skill_name} failed: {str(e)}"))

    async def _handle_get_products_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit get_products skill invocation.

        Aligned with adcp spec - brand must be a BrandReference dict.

        NOTE: Authentication is OPTIONAL for this endpoint. Access depends on tenant's
        brand_manifest_policy setting (public/require_brand/require_auth).
        """
        try:
            brief = parameters.get("brief", "")
            brand = parameters.get("brand")
            filters = parameters.get("filters")

            # Call core function with identity — _impl validates search criteria
            response = await core_get_products_tool(
                brief=brief,
                brand=brand,
                filters=filters,
                property_list=parameters.get("property_list"),
                min_exposures=parameters.get("min_exposures"),
                strategy_id=parameters.get("strategy_id"),
                context=parameters.get("context"),
                identity=identity,
            )

            # Apply v2 compat for pre-3.0 clients at the boundary
            from src.core.version_compat import apply_version_compat

            adcp_version = parameters.get("adcp_version")
            if isinstance(response, dict):
                response_data = response
            else:
                # Capture human-readable message before converting to dict
                message = str(response)
                response_data = response.model_dump(mode="json")
                # Add protocol fields that _serialize_for_a2a would add for Pydantic models,
                # since returning a dict bypasses that logic
                response_data["message"] = message
                response_data.setdefault("success", True)
            return apply_version_compat("get_products", response_data, adcp_version)

        except AdCPError:
            # Let AdCPError propagate to outer handler for proper translation
            raise
        except Exception as e:
            logger.error(f"Error in get_products skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve products: {str(e)}"))

    async def _handle_create_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit create_media_buy skill invocation.

        IMPORTANT: This handler ONLY accepts AdCP spec-compliant format:
        - packages[] (required) - each package must have budget
        - brand (required)
        - start_time (required)
        - end_time (required)

        Per AdCP v2.2.0 spec, budget is specified at the PACKAGE level, not top level.
        Legacy format (product_ids, total_budget, start_date, end_date) is NOT supported.
        """
        try:
            tool_context = self._make_tool_context(identity, "create_media_buy")

            # Parse parameters into typed request model (validation at A2A boundary)
            from pydantic import ValidationError

            from src.core.schemas import CreateMediaBuyRequest

            # Pre-process: A2A field name translations
            params = {**parameters}
            if "custom_targeting" in params:
                params.setdefault("targeting_overlay", params.pop("custom_targeting"))
            # Set A2A defaults for optional fields
            params.setdefault("po_number", f"A2A-{uuid.uuid4().hex[:8]}")
            params.setdefault("buyer_ref", f"A2A-{identity.principal_id}")

            # Validate required AdCP parameters (packages is optional in model but required by spec)
            required_params = ["brand", "packages", "start_time", "end_time"]
            missing_params = [p for p in required_params if p not in params]
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
                        }
                    ],
                }

            try:
                req = CreateMediaBuyRequest.model_validate(params)
            except ValidationError as e:
                return {
                    "success": False,
                    "message": f"Invalid parameters: {e}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                    "errors": [
                        {
                            "code": "validation_error",
                            "message": str(e),
                        }
                    ],
                }

            # Call core function with validated parameters and identity
            response = await core_create_media_buy_tool(
                brand=params.get("brand"),
                po_number=req.po_number,
                buyer_ref=req.buyer_ref,
                packages=params["packages"],  # Required — validated above
                start_time=params.get("start_time"),
                end_time=params.get("end_time"),
                budget=params.get("budget"),
                targeting_overlay=params.get("targeting_overlay", {}),
                push_notification_config=params.get("push_notification_config"),
                reporting_webhook=params.get("reporting_webhook"),
                context=params.get("context"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in create_media_buy skill: {e}")
            raise ServerError(InternalError(message=f"Failed to create media buy: {str(e)}"))

    async def _handle_sync_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit sync_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # DEBUG: Log incoming parameters
            logger.info(f"[A2A sync_creatives] Received parameters keys: {list(parameters.keys())}")
            logger.info(f"[A2A sync_creatives] assignments param: {parameters.get('assignments')}")
            logger.info(f"[A2A sync_creatives] creatives count: {len(parameters.get('creatives', []))}")

            # Create ToolContext from A2A auth info and resolve identity
            tool_context = self._make_tool_context(identity, "sync_creatives")

            # Map A2A parameters - creatives is required
            if "creatives" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'creatives'",
                    "required_parameters": ["creatives"],
                    "received_parameters": list(parameters.keys()),
                }

            # Construct typed models at the A2A boundary (Pydantic validation at entry).
            # Pre-process format_id: upgrade legacy strings to FormatId models.
            from src.core.format_cache import upgrade_legacy_format_id

            creatives = []
            for c in parameters["creatives"]:
                if isinstance(c, dict) and "format_id" in c:
                    c = {**c, "format_id": upgrade_legacy_format_id(c["format_id"])}
                creatives.append(CreativeAsset(**c) if isinstance(c, dict) else c)

            ctx_param = parameters.get("context")
            context = ContextObject(**ctx_param) if isinstance(ctx_param, dict) else ctx_param

            # Call core function with spec-compliant parameters (AdCP v2.5)
            response = core_sync_creatives_tool(
                creatives=creatives,
                # AdCP 2.5: Full upsert semantics (patch parameter removed)
                creative_ids=parameters.get("creative_ids"),
                assignments=parameters.get("assignments"),
                delete_missing=parameters.get("delete_missing", False),
                dry_run=parameters.get("dry_run", False),
                validation_mode=parameters.get("validation_mode", "strict"),
                push_notification_config=parameters.get("push_notification_config"),
                context=context,
                account=parameters.get("account"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in sync_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to sync creatives: {str(e)}"))

    async def _handle_list_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit list_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info and resolve identity
            tool_context = self._make_tool_context(identity, "list_creatives")

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
                context=parameters.get("context"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in list_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to list creatives: {str(e)}"))

    async def _handle_create_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit create_creative skill invocation."""
        try:
            tool_context = self._make_tool_context(identity, "create_creative")

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

            # TODO: Implement create_creative tool
            # Call core function with individual parameters
            # response = core_create_creative_tool(...)
            raise ServerError(UnsupportedOperationError(message="create_creative skill not yet implemented"))

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in create_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to create creative: {str(e)}"))

    async def _handle_get_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_creatives skill invocation."""
        try:
            tool_context = self._make_tool_context(identity, "get_creatives")

            # TODO: Implement get_creatives tool
            # identity already resolved at transport boundary
            # response = core_get_creatives_tool(
            #     group_id=parameters.get("group_id"),
            #     media_buy_id=parameters.get("media_buy_id"),
            #     status=parameters.get("status"),
            #     tags=parameters.get("tags", []),
            #     include_assignments=parameters.get("include_assignments", False),
            #     identity=identity,
            # )
            raise ServerError(UnsupportedOperationError(message="get_creatives skill not yet implemented"))

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in get_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to get creatives: {str(e)}"))

    async def _handle_assign_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit assign_creative skill invocation."""
        try:
            tool_context = self._make_tool_context(identity, "assign_creative")

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

            # TODO: Implement assign_creative tool
            # identity already resolved at transport boundary
            # response = core_assign_creative_tool(
            #     media_buy_id=parameters["media_buy_id"],
            #     package_id=parameters["package_id"],
            #     creative_id=parameters["creative_id"],
            #     weight=parameters.get("weight", 100),
            #     percentage_goal=parameters.get("percentage_goal"),
            #     rotation_type=parameters.get("rotation_type", "weighted"),
            #     override_click_url=parameters.get("override_click_url"),
            #     identity=identity,
            # )
            raise ServerError(UnsupportedOperationError(message="assign_creative skill not yet implemented"))

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in assign_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to assign creative: {str(e)}"))

    async def _handle_approve_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit approve_creative skill invocation."""
        from a2a.types import UnsupportedOperationError

        raise ServerError(UnsupportedOperationError(message="approve_creative skill not yet implemented"))

    # Signals skill handlers removed - should come from dedicated signals agents

    async def _handle_get_media_buy_status_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_media_buy_status skill invocation."""
        from a2a.types import UnsupportedOperationError

        raise ServerError(UnsupportedOperationError(message="get_media_buy_status skill not yet implemented"))

    async def _handle_optimize_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit optimize_media_buy skill invocation."""
        from a2a.types import UnsupportedOperationError

        raise ServerError(UnsupportedOperationError(message="optimize_media_buy skill not yet implemented"))

    async def _handle_get_adcp_capabilities_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit get_adcp_capabilities skill invocation (CRITICAL AdCP discovery endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        Returns agent capabilities including supported protocols, targeting, and portfolio info.
        """
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Import and call the core implementation
            from src.core.tools.capabilities import get_adcp_capabilities_raw

            # Call core function with identity
            response = await get_adcp_capabilities_raw(
                protocols=parameters.get("protocols"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in get_adcp_capabilities skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve AdCP capabilities: {str(e)}"))

    async def _handle_list_creative_formats_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit list_creative_formats skill invocation (CRITICAL AdCP endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        """
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Build request from parameters (all optional)
            # Use local schema (extends library type) for proper type compatibility
            from src.core.schemas import ListCreativeFormatsRequest

            req = ListCreativeFormatsRequest(
                type=parameters.get("type"),
                format_ids=parameters.get("format_ids"),
                output_format_ids=parameters.get("output_format_ids"),
                input_format_ids=parameters.get("input_format_ids"),
                is_responsive=parameters.get("is_responsive"),
                name_search=parameters.get("name_search"),
                asset_types=parameters.get("asset_types"),
                min_width=parameters.get("min_width"),
                max_width=parameters.get("max_width"),
                min_height=parameters.get("min_height"),
                max_height=parameters.get("max_height"),
                context=parameters.get("context"),
            )

            # Call core function with identity
            response = core_list_creative_formats_tool(req=req, identity=identity)

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in list_creative_formats skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve creative formats: {str(e)}"))

    async def _handle_list_accounts_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit list_accounts skill invocation.

        Authentication is OPTIONAL per BR-RULE-055 — unauthenticated calls
        return an empty account list.
        """
        from src.core.schemas.account import ListAccountsRequest

        request = ListAccountsRequest(
            status=parameters.get("status"),
            pagination=parameters.get("pagination"),
            sandbox=parameters.get("sandbox"),
            context=parameters.get("context"),
        )
        return core_list_accounts_tool(req=request, identity=identity)

    async def _handle_sync_accounts_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit sync_accounts skill invocation.

        Authentication is REQUIRED per BR-RULE-055.
        """
        from src.core.schemas.account import SyncAccountsRequest

        request = SyncAccountsRequest(
            accounts=parameters.get("accounts", []),
            delete_missing=parameters.get("delete_missing", False),
            dry_run=parameters.get("dry_run", False),
            context=parameters.get("context"),
        )
        return await core_sync_accounts_tool(req=request, identity=identity)

    async def _handle_list_authorized_properties_skill(
        self, parameters: dict, identity: ResolvedIdentity | None
    ) -> Any:
        """Handle explicit list_authorized_properties skill invocation (CRITICAL AdCP endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        If no auth token provided, uses headers for tenant detection.

        Per AdCP v2.4 spec, returns publisher_domains (not properties/tags).
        """
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Map A2A parameters to ListAuthorizedPropertiesRequest
            # Note: ListAuthorizedPropertiesRequest was removed from adcp 3.2.0, use local schema
            from src.core.schemas import ListAuthorizedPropertiesRequest

            # Warn about deprecated 'tags' parameter (removed in AdCP 2.5)
            if "tags" in parameters:
                logger.warning(
                    "Deprecated parameter 'tags' passed to list_authorized_properties. "
                    "This parameter was removed in AdCP 2.5 and will be ignored."
                )

            request = ListAuthorizedPropertiesRequest(context=parameters.get("context"))

            # Call core function with identity
            response = core_list_authorized_properties_tool(req=request, identity=identity)

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in list_authorized_properties skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve authorized properties: {str(e)}"))

    async def _handle_update_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit update_media_buy skill invocation (CRITICAL for campaign management)."""
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Parse parameters into typed request model (validation at A2A boundary)
            from pydantic import ValidationError

            from src.core.schemas import UpdateMediaBuyRequest

            # Pre-process: support legacy 'updates.packages' → 'packages'
            params = {**parameters}
            if "packages" not in params and "updates" in params:
                legacy_updates = params.pop("updates")
                if isinstance(legacy_updates, dict) and "packages" in legacy_updates:
                    params["packages"] = legacy_updates["packages"]

            # Require at least one identifier
            if "media_buy_id" not in params and "buyer_ref" not in params:
                raise ServerError(
                    InvalidParamsError(
                        message="Missing required parameter: one of 'media_buy_id' or 'buyer_ref' is required"
                    )
                )

            # Validate top-level fields via typed model (packages validated by _raw
            # which handles legacy formats with extra fields like 'status')
            try:
                req = UpdateMediaBuyRequest(
                    media_buy_id=params.get("media_buy_id"),
                    buyer_ref=params.get("buyer_ref"),
                    paused=params.get("paused"),
                    start_time=params.get("start_time"),
                    end_time=params.get("end_time"),
                    context=params.get("context"),
                )
            except ValidationError as e:
                raise ServerError(InvalidParamsError(message=f"Invalid parameters: {e}"))

            # Call core function with validated fields + raw nested structures and identity
            response = core_update_media_buy_tool(
                media_buy_id=req.media_buy_id or "",
                buyer_ref=req.buyer_ref,
                paused=req.paused,
                start_time=params.get("start_time"),
                end_time=params.get("end_time"),
                budget=params.get("budget"),
                packages=params.get("packages"),
                push_notification_config=params.get("push_notification_config"),
                context=params.get("context"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in update_media_buy skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update media buy: {str(e)}"))

    async def _handle_get_media_buys_skill(self, parameters: dict, identity: ResolvedIdentity) -> Any:
        """Handle get_media_buys skill invocation."""
        try:
            from src.core.schemas import GetMediaBuysRequest
            from src.core.tools.media_buy_list import _get_media_buys_impl

            params = {**parameters}
            include_snapshot = params.pop("include_snapshot", False)
            req = GetMediaBuysRequest.model_validate(params)
            response = _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in get_media_buys skill: {e}")
            raise ServerError(InternalError(message=f"Unable to get media buys: {str(e)}"))

    async def _handle_get_media_buy_delivery_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_media_buy_delivery skill invocation (CRITICAL for monitoring).

        Per AdCP spec, all parameters are optional:
        - media_buy_ids (plural, per AdCP v1.6.0 spec) or media_buy_id (singular, legacy)
        - buyer_refs: Filter by buyer reference IDs
        - status_filter: Filter by status (active, pending, paused, completed, failed, all)
        - start_date: Start date for reporting period (YYYY-MM-DD)
        - end_date: End date for reporting period (YYYY-MM-DD)

        When no media_buy_ids are provided, returns delivery data for all media buys
        the requester has access to, filtered by the provided criteria.
        """
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Parse parameters into typed request model (validation at A2A boundary)
            # Pre-process: support singular media_buy_id (legacy) → media_buy_ids (spec)
            from src.core.schemas import GetMediaBuyDeliveryRequest

            params = {**parameters}
            if "media_buy_ids" not in params and "media_buy_id" in params:
                params["media_buy_ids"] = [params.pop("media_buy_id")]

            req = GetMediaBuyDeliveryRequest.model_validate(params)

            # Call core function with validated fields (all optional per AdCP spec)
            # Pass raw values for fields where _raw handles its own type coercion
            # (e.g., status_filter str→MediaBuyStatus, date str→date)
            response = core_get_media_buy_delivery_tool(
                media_buy_ids=req.media_buy_ids,
                buyer_refs=req.buyer_refs,
                status_filter=params.get("status_filter"),
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                context=params.get("context"),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in get_media_buy_delivery skill: {e}")
            raise ServerError(InternalError(message=f"Unable to get media buy delivery: {str(e)}"))

    async def _handle_update_performance_index_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit update_performance_index skill invocation (CRITICAL for optimization)."""
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Parse parameters into typed request model (validation at A2A boundary)
            from pydantic import ValidationError

            from src.core.schemas import UpdatePerformanceIndexRequest

            try:
                req = UpdatePerformanceIndexRequest.model_validate(parameters)
            except ValidationError as e:
                return {
                    "success": False,
                    "message": f"Invalid parameters: {e}",
                    "required_parameters": ["media_buy_id", "performance_data"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with validated fields and identity
            response = core_update_performance_index_tool(
                media_buy_id=req.media_buy_id,
                performance_data=[p.model_dump(mode="json") for p in req.performance_data],
                context=req.context,
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in update_performance_index skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update performance index: {str(e)}"))

    async def _get_products(self, query: str, identity: ResolvedIdentity | None) -> dict:
        """Get available advertising products by calling core functions directly.

        Args:
            query: User's product query
            identity: Pre-resolved identity from transport boundary

        Returns:
            Dictionary containing product information
        """
        try:
            # Identity already resolved at transport boundary (on_message_send)

            # Call core function directly using the underlying function
            response = await core_get_products_tool(
                brief=query,
                identity=identity,
            )

            # Convert to A2A response format with v2.x backward compatibility
            from src.core.version_compat import apply_version_compat

            products = [product.model_dump(mode="json") for product in response.products]
            response_data = {
                "products": products,
                "message": str(response),  # Use __str__ method for human-readable message
            }
            return apply_version_compat("get_products", response_data, None)

        except Exception as e:
            logger.error(f"Error getting products: {e}")
            # Return empty products list instead of fallback data
            return {"products": [], "message": f"Unable to retrieve products: {str(e)}"}

    def _extract_brand_name_from_query(self, query: str) -> str:
        """Extract or infer brand name from the user query.

        Used for backward compatibility with natural language queries.
        Extracts a brand name to populate brand (BrandReference) for adcp v3.6.0.
        """
        # Look for common patterns that might indicate the brand/offering
        query_lower = query.lower()

        # If the query mentions specific brands or products, use those
        if "advertise" in query_lower or "promote" in query_lower:
            # Try to extract what they're promoting
            parts = query.split()
            for i, word in enumerate(parts):
                if word.lower() in ["advertise", "promote", "advertising", "promoting"]:
                    if i + 1 < len(parts):
                        # Take the next few words as the brand name
                        brand_parts = parts[i + 1 : i + 4]  # Take up to 3 words
                        brand_name = " ".join(brand_parts).strip(".,!?")
                        if len(brand_name) > 5:  # Make sure it's substantial
                            return f"Business promoting {brand_name}"

        # Default brand name based on query type
        if any(word in query_lower for word in ["video", "display", "banner", "ad"]):
            return "Brand advertising products and services"
        elif any(word in query_lower for word in ["coffee", "beverage", "food"]):
            return "Food and beverage company"
        elif any(word in query_lower for word in ["tech", "software", "app", "digital"]):
            return "Technology company digital products"
        else:
            # Generic fallback that should pass AdCP validation
            return "Business advertising products and services"

    async def _create_media_buy(self, request: str, identity: ResolvedIdentity | None) -> dict:
        """Create a media buy based on the request.

        Args:
            request: User's media buy request
            identity: Pre-resolved identity from transport boundary

        Returns:
            Dictionary containing media buy creation result
        """
        # For now, return a mock response indicating authentication is working
        # but media buy creation needs more implementation
        try:
            # Identity already resolved at transport boundary (on_message_send)
            tenant_id = identity.tenant_id if identity else "unknown"
            principal_id = identity.principal_id if identity else "unknown"

            return {
                "success": False,
                "message": f"Authentication successful for {principal_id}. To create a media buy, use explicit skill invocation with AdCP v2.2.0 spec-compliant format.",
                "required_fields": ["brand", "packages", "start_time", "end_time"],
                "note": "Per AdCP v2.2.0 spec, budget is specified at the PACKAGE level, not top level",
                "authenticated_tenant": tenant_id,
                "authenticated_principal": principal_id,
                "example": {
                    "brand": {"domain": "example.com"},
                    "packages": [
                        {
                            "buyer_ref": "pkg_1",
                            "product_id": "video_premium",
                            "budget": 10000.0,  # Budget is per package (required)
                            "pricing_option_id": "cpm-fixed",
                        }
                    ],
                    # Note: NO top-level budget field per AdCP v2.2.0 spec
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
        AgentCard with Prebid Sales Agent capabilities
    """
    # Use configured domain for agent card
    # Note: This will be overridden dynamically in the endpoint handlers
    # Fallback to localhost if SALES_AGENT_DOMAIN not configured
    server_url = get_a2a_server_url() or "http://localhost:8091/a2a"

    from a2a.types import AgentCapabilities, AgentSkill
    from adcp import get_adcp_version

    # Get sales agent version from package metadata or pyproject.toml
    sales_agent_version = get_version()

    # Create AdCP extension (AdCP 2.5 spec)
    # As of adcp 2.12.1, get_adcp_version() returns the protocol version (e.g., "2.5.0")
    # Previously it returned the schema version (e.g., "v1"), but this was fixed upstream
    protocol_version = get_adcp_version()
    adcp_extension = AgentExtension(
        uri=f"https://adcontextprotocol.org/schemas/{protocol_version}/protocols/adcp-extension.json",
        description="AdCP protocol version and supported domains",
        params={
            "adcp_version": protocol_version,
            "protocols_supported": ["media_buy"],  # Only media_buy protocol is currently supported
        },
    )

    # Create the agent card with minimal required fields
    agent_card = AgentCard(
        name="Prebid Sales Agent",
        description="AI agent for programmatic advertising campaigns via AdCP protocol",
        version=sales_agent_version,
        protocol_version="1.0",
        capabilities=AgentCapabilities(
            push_notifications=True,
            extensions=[adcp_extension],
        ),
        default_input_modes=["message"],
        default_output_modes=["message"],
        skills=[
            # Core AdCP Discovery Skills
            AgentSkill(
                id="get_adcp_capabilities",
                name="get_adcp_capabilities",
                description="Get the capabilities of this AdCP sales agent including supported protocols and targeting",
                tags=["capabilities", "discovery", "adcp"],
            ),
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
            AgentSkill(
                id="list_accounts",
                name="list_accounts",
                description="List billing accounts accessible to this agent",
                tags=["accounts", "billing", "discovery", "adcp"],
            ),
            AgentSkill(
                id="sync_accounts",
                name="sync_accounts",
                description="Sync billing accounts by natural key (upsert, delete_missing, dry_run)",
                tags=["accounts", "billing", "sync", "upsert", "adcp"],
            ),
            # ✅ NEW: Media Buy Management Skills (CRITICAL for campaign lifecycle)
            AgentSkill(
                id="update_media_buy",
                name="update_media_buy",
                description="Update existing media buy configuration and settings",
                tags=["campaign", "update", "management", "adcp"],
            ),
            AgentSkill(
                id="get_media_buys",
                name="get_media_buys",
                description="Get media buy status, creative approval state, and optional near-real-time delivery snapshots",
                tags=["media_buy", "status", "creative", "snapshot", "monitoring", "adcp"],
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
            # Note: signals skills removed - should come from dedicated signals agents
            # Note: legacy get_pricing/get_targeting removed - use get_products and get_adcp_capabilities instead
        ],
        url=server_url,
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


# Standalone execution removed — A2A is now integrated into the unified
# FastAPI app (src/app.py) via add_routes_to_app(). The AdCPRequestHandler
# and create_agent_card() are imported by src/app.py.

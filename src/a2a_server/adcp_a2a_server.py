#!/usr/bin/env python3
"""
Prebid Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import json
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
    AgentInterface,
    Artifact,
    AuthenticationInfo,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    MethodNotFoundError,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    UnsupportedOperationError,
)
from a2a.utils.errors import A2AError
from adcp import create_a2a_webhook_payload
from adcp.types import GeneratedTaskStatus
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from google.protobuf import json_format, struct_pb2
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


def _coerce_account_reference(account: Any) -> Any:
    """Coerce a raw dict account param to AccountReference at the A2A boundary.

    A2A clients may send account as a plain dict. The MCP wrapper's TypeAdapter
    handles this automatically, but the A2A raw path passes dicts through. This
    wraps dicts in the SDK AccountReference Pydantic model so downstream code
    gets a typed object.
    """
    if account is None or not isinstance(account, dict):
        return account
    from adcp.types import AccountReference as LibraryAccountReference

    return LibraryAccountReference.model_validate(account)


def _dict_to_value(d: dict) -> struct_pb2.Value:
    """Convert a Python dict to a protobuf Value for use in Part.data."""
    val = struct_pb2.Value()
    json_format.Parse(json.dumps(d, default=str), val)
    return val


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    """Convert a Python dict to a protobuf Struct for use in Task.metadata."""
    s = struct_pb2.Struct()
    s.update(d)
    return s


def _adcp_to_a2a_error(exc: AdCPError) -> InvalidParamsError | InvalidRequestError | InternalError:
    """Translate AdCPError to an A2A SDK error type preserving semantics.

    The recovery classification, error_code, and details are forwarded in the
    ``data`` field so that buyer agents (and test harness unwrapping) can
    reconstruct the original AdCPError. Non-standard codes are translated
    to STANDARD_ERROR_CODES at this transport boundary.
    """
    data: dict[str, Any] = {"recovery": exc.recovery, "error_code": exc.wire_error_code}
    if exc.details:
        data["details"] = exc.details
    if isinstance(exc, (AdCPValidationError, AdCPConflictError, AdCPBudgetExhaustedError)):
        return InvalidParamsError(message=str(exc.message), data=data)
    elif isinstance(exc, (AdCPAuthenticationError, AdCPAuthorizationError)):
        return InvalidRequestError(message=str(exc.message), data=data)
    else:
        return InternalError(message=str(exc.message), data=data)


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
        self.tasks: dict[str, Task] = {}  # In-memory task storage
        self._task_push_configs: dict[str, TaskPushNotificationConfig] = {}
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
            require_valid_token: If True, auth failures raise A2AError
            context: ServerCallContext from SDK (None when called directly in tests).

        Returns:
            ResolvedIdentity with tenant and (optionally) principal info

        Raises:
            A2AError: If require_valid_token=True and authentication fails
        """
        from src.core.resolved_identity import resolve_identity
        from src.core.testing_hooks import AdCPTestContext

        auth_ctx = context.state.get(AUTH_CONTEXT_STATE_KEY) if context is not None else None
        headers = auth_ctx.headers if auth_ctx else {}

        if require_valid_token and not auth_token:
            raise InvalidRequestError(message="Missing authentication token")

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
            raise InvalidRequestError(message=str(e)) from e

        if require_valid_token:
            if not identity.principal_id:
                raise InvalidRequestError(message="Authentication token is invalid or expired.")

            if not identity.tenant:
                raise InvalidRequestError(
                    message=f"Unable to determine tenant from authentication. Principal: {identity.principal_id}"
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
            # Check if task has push notification config stored
            webhook_config = self._task_push_configs.get(task.id)
            if not webhook_config:
                return

            push_notification_service = get_protocol_webhook_service()

            from uuid import uuid4

            url = webhook_config.url
            if not url:
                logger.info("[red]No push notification URL present; skipping webhook[/red]")
                return

            auth = webhook_config.authentication if webhook_config.HasField("authentication") else None
            auth_type = auth.scheme if auth and auth.scheme else None
            auth_token = auth.credentials if auth and auth.credentials else None

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

            # Extract skills_requested from protobuf Struct metadata
            meta_dict = json_format.MessageToDict(task.metadata) if task.metadata.ByteSize() > 0 else {}
            skills = list(meta_dict.get("skills_requested", []))
            metadata = {
                "task_type": skills[0] if skills else "unknown",
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
        params: SendMessageRequest,
        context: ServerCallContext,
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
                if part.text:
                    text_parts.append(part.text)

                # Handle structured data parts (explicit skill invocation)
                # part.data is a protobuf Value — convert to Python dict
                elif part.HasField("data"):
                    data = json_format.MessageToDict(part.data)
                    if isinstance(data, dict) and "skill" in data:
                        # Support both "input" (A2A spec) and "parameters" (legacy) for skill params
                        params_data = data.get("input") or data.get("parameters", {})
                        skill_invocations.append({"skill": data["skill"], "parameters": params_data})
                        logger.info(
                            f"Found explicit skill invocation: {data['skill']} with params: {list(params_data.keys())}"
                        )

        # Combine text for natural language fallback
        combined_text = " ".join(text_parts).strip().lower()

        # Create task for tracking
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        # In protobuf, message_id is always a string (empty string default)
        msg_id = params.message.message_id or None
        context_id = params.message.context_id or msg_id or f"ctx_{task_id}"

        # Extract push notification config from protocol layer (A2A SendMessageConfiguration)
        push_notification_config: TaskPushNotificationConfig | None = None
        if params.HasField("configuration") and params.configuration.HasField("task_push_notification_config"):
            push_notification_config = params.configuration.task_push_notification_config
            if push_notification_config.url:
                logger.info(
                    f"Protocol-level push notification config provided for task {task_id}: {push_notification_config.url}"
                )

        # Prepare task metadata (JSON-serializable only — protobuf Struct)
        task_metadata: dict[str, Any] = {
            "request_text": combined_text,
            "invocation_type": "explicit_skill" if skill_invocations else "natural_language",
        }
        if skill_invocations:
            task_metadata["skills_requested"] = [inv["skill"] for inv in skill_invocations]

        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            metadata=_dict_to_struct(task_metadata),
        )
        # Store push notification config outside protobuf metadata (not JSON-serializable)
        if push_notification_config:
            self._task_push_configs[task_id] = push_notification_config
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
                raise InvalidRequestError(
                    message="Missing authentication token - Bearer token required in Authorization header"
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
                            push_notification_config=push_notification_config,
                        )
                        results.append({"skill": skill_name, "result": result, "success": True})
                    except A2AError:
                        # A2AError should bubble up immediately (JSON-RPC error)
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
                            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
                            del task.artifacts[:]  # No artifacts for pending tasks
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
                            logger.debug("Response reconstruction failed, skipping text part", exc_info=True)

                    # Build parts list per A2A spec: optional text Part + required data Part
                    parts = []
                    if text_message:
                        parts.append(Part(text=text_message))
                    parts.append(Part(data=_dict_to_value(artifact_data)))

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
                    task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_FAILED))

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
                del task.artifacts[:]
                task.artifacts.append(
                    Artifact(
                        artifact_id="product_catalog_1",
                        name="product_catalog",
                        parts=[Part(data=_dict_to_value(result))],
                    )
                )
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
                del task.artifacts[:]
                task.artifacts.append(
                    Artifact(
                        artifact_id="pricing_info_1",
                        name="pricing_information",
                        parts=[Part(data=_dict_to_value(result))],
                    )
                )
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
                del task.artifacts[:]
                task.artifacts.append(
                    Artifact(
                        artifact_id="targeting_opts_1",
                        name="targeting_options",
                        parts=[Part(data=_dict_to_value(result))],
                    )
                )
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
                del task.artifacts[:]
                if result.get("success"):
                    task.artifacts.append(
                        Artifact(
                            artifact_id="media_buy_1",
                            name="media_buy_created",
                            parts=[Part(data=_dict_to_value(result))],
                        )
                    )
                else:
                    task.artifacts.append(
                        Artifact(
                            artifact_id="media_buy_error_1",
                            name="media_buy_error",
                            parts=[Part(data=_dict_to_value(result))],
                        )
                    )
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
                del task.artifacts[:]
                task.artifacts.append(
                    Artifact(
                        artifact_id="capabilities_1",
                        name="capabilities",
                        parts=[Part(data=_dict_to_value(capabilities))],
                    )
                )

            # Determine task status based on operation result
            # For sync_creatives, check if any creatives are pending review
            task_state = TaskState.TASK_STATE_COMPLETED
            task_status_str = "completed"

            result_data = {}
            if task.artifacts:
                # Extract result from artifacts — part.data is a protobuf Value
                for artifact in task.artifacts:
                    if artifact.parts:
                        for part in artifact.parts:
                            if part.HasField("data"):
                                data_dict = json.loads(json_format.MessageToJson(part.data))
                                result_data[artifact.name] = data_dict

                                # Check if this is a sync_creatives response with pending creatives
                                if artifact.name == "result" and isinstance(data_dict, dict):
                                    creatives = data_dict.get("creatives", [])
                                    if any(
                                        c.get("status") == CreativeStatusEnum.pending_review.value
                                        for c in creatives
                                        if isinstance(c, dict)
                                    ):
                                        task_state = TaskState.TASK_STATE_SUBMITTED
                                        task_status_str = "submitted"

                                    # Check for explicit status field (e.g., create_media_buy returns this)
                                    result_status = data_dict.get("status")
                                    if result_status == "submitted":
                                        task_state = TaskState.TASK_STATE_SUBMITTED
                                        task_status_str = "submitted"

            # Mark task with appropriate status
            task.status.CopyFrom(TaskStatus(state=task_state))

            # Send protocol-level webhook notification if configured
            await self._send_protocol_webhook(task, status=task_status_str)

        except A2AError:
            # Re-raise A2AError as-is (will be caught by JSON-RPC handler)
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
            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_FAILED))
            # Attach error to task artifacts
            del task.artifacts[:]
            task.artifacts.append(
                Artifact(
                    artifact_id="error_1",
                    name="processing_error",
                    parts=[Part(data=_dict_to_value({"error": str(e), "error_type": type(e).__name__}))],
                )
            )

            await self._send_protocol_webhook(task, status="failed")

            # Raise A2A error instead of creating failed task
            raise InternalError(message=f"Message processing failed: {str(e)}")

        self.tasks[task_id] = task
        return task

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        """Handle 'message/stream' method for streaming requests.

        Args:
            params: Parameters including the message and configuration
            context: Server call context

        Yields:
            Event objects (Task or Message) from the agent's execution
        """
        # For now, implement non-streaming behavior
        # In production, this would yield events as they occur
        result = await self.on_message_send(params, context)

        # Event is a union type: Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
        # result is already Task | Message — yield it directly
        yield result

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
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
        params: CancelTaskRequest,
        context: ServerCallContext,
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
            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_CANCELED))
            self.tasks[task_id] = task
        return task

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Handle 'tasks/list' method."""
        raise UnsupportedOperationError(message="Task listing not supported")

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        """Handle task subscription requests."""
        raise UnsupportedOperationError(message="Task subscription not supported")
        yield  # Make this a generator (unreachable but satisfies type checker)

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Handle get push notification config requests.

        Retrieves the push notification configuration for a specific config ID.
        """
        from src.core.database.database_session import get_db_session

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "get_push_notification_config")

            # Extract config_id from params
            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise InvalidParamsError(message="Missing required parameter: id")

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
                    raise TaskNotFoundError(message=f"Push notification config not found: {config_id}")

                # Return TaskPushNotificationConfig (protobuf)
                auth_info = None
                if config.authentication_type and config.authentication_token:
                    auth_info = AuthenticationInfo(
                        scheme=config.authentication_type, credentials=config.authentication_token
                    )
                return TaskPushNotificationConfig(
                    id=config.id,
                    task_id=params.task_id,
                    url=config.url,
                    authentication=auth_info,
                    token=config.validation_token or "",
                )

        except A2AError:
            raise
        except Exception as e:
            logger.error(f"Error getting push notification config: {e}")
            raise InternalError(message=f"Failed to get push notification config: {str(e)}")

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Handle set push notification config requests.

        Creates or updates a push notification configuration for async operation callbacks.
        Buyers use this to register webhook URLs where they want to receive status updates.
        """
        import uuid
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "set_push_notification_config")

            # In a2a-sdk 1.0, TaskPushNotificationConfig is a flat protobuf message
            # with fields: tenant, id, task_id, url, token, authentication
            task_id = params.task_id
            url = params.url
            config_id = params.id or f"pnc_{uuid.uuid4().hex[:16]}"
            validation_token = params.token
            session_id = None  # Not in A2A spec

            if not url:
                raise InvalidParamsError(message="Missing required parameter: url")

            # Extract authentication details from protobuf AuthenticationInfo
            auth_type = None
            auth_token_value = None
            if params.HasField("authentication"):
                auth_type = params.authentication.scheme or None
                auth_token_value = params.authentication.credentials or None

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
                # Build authentication info if present
                auth_info = None
                if auth_type and auth_token_value:
                    auth_info = AuthenticationInfo(scheme=auth_type, credentials=auth_token_value)

                # Return TaskPushNotificationConfig (protobuf)
                return TaskPushNotificationConfig(
                    task_id=task_id or "*",
                    url=url,
                    authentication=auth_info,
                    id=config_id,
                    token=validation_token or "",
                )

        except A2AError:
            raise
        except Exception as e:
            logger.error(f"Error setting push notification config: {e}")
            raise InternalError(message=f"Failed to set push notification config: {str(e)}")

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Handle list push notification config requests.

        Returns all active push notification configurations for the authenticated principal.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "list_push_notification_configs")

            # Query database for all active configs
            with get_db_session() as db:
                stmt = select(DBPushNotificationConfig).filter_by(
                    tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id, is_active=True
                )
                configs = db.scalars(stmt).all()

                # Convert to protobuf TaskPushNotificationConfig list
                configs_list = []
                for config in configs:
                    auth_info = None
                    if config.authentication_type and config.authentication_token:
                        auth_info = AuthenticationInfo(
                            scheme=config.authentication_type, credentials=config.authentication_token
                        )
                    configs_list.append(
                        TaskPushNotificationConfig(
                            id=config.id,
                            task_id=params.task_id,
                            url=config.url,
                            authentication=auth_info,
                            token=config.validation_token or "",
                        )
                    )

                logger.info(f"Listed {len(configs_list)} push notification configs for tenant {tool_context.tenant_id}")

                return ListTaskPushNotificationConfigsResponse(configs=configs_list)

        except A2AError:
            raise
        except Exception as e:
            logger.error(f"Error listing push notification configs: {e}")
            raise InternalError(message=f"Failed to list push notification configs: {str(e)}")

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        """Handle delete push notification config requests.

        Marks a push notification configuration as inactive (soft delete).
        """
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token and resolve identity at transport boundary
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "delete_push_notification_config")

            # Extract config_id from protobuf params
            config_id = params.id
            if not config_id:
                raise InvalidParamsError(message="Missing required parameter: id")

            # Query database and mark as inactive
            with get_db_session() as db:
                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id
                )
                config = db.scalars(stmt).first()

                if not config:
                    raise TaskNotFoundError(message=f"Push notification config not found: {config_id}")

                # Soft delete by marking as inactive
                config.is_active = False
                config.updated_at = datetime.now(UTC)
                db.commit()

                logger.info(f"Deleted push notification config: {config_id} for tenant {tool_context.tenant_id}")
                return None

        except A2AError:
            raise
        except Exception as e:
            logger.error(f"Error deleting push notification config: {e}")
            raise InternalError(message=f"Failed to delete push notification config: {str(e)}")

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        """Handle 'GetExtendedAgentCard' method."""
        raise UnsupportedOperationError(message="Extended agent card not supported")

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
        push_notification_config: TaskPushNotificationConfig | None = None,
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
        # Serialize protobuf to dict at the transport boundary — _impl accepts dict
        if push_notification_config and skill_name in ("create_media_buy", "sync_creatives"):
            pnc_dict = json_format.MessageToDict(push_notification_config)
            # Translate A2A protobuf authentication.scheme (singular) → AdCP schemes (plural list).
            # A2A's protobuf AuthenticationInfo uses a single `scheme` field; AdCP's
            # PushNotificationConfig schema uses a `schemes` array.
            auth = pnc_dict.get("authentication") if isinstance(pnc_dict, dict) else None
            if isinstance(auth, dict) and "scheme" in auth and "schemes" not in auth:
                scheme_value = auth.pop("scheme")
                auth["schemes"] = [scheme_value] if scheme_value else []
            parameters = {**parameters, "push_notification_config": pnc_dict}
        # Normalize deprecated fields before any handler sees the parameters
        from src.core.request_compat import normalize_request_params

        compat_result = normalize_request_params(skill_name, parameters)
        parameters = compat_result.params

        logger.info(f"Handling explicit skill: {skill_name} with parameters: {list(parameters.keys())}")

        # Validate identity for non-discovery skills
        if skill_name not in DISCOVERY_SKILLS and (identity is None or not identity.principal_id):
            raise InvalidRequestError(message="Authentication required for skill invocation")

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
            raise MethodNotFoundError(message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}")

        try:
            handler = skill_handlers[skill_name]
            # Handlers return raw Pydantic models (or dicts for early-return errors)
            result = await handler(parameters, identity)  # type: ignore[arg-type]
            # Serialize at the boundary — models become dicts with protocol fields
            return self._serialize_for_a2a(result)
        except A2AError:
            # Re-raise A2AError as-is (already properly formatted)
            raise
        except AdCPError as e:
            # Translate AdCPError to protocol-specific A2A error
            logger.error(f"AdCPError in skill handler {skill_name}: {e.error_code} - {e.message}")
            raise _adcp_to_a2a_error(e)
        except ValueError as e:
            # Same translation as MCP: ValueError → VALIDATION_ERROR
            logger.error(f"ValueError in skill handler {skill_name}: {e}")
            raise InvalidParamsError(message=str(e))
        except PermissionError as e:
            # Same translation as MCP: PermissionError → AUTHORIZATION_ERROR
            logger.error(f"PermissionError in skill handler {skill_name}: {e}")
            raise InvalidRequestError(message=str(e))
        except Exception as e:
            logger.error(f"Error in skill handler {skill_name}: {e}")
            raise InternalError(message=f"Skill {skill_name} failed: {str(e)}")

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
            raise InternalError(message=f"Unable to retrieve products: {str(e)}")

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
            # buyer_ref removed in adcp 3.12

            # Coerce string brand shorthand to BrandReference dict (A2A may send "acme.com")
            if isinstance(params.get("brand"), str):
                params["brand"] = {"domain": params["brand"]}

            # Validate required AdCP parameters (packages is optional in model but required by spec)
            required_params = ["brand", "packages", "start_time", "end_time"]
            missing_params = [p for p in required_params if p not in params]
            if missing_params:
                from adcp.server.helpers import adcp_error

                error_body = adcp_error("VALIDATION_ERROR", f"Missing required AdCP parameters: {missing_params}")
                return {
                    "success": False,
                    "message": f"Missing required AdCP parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                    **error_body,
                }

            try:
                req = CreateMediaBuyRequest.model_validate(params)
            except ValidationError as e:
                from adcp.server.helpers import adcp_error

                error_body = adcp_error("VALIDATION_ERROR", str(e))
                return {
                    "success": False,
                    "message": f"Invalid parameters: {e}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                    **error_body,
                }

            # Call core function with validated parameters and identity.
            # Per AdCP 4.3 (commit 3c604130) targeting_overlay and budgets live on each
            # PackageRequest; only request-level spec fields are forwarded here.
            response = await core_create_media_buy_tool(
                brand=params.get("brand"),
                po_number=req.po_number,
                packages=params["packages"],  # Required — validated above
                start_time=params.get("start_time"),
                end_time=params.get("end_time"),
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
            raise InternalError(message=f"Failed to create media buy: {str(e)}")

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
                account=_coerce_account_reference(parameters.get("account")),
                identity=identity,
            )

            return response

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in sync_creatives skill: {e}")
            raise InternalError(message=f"Failed to sync creatives: {str(e)}")

    async def _handle_list_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit list_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info and resolve identity
            tool_context = self._make_tool_context(identity, "list_creatives")

            # Call core function with optional parameters (fixing original validation bug)
            response = core_list_creatives_tool(
                media_buy_id=parameters.get("media_buy_id"),
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
            raise InternalError(message=f"Failed to list creatives: {str(e)}")

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
            raise UnsupportedOperationError(message="create_creative skill not yet implemented")

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in create_creative skill: {e}")
            raise InternalError(message=f"Failed to create creative: {str(e)}")

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
            raise UnsupportedOperationError(message="get_creatives skill not yet implemented")

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in get_creatives skill: {e}")
            raise InternalError(message=f"Failed to get creatives: {str(e)}")

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
            raise UnsupportedOperationError(message="assign_creative skill not yet implemented")

        except AdCPError:
            raise  # Let _handle_explicit_skill translate to proper A2A error
        except Exception as e:
            logger.error(f"Error in assign_creative skill: {e}")
            raise InternalError(message=f"Failed to assign creative: {str(e)}")

    async def _handle_approve_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit approve_creative skill invocation."""
        raise UnsupportedOperationError(message="approve_creative skill not yet implemented")

    # Signals skill handlers removed - should come from dedicated signals agents

    async def _handle_get_media_buy_status_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_media_buy_status skill invocation."""
        raise UnsupportedOperationError(message="get_media_buy_status skill not yet implemented")

    async def _handle_optimize_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit optimize_media_buy skill invocation."""
        raise UnsupportedOperationError(message="optimize_media_buy skill not yet implemented")

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
            raise InternalError(message=f"Unable to retrieve AdCP capabilities: {str(e)}")

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
            raise InternalError(message=f"Unable to retrieve creative formats: {str(e)}")

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
            raise InternalError(message=f"Unable to retrieve authorized properties: {str(e)}")

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

            # media_buy_id is required
            if "media_buy_id" not in params:
                raise InvalidParamsError(message="Missing required parameter: 'media_buy_id'")

            # Validate top-level fields via typed model (packages validated by _raw
            # which handles legacy formats with extra fields like 'status')
            try:
                req = UpdateMediaBuyRequest(
                    media_buy_id=params.get("media_buy_id"),
                    paused=params.get("paused"),
                    start_time=params.get("start_time"),
                    end_time=params.get("end_time"),
                    context=params.get("context"),
                )
            except ValidationError as e:
                raise InvalidParamsError(message=f"Invalid parameters: {e}")

            # Call core function with validated fields + raw nested structures and identity
            response = core_update_media_buy_tool(
                media_buy_id=req.media_buy_id or "",
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
            raise InternalError(message=f"Unable to update media buy: {str(e)}")

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
            raise InternalError(message=f"Unable to get media buys: {str(e)}")

    async def _handle_get_media_buy_delivery_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_media_buy_delivery skill invocation (CRITICAL for monitoring).

        Per AdCP spec, all parameters are optional:
        - media_buy_ids (plural, per AdCP v1.6.0 spec) or media_buy_id (singular, legacy)
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
            raise InternalError(message=f"Unable to get media buy delivery: {str(e)}")

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
            raise InternalError(message=f"Unable to update performance index: {str(e)}")

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
            raise InternalError(message=f"Authentication failed: {str(e)}")


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
    from adcp import get_adcp_spec_version

    # Get sales agent version from package metadata or pyproject.toml
    sales_agent_version = get_version()

    # Create AdCP extension (AdCP 2.5 spec)
    # As of adcp 2.12.1, get_adcp_spec_version() returns the protocol version (e.g., "2.5.0")
    # Previously it returned the schema version (e.g., "v1"), but this was fixed upstream
    protocol_version = get_adcp_spec_version()
    adcp_extension = AgentExtension(
        uri=f"https://adcontextprotocol.org/schemas/{protocol_version}/protocols/adcp-extension.json",
        description="AdCP protocol version and supported domains",
        params=_dict_to_struct(
            {
                "adcp_version": protocol_version,
                "protocols_supported": ["media_buy"],  # Only media_buy protocol is currently supported
            }
        ),
    )

    # Create the agent card with minimal required fields
    agent_card = AgentCard(
        name="Prebid Sales Agent",
        description="AI agent for programmatic advertising campaigns via AdCP protocol",
        version=sales_agent_version,
        supported_interfaces=[
            AgentInterface(url=server_url, protocol_version="1.0"),
        ],
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
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


# Standalone execution removed — A2A is now integrated into the unified
# FastAPI app (src/app.py) via add_routes_to_app(). The AdCPRequestHandler
# and create_agent_card() are imported by src/app.py.

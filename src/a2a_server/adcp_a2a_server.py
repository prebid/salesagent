#!/usr/bin/env python3
"""
Prebid Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import copy
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

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
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    UnsupportedOperationError,
)
from a2a.utils.errors import A2AError
from adcp import create_a2a_webhook_payload
from adcp.types import ContextObject, CreativeAsset, GeneratedTaskStatus
from google.protobuf import json_format, struct_pb2
from pydantic import BaseModel

from src.core.audit_logger import get_audit_logger
from src.core.auth import AUTH_REQUIRED_SUGGESTION
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.database.repositories import PushNotificationConfigUoW
from src.core.database.repositories.workflow import TERMINAL_STEP_STATUSES
from src.core.domain_config import get_a2a_server_url
from src.core.exceptions import (
    AdCPAuthenticationError,
    AdCPAuthRequiredError,
    AdCPCapabilityNotSupportedError,
    AdCPError,
    AdCPValidationError,
    build_two_layer_error_envelope,
    normalize_to_adcp_error,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import coerce_creative_filters, to_account_reference, to_brand_reference
from src.core.schemas import CreativeStatusEnum
from src.core.tool_context import ToolContext
from src.core.tool_error_logging import record_boundary_error
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
from src.core.validation_helpers import (
    adcp_validation_boundary,
)
from src.core.version import get_version
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)


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


_SANITIZED_INTERNAL_MESSAGE = "An internal error occurred while processing the request."
# error.json grades a non-empty TOP-LEVEL suggestion on every error; the scrub
# replaces the raise site's suggestion (which, like the message, may interpolate
# internals) with static retry guidance that matches the bucket's transient
# semantics (#1417 top-level-suggestion conformance).
_SANITIZED_INTERNAL_SUGGESTION = "Retry the request later; if the problem persists, contact the seller."


def _safe_adcp_error(exc: Exception) -> AdCPError:
    """The single sanitization policy for the A2A boundary.

    Message safety is decided by ERROR CLASS, not by whether the error is typed —
    because a *typed* ``AdCPError`` can still carry an interpolated ``str(exc)`` in
    its message (e.g. reachable handlers do ``AdCPAdapterError(f"...: {e}")`` where
    ``e`` is a broadly-caught DB/adapter exception carrying a connection string).

    - CLIENT-CORRECTABLE errors (VALIDATION_ERROR, *_NOT_FOUND, AUTH_*,
      POLICY_VIOLATION, BUDGET_*, RATE_LIMITED, …) carry a controlled, client-facing
      message that the buyer needs to fix their request → pass through unchanged.
    - INTERNAL/INFRA errors — the ``wire_error_code == "SERVICE_UNAVAILABLE"`` bucket
      (base ``AdCPError``, ``AdCPAdapterError`` + subclasses, ``AdCPServiceUnavailableError``,
      ``AdCPConfigurationError``) — have messages that may embed internals. The message
      is replaced with a generic one while the wire code + recovery (retry semantics)
      are PRESERVED via ``AdCPError.synthesize``. The raw message is already logged
      server-side via ``record_boundary_error`` before we get here.
    - UNTYPED exceptions → generic ``AdCPError``; ``str(exc)`` never reaches the wire.

    ``wire_error_code == "SERVICE_UNAVAILABLE"`` is the discriminator (not
    ``recovery == "transient"``, which misses terminal base/Config errors and
    false-positives on the safe-message ``RateLimit``). Both A2A error paths route
    through this one helper: the top-level ``_internal_error_for`` (→ JSON-RPC
    ``InternalError``) and the per-skill ``_build_error_envelope`` (→ failed-Task
    artifact). Do NOT reintroduce a normalizer that trusts a typed message verbatim.
    """
    if isinstance(exc, AdCPError):
        if exc.wire_error_code == "SERVICE_UNAVAILABLE":
            # Internal/infra bucket — scrub the (possibly internals-bearing) message,
            # keep the wire code + recovery so the buyer still gets accurate retry
            # semantics. Drop details/field and replace the suggestion (raise-site
            # suggestions can interpolate internals just like messages) with static
            # retry guidance so the envelope keeps the graded top-level suggestion;
            # keep context for request correlation.
            return AdCPError.synthesize(
                _SANITIZED_INTERNAL_MESSAGE,
                error_code=exc.error_code,
                status_code=exc.status_code,
                recovery=exc.recovery,
                suggestion=_SANITIZED_INTERNAL_SUGGESTION,
                context=exc.context,
            )
        return exc
    return AdCPError(_SANITIZED_INTERNAL_MESSAGE, suggestion=_SANITIZED_INTERNAL_SUGGESTION)


def _internal_error_for(operation: str, exc: Exception) -> InternalError:
    """Canonical JSON-RPC ``InternalError`` for A2A boundary failures — SANITIZED.

    Security (transport-errors.mdx "Security Considerations" § Seller Requirements): raw exception text may
    contain credentials, connection strings, SQL, hostnames, filesystem paths, or
    upstream responses and MUST NOT reach the client. So:

    - A TYPED ``AdCPError`` passes through with its own wire code, but the message
      placed on the JSON-RPC layer is the SANITIZED one from ``_safe_adcp_error`` —
      a typed internal-bucket error (``AdCPAdapterError`` et al.) can interpolate
      ``str(e)`` into its message, so the ORIGINAL ``exc.message`` must never be
      used here (it would leak through ``error.message`` even while ``error.data``
      is scrubbed).
    - Any UNTYPED exception is replaced with a generic message and a base
      ``SERVICE_UNAVAILABLE`` envelope; the raw ``str(exc)`` is NEVER placed on the
      wire (callers log it server-side via ``record_boundary_error``).

    ``InternalError`` stays an ``A2AError`` so the SDK's ``JsonRpcDispatcher``
    serializes it as a structured JSON-RPC error (the four
    ``on_*_task_push_notification_config`` methods, and the untyped-crash branch of
    ``on_message_send``, all raise through here). The two-layer envelope rides in the
    error's ``data`` field (``error.data["adcp_error"]`` / ``error.data["errors"][0]``).
    """
    adcp_error = _safe_adcp_error(exc)
    if isinstance(exc, AdCPError):
        # adcp_error.message, NOT exc.message: the sanitized message (identical for
        # client-correctable errors, scrubbed for the internal bucket).
        message = f"{operation} failed: {adcp_error.message}"
    else:
        message = f"Internal error during {operation}"
    return InternalError(message=message, data=build_two_layer_error_envelope(adcp_error))


class AdCPRequestHandler(RequestHandler):
    """Request handler for AdCP A2A operations supporting JSON-RPC 2.0."""

    def __init__(self):
        """Initialize the AdCP A2A request handler."""
        self.tasks: dict[str, Task] = {}  # In-memory task storage
        self._task_push_configs: dict[str, TaskPushNotificationConfig] = {}
        logger.info("AdCP Request Handler initialized for direct function calls")

    @staticmethod
    def _build_error_envelope(exc: Exception) -> dict[str, Any]:
        """Build a spec-compliant two-layer envelope for any exception.

        Single source of truth for "wrap-arbitrary-exception → wire envelope"
        used by both the per-skill dispatcher (``_build_failed_skill_result``)
        and the top-level ``on_message_send`` error handler. Sanitizes via
        ``_safe_adcp_error`` — the SAME policy the top-level ``_internal_error_for``
        uses — so a TYPED ``AdCPError`` keeps its controlled message + wire code,
        while any UNTYPED exception becomes a generic ``AdCPError`` and its raw
        ``str(exc)`` is NEVER placed on the wire. (It deliberately does NOT use
        ``normalize_to_adcp_error``, which maps ``Exception → AdCPError(str(exc))``
        and would leak credentials/SQL/hostnames through the per-skill failed-Task
        artifact.) The wire output stays in ``WIRE_STANDARD_CODES`` (SDK
        ``STANDARD_ERROR_CODES`` plus the pinned-spec supplement) and the envelope
        shape stays a two-layer ``errors[]`` structure, never a flat
        ``{"error": "..."}`` dict the storyboard runner would treat as
        ``MCP_ERROR``.
        """

        return build_two_layer_error_envelope(_safe_adcp_error(exc))

    @staticmethod
    def _failed_task_artifact(exc: Exception) -> "Artifact":
        """The ``processing_error`` artifact for a failed Task.

        Per the A2A binding for errors, a failed artifact carries BOTH a
        human-readable TextPart and the authoritative structured DataPart (the
        two-layer AdCP envelope) — not a DataPart alone. The strict reader accepts
        this shape (one DataPart, optional TextPart)."""
        envelope = AdCPRequestHandler._build_error_envelope(exc)
        errors = envelope.get("errors") or []
        text = errors[0].get("message") if errors else "Request failed."
        return Artifact(
            artifact_id="error_1",
            name="processing_error",
            parts=[Part(text=text), Part(data=_dict_to_value(envelope))],
        )

    @staticmethod
    def _build_failed_skill_result(skill_name: str, exc: Exception) -> dict[str, Any]:
        """Build the dispatcher result dict for a failed skill invocation.

        Both the typed-AdCPError branch and the untyped fallthrough land here so
        the artifact DataPart always carries a spec-compliant two-layer envelope
        under ``error_envelope`` — the single source of truth on the wire, never a
        flat ``{"error": "..."}`` dict. Callers needing the human-readable message
        read ``error_envelope["errors"][0]["message"]``.
        """
        return {
            "skill": skill_name,
            "error_envelope": AdCPRequestHandler._build_error_envelope(exc),
            "success": False,
        }

    def _mark_task_failed(self, task: Task) -> None:
        """Mark a task FAILED. No webhook — the caller returns this terminal Task
        synchronously in the response, and AdCP 3.1.1 a2a-guide.mdx
        ("Webhook Trigger Rules for Terminal States") says a push notification is
        NOT sent when the initial response is already terminal (the buyer already
        has the result). Webhooks fire only for genuinely async transitions
        (initial response ``working``/``submitted`` → later terminal); those must
        carry the Task's structured artifacts (see ``_send_protocol_webhook``)."""
        task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_FAILED))

    @staticmethod
    def _task_artifacts_data(task: Task) -> list[tuple[str, dict[str, Any]]]:
        """Every artifact DataPart as an ordered ``(artifact_name, decoded_data)``.

        Single decoder for A2A DataPart → dict (protobuf ``Value`` → JSON), shared
        by completed-status detection and the webhook payload builder. Returns a
        LIST, not a name-keyed dict, so repeated skills that emit identically-named
        artifacts (e.g. two ``error_result``) are all preserved — none silently
        overwrites another. A failed Task's error envelope, a sync_creatives
        result, and every sibling result are kept."""
        pairs: list[tuple[str, dict[str, Any]]] = []
        for artifact in task.artifacts:
            for part in artifact.parts:
                if part.HasField("data"):
                    pairs.append((artifact.name, json.loads(json_format.MessageToJson(part.data))))
        return pairs

    @staticmethod
    def _webhook_result_data(task: Task) -> dict[str, Any]:
        """Pack all artifact data into one dict for ``create_a2a_webhook_payload``.

        The library renders a single artifact from this dict, so we key by artifact
        name but DE-COLLIDE duplicates (``error_result``, ``error_result#2``, …) —
        preserving every artifact's data on the wire rather than overwriting."""
        result_data: dict[str, Any] = {}
        for name, data in AdCPRequestHandler._task_artifacts_data(task):
            key, n = name, 2
            while key in result_data:
                key, n = f"{name}#{n}", n + 1
            result_data[key] = data
        return result_data

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
            logger.warning("Failed to log A2A operation: %s", e)

    async def _send_protocol_webhook(
        self,
        task: Task,
        status: str,
        result: dict[str, Any] | None = None,
    ):
        """Send protocol-level push notification if configured.

        Per AdCP A2A spec (https://docs.adcontextprotocol.org/docs/protocols/a2a-guide#push-notifications-a2a-specific):
        - Final states (completed, failed, canceled): Send full Task object with artifacts
        - Intermediate states (working, input-required, submitted): Send TaskStatusUpdateEvent

        Uses create_a2a_webhook_payload from adcp library to automatically select correct type.

        Failure payloads carry the Task's structured artifacts (the two-layer AdCP
        envelope), never a flattened ``{"error": "..."}`` — for a ``failed`` final
        state the library extracts from ``.artifacts``. Only meaningful for async
        transitions: immediate terminal responses are returned synchronously and
        do not notify (see ``_mark_task_failed``).
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
                logger.warning("Unknown status '%s', defaulting to 'working'", status)
                status_enum = GeneratedTaskStatus.working

            # Build result data for the webhook payload. ``create_a2a_webhook_payload``
            # renders its artifact FROM this dict, so we pass the Task's own structured
            # artifact data — EVERY artifact, de-colliding duplicate names (the two-layer
            # AdCP envelope on a ``failed`` task, the full sync_creatives result, every
            # sibling) — never a lossy ``{"error": "..."}``, a single stale DataPart, an
            # empty dict, or a name-overwritten sibling. Callers may still pass an
            # explicit ``result``; otherwise we read it off the task's artifacts.
            result_data: dict[str, Any] = result if result is not None else self._webhook_result_data(task)

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
            logger.warning("Failed to send protocol-level webhook for task %s: %s", task.id, e)

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
                # Success responses have media_buy_id, error responses have errors.
                # No CreateMediaBuySubmitted branch on purpose: submitted results
                # take the status=="submitted" early-return in on_message_send
                # (Task state=SUBMITTED, no artifacts) BEFORE artifact/text
                # reconstruction, so a submitted body can never reach here —
                # same control-flow fact as update_media_buy (PR #1567 round-2 follow-up).
                if "media_buy_id" in data:
                    return CreateMediaBuySuccess(**data)
                else:
                    return CreateMediaBuyError(**data)
            elif skill_name == "update_media_buy":
                # Success responses have media_buy_id, error responses have errors.
                # No UpdateMediaBuySubmitted branch on purpose: submitted results
                # take the status=="submitted" early-return in on_message_send
                # (Task state=SUBMITTED, no artifacts) BEFORE artifact/text
                # reconstruction, so a submitted body can never reach here
                # (PR #1567 round-2 follow-up; same rationale as create_media_buy above).
                # Guarded by test_a2a_update_media_buy_submitted_guard.py.
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
            logger.debug("Could not reconstruct response object for %s: %s", skill_name, e)
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
        logger.info("Handling message/send request: %s", params)

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

        # Initialized before the try so the outer error handler can always read
        # it — a failure during auth-token extraction (before resolution) must
        # not turn into a NameError inside the except block.
        identity: ResolvedIdentity | None = None

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

            # Require authentication for non-public skills. Stay a JSON-RPC
            # InvalidRequestError (protocol-level rejection, top-level error), but
            # carry the two-layer envelope in ``data`` so the buyer-facing
            # AUTH_REQUIRED code + AUTH_REQUIRED_SUGGESTION reach the A2A wire —
            # matching REST's no-identity envelope (auth_context.py), which the
            # bare A2AError previously dropped. (#1417)
            if requires_auth and not auth_token:
                raise InvalidRequestError(
                    message="Missing authentication token - Bearer token required in Authorization header",
                    data=build_two_layer_error_envelope(
                        AdCPAuthRequiredError(
                            "Authentication required - Bearer token required in Authorization header",
                            suggestion=AUTH_REQUIRED_SUGGESTION,
                        )
                    ),
                )

            # ── Transport boundary: resolve identity ONCE ──
            # Like REST's _resolve_auth(), identity is resolved here and passed
            # to all downstream handlers. No handler should call resolve_identity().
            if auth_token:
                identity = self._resolve_a2a_identity(auth_token, require_valid_token=requires_auth, context=context)
            elif not requires_auth:
                # Unauthenticated discovery request — resolve tenant from headers only
                identity = self._resolve_a2a_identity(None, require_valid_token=False, context=context)

            # Route: Handle explicit skill invocations first, then natural language fallback
            if skill_invocations:
                # Reject a multi-skill batch BEFORE executing ANY skill. Aggregating
                # divergent per-skill outcomes into one Task is incoherent when a skill
                # has real side effects: e.g. create_media_buy persists a pending
                # (submitted) workflow while a sibling fails, which would terminalize
                # the Task as failed even though the accepted work keeps running. Until
                # per-skill child Tasks exist (tracked as a follow-up), one skill per
                # message is the contract. Raised as a typed application error →
                # failed Task (UNSUPPORTED_FEATURE); no skill runs, so no side effects.
                if len(skill_invocations) > 1:
                    raise AdCPCapabilityNotSupportedError(
                        message=(
                            "Batching multiple skills in one message is not supported "
                            f"({[inv['skill'] for inv in skill_invocations]}); send one skill per message."
                        )
                    )

                # Process the single explicit skill invocation.
                results = []
                for invocation in skill_invocations:
                    skill_name = invocation["skill"]
                    parameters = invocation["parameters"]
                    logger.info("Processing explicit skill: %s with parameters: %s", skill_name, parameters)

                    try:
                        result = await self._handle_explicit_skill(
                            skill_name,
                            parameters,
                            identity,
                            push_notification_config=push_notification_config,
                            task_id=task_id,
                        )
                        results.append({"skill": skill_name, "result": result, "success": True})
                    except A2AError:
                        # A2AError should bubble up immediately (JSON-RPC error).
                        # Reserved for transport-protocol failures (MethodNotFound,
                        # malformed request, etc.) — never AdCP-level errors, which
                        # are now caught below and surfaced as failed Tasks with a
                        # two-layer envelope in the artifact DataPart.
                        raise
                    except AdCPError as e:
                        # AdCP-level errors are async-task failures, not JSON-RPC
                        # errors. Mirrors the SDK's _send_adcp_error reference for
                        # storyboard scenarios that exercise invalid-state
                        # transitions on an otherwise-routable skill.
                        # NOTE: logging happens in ``_handle_explicit_skill``'s
                        # except branch (with audit log + activity feed); duplicating
                        # the logger call here would produce two messages for the
                        # same failure.
                        results.append(self._build_failed_skill_result(skill_name, e))
                    except Exception as e:
                        # Untyped fallthrough — same envelope shape as the AdCPError
                        # branch so storyboard runners can `JSON.parse` the DataPart
                        # uniformly regardless of which branch caught the failure.
                        # Route through the canonical boundary hook (ERROR + exc_info
                        # for untyped failures, plus activity-feed + audit) so untyped
                        # A2A skill failures land on the same observability surface as
                        # MCP/REST and the typed path. The typed
                        # (AdCPError/ValueError/PermissionError) failures were already
                        # recorded inside _handle_explicit_skill, so this only fires for
                        # genuinely-unexpected exceptions that escaped it.
                        record_boundary_error(
                            "a2a",
                            skill_name,
                            e,
                            tenant_id=getattr(identity, "tenant_id", None),
                            principal_id=getattr(identity, "principal_id", None) or "anonymous",
                        )
                        results.append(self._build_failed_skill_result(skill_name, e))

                # Create artifacts for ALL skill results FIRST, before any status
                # decision. A mixed submitted+failed batch must never lose a failure
                # envelope to an early return — status is decided below by precedence.
                # Create artifacts for all skill results with human-readable text
                for i, res in enumerate(results):
                    if res["success"]:
                        artifact_data = res["result"]
                    elif "error_envelope" in res:
                        # Failure path: surface the full two-layer envelope as
                        # the DataPart so the storyboard runner / harness can
                        # read either ``adcp_error.code`` or ``errors[0].code``.
                        artifact_data = res["error_envelope"]
                    else:
                        # Every failure result comes from _build_failed_skill_result,
                        # which always sets error_envelope. A failed result without it
                        # is a contract violation — fail loud rather than silently emit
                        # the legacy flat ``{"error": ...}`` shape.
                        raise AdCPError(
                            f"Skill result for {res.get('skill', '?')!r} is marked failed but carries no error_envelope"
                        )

                    # Generate human-readable text from response __str__()
                    # Per A2A spec, use TextPart + DataPart pattern (not description field).
                    # A FAILED artifact carries the error message as its TextPart (A2A
                    # error binding: TextPart + DataPart), never a DataPart alone.
                    text_message = None
                    if res["success"] and isinstance(artifact_data, dict):
                        try:
                            response_obj = self._reconstruct_response_object(res["skill"], artifact_data)
                            if response_obj and hasattr(response_obj, "__str__"):
                                text_message = str(response_obj)
                        except Exception:
                            logger.debug("Response reconstruction failed, skipping text part", exc_info=True)
                    elif not res["success"] and isinstance(artifact_data, dict):
                        errors = artifact_data.get("errors") or []
                        text_message = errors[0].get("message") if errors else "Skill invocation failed."

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

                # Determine task status by precedence: failed > submitted > completed.
                # Every result's artifact was built above and is preserved regardless
                # of the chosen status, so a mixed batch never silently drops a result.
                failed_skills = [res["skill"] for res in results if not res["success"]]
                submitted_skills = [
                    res["skill"]
                    for res in results
                    if res["success"] and isinstance(res["result"], dict) and res["result"].get("status") == "submitted"
                ]
                successful_skills = [res["skill"] for res in results if res["success"]]

                if failed_skills:
                    # Any failure makes the batch terminal-failed; all artifacts (each
                    # failed skill's two-layer envelope AND any sibling successes or
                    # pending results) ride in the Task body. Immediate terminal
                    # response returned synchronously → no webhook (a2a-guide.mdx
                    # terminal-state rule).
                    self._mark_task_failed(task)
                    return task

                if submitted_skills:
                    # A pending-approval skill is present with no outright failures →
                    # non-terminal SUBMITTED. A single-skill async op keeps the "no
                    # artifacts until approved" convention; a mixed batch preserves
                    # every sibling result. Non-terminal initial response → notify.
                    task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
                    if len(results) == 1:
                        del task.artifacts[:]
                    await self._send_protocol_webhook(task, status="submitted")
                    self.tasks[task_id] = task
                    return task

                if successful_skills:
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
                        logger.warning("Could not log skill invocations: %s", e)

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
                # ``_create_media_buy`` is an NL stub that always raises
                # ``AdCPCapabilityNotSupportedError`` — the explicit-skill
                # path is the spec contract for media buy creation. The
                # outer error handler at on_message_send catches the raise,
                # attaches a spec-compliant two-layer envelope to the failed
                # Task artifact, and returns that failed Task (never a
                # JSON-RPC error).
                await self._create_media_buy(combined_text, identity)
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

            # Single DataPart decode via the shared helper (consolidated decoder).
            for artifact_name, data_dict in self._task_artifacts_data(task):
                # sync_creatives returns a "result" artifact whose creatives may be
                # pending review → the task is non-terminal (submitted), not completed.
                if artifact_name == "result" and isinstance(data_dict, dict):
                    creatives = data_dict.get("creatives", [])
                    if any(
                        c.get("status") == CreativeStatusEnum.pending_review.value
                        for c in creatives
                        if isinstance(c, dict)
                    ):
                        task_state = TaskState.TASK_STATE_SUBMITTED
                        task_status_str = "submitted"

                    # Explicit status field (e.g. create_media_buy returns this).
                    if data_dict.get("status") == "submitted":
                        task_state = TaskState.TASK_STATE_SUBMITTED
                        task_status_str = "submitted"

            # Mark task with appropriate status
            task.status.CopyFrom(TaskStatus(state=task_state))

            # Notify ONLY for a non-terminal (submitted) initial response. An
            # immediately-completed task is returned synchronously in this response,
            # and AdCP 3.1.1 a2a-guide.mdx ("Webhook Trigger Rules for
            # Terminal States") says no webhook is sent when the initial response is
            # already terminal — the buyer already has the result. Only the
            # sync_creatives-pending → submitted transition reaches here as
            # non-terminal (create_media_buy submitted returns earlier).
            if task_status_str == "submitted":
                await self._send_protocol_webhook(task, status="submitted")

        except A2AError:
            # Re-raise A2AError as-is (will be caught by JSON-RPC handler)
            raise
        except AdCPError as e:
            # TYPED application/task failure → failed Task carrying the two-layer
            # envelope (transport-errors.mdx "Layer Separation"). The AdCPError
            # message is CONTROLLED (e.g. "Unknown skill 'x'", "brief must not be
            # empty"), so it is client-safe to surface. Immediate terminal response
            # returned synchronously below → no webhook (a2a-guide.mdx). Falls through
            # to the shared store-and-return.
            del task.artifacts[:]
            task.artifacts.append(self._failed_task_artifact(e))
            self._mark_task_failed(task)
        except Exception as e:
            # UNTYPED internal crash. The spec table classifies an internal crash as
            # a TRANSPORT-layer error, and the security requirements forbid exposing
            # raw internals (credentials, SQL, hostnames, paths, upstream responses).
            # So we log the raw exception SERVER-SIDE only (record_boundary_error) and
            # raise a SANITIZED JSON-RPC InternalError whose client-facing envelope
            # carries NO raw exception text. Never build a failed-Task envelope from
            # ``str(exc)`` here — that is the leak fixed by this branch.
            err_tenant_id = (identity.tenant_id or "unknown") if identity else "unknown"
            err_principal_id = (identity.principal_id or "unknown") if identity else "unknown"
            record_boundary_error(
                "a2a",
                "message_processing",
                e,
                tenant_id=err_tenant_id,
                principal_id=err_principal_id,
            )
            # This path yields a JSON-RPC InternalError (transport-layer), NOT a
            # Task-layer outcome — so the provisional WORKING task stored before
            # dispatch must not survive as a retrievable orphan. Drop it (and its
            # push config) before raising so ``tasks/get`` returns nothing.
            self.tasks.pop(task_id, None)
            self._task_push_configs.pop(task_id, None)
            raise _internal_error_for("message processing", e) from e

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

    # Terminal persisted workflow-step status → A2A TaskState, for the durable
    # tasks/get fallback. Non-terminal steps (in_progress, approved, …) surface as
    # WORKING. #1544 B6.
    _STEP_STATUS_TO_TASK_STATE = {
        "completed": TaskState.TASK_STATE_COMPLETED,
        "rejected": TaskState.TASK_STATE_REJECTED,
        "failed": TaskState.TASK_STATE_FAILED,
        "canceled": TaskState.TASK_STATE_CANCELED,
    }

    # Step statuses that are final outcomes — a buyer's tasks/cancel cannot undo
    # work that already completed/failed/was rejected (or was already canceled).
    # Single source of truth is the repository's TERMINAL_STEP_STATUSES (the atomic
    # cancel guard's vocabulary); the state mapping above must cover exactly that
    # set, checked at import time so the two can't silently drift.
    _TERMINAL_STEP_STATUSES = TERMINAL_STEP_STATUSES
    if frozenset(_STEP_STATUS_TO_TASK_STATE) != _TERMINAL_STEP_STATUSES:
        raise RuntimeError("A2A step->TaskState mapping out of sync with WorkflowRepository.TERMINAL_STEP_STATUSES")

    _TERMINAL_TASK_STATES = frozenset(_STEP_STATUS_TO_TASK_STATE.values())

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Handle 'tasks/get' method to retrieve task status.

        The persisted workflow step is the source of truth for an async task's
        outcome: the admin decision that terminalizes it runs in a DIFFERENT
        process, so this process's in-memory entry can be stale forever (a
        SUBMITTED/WORKING task whose workflow already completed). A poll therefore
        returns the in-memory task only when IT is already terminal; otherwise the
        durable step is consulted and, if it reached a terminal status, wins (and
        reconciles the in-memory map). The durable fallback also serves polls after
        a restart, when the map is empty. See #1544 (B6).
        """
        task_id = params.id
        task = self.tasks.get(task_id)
        if task is not None and task.status.state in self._TERMINAL_TASK_STATES:
            return task
        durable = self._durable_task_from_step(task_id, context)
        if durable is not None and durable.status.state in self._TERMINAL_TASK_STATES:
            self.tasks[task_id] = durable
            return durable
        # No terminal durable outcome: the richer in-memory task (metadata,
        # artifacts) beats the durable WORKING skeleton.
        return task if task is not None else durable

    def _durable_lookup_identity(self, context: ServerCallContext | None):
        """Resolve the caller's identity for a durable (cross-process) task lookup.

        A restart-surviving lookup needs a tenant AND principal scope, so identity
        is resolved from the request's own auth (the buyer who created the task
        authenticated). Returns None when either is unresolvable — the durable
        lookup must then be refused rather than risk serving or mutating another
        tenant's (or same-tenant sibling principal's) task.
        """
        try:
            auth_token = self._get_auth_token(context)
            identity = (
                self._resolve_a2a_identity(auth_token, require_valid_token=True, context=context)
                if auth_token
                else None
            )
        except Exception:
            return None
        if identity is None or not identity.tenant_id or not identity.principal_id:
            return None
        return identity

    def _durable_task_from_step(self, task_id: str, context: ServerCallContext | None) -> Task | None:
        """Rebuild a terminal Task from the workflow step that stored this transport id.

        See ``_durable_lookup_identity`` for why the poll's own auth provides the
        tenant scope.
        """
        identity = self._durable_lookup_identity(context)
        if identity is None:
            return None

        from src.core.database.database_session import get_db_session
        from src.core.database.repositories.workflow import WorkflowRepository

        with get_db_session() as session:
            step = WorkflowRepository(session, identity.tenant_id).get_by_external_task_id(
                task_id, principal_id=identity.principal_id
            )
            if step is None:
                return None
            state = self._STEP_STATUS_TO_TASK_STATE.get(step.status, TaskState.TASK_STATE_WORKING)
            task = Task(id=task_id, context_id=step.context_id, status=TaskStatus(state=state))
            if step.response_data:
                task.artifacts.append(
                    Artifact(
                        artifact_id=f"{task_id}_result",
                        name="media_buy_result",
                        parts=[Part(data=_dict_to_value(step.response_data))],
                    )
                )
            return task

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Handle 'tasks/cancel' method to cancel a task.

        Mirrors ``on_get_task``'s durability (#1544 B6): the in-memory task is
        resolved first, then the persisted workflow step carrying the buyer's outer
        ``task_*`` id — so a cancel still lands after a restart or in a different
        process than the create. A task/step already in a terminal state cannot be
        canceled; the durable check runs even on an in-memory hit so a stale
        WORKING task can't cancel a workflow that was approved out-of-band.

        Grounding: ``tasks/cancel`` semantics are A2A-protocol-native (A2A spec
        Task Management: ``TaskNotCancelableError`` for tasks already in a
        terminal state; the SDK ``default_request_handler`` is the reference
        cross-check). AdCP 3.1.1 prose defines no cancel contract of its own —
        a2a-guide.mdx "Webhook Trigger Rules" lists ``canceled`` among the final
        states ("Cancellation confirmed"). Storyboard: ungraded, pending the
        upstream task-lifecycle obligation (#1574).

        Returns:
            Task object with canceled status, or None if not found anywhere.
        """
        task_id = params.id
        task = self.tasks.get(task_id)
        if task is not None and task.status.state in self._TERMINAL_TASK_STATES:
            raise TaskNotCancelableError(message=f"Task cannot be canceled - current state: {task.status.state}")
        durable = self._durable_cancel_step(task_id, context)
        if task is not None:
            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_CANCELED))
            self.tasks[task_id] = task
            return task
        return durable

    def _durable_cancel_step(self, task_id: str, context: ServerCallContext | None) -> Task | None:
        """Durably cancel the workflow step carrying this outer task id.

        Tenant- AND principal-scoped (see ``_durable_lookup_identity``). Returns
        None when no persisted step matches. Raises ``TaskNotCancelableError`` when
        the step already reached a terminal status: an approved/completed media buy
        cannot be undone by ``tasks/cancel``.

        The transition itself is a single conditional UPDATE
        (``cancel_if_nonterminal``) so a concurrent approval that commits
        ``completed``/``rejected`` after our read cannot be overwritten — the
        zero-row outcome is reported as ``TaskNotCancelableError`` with the fresh
        status, and the terminal decision stands.
        """
        identity = self._durable_lookup_identity(context)
        if identity is None:
            return None

        from src.core.database.database_session import get_db_session
        from src.core.database.repositories.workflow import WorkflowRepository

        with get_db_session() as session:
            repo = WorkflowRepository(session, identity.tenant_id)
            step = repo.get_by_external_task_id(task_id, principal_id=identity.principal_id)
            if step is None:
                return None
            if not repo.cancel_if_nonterminal(step.step_id, completed_at=datetime.now(UTC)):
                session.rollback()
                fresh = repo.get_by_step_id(step.step_id)
                current = fresh.status if fresh is not None else "unknown"
                raise TaskNotCancelableError(message=f"Task cannot be canceled - current step status: {current}")
            session.commit()
            return Task(
                id=task_id,
                context_id=step.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )

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
        tool_context = None
        try:
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "get_push_notification_config")

            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise InvalidParamsError(message="Missing required parameter: id")

            with PushNotificationConfigUoW(tool_context.tenant_id) as uow:
                assert uow.push_notification_configs is not None
                config = uow.push_notification_configs.get_by_id(
                    config_id,
                    principal_id=tool_context.principal_id,
                )

                if not config:
                    raise TaskNotFoundError(message=f"Push notification config not found: {config_id}")

                response_id = config.id
                response_url = config.url
                response_validation_token = config.validation_token or ""
                auth_scheme = config.authentication_type
                auth_credentials = config.authentication_token

            auth_info = (
                AuthenticationInfo(scheme=auth_scheme, credentials=auth_credentials)
                if auth_scheme and auth_credentials
                else None
            )
            return TaskPushNotificationConfig(
                id=response_id,
                task_id=params.task_id,
                url=response_url,
                authentication=auth_info,
                token=response_validation_token,
            )

        except A2AError:
            raise
        except Exception as e:
            record_boundary_error(
                "a2a",
                "get_push_notification_config",
                e,
                tenant_id=tool_context.tenant_id if tool_context else None,
                principal_id=tool_context.principal_id if tool_context else None,
            )
            raise _internal_error_for("get push notification config", e) from e

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Handle set push notification config requests.

        Creates or updates a push notification configuration for async operation callbacks.
        Buyers use this to register webhook URLs where they want to receive status updates.
        """
        tool_context = None
        try:
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

            if not url:
                raise InvalidParamsError(message="Missing required parameter: url")

            auth_type = None
            auth_token_value = None
            if params.HasField("authentication"):
                auth_type = params.authentication.scheme or None
                auth_token_value = params.authentication.credentials or None

            with PushNotificationConfigUoW(tool_context.tenant_id) as uow:
                assert uow.push_notification_configs is not None
                _config, created = uow.push_notification_configs.upsert(
                    config_id=config_id,
                    principal_id=tool_context.principal_id,
                    url=url,
                    authentication_type=auth_type,
                    authentication_token=auth_token_value,
                    validation_token=validation_token,
                    session_id=None,
                )

            logger.info(
                f"Push notification config {'created' if created else 'updated'}: {config_id} for tenant {tool_context.tenant_id}"
            )

            auth_info = (
                AuthenticationInfo(scheme=auth_type, credentials=auth_token_value)
                if auth_type and auth_token_value
                else None
            )
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
            record_boundary_error(
                "a2a",
                "create_push_notification_config",
                e,
                tenant_id=tool_context.tenant_id if tool_context else None,
                principal_id=tool_context.principal_id if tool_context else None,
            )
            raise _internal_error_for("set push notification config", e) from e

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Handle list push notification config requests.

        Returns all active push notification configurations for the authenticated principal.
        """
        tool_context = None
        try:
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "list_push_notification_configs")

            with PushNotificationConfigUoW(tool_context.tenant_id) as uow:
                assert uow.push_notification_configs is not None
                configs = uow.push_notification_configs.list_active_by_principal(
                    principal_id=tool_context.principal_id,
                )
                config_snapshots = [
                    (c.id, c.url, c.authentication_type, c.authentication_token, c.validation_token or "")
                    for c in configs
                ]

            configs_list = [
                TaskPushNotificationConfig(
                    id=snap_id,
                    task_id=params.task_id,
                    url=snap_url,
                    authentication=(
                        AuthenticationInfo(scheme=snap_auth_type, credentials=snap_auth_token)
                        if snap_auth_type and snap_auth_token
                        else None
                    ),
                    token=snap_validation_token,
                )
                for snap_id, snap_url, snap_auth_type, snap_auth_token, snap_validation_token in config_snapshots
            ]

            logger.info("Listed %s push notification configs for tenant %s", len(configs_list), tool_context.tenant_id)

            return ListTaskPushNotificationConfigsResponse(configs=configs_list)

        except A2AError:
            raise
        except Exception as e:
            record_boundary_error(
                "a2a",
                "list_push_notification_configs",
                e,
                tenant_id=tool_context.tenant_id if tool_context else None,
                principal_id=tool_context.principal_id if tool_context else None,
            )
            raise _internal_error_for("list push notification configs", e) from e

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        """Handle delete push notification config requests.

        Marks a push notification configuration as inactive (soft delete).
        """
        tool_context = None
        try:
            auth_token = self._get_auth_token(context)
            if not auth_token:
                raise InvalidRequestError(message="Missing authentication token")
            identity = self._resolve_a2a_identity(auth_token, context=context)
            tool_context = self._make_tool_context(identity, "delete_push_notification_config")

            config_id = params.id
            if not config_id:
                raise InvalidParamsError(message="Missing required parameter: id")

            with PushNotificationConfigUoW(tool_context.tenant_id) as uow:
                assert uow.push_notification_configs is not None
                deleted = uow.push_notification_configs.soft_delete(
                    config_id,
                    principal_id=tool_context.principal_id,
                )
                if not deleted:
                    raise TaskNotFoundError(message=f"Push notification config not found: {config_id}")

            logger.info("Deleted push notification config: %s for tenant %s", config_id, tool_context.tenant_id)
            return None

        except A2AError:
            raise
        except Exception as e:
            record_boundary_error(
                "a2a",
                "delete_push_notification_config",
                e,
                tenant_id=tool_context.tenant_id if tool_context else None,
                principal_id=tool_context.principal_id if tool_context else None,
            )
            raise _internal_error_for("delete push notification config", e) from e

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        """Handle 'GetExtendedAgentCard' method."""
        raise UnsupportedOperationError(message="Extended agent card not supported")

    @staticmethod
    def _serialize_for_a2a(response: BaseModel | dict) -> dict:
        """Serialize a handler response for A2A protocol at the framework boundary.

        Single serialization point for all explicit-skill A2A responses.

        - Pydantic models: serialized via ``model_dump(mode="json")`` here,
          and the protocol fields (``message``, ``success``) are added.
        - Dicts: passed through. Only skill handlers that pre-apply version
          compat (e.g., ``_handle_get_products_skill`` calls
          ``apply_version_compat`` and emits a dict already populated with
          ``message``/``success``) use this path. Error dicts that bypass
          the envelope contract were retired in this PR — NL handlers now
          raise typed ``AdCPError`` instead.

        Args:
            response: Pydantic model OR pre-serialized dict from a skill
                handler.

        Returns:
            Dict ready for A2A DataPart.
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

    def _skill_handler_map(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        """Explicit-skill dispatch registry: skill name → bound handler.

        The single source of truth for which skills A2A dispatches. Exposed as a
        method so the transport-contract suite can assert a registry↔test bijection
        (every registered skill is exercised on the wire). Handler signatures are
        heterogeneous (discovery skills accept ``identity: ResolvedIdentity | None``;
        the rest require non-None), so dispatch is typed dynamically — the
        non-discovery guard in ``_handle_explicit_skill`` enforces identity first.
        """
        return {
            # Core AdCP Discovery Skills
            "get_adcp_capabilities": self._handle_get_adcp_capabilities_skill,
            # Core AdCP Media Buy Skills
            "get_products": self._handle_get_products_skill,
            "create_media_buy": self._handle_create_media_buy_skill,
            # Discovery Skills
            "list_creative_formats": self._handle_list_creative_formats_skill,
            "list_accounts": self._handle_list_accounts_skill,
            "sync_accounts": self._handle_sync_accounts_skill,
            "list_authorized_properties": self._handle_list_authorized_properties_skill,
            # Media Buy Management Skills
            "update_media_buy": self._handle_update_media_buy_skill,
            "get_media_buys": self._handle_get_media_buys_skill,
            "get_media_buy_delivery": self._handle_get_media_buy_delivery_skill,
            "update_performance_index": self._handle_update_performance_index_skill,
            # AdCP Spec Creative Management (centralized library approach)
            "sync_creatives": self._handle_sync_creatives_skill,
            "list_creatives": self._handle_list_creatives_skill,
            "create_creative": self._handle_create_creative_skill,
            "assign_creative": self._handle_assign_creative_skill,
            # Creative Management & Approval
            "approve_creative": self._handle_approve_creative_skill,
            "get_media_buy_status": self._handle_get_media_buy_status_skill,
            "optimize_media_buy": self._handle_optimize_media_buy_skill,
        }

    async def _handle_explicit_skill(
        self,
        skill_name: str,
        parameters: dict,
        identity: ResolvedIdentity | None,
        push_notification_config: TaskPushNotificationConfig | None = None,
        task_id: str | None = None,
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
        # The buyer's wire payload, captured BEFORE the pnc protocol-layer
        # injection, deprecated-field normalization, and any handler mutations —
        # the idempotency payload-hash input (AdCP defines equivalence over the
        # request as sent). Deep copy: downstream steps mutate nested dicts.
        raw_wire_payload = copy.deepcopy(parameters)

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

        logger.info("Handling explicit skill: %s with parameters: %s", skill_name, list(parameters.keys()))

        # Validate identity for non-discovery skills
        if skill_name not in DISCOVERY_SKILLS and (identity is None or not identity.principal_id):
            raise InvalidRequestError(message="Authentication required for skill invocation")

        skill_handlers = self._skill_handler_map()

        try:
            # An unknown SKILL is an application-layer failure — the JSON-RPC method
            # (message/send) is valid; routing failed inside skill dispatch. Per AdCP
            # 3.1.1 transport-errors.mdx "Layer Separation", it belongs in the
            # task body as a failed Task with a two-layer envelope, NOT a JSON-RPC
            # MethodNotFoundError (reserved for unknown JSON-RPC methods). Raised
            # INSIDE this try so the boundary observability below records it exactly
            # once (Finding: unknown skills bypassed record_boundary_error); the outer
            # dispatcher's `except AdCPError` re-wraps it into a failed-skill result,
            # preserving accumulated results from earlier skills.
            if skill_name not in skill_handlers:
                available_skills = list(skill_handlers.keys())
                raise AdCPCapabilityNotSupportedError(
                    message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}"
                )

            handler = skill_handlers[skill_name]
            # Handlers return raw Pydantic models (or raise typed AdCPError on validation failure)
            if skill_name == "create_media_buy":
                result = await handler(parameters, identity, raw_wire_payload=raw_wire_payload, a2a_task_id=task_id)
            else:
                result = await handler(parameters, identity)
            # Serialize at the boundary — models become dicts with protocol fields
            return self._serialize_for_a2a(result)
        except A2AError:
            # Re-raise A2AError as-is (already properly formatted)
            raise
        except (AdCPError, ValueError, PermissionError) as e:
            # Map to the SEMANTIC typed error the buyer's contract expects — the same
            # mapping MCP/REST apply: ``ValueError → AdCPValidationError`` (VALIDATION_ERROR,
            # correctable), ``PermissionError → AdCPAuthorizationError`` (AUTH_REQUIRED), a
            # native ``AdCPError`` passes through unchanged — then re-raise so the outer
            # dispatcher wraps it into a failed Task. Wire-MESSAGE sanitization is NOT done
            # here; it happens once at the envelope boundary (``_build_error_envelope`` →
            # ``_safe_adcp_error``), which scrubs ONLY the internal-error bucket
            # (``wire_error_code == "SERVICE_UNAVAILABLE"`` — untyped crashes that normalize
            # to a base ``AdCPError`` here, and ``AdCPAdapterError(str(e))``) while preserving
            # client-correctable messages. Keeping the semantic class here means the wire
            # code stays VALIDATION_ERROR / AUTH_REQUIRED, not a blanket internal error.
            #
            # Defensive about identity shape — test fixtures sometimes pass a string or
            # partially-built identity; record_boundary_error handles None internally.
            normalized = normalize_to_adcp_error(e)
            record_boundary_error(
                "a2a",
                skill_name,
                normalized,
                tenant_id=getattr(identity, "tenant_id", None),
                principal_id=getattr(identity, "principal_id", None) or "anonymous",
            )

            if normalized is not e:
                raise normalized from e
            raise
        # Untyped exceptions fall through to the dispatcher's `except Exception`
        # at the call site, which routes them through `_build_failed_skill_result`
        # for uniform envelope shape. No catch-all here.

    async def _handle_get_products_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit get_products skill invocation.

        Aligned with adcp spec - brand must be a BrandReference dict.

        NOTE: Authentication is OPTIONAL for this endpoint. Access depends on tenant's
        brand_manifest_policy setting (public/require_brand/require_auth).
        """
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

    async def _handle_create_media_buy_skill(
        self,
        parameters: dict,
        identity: ResolvedIdentity,
        raw_wire_payload: dict | None = None,
        a2a_task_id: str | None = None,
    ) -> dict:
        """Handle explicit create_media_buy skill invocation.

        IMPORTANT: This handler ONLY accepts AdCP spec-compliant format:
        - packages[] (required) - each package must have budget
        - brand (required)
        - start_time (required)
        - end_time (required)

        Per AdCP v2.2.0 spec, budget is specified at the PACKAGE level, not top level.
        Legacy format (product_ids, total_budget, start_date, end_date) is NOT supported.
        """
        tool_context = self._make_tool_context(identity, "create_media_buy")

        # Parse parameters into typed request model (validation at A2A boundary)
        from src.core.schemas import CreateMediaBuyRequest

        # Pre-process: A2A field name translations
        params = {**parameters}
        if "custom_targeting" in params:
            params.setdefault("targeting_overlay", params.pop("custom_targeting"))
        # No server-minted defaults for buyer payload fields: a randomized
        # po_number would change the request's canonical idempotency hash, so an
        # identical A2A retry would reject as IDEMPOTENCY_CONFLICT instead of
        # replaying — and the stored payload would diverge from the same request
        # sent via MCP/REST (cross-transport parity). po_number stays None when
        # the buyer omits it, exactly like the other transports.
        # buyer_ref removed in adcp 3.12

        # push_notification_config is an A2A *transport-layer* parameter
        # (injected by _handle_explicit_skill from the SendMessageConfiguration).
        # It is forwarded to core_create_media_buy_tool as a SEPARATE argument
        # below — exactly like create_media_buy_raw / the MCP wrapper, which
        # never fold it into CreateMediaBuyRequest. Validating it as part of
        # the request body would apply the adcp Authentication.credentials
        # MinLen(32) constraint to the whole create_media_buy, so a short
        # webhook credential would (incorrectly) divert the request away from
        # the manual-approval gate (gh-#1299).
        push_notification_config = params.pop("push_notification_config", None)

        # Normalize explicit brand through the shared coercion funnel (#1324).
        # Keep params JSON-serializable: raw_wire_payload falls back to params for
        # direct handler callers, and idempotency hashes RFC 8785 over that dict.
        # to_brand_reference returns None only for None input (excluded above); every
        # other input returns BrandReference or raises typed AdCPValidationError.
        if params.get("brand") is not None:
            brand_ref = to_brand_reference(params["brand"])
            assert brand_ref is not None  # None only for None input; excluded by guard
            params["brand"] = brand_ref.model_dump(mode="json")

        # Validate required AdCP parameters (packages is optional in model but required by spec).
        # Raise typed AdCPValidationError so the outer dispatcher's `except AdCPError` branch
        # routes through `_build_failed_skill_result` -> `_build_error_envelope`, producing
        # the single two-layer envelope wire shape. Returning a custom dict here bypasses
        # the envelope builder and erases the real code on the buyer side.
        required_params = ["brand", "packages", "start_time", "end_time"]
        missing_params = [p for p in required_params if p not in params]
        if missing_params:
            raise AdCPValidationError(
                f"Missing required AdCP parameters: {missing_params}",
                suggestion=f"Required: {required_params}",
            )

        # Validate via the shared boundary so every A2A handler emits the same
        # field + message + buyer-facing suggestion (AdCP POST-F3, #1417):
        # idempotency_key_missing / duplicate_product_id rejections include a
        # non-empty suggestion derived by adcp_validation_boundary.
        with adcp_validation_boundary():
            req = CreateMediaBuyRequest.model_validate(params)

        # Call core function with validated parameters and identity.
        # Per AdCP 4.3 (commit 3c604130) targeting_overlay and budgets live on each
        # PackageRequest; only request-level spec fields are forwarded here.
        response = await core_create_media_buy_tool(
            brand=params.get("brand"),
            po_number=req.po_number,
            packages=params["packages"],  # Required — validated above
            start_time=params.get("start_time"),
            end_time=params.get("end_time"),
            push_notification_config=push_notification_config,
            reporting_webhook=params.get("reporting_webhook"),
            context=params.get("context"),
            # Wrap for boundary-pattern consistency with delivery/sync_creatives. A crash is
            # structurally impossible here (create_media_buy_raw re-coerces via
            # CreateMediaBuyRequest), and to_account_reference is idempotent on an already
            # typed/dict account — but resolving at the boundary keeps all three handlers uniform.
            account=to_account_reference(params.get("account")),
            idempotency_key=params.get("idempotency_key"),
            identity=identity,
            # The DataPart params AS SENT (pre-normalization, pre-mutation) are
            # the idempotency payload-hash input; the post-processed dict is the
            # fallback only for direct handler callers.
            raw_wire_payload=raw_wire_payload if raw_wire_payload is not None else params,
            # Persist the outer A2A task id on the workflow step so the completion
            # webhook / tasks/get correlate to the id the buyer holds. #1544 B6.
            external_task_id=a2a_task_id,
        )

        return response

    async def _handle_sync_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit sync_creatives skill invocation (AdCP spec endpoint)."""
        # DEBUG: Log incoming parameters
        logger.info("[A2A sync_creatives] Received parameters keys: %s", list(parameters.keys()))
        logger.info("[A2A sync_creatives] assignments param: %s", parameters.get("assignments"))
        logger.info("[A2A sync_creatives] creatives count: %s", len(parameters.get("creatives", [])))

        # Create ToolContext from A2A auth info and resolve identity
        tool_context = self._make_tool_context(identity, "sync_creatives")

        # Map A2A parameters - creatives is required.
        # Raise typed AdCPValidationError so the outer dispatcher emits a two-layer envelope.
        if "creatives" not in parameters:
            raise AdCPValidationError(
                "Missing required parameter: 'creatives'",
                suggestion="Required: ['creatives']",
            )

        # Construct typed models at the A2A boundary (Pydantic validation at entry).
        # Pre-process format_id: upgrade legacy strings to FormatId models.
        from src.core.format_cache import upgrade_legacy_format_id

        with adcp_validation_boundary(context="sync_creatives request"):
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
            account=to_account_reference(parameters.get("account")),
            identity=identity,
        )

        return response

    async def _handle_list_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit list_creatives skill invocation (AdCP spec endpoint)."""
        # Create ToolContext from A2A auth info and resolve identity
        tool_context = self._make_tool_context(identity, "list_creatives")

        # Structured AdCP CreativeFilters (statuses, concept_ids, format_ids, …)
        # arrive over the wire as a JSON dict; coerce to the typed model the core
        # function expects so they are honoured rather than dropped. Invalid filters
        # raise AdCPValidationError (VALIDATION_ERROR + suggestion) via the shared helper.
        filters = coerce_creative_filters(parameters.get("filters"))

        # Call core function with optional parameters (fixing original validation bug)
        response = core_list_creatives_tool(
            media_buy_id=parameters.get("media_buy_id"),
            status=parameters.get("status"),
            format=parameters.get("format"),
            tags=parameters.get("tags", []),
            created_after=parameters.get("created_after"),
            created_before=parameters.get("created_before"),
            search=parameters.get("search"),
            filters=filters,
            page=parameters.get("page", 1),
            limit=parameters.get("limit", 50),
            sort_by=parameters.get("sort_by", "created_date"),
            sort_order=parameters.get("sort_order", "desc"),
            context=parameters.get("context"),
            identity=identity,
        )

        return response

    async def _handle_create_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit create_creative skill invocation."""
        tool_context = self._make_tool_context(identity, "create_creative")

        # Map A2A parameters - format_id, content_uri, and name are required.
        # Raise typed AdCPValidationError so the outer dispatcher emits a two-layer envelope.
        required_params = ["format_id", "content_uri", "name"]
        missing_params = [param for param in required_params if param not in parameters]

        if missing_params:
            raise AdCPValidationError(
                f"Missing required parameters: {missing_params}",
                suggestion=f"Required: {required_params}",
            )

        # TODO: Implement create_creative tool
        # Call core function with individual parameters
        # response = core_create_creative_tool(...)
        raise AdCPCapabilityNotSupportedError(message="create_creative skill not yet implemented")

    async def _handle_get_creatives_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_creatives skill invocation."""
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
        raise AdCPCapabilityNotSupportedError(message="get_creatives skill not yet implemented")

    async def _handle_assign_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit assign_creative skill invocation."""
        tool_context = self._make_tool_context(identity, "assign_creative")

        # Map A2A parameters - media_buy_id, package_id, and creative_id are required.
        # Raise typed AdCPValidationError so the outer dispatcher emits a two-layer envelope.
        required_params = ["media_buy_id", "package_id", "creative_id"]
        missing_params = [param for param in required_params if param not in parameters]

        if missing_params:
            raise AdCPValidationError(
                f"Missing required parameters: {missing_params}",
                suggestion=f"Required: {required_params}",
            )

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
        raise AdCPCapabilityNotSupportedError(message="assign_creative skill not yet implemented")

    async def _handle_approve_creative_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit approve_creative skill invocation."""
        raise AdCPCapabilityNotSupportedError(message="approve_creative skill not yet implemented")

    # Signals skill handlers removed - should come from dedicated signals agents

    async def _handle_get_media_buy_status_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit get_media_buy_status skill invocation."""
        raise AdCPCapabilityNotSupportedError(message="get_media_buy_status skill not yet implemented")

    async def _handle_optimize_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit optimize_media_buy skill invocation."""
        raise AdCPCapabilityNotSupportedError(message="optimize_media_buy skill not yet implemented")

    async def _handle_get_adcp_capabilities_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit get_adcp_capabilities skill invocation (CRITICAL AdCP discovery endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        Returns agent capabilities including supported protocols, targeting, and portfolio info.
        """
        # Identity already resolved at transport boundary (on_message_send)

        # Import and call the core implementation
        from src.core.tools.capabilities import get_adcp_capabilities_raw

        # Call core function with identity
        response = await get_adcp_capabilities_raw(
            protocols=parameters.get("protocols"),
            identity=identity,
        )

        return response

    async def _handle_list_creative_formats_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit list_creative_formats skill invocation (CRITICAL AdCP endpoint).

        NOTE: Authentication is OPTIONAL for this endpoint since it returns public discovery data.
        """
        # Identity already resolved at transport boundary (on_message_send)

        # Build request from parameters (all optional).
        from src.core.tools.creative_formats import build_list_creative_formats_request

        # Same context string as the REST route's boundary so buyer-invalid
        # input produces a byte-identical envelope on every transport (klkg).
        with adcp_validation_boundary(context="list_creative_formats request"):
            req = build_list_creative_formats_request(
                format_ids=parameters.get("format_ids"),
                output_format_ids=parameters.get("output_format_ids"),
                input_format_ids=parameters.get("input_format_ids"),
                is_responsive=parameters.get("is_responsive"),
                name_search=parameters.get("name_search"),
                asset_types=parameters.get("asset_types"),
                wcag_level=parameters.get("wcag_level"),
                min_width=parameters.get("min_width"),
                max_width=parameters.get("max_width"),
                min_height=parameters.get("min_height"),
                max_height=parameters.get("max_height"),
                context=parameters.get("context"),
            )

        # Call core function with identity
        response = core_list_creative_formats_tool(req=req, identity=identity)

        return response

    async def _handle_list_accounts_skill(self, parameters: dict, identity: ResolvedIdentity | None) -> Any:
        """Handle explicit list_accounts skill invocation.

        Authentication is OPTIONAL per BR-RULE-055 — unauthenticated calls
        return an empty account list.
        """
        from src.core.schemas.account import ListAccountsRequest

        # Same context string as the REST route's boundary (klkg parity).
        with adcp_validation_boundary(context="list_accounts request"):
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

        # Same context string as the REST route's boundary (klkg parity).
        with adcp_validation_boundary(context="sync_accounts request"):
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

        # Same context string as the REST route's boundary (klkg parity).
        with adcp_validation_boundary(context="list_authorized_properties request"):
            request = ListAuthorizedPropertiesRequest(context=parameters.get("context"))

        # Call core function with identity
        response = core_list_authorized_properties_tool(req=request, identity=identity)

        return response

    async def _handle_update_media_buy_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit update_media_buy skill invocation (CRITICAL for campaign management)."""
        # Identity already resolved at transport boundary (on_message_send)

        # Parse parameters into typed request model (validation at A2A boundary)
        from src.core.schemas import UpdateMediaBuyRequest

        # Pre-process: support legacy 'updates.packages' → 'packages'
        params = {**parameters}
        if "packages" not in params and "updates" in params:
            legacy_updates = params.pop("updates")
            if isinstance(legacy_updates, dict) and "packages" in legacy_updates:
                params["packages"] = legacy_updates["packages"]

        # media_buy_id is required. Raise typed AdCPValidationError so the dispatcher
        # routes it through the two-layer envelope, matching the create_media_buy skill.
        if "media_buy_id" not in params:
            raise AdCPValidationError(
                "Missing required parameter: media_buy_id",
                suggestion="Provide the media_buy_id of the media buy to update",
            )

        # Validate top-level fields via typed model (packages validated by _raw
        # which handles legacy formats with extra fields like 'status')
        with adcp_validation_boundary():
            req = UpdateMediaBuyRequest(
                media_buy_id=params.get("media_buy_id"),
                paused=params.get("paused"),
                start_time=params.get("start_time"),
                end_time=params.get("end_time"),
                context=params.get("context"),
            )

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

    async def _handle_get_media_buys_skill(self, parameters: dict, identity: ResolvedIdentity) -> Any:
        """Handle get_media_buys skill invocation."""
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        params = {**parameters}
        include_snapshot = params.pop("include_snapshot", False)
        # No REST route exists for get_media_buys; context string follows the
        # same "<tool> request" convention as the sibling boundaries (klkg).
        with adcp_validation_boundary(context="get_media_buys request"):
            req = GetMediaBuysRequest.model_validate(params)
        response = _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)

        return response

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
        # Identity already resolved at transport boundary (on_message_send)

        # Parse parameters into typed request model (validation at A2A boundary)
        # Pre-process: support singular media_buy_id (legacy) → media_buy_ids (spec)
        from src.core.schemas import GetMediaBuyDeliveryRequest

        params = {**parameters}
        if "media_buy_ids" not in params and "media_buy_id" in params:
            params["media_buy_ids"] = [params.pop("media_buy_id")]

        with adcp_validation_boundary():
            req = GetMediaBuyDeliveryRequest.model_validate(params)

        # Call core function with validated fields (all optional per AdCP spec).
        # Every _impl parameter MUST be forwarded (Critical Pattern #5 —
        # transport boundary completeness): reporting_dimensions,
        # attribution_window, include_package_daily_breakdown and account
        # were previously dropped, silently discarding the buyer's
        # requested attribution window (gh-#1299 follow-up).
        # Pass raw values for fields where _raw handles its own type coercion
        # (e.g., status_filter str→MediaBuyStatus, date str→date).
        response = core_get_media_buy_delivery_tool(
            media_buy_ids=req.media_buy_ids,
            status_filter=params.get("status_filter"),
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            reporting_dimensions=req.reporting_dimensions,
            attribution_window=req.attribution_window,
            include_package_daily_breakdown=req.include_package_daily_breakdown,
            # account is a typed AccountReference on GetMediaBuyDeliveryRequest (adcp SDK 5.7);
            # forward the validated model field rather than re-coercing the raw dict (#1438).
            account=req.account,
            context=params.get("context"),
            identity=identity,
        )

        return response

    async def _handle_update_performance_index_skill(self, parameters: dict, identity: ResolvedIdentity) -> dict:
        """Handle explicit update_performance_index skill invocation (CRITICAL for optimization)."""
        # Identity already resolved at transport boundary (on_message_send)

        # Parse parameters into typed request model (validation at A2A boundary)
        from src.core.schemas import UpdatePerformanceIndexRequest

        with adcp_validation_boundary():
            req = UpdatePerformanceIndexRequest.model_validate(parameters)

        # Call core function with validated fields and identity
        response = core_update_performance_index_tool(
            media_buy_id=req.media_buy_id,
            performance_data=[p.model_dump(mode="json") for p in req.performance_data],
            context=req.context,
            identity=identity,
        )

        return response

    async def _get_products(self, query: str, identity: ResolvedIdentity | None) -> dict:
        """Get available advertising products by calling core functions directly.

        Args:
            query: User's product query
            identity: Pre-resolved identity from transport boundary

        Returns:
            Dictionary containing product information
        """
        # Identity already resolved at transport boundary (on_message_send).
        # Exceptions propagate to the outer ``on_message_send`` handler, which
        # attaches a spec-compliant two-layer envelope to the failed Task
        # artifact. The previous ``except Exception → return {"products": []}``
        # bypass synthesized a fake-success Task DataPart that storyboard
        # runners parsed as ``MCP_ERROR`` — that violates the envelope contract.

        # Call core function directly using the underlying function
        response = await core_get_products_tool(
            brief=query,
            identity=identity,
        )

        # Convert to A2A response format with v2.x backward compatibility
        from src.core.version_compat import apply_version_compat

        products = [product.model_dump(mode="json") for product in (response.products or [])]
        response_data = {
            "products": products,
            "message": str(response),  # Use __str__ method for human-readable message
        }
        return apply_version_compat("get_products", response_data, None)

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
        """Natural-language create_media_buy is not supported; explicit skill is the spec contract.

        Always raises ``AdCPCapabilityNotSupportedError``. Buyer agents reach
        the explicit-skill path via ``create_media_buy`` skill invocation
        through ``_handle_explicit_skill`` — that path runs the full
        ``_create_media_buy_impl``, produces a spec-compliant Pydantic
        response, and goes through ``_serialize_for_a2a``.

        The previous NL stub returned a flat ``{"success": False, "message": "...
        use explicit skill"}`` dict that bypassed the two-layer-envelope
        contract — storyboard runners parsing that artifact synthesized
        ``MCP_ERROR`` rather than seeing the real wire code. Raising here
        flows to the outer ``on_message_send`` error handler which attaches
        the proper two-layer envelope to the failed Task artifact.
        """
        raise AdCPCapabilityNotSupportedError(
            "Natural-language create_media_buy is not supported. "
            "Invoke the explicit ``create_media_buy`` skill with AdCP-spec parameters."
        )


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
            # Note: approve_creative, get_media_buy_status, and optimize_media_buy are
            # deliberately NOT advertised (round-9 SF-B). Their handlers unconditionally
            # raise UNSUPPORTED_FEATURE, so advertising them would promise capabilities
            # the agent does not provide. They stay registered in _skill_handler_map and
            # remain reachable-but-unsupported (structured UNSUPPORTED_FEATURE failed
            # Task) if a buyer invokes them by name — they are just no longer offered on
            # the card. The test oracle (SKILL_METADATA) marks them advertised: False.
            # Note: signals skills removed - should come from dedicated signals agents
            # Note: legacy get_pricing/get_targeting removed - use get_products and get_adcp_capabilities instead
        ],
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


# Standalone execution removed — A2A is now integrated into the unified
# FastAPI app (src/app.py) via add_routes_to_app(). The AdCPRequestHandler
# and create_agent_card() are imported by src/app.py.

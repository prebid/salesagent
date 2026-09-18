#!/usr/bin/env python3
"""
Prebid Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator, Mapping

# Import core functions for direct calls (raw functions without FastMCP decorators)
from typing import Any

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    InternalError,
    InvalidRequestError,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    MethodNotFoundError,
    Part,
    PushNotificationNotSupportedError,
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
from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS
from adcp.types.generated_poc.enums.task_status import TaskStatus as LibraryTaskStatus
from google.protobuf import json_format, struct_pb2

from src.a2a_server.context_builder import EXCHANGE_STATE_KEY
from src.core.domain_config import get_a2a_server_url
from src.core.exceptions import AdcpFailure
from src.core.resolved_identity import TransportProtocol
from src.core.signing.capture import HttpExchange
from src.core.tools._boundary import failure_response, serve
from src.core.tools._wire import to_wire
from src.core.tools.registry import TOOLS
from src.core.version import get_version

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


# Field names typed `integer` (not `number`) in the pinned AdCP v3.1.1 schema
# that this server can place in an A2A Part.data (via _dict_to_value above).
#
# google.protobuf.Value/Struct -- the well-known types backing Part.data --
# have NO integer variant: every JSON number is stored as number_value (a
# double), by protobuf's own well-known-type design. Any int placed in a
# Part.data is therefore irreversibly widened to a double the moment it
# enters the Struct/Value, and comes back out as a JSON float (86400 ->
# 86400.0) from ANY subsequent json_format.MessageToJson/MessageToDict call
# -- ours or the a2a-sdk's own jsonrpc_dispatcher.py, which performs the
# identical conversion to build the real HTTP response body. There is no way
# to preserve the distinction inside the Struct/Value representation itself;
# the only fix point is a coercion applied to the JSON produced FROM the
# Struct/Value, driven by which fields are known to be integer-typed per spec.
#
# Spec: v3.1.1 (adcp==6.6.0) -- replay_ttl_seconds:
# get-adcp-capabilities-response.json #/properties/adcp/properties/idempotency
# (type: integer). limit: get-creative-delivery-response.json
# #/properties/limit. Others below are verified `type: integer` fields on
# this server's other explicit-skill responses (sync/assign counts, delivery
# totals, revision, attribution window). Extend this set as new integer
# fields are found on the A2A wire -- coercion only fires for a listed name
# whose value is a whole-numbered float, so an unlisted or genuinely
# fractional field is never touched.
A2A_WIRE_INTEGER_FIELDS = frozenset(
    {
        "replay_ttl_seconds",
        "limit",
        "returned_count",
        "revision",
        "interval",
        "attribution_window_days",
        "total_processed",
        "created",
        "updated",
        "unchanged",
        "failed",
        "deleted",
        "total_assignments_processed",
        "assigned",
        "unassigned",
        "total_impressions",
        "active_count",
        "impressions",
    }
)


#: AdCP envelope status -> A2A Task state. The mapping A2A's Task state IS, declared once.
#:
#: TOTAL over ``enums/task-status.json``: all nine members are keys, because the consumer
#: subscripts it. A partial mapping plus a default is how a status nobody had thought about
#: came to report COMPLETED, and it is what the inline ``== "submitted"`` compares this
#: replaces did for seven of the nine.
#:
#: The two vocabularies turn out to be the same set, one name apart, so every row is an exact
#: counterpart rather than a judgement. ``unknown`` -> ``UNSPECIFIED`` is the only rename.
_TASK_STATE_BY_ADCP_STATUS: dict[LibraryTaskStatus, TaskState] = {
    LibraryTaskStatus.submitted: TaskState.TASK_STATE_SUBMITTED,
    LibraryTaskStatus.working: TaskState.TASK_STATE_WORKING,
    LibraryTaskStatus.input_required: TaskState.TASK_STATE_INPUT_REQUIRED,
    LibraryTaskStatus.completed: TaskState.TASK_STATE_COMPLETED,
    LibraryTaskStatus.canceled: TaskState.TASK_STATE_CANCELED,
    LibraryTaskStatus.failed: TaskState.TASK_STATE_FAILED,
    LibraryTaskStatus.rejected: TaskState.TASK_STATE_REJECTED,
    LibraryTaskStatus.auth_required: TaskState.TASK_STATE_AUTH_REQUIRED,
    LibraryTaskStatus.unknown: TaskState.TASK_STATE_UNSPECIFIED,
}


def restore_a2a_integer_types(data: Any, integer_field_names: frozenset[str] = A2A_WIRE_INTEGER_FIELDS) -> Any:
    """Recursively coerce known integer-typed fields back to ``int``.

    Reverses the double-widening every number undergoes when it round-trips
    through a protobuf Struct/Value (see A2A_WIRE_INTEGER_FIELDS above).
    Only touches a value that is BOTH a whole-numbered float AND at a key in
    ``integer_field_names`` -- an unlisted key or a genuinely fractional
    value is returned unchanged, so this cannot silently corrupt real
    ``number``-typed fields.

    Shared by the production ``/a2a`` route wrapper (src/app.py) and the test
    harness's ``extract_data_from_artifact`` (tests/utils/a2a_helpers.py) --
    both perform the same Struct/Value -> JSON conversion the a2a-sdk itself
    performs, so both need the same restoration to keep the harness's "real
    A2A wire" claim honest.
    """
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in integer_field_names and isinstance(value, float) and value.is_integer():
                result[key] = int(value)
            else:
                result[key] = restore_a2a_integer_types(value, integer_field_names)
        return result
    if isinstance(data, list):
        return [restore_a2a_integer_types(item, integer_field_names) for item in data]
    return data


class AdCPRequestHandler(RequestHandler):
    """Request handler for AdCP A2A operations supporting JSON-RPC 2.0."""

    def __init__(self):
        """Initialize the AdCP A2A request handler."""
        self.tasks: dict[str, Task] = {}  # In-memory task storage
        # The VALUE, not the raw protobuf: what is stashed here is handed straight
        # to the sender, so it must carry the gate's receipt.
        logger.info("AdCP Request Handler initialized for direct function calls")

    def _headers_of(self, context: ServerCallContext) -> Mapping[str, str]:
        """The request headers the SDK's context builder placed on the call context.

        A subscript with no default. ``DefaultServerCallContextBuilder`` puts
        ``dict(request.headers)`` on ``state["headers"]`` for every HTTP request, so a
        context without them was never built from a request. That is a wiring fault: the
        ``KeyError`` reaches ``on_message_send``'s outer ``except`` as INTERNAL_ERROR rather
        than being read as an anonymous buyer.
        """
        return context.state["headers"]

    def _exchange_of(self, context: ServerCallContext) -> HttpExchange | None:
        """The captured HTTP message ``AdCPCallContextBuilder`` placed on the call context.

        ``.get`` with a default, unlike the headers above, and the asymmetry is deliberate.
        Headers absent means the context was not built from a request at all, which is a
        wiring fault worth an INTERNAL_ERROR. A capture absent is an ordinary outcome —
        nothing captures one outside an AdCP path — and it means "this request presented no
        signature", which the resolver handles without a branch here.
        """
        exchange = context.state.get(EXCHANGE_STATE_KEY)
        return exchange if isinstance(exchange, HttpExchange) else None

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        """Handle ``SendMessage``: one explicit skill invocation per message, answered as a Task.

        The invocation is a DataPart carrying ``{"skill": ..., "input": {...}}``. Text parts
        are recorded on the Task and route nothing.
        """
        logger.info("Handling SendMessage request: %s", params)

        # Before anything runs, and for the same reason a two-skill message is refused whole:
        # an envelope carrying something this agent will not honour is not a request it
        # half-serves. See ``_refuse_envelope_push_config``.
        self._refuse_envelope_push_config(params)

        text_parts: list[str] = []
        skill: str | None = None
        parameters: Any = {}
        for part in params.message.parts:
            # Text is recorded on the Task and routes nothing.
            if part.text:
                text_parts.append(part.text)
            elif part.HasField("data"):
                data = json_format.MessageToDict(part.data)
                if isinstance(data, dict) and "skill" in data:
                    # One skill per message. The pinned 3.1.1 text describes an A2A invocation
                    # as one DataPart naming one skill and says nothing about batching, so a
                    # message naming two is malformed rather than a batch: refused whole,
                    # before anything runs.
                    if skill is not None:
                        raise InvalidRequestError(
                            message="One skill per message. Send each skill invocation as its own message."
                        )
                    skill = data["skill"]
                    # ``input`` is the A2A spelling; ``parameters`` is accepted for the same value.
                    parameters = data.get("input") or data.get("parameters", {})
        if skill is None:
            # Guessing a tool from text is a translation concern. If it returns, it belongs IN
            # FRONT of this seam -- resolving text to (skill, parameters) and then invoking like
            # any other caller -- not beside it with its own response and its own auth story.
            raise InvalidRequestError(
                message=(
                    "This agent is invoked by explicit skill. Send a data part carrying "
                    "{'skill': <name>, 'input': {...}}; a text-only message names no skill."
                )
            )

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        # In protobuf, message_id is always a string (empty string default).
        msg_id = params.message.message_id or None
        task = Task(
            id=task_id,
            context_id=params.message.context_id or msg_id or f"ctx_{task_id}",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            # Recorded so a refused request is diagnosable; nothing routes on it.
            metadata=_dict_to_struct({"request_text": " ".join(text_parts).strip(), "skill": skill}),
        )
        self.tasks[task_id] = task

        try:
            # The request HEADERS, not an identity: the boundary resolves the caller once and
            # reads the row's auth declaration itself. A2A holds no identity of its own.
            headers = self._headers_of(context)
            exchange = self._exchange_of(context)

            # No ``except`` around this. A refused credential, a malformed payload and a failing
            # tool all leave ``serve`` as ``AdcpFailure``, which ``_dispatch_skill`` serializes
            # into the answer like any other response; the Task state below is read off that
            # answer's own ``status``. An unknown skill is an ``A2AError`` and propagates to the
            # JSON-RPC layer. AuthChallengeResponder reads a refused credential off the artifact
            # (``adcp_error_code_in``, shape 4), so the 401 handshake needs no branch here keyed
            # on an error class.
            result = await self._dispatch_skill(skill, parameters, headers, exchange)

            # Per AdCP spec, an async operation returns a Task with status=submitted and no
            # artifacts. The SAME read the final state uses, so the two cannot disagree.
            if LibraryTaskStatus(result["status"]) is LibraryTaskStatus.submitted:
                task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
                logger.info("Task %s requires manual approval, returning status=submitted with no artifacts", task_id)
                return task

            # Per A2A spec, an optional TextPart then the DataPart. The text is READ from the
            # payload: ``message`` is a declared envelope field, serialized with the rest, and
            # nothing rebuilds an outbound payload to recover it.
            parts = [Part(text=result["message"])] if result.get("message") else []
            parts.append(Part(data=_dict_to_value(result)))
            task.artifacts.append(Artifact(artifact_id="skill_result_1", name=f"{skill}_result", parts=parts))

            # The Task state is the RESPONSE's own status, mapped once. ``status`` is required
            # on the response envelope and defaulted to ``completed``, so it is never absent;
            # subscripted, not ``.get``-with-a-default, because a missing ``status`` would mean
            # a response that did not come from the boundary, and answering COMPLETED for it
            # would report success for something never examined.
            #
            # A per-creative ``pending_review`` deliberately does NOT make the Task
            # ``submitted``. Pinned ``creative/sync-creatives-response.json`` declares three
            # branches: a synchronous success required to carry ``creatives`` ("best-effort
            # processing with per-item status/failures"), a terminal failure carrying
            # ``errors``, and a submitted envelope whose ``status`` is ``const: "submitted"``
            # and which carries ``task_id`` and NO creatives. Per-creative review state is
            # therefore per-item information inside branch one, and promoting it to a
            # task-level status would claim the shape that cannot carry the creatives it just
            # processed.
            task.status.CopyFrom(TaskStatus(state=_TASK_STATE_BY_ADCP_STATUS[LibraryTaskStatus(result["status"])]))

        except A2AError:
            raise
        except Exception as e:
            # Raised before any tool ran -- reading the headers off the context, framing the
            # answer -- so no caller was resolved and the record is unscoped. Answered as the JSON-RPC error the
            # SDK's dispatcher serializes structurally, with the failure response in ``data``:
            # a non-``A2AError`` would be flattened to a bare InternalError with no body.
            response = failure_response(TransportProtocol.A2A, "message_processing", e)
            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_FAILED))
            raise InternalError(
                message=f"message processing failed: {response.errors[0].message}", data=to_wire(response)
            ) from e

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

    def _get_task_or_raise(self, task_id: str) -> Task:
        """Return the in-memory task, or raise ``TaskNotFoundError``.

        A bare ``None`` return makes the SDK synthesize a generic internal error;
        the A2A spec defines ``TaskNotFoundError`` for an unknown task id, so
        raising it is the correct thing to do here and is what an A2A client
        should be able to react to precisely.

        A client sees the spec's ``-32001``. It saw ``-32603`` for as long as the routes
        carried ``enable_v0_3_compat=True``: requests dispatched through
        ``a2a.compat.v0_3.jsonrpc_adapter``, whose ``handle_request`` ended in a bare
        ``except Exception -> CoreInternalError`` with no ``A2AError -> code`` mapping —
        the mapping the SDK's own dispatcher performs. That adapter is gone (#1670), so
        raising the right type now surfaces the right code, and the live-server test that
        pinned ``-32603`` under a strict xfail has graduated.

        The requested id rides both the message and structured ``data``, and both reach a
        client for the same reason: the compat adapter that rebuilt the error as
        ``CoreInternalError(message=str(e))`` — dropping ``data`` and returning
        ``data: null`` on the real route — is no longer in the path.

        Shared by ``on_get_task`` and ``on_cancel_task`` so both surface the
        same error.
        """
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(message=f"Task not found: {task_id}", data={"task_id": task_id})
        return task

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task:
        """Handle 'tasks/get' method to retrieve task status.

        Raises ``TaskNotFoundError`` for an unknown task id — see
        ``_get_task_or_raise`` (and #1670 for why the wire code is still -32603).
        """
        return self._get_task_or_raise(params.id)

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task:
        """Handle 'tasks/cancel' method to cancel a task.

        Raises ``TaskNotFoundError`` for an unknown task id — cancelling a task
        that does not exist is the same not-found condition as get, not a silent
        no-op. See ``_get_task_or_raise`` (and #1670 for why the wire code is
        still -32603).
        """
        task = self._get_task_or_raise(params.id)
        # CopyFrom mutates the stored Task in place — self.tasks already holds
        # this exact reference, so re-storing it would rebind the same object.
        task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_CANCELED))
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
        """Handle 'tasks/pushNotificationConfig/get'. Declined: this agent advertises push_notifications=False."""
        raise PushNotificationNotSupportedError()

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Handle 'tasks/pushNotificationConfig/set'. Declined: this agent advertises push_notifications=False."""
        raise PushNotificationNotSupportedError()

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Handle 'tasks/pushNotificationConfig/list'. Declined: this agent advertises push_notifications=False."""
        raise PushNotificationNotSupportedError()

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        """Handle 'tasks/pushNotificationConfig/delete'. Declined: this agent advertises push_notifications=False."""
        raise PushNotificationNotSupportedError()

    def _refuse_envelope_push_config(self, params: SendMessageRequest) -> None:
        """The FIFTH entry point to the same declined capability. Here, beside the other four.

        A2A lets a buyer attach a webhook to a skill invocation through
        ``params.configuration.task_push_notification_config``. AdCP defines no such channel
        — a webhook is a declared field of the AdCP request itself, ``push_notification_config``
        on the tool's own schema, which on this transport travels in the DataPart like every
        other request field. So the A2A push-notification capability is declined WHOLESALE:
        the agent card advertises ``push_notifications=False`` (``create_agent_card``, :571)
        and the four ``tasks/pushNotificationConfig/*`` methods above refuse.

        This envelope field was the one entry point that did neither. ``on_message_send``
        read ``params.message.parts`` and nothing else, so a registration sent here was
        silently DROPPED: the buyer received 200 for a webhook that was never registered.
        Worse, since the config never reached the validated request, neither the
        security.mdx @ v3.1.1 :1464 log duty nor the :1465 signature escalation ran for a
        payload carrying ``authentication`` — the two obligations the AdCP field triggers on
        every transport, including this one. Dropping and declining are indistinguishable
        from inside this agent and opposite from outside it.

        REFUSING, not threading it into the skill's parameters. Merging it would build an
        AdCP registration channel out of a transport-specific one, which is precisely what
        declining the capability decided against; refusing says what the agent card already
        said. A buyer that wants a webhook on this seller declares it in the AdCP request.
        """
        if params.HasField("configuration") and params.configuration.HasField("task_push_notification_config"):
            raise PushNotificationNotSupportedError()

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        """Handle 'GetExtendedAgentCard' method."""
        raise UnsupportedOperationError(message="Extended agent card not supported")

    async def _dispatch_skill(
        self,
        skill_name: str,
        parameters: Any,
        headers: Mapping[str, str],
        exchange: HttpExchange | None = None,
    ) -> dict[str, Any]:
        """Run one skill through ``serve`` and return the body the artifact DataPart carries.

        The whole of A2A's request path. A row with ``a2a=True`` IS dispatchable: the registry
        says which tools this transport serves and the card is derived from the same rows, so
        the two cannot disagree; anything else is ``MethodNotFoundError``, a JSON-RPC error.

        ``parameters`` is a plain JSON-shaped value by the time it arrives -- the A2A path is
        ``json_format.MessageToDict`` over a ``Struct`` -- and ``validated_request`` inside
        ``serve`` is the one parse, shared with MCP and REST. The one Struct artifact is that it
        has no integer type, and pydantic's non-strict mode already coerces ``2.0`` to an
        ``int`` field.
        """
        if skill_name not in TOOLS or not TOOLS[skill_name].a2a:
            available_skills = [name for name, spec in TOOLS.items() if spec.a2a]
            raise MethodNotFoundError(message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}")
        try:
            response = await serve(skill_name, parameters, headers, TransportProtocol.A2A, exchange)
        except AdcpFailure as failure:
            # A2A's wire failure marker is the Task STATE, set by the caller from the
            # response's own ``status``. This transport adds nothing to the BODY.
            return to_wire(failure.response)
        return to_wire(response)


def _derived_skills() -> list[AgentSkill]:
    """The agent card's skills, generated from :data:`TOOLS`.

    ``id`` and ``name`` are the tool name -- a REST route, an MCP tool and an A2A skill for
    one tool carry one name, so there is nothing here that could diverge from the other two
    transports. ``description`` comes from the pinned SDK, the same source MCP registration
    reads. ``tags`` come off the DTO (``DTO.TAGS``): the SDK carries none, so they are ours,
    but they describe the tool's shape like the field descriptions beside them rather than
    its wiring -- so they sit with the shape, not in the registry.

    A tool whose DTO declares no TAGS contributes none. That is not an omission to fix: the
    three task tools have never been on A2A, so nobody has written tags for them, and
    inventing some here would be a declaration this file is not entitled to make.
    """
    descriptions = {d["name"]: d["description"] for d in ADCP_TOOL_DEFINITIONS}
    return [
        AgentSkill(
            id=name,
            name=name,
            description=descriptions.get(name, (spec.impl.__doc__ or "").strip().split("\n")[0]),
            tags=list(getattr(spec.dto, "TAGS", ())),
        )
        for name, spec in TOOLS.items()
        if spec.a2a
    ]


def create_agent_card() -> AgentCard:
    """Create the agent card describing capabilities.

    Returns:
        AgentCard with Prebid Sales Agent capabilities
    """
    # Use configured domain for agent card
    # Note: This will be overridden dynamically in the endpoint handlers
    # Fallback to localhost if SALES_AGENT_DOMAIN not configured
    server_url = get_a2a_server_url() or "http://localhost:8091/a2a"

    from a2a.types import AgentCapabilities
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
            # protocol_binding is REQUIRED in practice, not decorative. An A2A 1.x client
            # selects its interface with `i.protocolBinding?.toUpperCase() === "JSONRPC"`
            # (@a2a-js/sdk pick_interface.ts), so a card that omits it matches NOTHING: the
            # client finds no usable interface and reports the agent UNREACHABLE, having
            # never sent a request. Measured against @adcp/sdk 14.0.0-rc.35, whose runner
            # graded 0 checks for exactly this reason.
            AgentInterface(url=server_url, protocol_binding="JSONRPC", protocol_version="1.0"),
        ],
        capabilities=AgentCapabilities(
            push_notifications=False,
            extensions=[adcp_extension],
        ),
        default_input_modes=["message"],
        default_output_modes=["message"],
        skills=_derived_skills(),
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


# Standalone execution removed — A2A is now integrated into the unified
# FastAPI app (src/app.py) via add_routes_to_app(). The AdCPRequestHandler
# and create_agent_card() are imported by src/app.py.

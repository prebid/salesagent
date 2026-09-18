import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool, ToolResult
from rich.console import Console

from src.adapters.mock_creative_engine import MockCreativeEngine

logger = logging.getLogger(__name__)

# Database models

# Other imports
from src.core.database.database import init_db
from src.core.database.models import (
    WorkflowStep,
)

# Schema models (explicit imports to avoid collisions)
# Schema adapters (wrapping generated schemas)
from src.core.schemas import (
    Creative,
    CreativeAssignment,
    CreativeStatus,
    Error,  # noqa: F401 - Required for MCP protocol error handling (regression test PR #332)
    Product,
)

# Initialize Rich console
console = Console()

# Backward compatibility alias for deprecated Task model
# The workflow system now uses WorkflowStep exclusively
Task = WorkflowStep

# --- Helper Functions ---


# --- Helper Functions ---
# Helper functions moved to src/core/helpers/ modules and imported above

# --- Authentication ---
# Auth functions moved to src/core/auth.py and imported above


# --- Initialization ---
# NOTE: Database initialization moved to startup script to avoid import-time failures
# The run_all_services.py script handles database initialization before starting the MCP server


from contextlib import asynccontextmanager


def _background_schedulers_enabled() -> bool:
    """Whether to start the background schedulers on app startup.

    ``ADCP_RUN_BACKGROUND_SCHEDULERS`` is a **test-only** knob that defaults to
    ENABLED (schedulers run in production unless the value is exactly ``"false"``,
    read at runtime). The test harness sets it to ``false`` because the schedulers
    run a batch immediately on startup, on the *real* wall clock, and mutate
    media-buy status rows — which silently rewrites the rows a test just seeded
    (e.g. promoting a seeded ``pending_start`` buy to ``active`` before the
    assertion runs). It is NOT an operator control: disabling it in production
    stops automatic pending->active->completed status transitions and delivery
    webhooks, so an accidental disable is logged at WARNING (below) to make it
    visible in production logs.
    """
    from src.core.config import get_settings

    return get_settings().runtime.adcp_run_background_schedulers


# Lifespan context manager for FastMCP startup/shutdown
@asynccontextmanager
async def lifespan_context(app):
    """Handle application startup and shutdown."""
    schedulers_enabled = _background_schedulers_enabled()
    if not schedulers_enabled:
        # WARNING, not INFO: this is a test-only knob (see
        # _background_schedulers_enabled). If it is ever set in production the
        # status/webhook schedulers do not run — surface that loudly.
        logger.warning(
            "Background schedulers DISABLED via ADCP_RUN_BACKGROUND_SCHEDULERS=false — "
            "media-buy status transitions and delivery webhooks will NOT run. "
            "This is a test-only knob; unset it in production."
        )

    if schedulers_enabled:
        # Startup: Initialize delivery webhook scheduler
        from src.services.delivery_webhook_scheduler import start_delivery_webhook_scheduler

        logger.info("Starting delivery webhook scheduler...")
        try:
            await start_delivery_webhook_scheduler()
            logger.info("✅ Delivery webhook scheduler started")
        except Exception as e:
            logger.error(f"Failed to start delivery webhook scheduler: {e}", exc_info=True)

        # Startup: Initialize media buy status scheduler
        from src.services.media_buy_status_scheduler import start_media_buy_status_scheduler

        logger.info("Starting media buy status scheduler...")
        try:
            await start_media_buy_status_scheduler()
            logger.info("✅ Media buy status scheduler started")
        except Exception as e:
            logger.error(f"Failed to start media buy status scheduler: {e}", exc_info=True)

    yield

    if not schedulers_enabled:
        return

    # Shutdown: Stop media buy status scheduler
    from src.services.media_buy_status_scheduler import stop_media_buy_status_scheduler

    logger.info("Stopping media buy status scheduler...")
    try:
        await stop_media_buy_status_scheduler()
        logger.info("✅ Media buy status scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop media buy status scheduler: {e}", exc_info=True)

    # Shutdown: Stop delivery webhook scheduler
    from src.services.delivery_webhook_scheduler import stop_delivery_webhook_scheduler

    logger.info("Stopping delivery webhook scheduler...")
    try:
        await stop_delivery_webhook_scheduler()
        logger.info("✅ Delivery webhook scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop delivery webhook scheduler: {e}", exc_info=True)


mcp = FastMCP(
    name="AdCPSalesAgent",
    # Sessions enabled for HTTP context (tenant detection via headers)
    # Note: stateless_http is now configured at runtime via run() or global settings
    lifespan=lifespan_context,
)

# (Deleted) MCPAuthMiddleware resolved an identity before every tool call and stashed it on
# FastMCP context state for RegistryTool.run to read. The boundary resolves now, from the
# credential the tool hands it, so this was pure double resolution -- measured at 2x
# resolve_identity per MCP request while it stood.

# Initialize creative engine with minimal config (will be tenant-specific later)
creative_engine_config: dict[str, Any] = {}
creative_engine = MockCreativeEngine(creative_engine_config)


# Removed get_task_from_db - replaced by workflow-based system


# --- In-Memory State ---
creative_assignments: dict[str, dict[str, list[str]]] = {}
creative_statuses: dict[str, CreativeStatus] = {}
product_catalog: list[Product] = []
creative_library: dict[str, Creative] = {}  # creative_id -> Creative
creative_assignments_v2: dict[str, CreativeAssignment] = {}  # assignment_id -> CreativeAssignment
# REMOVED: human_tasks dictionary - now using direct database queries only

# Authentication cache removed - FastMCP v2.11.0+ properly forwards headers

# Import audit logger for later use

# Import context manager for workflow steps
from src.core.context_manager import ContextManager

context_mgr = ContextManager()

# --- In-Memory State (already initialized above, just adding context_map) ---
context_map: dict[str, str] = {}  # Maps context_id to media_buy_id


# --- Creative Conversion Helper ---
# Creative helper functions moved to src/core/helpers.py and imported above


# --- Security Helper ---


# --- Activity Feed Helper ---


# --- MCP Tools (Full Implementation) ---


# Unified update tools


# --- Admin Tools ---


# --- Human-in-the-Loop Task Queue Tools ---
# DEPRECATED workflow functions moved to src/core/helpers/workflow_helpers.py and imported above

# Removed get_pending_workflows - replaced by admin dashboard workflow views

# Removed assign_task - assignment handled through admin UI workflow management

# Dry run logs are now handled by the adapters themselves


# Creative macro support is now simplified to a single creative_macro string
# that AEE can provide as a third type of provided_signal.
# Ad servers like GAM can inject this string into creatives.

if __name__ == "__main__":
    init_db(exit_on_error=True)  # Exit on error when run as main
    # Server is now run via run_server.py script

# Always add health check endpoint

# --- Strategy and Simulation Control ---


# Health/debug routes moved to src/routes/health.py (FastAPI migration).
# Admin and landing routes moved to src/app.py (FastAPI migration).
# Task management tools extracted to src/core/tools/task_management.py.


# Import MCP tools from separate modules and register with MCP manually.
# Tool descriptions and ToolAnnotations are imported from the AdCP SDK at
# registration time. Our tools are a subset of the SDK's 57 — matching tools
# get agent-facing descriptions and annotations (readOnlyHint, destructiveHint,
# idempotentHint). Non-matching tools keep their existing docstrings.
from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS
from adcp.types.generated_poc.core.version_envelope import AdcpVersionEnvelope

# Request DTOs named explicitly for tools that do not reach one through a builder. The
# advertised shape is "DTO fields INTERSECT the implementation's arguments", so the DTO is
# not optional -- _register_tool refuses a tool without one.
from mcp.types import ToolAnnotations

from src.core.resolved_identity import TransportProtocol
from src.core.schemas._base import AdcpResponse
from src.core.signing.capture import HttpExchange, captured_exchange
from src.core.tools._announced_shape import sdk_grounding
from src.core.tools._boundary import _response_model_for
from src.core.tools.registry import TOOLS

_sdk_tool_defs = {td["name"]: td for td in ADCP_TOOL_DEFINITIONS}


#: The scope gate that used to live here is gone: the derivation now applies to EVERY tool,
#: and _register_tool refuses to register one whose DTO cannot be resolved, so there is no
#: unlisted-tool state left for a gate to describe.
#:
#: It documented two defects that widening the scope would cause. Both have been settled
#: rather than avoided, which is why the gate could go:
#:   * update_media_buy.budget widened from number to Budget|number by adopting the DTO type,
#:     while _build_update_request still did float(budget) -- the advertised payload 500'd.
#:     The builder now takes the object it advertises; graded by
#:     TestAdvertisedTypesAreAccepted, which constructs the advertised type and calls the real
#:     builder. That test exists because the name-dimension rule could not see a TYPE widening.
#:   * some object-typed parameters carry their description on the referenced $def rather than
#:     inline. Cosmetic, tracked separately; it never affected what buyers may send.


def _register_tool(tool_name: str, spec: Any) -> None:
    """Register an MCP tool with SDK description, annotations and ADVERTISED SHAPE.

    The request DTO is RESOLVED FROM THE REGISTRY ROW. There is no parameter to pass one
    explicitly: the escape hatch that allowed it is gone, so "registered with a hand-supplied
    DTO" is not a state this function can produce.

    It existed for list_tasks, whose pre-3.1.1 vocabulary intersected the SDK model at
    ``context`` alone. Rebasing that tool onto the spec shape left the parameter with zero
    users, and a zero-user escape hatch is one refactor away from being used again -- so it
    is deleted rather than documented as discouraged. A tool that cannot name its request
    DTO still cannot be registered; it just has exactly one way to name it now.

    RESOLVABLE IS NOT ENOUGH, so there is a second refusal. A DTO authored FROM the
    wrapper's signature satisfies the intersection by construction and grades nothing -- the
    tool would advertise whatever we wrote, derived from itself. For a tool THE PINNED SDK
    DEFINES, the DTO must therefore inherit the SDK's own request model. The condition is
    derived, not a list: ``sdk_def`` is the same lookup that supplies the description above,
    so a tool the SDK does not define carries no obligation it cannot meet, and gains one
    automatically the day it is renamed onto its spec operation. The four tools in that
    state today cannot be registered at all: this refusal runs at import, so the tree
    cannot start carrying an ungrounded spec tool.
    """
    sdk_def = _sdk_tool_defs.get(tool_name)
    model = spec.dto
    if model is not None and not issubclass(model, AdcpVersionEnvelope):
        raise RuntimeError(
            f"{tool_name} cannot be registered: {model.__name__} does not descend from "
            f"adcp's AdcpVersionEnvelope. Every request DTO reaches adcp_version and "
            f"adcp_major_version through it, so a model that does not is not a request in "
            f"this protocol -- and in practice it means the DTO came from a PARALLEL "
            f"HIERARCHY rather than from the SDK. That has happened: complete_task once had "
            f"two hand-written models, CompleteTaskRequest and CompleteTaskRequestLocal, "
            f"neither a subclass of the other, both descending from SalesAgentBaseModel, "
            f"differing by three required fields (salesagent-fdkub). Extend the SDK type for "
            f"this tool, or -- where the SDK ships none -- AdcpVersionEnvelope itself.\n\n"
            f"This check is UNGATED on purpose. The SDK-grounding refusal below fires only "
            f"when the SDK defines the tool, which exempts precisely the tools most likely "
            f"to grow a parallel model."
        )
    response_model = _response_model_for(spec.impl)
    if response_model is not None and not issubclass(response_model, AdcpResponse):
        raise RuntimeError(
            f"{tool_name} cannot be registered: {response_model.__name__} does not descend from "
            f"AdcpResponse, the base carrying the two envelopes every pinned response schema "
            f"composes at its root (version-envelope and protocol-envelope). The boundary "
            f"assigns adcp_version and replayed on every response and calls revive() on every "
            f"replay; a model without the base has nowhere to put them.\n\n"
            f"A response model that cannot hold an envelope field fails SILENTLY, which is why "
            f"this is a refusal and not a check: AdCPBaseModel serializes with "
            f"exclude_none=True, so the field is absent from the wire rather than null.\n\n"
            f"Add AdcpResponse to the response model's bases, AFTER its SDK type -- base order "
            f"decides which class's field definitions win, and the SDK type must keep its own "
            f"(a oneOf branch's const status among them)."
        )
    if sdk_def is not None and model is not None and sdk_grounding(model) is None:
        raise RuntimeError(
            f"{tool_name} cannot be registered: {model.__name__} does not inherit the SDK's "
            f"request model. The pinned AdCP version defines this tool, so its vocabulary is "
            f"the spec's, not ours -- and a DTO written from the wrapper's own signature "
            f"makes the announced shape tautological, advertising whatever we happened to "
            f"write. Extend the SDK's "
            f"request model for this tool -- most likely "
            f"adcp.types.{tool_name.title().replace('_', '')}Request, though the SDK is the "
            f"authority on the spelling -- per the Library* alias convention (critical "
            f"pattern #1), instead of redeclaring its fields."
        )
    mcp.add_tool(
        RegistryTool(
            name=tool_name,
            parameters=model.model_json_schema(),
            description=sdk_def["description"] if sdk_def else None,
            annotations=ToolAnnotations(**sdk_def["annotations"]) if sdk_def and sdk_def.get("annotations") else None,
        )
    )


def _mcp_exchange() -> HttpExchange | None:
    """The captured HTTP message for the MCP request in flight, or None outside one.

    FastMCP exposes the headers (``get_http_headers``) and the Starlette ``Request``
    (``get_http_request``), but NOT the raw body: by the time a tool runs, the streamable-HTTP
    transport has already read the JSON-RPC message off the ASGI receive channel itself,
    without going through that ``Request`` object — so ``await request.body()`` inside a tool
    would call an exhausted channel rather than return a cache. Checked, because the design
    note asked for it to be; the answer is that MCP needs the capture middleware and the other
    two transports could in principle have got by without it.

    What the middleware leaves behind is reachable from here, and that is the whole reason it
    writes to ``scope["state"]``: ``app.mount("/mcp", mcp_app)`` is a Starlette ``Mount``,
    which MUTATES the one scope dict rather than copying it, so the dict FastMCP hands back is
    the dict the middleware wrote to.

    ``RuntimeError`` is the SDK's own signal for "no active HTTP request" — an in-process
    invocation, or a Docket worker replaying snapshotted headers — and is caught rather than
    pre-tested because there is no non-raising predicate for it. The answer is None, which is
    a request presenting no signature, which is what it is.
    """
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:
        return None
    return captured_exchange(request.scope)


# MCP registration is DERIVED from the registry: TOOLS decides which tools exist, and this
# module only registers them. There is no list here to keep in step -- adding a row is
# sufficient to register a tool, which is what makes TOOLS the single declaration rather than
# a fourth one.
#
# The wrapper is resolved from the row rather than imported by name: it lives in the same
# module as the row's ``impl``, so ``TOOLS`` supplies the address and this file needs no
# sixteen imports whose only purpose was to be passed to the call below. A name that does not
# resolve is a defect in the row, not an optional registration.
class RegistryTool(Tool):
    """One registry row, served over MCP.

    ``run`` hands the buyer's argument object to ``serve``, the same entry REST and A2A use,
    so the one validation and the accepted-shape strip on ``BuyerRequest`` decide what reaches
    an implementation here too.

    A ``Tool`` subclass rather than a function, because FastMCP validates a FUNCTION tool
    against a TypeAdapter built from its annotations (``FunctionTool.run`` ->
    ``get_cached_typeadapter``). With the DTO's fields as parameters, that adapter reached
    the SDK's nested models first and refused or coerced the payload before any of our code
    ran -- so the strip was a no-op on MCP, dev rejection came from pydantic and production
    tolerance from a retry in the compat middleware. Two programs for one policy.

    ``parameters`` is the DTO's own JSON Schema, so the advertised shape is the model rather
    than a signature reconstructed from it.
    """

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        from fastmcp.server.dependencies import get_http_headers

        from src.core.exceptions import AdcpFailure
        from src.core.tool_error_logging import AdCPToolError
        from src.core.tools._boundary import failure_response, serve
        from src.core.tools._mcp import mcp_result
        from src.core.tools._wire import to_wire

        try:
            # The request headers, not an identity: the boundary resolves the caller. Outside
            # an HTTP request ``get_http_headers`` returns ``{}``, a request presenting
            # nothing, which the resolver answers AUTH_MISSING on a protected tool.
            headers = get_http_headers(include_all=True)
            return mcp_result(await serve(self.name, arguments, headers, TransportProtocol.MCP, _mcp_exchange()))
        except AdcpFailure as failure:
            response = failure.response
        except Exception as exc:
            # Raised OUTSIDE ``serve`` -- reading the headers, rendering the result -- so no
            # caller was resolved and the record is unscoped. A tool's own failure never
            # reaches here. What makes that true is upstream of the boundary: ``failure_response``
            # runs inside the boundary's own ``except Exception``, so anything it raises lands
            # HERE, identity-less and echo-less. A dict ``details`` with no ``to_wire`` did,
            # once, through the one raise site that had dropped the detail type parameter.
            response = failure_response(TransportProtocol.MCP, self.name, exc)
        # MCP's wire failure marker is a raised ToolError, and that marker is all this
        # transport adds: the BODY is the response the boundary built, serialized by the same
        # function the success path uses.
        raise AdCPToolError(to_wire(response))


for _tool_name, _spec in TOOLS.items():
    _register_tool(_tool_name, _spec)

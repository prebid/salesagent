"""FastMCP middleware for AdCP backward-compatibility normalization.

Translates deprecated field names, strips unknown fields, and converts FastMCP
TypeAdapter validation failures into AdCP envelopes in every environment. In
production, it first retries structural failures after schema-aware deep stripping.
Runs after MCPAuthMiddleware.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams
from pydantic import ValidationError

from src.core.adcp_version import validate_adcp_version_pins
from src.core.exceptions import AdCPError, normalize_to_adcp_error
from src.core.request_compat import (
    _log_dropped_fields,
    deep_strip_to_schema,
    normalize_request_params,
    strip_negotiation_fields,
    strip_undeclared_envelope_fields,
    strip_unknown_params,
    validate_standard_read_idempotency_key,
)
from src.core.tool_error_logging import (
    _reject_at_mcp_boundary,
    record_boundary_error,
    translate_to_tool_error,
)

logger = logging.getLogger(__name__)


class RequestCompatMiddleware(Middleware):
    """Normalize, strip, and provide forward-compatible fallback for MCP tools.

    Pipeline:
    1. Translate deprecated field names via normalize_request_params()
    2. Reject an unsupported AdCP version pin via validate_adcp_version_pins()
       (VERSION_UNSUPPORTED) — before the fields are stripped below.
    3. Validate a supplied idempotency_key on registered standard reads. With
       on reads it is validated inert metadata; omission is tolerated (3.1 grace).
    4. Drop AdCP version-negotiation envelope fields (adcp_version,
       adcp_major_version) via strip_negotiation_fields() — all environments,
       since no tool wrapper declares them (issue #1512).
    5. Drop standard AdCP envelope fields the tool doesn't declare
       (ADCP_ENVELOPE_FIELDS: context, ext, push_notification_config,
       idempotency_key) via strip_undeclared_envelope_fields() —
       all environments (issue #1512).
    6. Strip fields not in the tool's JSON Schema via strip_unknown_params()
       (production only).
    7. If TypeAdapter rejects the arguments, always translate and record the
       failure as an AdCP validation envelope (normalize_to_adcp_error +
       record_boundary_error + translate_to_tool_error). In production only,
       first deep-strip schema-unknown nested fields and retry when that changes
       the input. This lets our Pydantic models (with extra='ignore') remain the
       validation gate for forward-compatible fields while preserving typed
       failures in dev — matching A2A and REST behavior.

    The fallback only catches TypeAdapter ValidationErrors (structural type
    mismatches). Business logic errors from the tool function propagate normally.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        # ``None`` and ``{}`` both mean an argument-less tool call, but they
        # must still pass through the TypeAdapter fallback. Required-field
        # failures happen precisely on this path; bypassing middleware leaked
        # FastMCP's raw Pydantic text instead of an AdCP error envelope.
        arguments = context.message.arguments or {}
        application_context = arguments.get("context")

        tool_name = context.message.name
        normalized = dict(arguments)
        modified = False

        # Step 1: Translate deprecated fields
        compat_result = normalize_request_params(tool_name, normalized)
        normalized = compat_result.params
        if compat_result.translations_applied:
            modified = True

        # Step 2: Version negotiation — reject an unsupported AdCP version pin
        # (string adcp_version or deprecated int adcp_major_version). Runs
        # before the negotiation-field strip below, while the claim is still
        # present. validate_* raises a transport-agnostic AdCPError; a
        # middleware raise bypasses the tool wrapper's with_error_logging
        # entirely (the tool is never dispatched), so BOTH halves of
        # _handle_tool_exception are applied here: record_boundary_error
        # (audit log + activity feed, best-effort identity from the auth
        # middleware's context state) and the AdCPToolError wire translation
        # (VERSION_UNSUPPORTED).
        try:
            validate_adcp_version_pins(normalized)
            # Step 3: optional read-key shape. Authentication already ran in
            # MCPAuthMiddleware, and VERSION wins when both fields are bad.
            # Validate before Step 5 strips an undeclared envelope key.
            validate_standard_read_idempotency_key(tool_name, normalized)
        except AdCPError as exc:
            identity = None
            try:
                if context.fastmcp_context is not None:
                    identity = await context.fastmcp_context.get_state("identity")
            except Exception:  # best-effort — never mask the version error
                identity = None
            _reject_at_mcp_boundary(tool_name, exc, identity, context=application_context)

        # Step 4: Drop AdCP version-negotiation envelope fields (all environments).
        # Every AdCP SDK client injects adcp_version / adcp_major_version for
        # version negotiation; no tool wrapper declares them, so FastMCP's strict
        # per-tool arg-validation would reject conformant clients (#1512). These
        # are protocol envelope, not tool params — unlike Step 5's schema strip,
        # this runs in every environment.
        normalized, dropped = strip_negotiation_fields(normalized)
        modified = modified or bool(dropped)
        _log_dropped_fields(tool_name, "AdCP negotiation", dropped)

        # Resolve the tool's declared params once — used by the envelope strip
        # (all environments) and the production unknown-field strip below.
        known_params = await self._get_known_params(context, tool_name)

        # Step 5: Drop standard AdCP envelope fields the tool doesn't declare
        # (ADCP_ENVELOPE_FIELDS — context / ext / push_notification_config /
        # idempotency_key), all environments. SDK clients send
        # these on any request; a tool that declares one receives it, a tool
        # that doesn't would otherwise reject it (#1512). Protocol envelope,
        # not business data — unlike Step 5's general unknown strip.
        normalized, dropped_env = strip_undeclared_envelope_fields(normalized, known_params)
        modified = modified or bool(dropped_env)
        _log_dropped_fields(tool_name, "undeclared AdCP envelope", dropped_env)

        # Step 6: Strip unknown fields (schema-aware, production only)
        # In dev mode, unknown fields reach TypeAdapter and fail loudly —
        # this is how we detect that the seller agent doesn't support a
        # field the spec requires. In production, strip silently to avoid
        # rejecting callers using newer schema versions.
        from src.core.config import is_production

        if is_production() and known_params is not None:
            normalized, stripped = strip_unknown_params(normalized, known_params)
            if stripped:
                modified = True
                logger.warning(
                    "Stripped unknown fields from %s: %s",
                    tool_name,
                    ", ".join(stripped),
                )

        if modified:
            new_message = CallToolRequestParams(
                name=tool_name,
                arguments=normalized,
            )
            context = context.copy(message=new_message)

        # Step 7: Dispatch — with production fallback on TypeAdapter rejection.
        # Extracted to keep this method's cyclomatic size bounded (ADR-009 / #1610).
        return await self._dispatch_with_typeadapter_fallback(
            context,
            tool_name,
            normalized,
            call_next,
            application_context=application_context,
        )

    async def _dispatch_with_typeadapter_fallback(
        self,
        context: MiddlewareContext,
        tool_name: str,
        normalized: dict,
        call_next,
        *,
        application_context: Any = None,
    ) -> ToolResult:
        """Dispatch to the tool; on a TypeAdapter structural validation failure, retry
        (production only) after schema-aware deep-strip, then translate the failure to
        an AdCP validation envelope. Non-TypeAdapter exceptions propagate unchanged."""
        try:
            return await call_next(context)
        except Exception as exc:
            if not self._is_typeadapter_validation_error(exc):
                raise

            if self._should_retry(exc):
                # Deep-strip unknown fields at every nesting level using the tool's
                # JSON Schema. TypeAdapter rejects unknown fields in objects with
                # additionalProperties: false. Our Pydantic models (extra='ignore')
                # would accept them — stripping bridges the gap.
                tool_schema = await self._get_tool_schema(context, tool_name)
                if tool_schema is not None:
                    stripped = deep_strip_to_schema(normalized, tool_schema)
                    if stripped != normalized:
                        logger.warning(
                            "TypeAdapter rejected %s — retrying with deep-stripped arguments "
                            "(production forward-compat): %s",
                            tool_name,
                            _summarize_error(exc),
                        )
                        stripped_message = CallToolRequestParams(
                            name=tool_name,
                            arguments=stripped,
                        )
                        stripped_context = context.copy(message=stripped_message)
                        try:
                            return await call_next(stripped_context)
                        except Exception as retry_exc:
                            if not self._is_typeadapter_validation_error(retry_exc):
                                raise
                            exc = retry_exc

            # Normalize once for the audit record, then pass the raw exception to
            # translate_to_tool_error so the emitted AdCPToolError keeps it as
            # __cause__. The translator intentionally normalizes it a second time.
            typed = normalize_to_adcp_error(exc, context=application_context)
            tenant_id = None
            principal_id = None
            if context.fastmcp_context is not None:
                try:
                    identity = await context.fastmcp_context.get_state("identity")
                    if identity is not None:
                        tenant_id = identity.tenant_id
                        principal_id = identity.principal_id
                except Exception:
                    logger.debug("Could not read MCP identity for validation error logging", exc_info=True)
            record_boundary_error(
                "mcp",
                tool_name,
                typed,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
            translate_to_tool_error(exc, context=application_context)

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        """Determine if the exception is a TypeAdapter structural error worth retrying.

        Only retries in production mode. Only retries Pydantic ValidationErrors
        that come from FastMCP's TypeAdapter (not from our business logic).

        FastMCP's TypeAdapter raises raw pydantic.ValidationError with title
        "call[tool_name]". Business logic ValidationErrors (from model construction
        inside _impl) have the model class name (e.g. "CreateMediaBuyRequest").
        """
        from src.core.config import is_production

        return is_production() and RequestCompatMiddleware._is_typeadapter_validation_error(exc)

    @staticmethod
    def _is_typeadapter_validation_error(exc: Exception) -> bool:
        """Return True for FastMCP TypeAdapter validation failures."""
        return isinstance(exc, ValidationError) and exc.title.startswith("call[")

    async def _get_tool_schema(
        self,
        context: MiddlewareContext,
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Look up tool's full JSON Schema for deep stripping.

        Returns None if lookup fails (defensive — skip stripping).
        """
        try:
            fastmcp_ctx = context.fastmcp_context
            if fastmcp_ctx is None:
                return None
            server = fastmcp_ctx.fastmcp
            tool = await server.get_tool(tool_name)
            if tool is None:
                return None
            return tool.parameters
        except Exception:
            logger.debug("Could not look up schema for %s, skipping deep strip", tool_name)
            return None

    async def _get_known_params(
        self,
        context: MiddlewareContext,
        tool_name: str,
    ) -> set[str] | None:
        """Look up tool's declared parameter names from its JSON Schema.

        Returns None if lookup fails (defensive — skip stripping).
        """
        try:
            fastmcp_ctx = context.fastmcp_context
            if fastmcp_ctx is None:
                return None
            server = fastmcp_ctx.fastmcp
            tool = await server.get_tool(tool_name)
            if tool is None:
                return None
            return set(tool.parameters.get("properties", {}).keys())
        except Exception:
            logger.debug("Could not look up params for %s, skipping strip", tool_name)
            return None


def _summarize_error(exc: Exception) -> str:
    """Extract a short summary from a validation error for logging."""
    text = str(exc)
    # Take first line or first 150 chars
    first_line = text.split("\n")[0]
    return first_line[:150] if len(first_line) > 150 else first_line

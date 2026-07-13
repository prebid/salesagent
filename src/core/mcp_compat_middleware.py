"""FastMCP middleware for AdCP backward-compatibility normalization.

Translates deprecated field names, strips unknown fields, and provides
a production-mode fallback for TypeAdapter structural validation errors.
Runs after MCPAuthMiddleware.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from src.core.adcp_version import validate_adcp_version_pins
from src.core.exceptions import AdCPError
from src.core.request_compat import (
    _log_dropped_fields,
    deep_strip_to_schema,
    normalize_request_params,
    strip_negotiation_fields,
    strip_undeclared_envelope_fields,
    strip_unknown_params,
)
from src.core.tool_error_logging import _reject_at_mcp_boundary

logger = logging.getLogger(__name__)


class RequestCompatMiddleware(Middleware):
    """Normalize, strip, and provide forward-compatible fallback for MCP tools.

    Pipeline:
    1. Translate deprecated field names via normalize_request_params()
    2. Reject an unsupported AdCP version pin via validate_adcp_version_pins()
       (VERSION_UNSUPPORTED) — before the fields are stripped below.
    3. Drop AdCP version-negotiation envelope fields (adcp_version,
       adcp_major_version) via strip_negotiation_fields() — all environments,
       since no tool wrapper declares them (issue #1512).
    4. Drop standard AdCP envelope fields the tool doesn't declare
       (ADCP_ENVELOPE_FIELDS: context, ext, push_notification_config,
       idempotency_key, revision) via strip_undeclared_envelope_fields() —
       all environments (issue #1512).
    5. Strip fields not in the tool's JSON Schema via strip_unknown_params()
       (production only).
    6. (Production only) If TypeAdapter rejects the arguments with a structural
       validation error, erase complex types to raw dicts via JSON round-trip
       and retry. This lets our Pydantic models (with extra='ignore') be the
       sole validation gate — matching A2A and REST behavior.

    The fallback only catches TypeAdapter ValidationErrors (structural type
    mismatches). Business logic errors from the tool function propagate normally.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        arguments = context.message.arguments
        if not arguments:
            return await call_next(context)

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
        except AdCPError as exc:
            identity = None
            try:
                if context.fastmcp_context is not None:
                    identity = await context.fastmcp_context.get_state("identity")
            except Exception:  # best-effort — never mask the version error
                identity = None
            _reject_at_mcp_boundary(tool_name, exc, identity)

        # Step 3: Drop AdCP version-negotiation envelope fields (all environments).
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

        # Step 4: Drop standard AdCP envelope fields the tool doesn't declare
        # (ADCP_ENVELOPE_FIELDS — context / ext / push_notification_config /
        # idempotency_key / revision), all environments. SDK clients send
        # these on any request; a tool that declares one receives it, a tool
        # that doesn't would otherwise reject it (#1512). Protocol envelope,
        # not business data — unlike Step 5's general unknown strip.
        normalized, dropped_env = strip_undeclared_envelope_fields(normalized, known_params)
        modified = modified or bool(dropped_env)
        _log_dropped_fields(tool_name, "undeclared AdCP envelope", dropped_env)

        # Step 5: Strip unknown fields (schema-aware, production only)
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

        # Step 6: Dispatch — with production fallback on TypeAdapter rejection
        try:
            return await call_next(context)
        except Exception as exc:
            if not self._should_retry(exc):
                raise

            # Deep-strip unknown fields at every nesting level using the tool's
            # JSON Schema. TypeAdapter rejects unknown fields in objects with
            # additionalProperties: false. Our Pydantic models (extra='ignore')
            # would accept them — stripping bridges the gap.
            tool_schema = await self._get_tool_schema(context, tool_name)
            if tool_schema is None:
                raise  # Can't strip without schema — let the error propagate

            stripped = deep_strip_to_schema(normalized, tool_schema)
            if stripped == normalized:
                raise  # Stripping didn't change anything — no point retrying

            logger.warning(
                "TypeAdapter rejected %s — retrying with deep-stripped arguments (production forward-compat): %s",
                tool_name,
                _summarize_error(exc),
            )
            stripped_message = CallToolRequestParams(
                name=tool_name,
                arguments=stripped,
            )
            stripped_context = context.copy(message=stripped_message)
            return await call_next(stripped_context)

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

        if not is_production():
            return False

        from pydantic import ValidationError

        if not isinstance(exc, ValidationError):
            return False

        return exc.title.startswith("call[")

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

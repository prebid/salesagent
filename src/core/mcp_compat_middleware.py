"""FastMCP middleware for AdCP backward-compatibility normalization.

Translates deprecated field names, strips unknown fields, and converts FastMCP
TypeAdapter validation failures into AdCP envelopes in every environment. In
production, it first retries structural failures after schema-aware deep stripping.
Also translates FastMCP missing-required-argument ``ToolError`` into AdCP
envelopes (same seam as TypeAdapter failures). Runs after MCPAuthMiddleware.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams
from pydantic import ValidationError

from src.core.exceptions import normalize_to_adcp_error
from src.core.request_compat import deep_strip_to_schema, normalize_request_params, strip_unknown_params
from src.core.tool_error_logging import _translate_to_tool_error, record_boundary_error

if TYPE_CHECKING:
    from src.core.exceptions import AdCPError

logger = logging.getLogger(__name__)


class RequestCompatMiddleware(Middleware):
    """Normalize, strip, and provide forward-compatible fallback for MCP tools.

    Three-stage pipeline:
    1. Translate deprecated field names via normalize_request_params()
    2. Strip fields not in the tool's JSON Schema via strip_unknown_params()
    3. If TypeAdapter rejects the arguments, always translate and record the
       failure as an AdCP validation envelope. In production only, first deep-
       strip schema-unknown nested fields and retry when that changes the input.
       This lets our Pydantic models (with extra='ignore') remain the validation
       gate for forward-compatible fields while preserving typed failures in dev.
       FastMCP missing-required-argument ``ToolError`` is translated at the same
       seam (empty ``{}`` arguments included).

    Business logic errors from the tool function propagate normally.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        arguments = context.message.arguments or {}
        tool_name = context.message.name
        normalized = dict(arguments)
        context = await self._normalize_arguments(context, tool_name, normalized, bool(arguments))
        try:
            return await call_next(context)
        except Exception as exc:
            return await self._handle_call_tool_error(context, call_next, tool_name, normalized, exc)

    async def _normalize_arguments(
        self,
        context: MiddlewareContext,
        tool_name: str,
        normalized: dict[str, Any],
        has_arguments: bool,
    ) -> MiddlewareContext:
        """Translate deprecated fields, enforce task unknown-key, optional strip."""
        if not has_arguments:
            return context
        modified = False
        # Pass a copy into the normalizer: it may return the same dict object it
        # was given. Clearing ``normalized`` in that case would wipe the result.
        compat_result = normalize_request_params(tool_name, dict(normalized))
        normalized.clear()
        normalized.update(compat_result.params)
        if compat_result.translations_applied:
            modified = True

        await self._reject_unknown_task_params(context, tool_name, normalized)

        from src.core.config import is_production

        if is_production() and tool_name not in ("get_task", "complete_task"):
            known_params = await self._get_known_params(context, tool_name)
            if known_params is not None:
                stripped_params, stripped = strip_unknown_params(dict(normalized), known_params)
                normalized.clear()
                normalized.update(stripped_params)
                if stripped:
                    modified = True
                    logger.warning(
                        "Stripped unknown fields from %s: %s",
                        tool_name,
                        ", ".join(stripped),
                    )

        if modified:
            return context.copy(
                message=CallToolRequestParams(name=tool_name, arguments=dict(normalized)),
            )
        return context

    async def _reject_unknown_task_params(
        self,
        context: MiddlewareContext,
        tool_name: str,
        normalized: dict[str, Any],
    ) -> None:
        """A2A ≡ MCP unknown-key gate for durable task tools (no silent strip)."""
        if tool_name not in ("get_task", "complete_task"):
            return
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.task_management import (
            COMPLETE_TASK_BUYER_PARAMS,
            GET_TASK_BUYER_PARAMS,
            assert_known_task_params,
        )

        allowed = GET_TASK_BUYER_PARAMS if tool_name == "get_task" else COMPLETE_TASK_BUYER_PARAMS
        try:
            assert_known_task_params(normalized, allowed=allowed)
        except AdCPValidationError as validation_exc:
            await self._record_boundary(context, tool_name, validation_exc)
            _translate_to_tool_error(validation_exc)
            raise

    async def _handle_call_tool_error(
        self,
        context: MiddlewareContext,
        call_next,
        tool_name: str,
        normalized: dict[str, Any],
        exc: Exception,
    ) -> ToolResult:
        """Translate missing-required / TypeAdapter failures into AdCP envelopes."""
        if RequestCompatMiddleware._is_missing_required_argument_error(exc):
            await self._translate_missing_required(context, tool_name)
            raise exc  # pragma: no cover

        if not self._is_typeadapter_validation_error(exc):
            raise

        # FastMCP may surface omitted required args as TypeAdapter ValidationError
        # (not ToolError). Keep A2A ≡ MCP wording via the same L2 message.
        if tool_name in ("get_task", "complete_task") and self._is_missing_task_id_validation(exc):
            await self._translate_missing_required(context, tool_name)
            raise exc  # pragma: no cover

        if self._should_retry(exc):
            retried = await self._retry_deep_strip(context, call_next, tool_name, normalized, exc)
            if retried is not None:
                return retried
            # retry path may replace exc via deep-strip failure — fall through
            # with the latest TypeAdapter error still in ``exc`` when strip noop.

        adcp_typed = normalize_to_adcp_error(exc)
        await self._record_boundary(context, tool_name, adcp_typed)
        _translate_to_tool_error(exc)
        raise  # pragma: no cover

    async def _translate_missing_required(self, context: MiddlewareContext, tool_name: str) -> None:
        from src.core.exceptions import VALIDATION_ERROR_SUGGESTION, AdCPValidationError
        from src.core.tools.task_management import TASK_ID_REQUIRED_MESSAGE

        if tool_name in ("get_task", "complete_task"):
            missing_typed = AdCPValidationError(
                TASK_ID_REQUIRED_MESSAGE,
                field="task_id",
                suggestion=VALIDATION_ERROR_SUGGESTION,
            )
        else:
            missing_typed = AdCPValidationError(
                "Missing required argument",
                suggestion=VALIDATION_ERROR_SUGGESTION,
            )
        await self._record_boundary(context, tool_name, missing_typed)
        _translate_to_tool_error(missing_typed)

    async def _retry_deep_strip(
        self,
        context: MiddlewareContext,
        call_next,
        tool_name: str,
        normalized: dict[str, Any],
        exc: Exception,
    ) -> ToolResult | None:
        tool_schema = await self._get_tool_schema(context, tool_name)
        if tool_schema is None:
            return None
        stripped = deep_strip_to_schema(normalized, tool_schema)
        if stripped == normalized:
            return None
        logger.warning(
            "TypeAdapter rejected %s — retrying with deep-stripped arguments (production forward-compat): %s",
            tool_name,
            _summarize_error(exc),
        )
        stripped_context = context.copy(
            message=CallToolRequestParams(name=tool_name, arguments=stripped),
        )
        try:
            return await call_next(stripped_context)
        except Exception as retry_exc:
            if not self._is_typeadapter_validation_error(retry_exc):
                raise
            adcp_typed = normalize_to_adcp_error(retry_exc)
            await self._record_boundary(context, tool_name, adcp_typed)
            _translate_to_tool_error(retry_exc)
            raise

    async def _record_boundary(self, context: MiddlewareContext, tool_name: str, typed: AdCPError) -> None:
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

    @staticmethod
    def _is_missing_task_id_validation(exc: Exception) -> bool:
        """True when TypeAdapter reports ``task_id`` as missing (not wrong-type)."""
        if not isinstance(exc, ValidationError):
            return False
        for err in exc.errors():
            loc = err.get("loc") or ()
            if "task_id" not in loc:
                continue
            if err.get("type") == "missing":
                return True
            err_type = str(err.get("type", ""))
            msg = str(err.get("msg", "")).lower()
            if "missing" in err_type or "required" in msg or "missing" in msg:
                return True
        return False

    @staticmethod
    def _is_missing_required_argument_error(exc: Exception) -> bool:
        """Return True for FastMCP ToolError raised when a required arg is absent.

        FastMCP raises its own ``ToolError`` (not pydantic ``ValidationError``)
        for missing required parameters, so ``_is_typeadapter_validation_error``
        does not match. Translate those into AdCP envelopes at this seam so
        ``get_task`` / ``complete_task`` / ``sync_creatives`` /
        ``update_performance_index`` share one missing-arg path.
        """
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import AdCPToolError

        if not isinstance(exc, ToolError) or isinstance(exc, AdCPToolError):
            return False
        msg = str(exc).lower()
        return "missing" in msg and ("argument" in msg or "required" in msg)

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

"""Patch FastMCP to allow **kwargs on tool functions (AdCP additionalProperties compliance).

AdCP specifies ``additionalProperties: true`` on all schemas. Buyer agents may send fields
the seller doesn't recognise (e.g. ``brand_manifest``). Our MCP tool functions declare
``**kwargs: Any`` so that Python itself accepts extra keyword arguments, but FastMCP
explicitly rejects ``**kwargs`` during tool registration.

This patch:
1. Lets FastMCP skip the ``**kwargs`` rejection during registration.
2. Strips ``**kwargs`` from the function before Pydantic builds a TypeAdapter, so
   schema generation and runtime validation both work.
3. Sets ``additionalProperties: true`` in the generated JSON schema so the advertised
   schema matches the actual behaviour.

Must be called **before** any ``mcp.tool()`` registration.
"""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_PATCHED = False


def _strip_var_keyword(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return a wrapper whose signature has no **kwargs parameter.

    FastMCP's ``without_injected_parameters`` and ``get_cached_typeadapter`` inspect
    the function signature. We need them to see a clean signature so Pydantic doesn't
    choke, but the original function still receives extras at runtime.
    """
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if not has_var_kw:
        return fn

    new_params = [p for p in sig.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD]
    new_sig = sig.replace(parameters=new_params)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Filter to only params the original function knows about (plus extras via **kwargs)
        return await fn(*args, **kwargs)

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    return wrapper


def patch_fastmcp_extra_fields() -> None:
    """Monkey-patch FastMCP to accept **kwargs on tool functions."""
    global _PATCHED  # noqa: PLW0603
    if _PATCHED:
        return

    from fastmcp.tools import function_parsing

    _original_from_function = function_parsing.ParsedFunction.from_function.__func__  # type: ignore[attr-defined]

    @classmethod  # type: ignore[misc]
    def _patched_from_function(
        cls: type,
        fn: Callable[..., Any],
        exclude_args: list[str] | None = None,
        validate: bool = True,
        wrap_non_object_output_schema: bool = True,
    ) -> function_parsing.ParsedFunction:
        # Check if the original function has **kwargs before we strip it
        sig = inspect.signature(fn)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        # Strip **kwargs so FastMCP's validation doesn't reject it
        cleaned_fn = _strip_var_keyword(fn)

        result = _original_from_function(
            cls,
            cleaned_fn,
            exclude_args=exclude_args,
            validate=validate,
            wrap_non_object_output_schema=wrap_non_object_output_schema,
        )

        # Set additionalProperties: true in the schema if the original had **kwargs
        if has_var_kw:
            result.input_schema["additionalProperties"] = True

        return result

    function_parsing.ParsedFunction.from_function = _patched_from_function  # type: ignore[assignment]

    _PATCHED = True
    logger.info("Patched FastMCP to accept **kwargs on tool functions (AdCP additionalProperties)")

"""@traced decorator for _impl() functions.

Creates a child span per function call. No-op when tracing is disabled.
Span name is the function name with the `_impl` suffix stripped.
"""

import asyncio
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from opentelemetry.trace import Status, StatusCode

from src.core.telemetry import get_tracer, is_tracing_enabled

logger = logging.getLogger(__name__)

_TRACER_NAME = "salesagent.tools"


def _span_name(func: Callable) -> str:
    name = func.__name__
    if name.endswith("_impl"):
        name = name[: -len("_impl")]
    if name.startswith("_"):
        name = name[1:]
    return name


def traced(func: Callable) -> Callable:
    """Wrap an _impl() function with an OTEL child span.

    Works for both sync and async callables.
    Span name is derived from the function name by stripping leading `_` and trailing `_impl`.
    Sets `salesagent.tenant_id` from the `identity` parameter when present.
    Records exceptions and sets ERROR status on any unhandled exception, then re-raises.
    """
    name = _span_name(func)
    identity_arg_index = _identity_positional_index(func)

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return await func(*args, **kwargs)

            tracer = get_tracer(_TRACER_NAME)
            with tracer.start_as_current_span(name) as span:
                _set_identity_attribute(span, args, kwargs, identity_arg_index)
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return async_wrapper
    else:

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return func(*args, **kwargs)

            tracer = get_tracer(_TRACER_NAME)
            with tracer.start_as_current_span(name) as span:
                _set_identity_attribute(span, args, kwargs, identity_arg_index)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return sync_wrapper


def _identity_positional_index(func: Callable) -> int | None:
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return None

    positional_index = 0
    for param in params:
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            if param.name == "identity":
                return positional_index
            positional_index += 1
    return None


def _set_identity_attribute(
    span: Any,
    args: tuple,
    kwargs: dict,
    identity_arg_index: int | None,
) -> None:
    identity = kwargs.get("identity")
    if identity is None and identity_arg_index is not None and len(args) > identity_arg_index:
        identity = args[identity_arg_index]
    if identity is not None and hasattr(identity, "tenant_id"):
        span.set_attribute("salesagent.tenant_id", str(identity.tenant_id))

"""OpenTelemetry provider lifecycle.

Tracing is enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
All other OTEL configuration uses standard SDK env vars:
  OTEL_SERVICE_NAME, OTEL_EXPORTER_OTLP_HEADERS, OTEL_TRACES_SAMPLER, etc.
"""

import logging
import os

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

_tracing_enabled: bool = False
_tracer_provider: TracerProvider | None = None


def is_tracing_enabled() -> bool:
    return _tracing_enabled


def init_telemetry() -> None:
    """Initialise the OTEL tracer provider.

    No-op when OTEL_EXPORTER_OTLP_ENDPOINT is not set.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _tracing_enabled, _tracer_provider

    if _tracing_enabled:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    exporter = OTLPSpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    propagate.set_global_textmap(CompositePropagator([TraceContextTextMapPropagator()]))

    _tracer_provider = provider
    _tracing_enabled = True
    logger.info("OpenTelemetry tracing enabled", extra={"endpoint": endpoint})


def shutdown_telemetry() -> None:
    """Flush pending spans and shut down the provider.

    Called during ASGI lifespan shutdown. No-op when tracing is disabled.
    """
    global _tracing_enabled, _tracer_provider

    if not _tracing_enabled or _tracer_provider is None:
        return

    _tracer_provider.shutdown()
    _tracing_enabled = False
    _tracer_provider = None


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for the given instrumentation scope.

    Returns a no-op tracer when tracing is disabled.
    """
    return trace.get_tracer(name)

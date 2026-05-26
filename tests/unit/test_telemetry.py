from unittest.mock import patch


def test_init_telemetry_no_op_when_endpoint_not_set(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core.telemetry import init_telemetry, is_tracing_enabled

    init_telemetry()
    assert not is_tracing_enabled()


def test_init_telemetry_enables_when_endpoint_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "test-salesagent")

    with (
        patch("src.core.telemetry.OTLPSpanExporter"),
        patch("src.core.telemetry.TracerProvider"),
        patch("src.core.telemetry.BatchSpanProcessor"),
        patch("src.core.telemetry.trace.set_tracer_provider"),
        patch("src.core.telemetry.propagate.set_global_textmap"),
    ):
        from src.core import telemetry as tel

        # Reset module state so init runs fresh
        tel._tracing_enabled = False
        tel._tracer_provider = None
        tel.init_telemetry()
        assert tel.is_tracing_enabled()


def test_get_tracer_returns_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core import telemetry as tel

    tel._tracing_enabled = False
    tracer = tel.get_tracer("test")
    # NoOpTracer has no active spans
    with tracer.start_as_current_span("test-span") as span:
        assert not span.is_recording()


def test_shutdown_telemetry_no_op_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core import telemetry as tel

    tel._tracing_enabled = False
    # Should not raise
    tel.shutdown_telemetry()

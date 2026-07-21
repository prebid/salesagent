"""Unit tests for log-sanitization helpers in src.core.logging_config.

Buyer-controlled strings (callback URLs, SSRF rejection details) are
interpolated into log records; unscrubbed control characters would let one
request forge adjacent records in plain-text log mode (#1546 re-review item 4).
"""

from src.core.logging_config import _is_production_log_env, scrub_control_chars


class TestScrubControlChars:
    """Control characters are escaped; printable content passes verbatim."""

    def test_surviving_control_chars_are_escaped(self):
        """VT/FF/ESC survive urlparse and raw CR/LF live in stored URLs — all must escape."""
        raw = "https://cb.example/a\x0bb\x0cc\x1b[31md\re\nf\x7fg\x85h"
        scrubbed = scrub_control_chars(raw)

        assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in scrubbed)
        for escape in ("\\x0b", "\\x0c", "\\x1b", "\\r", "\\n", "\\x7f", "\\x85"):
            assert escape in scrubbed
        # Printable content is preserved in order around the escapes.
        assert scrubbed.startswith("https://cb.example/a")
        assert scrubbed.endswith("h")

    def test_plain_url_is_unchanged(self):
        url = "https://cb.example/webhook?a=1&b=%20&c=café"
        assert scrub_control_chars(url) == url


class TestProductionLogGate:
    """The production gate honors every documented production signal.

    The gate previously honored only FLY_APP_NAME / PRODUCTION, so a
    self-hosted deploy signalling via the documented ENVIRONMENT=production
    got plain-text (forgeable) logs. These tests pin the boolean gate;
    TestStructuredLoggingSeam below pins that a true gate actually installs
    the JSON formatter.
    """

    def _clear(self, monkeypatch):
        for var in ("FLY_APP_NAME", "PRODUCTION", "ENVIRONMENT"):
            monkeypatch.delenv(var, raising=False)

    def test_environment_production_enables_json_logs(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _is_production_log_env() is True

    def test_fly_app_name_enables_json_logs(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("FLY_APP_NAME", "salesagent")
        assert _is_production_log_env() is True

    def test_development_defaults_to_plain_logs(self, monkeypatch):
        self._clear(monkeypatch)
        assert _is_production_log_env() is False

    def test_non_production_environment_value_stays_plain(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "staging")
        assert _is_production_log_env() is False


class TestStructuredLoggingSeam:
    """setup_structured_logging WIRES the gate: true gate => JSONFormatter installed.

    The gate tests above call _is_production_log_env directly, so reverting
    setup_structured_logging to an inline FLY_APP_NAME-or-PRODUCTION check
    would redden none of them — this seam test is the oracle that does.
    """

    def test_true_gate_installs_json_formatter_on_root(self, monkeypatch):
        import logging

        from src.core.logging_config import JSONFormatter, setup_structured_logging

        for var in ("FLY_APP_NAME", "PRODUCTION"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")

        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        touched = ["uvicorn", "uvicorn.access", "uvicorn.error", "fastmcp", "starlette"]
        saved_lib = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate) for name in touched}
        try:
            setup_structured_logging()
            assert any(isinstance(h.formatter, JSONFormatter) for h in root.handlers), (
                "ENVIRONMENT=production must install the JSON formatter on the root logger"
            )
        finally:
            for h in root.handlers[:]:
                root.removeHandler(h)
            for h in saved_handlers:
                root.addHandler(h)
            root.setLevel(saved_level)
            for name, (handlers, propagate) in saved_lib.items():
                lib = logging.getLogger(name)
                lib.handlers = handlers
                lib.propagate = propagate


def _raw_buyer_channel_hits(tree) -> list[str]:
    """All raw {config.url}/{target.url}/{e} interpolations inside logger f-string calls."""
    import ast

    def is_raw_buyer_channel(expr) -> str | None:
        if isinstance(expr, ast.Attribute) and expr.attr == "url" and isinstance(expr.value, ast.Name):
            if expr.value.id in {"config", "target"}:
                return f"{expr.value.id}.url"
        if isinstance(expr, ast.Name) and expr.id == "e":
            return "e"
        return None

    hits: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.JoinedStr):
                continue
            for fv in arg.values:
                if isinstance(fv, ast.FormattedValue):
                    raw = is_raw_buyer_channel(fv.value)
                    if raw is not None:
                        hits.append(f"line {fv.value.lineno}: raw {{{raw}}} in a logger f-string")
    return hits


class TestWebhookLogScrubCoverage:
    """Every buyer-URL / exception-text interpolation in the delivery module is scrubbed.

    The original scrub sweep missed the two MULTI-LINE log calls (a single-line
    grep cannot see them); this AST oracle cannot: any f-string logger call in
    webhook_delivery_service that interpolates ``config.url`` / ``target.url``
    or a bare exception name must route it through ``scrub_control_chars``.
    """

    def test_no_raw_buyer_channel_in_delivery_log_fstrings(self):
        import ast
        from pathlib import Path

        module = Path(__file__).resolve().parents[2] / "src" / "services" / "webhook_delivery_service.py"
        violations = _raw_buyer_channel_hits(ast.parse(module.read_text(), filename=str(module)))
        assert not violations, (
            "Buyer-controlled channels must route through scrub_control_chars in log f-strings "
            "(wrap the value, e.g. {scrub_control_chars(config.url)}):\n  " + "\n  ".join(violations)
        )

    def test_oracle_matcher_catches_raw_sites_and_passes_scrubbed_ones(self):
        """Self-test: the matcher reddens on raw interpolations and stays quiet on scrubbed ones."""
        import ast

        raw = 'logger.warning(f"delivery to {config.url} failed: {e}")'
        assert len(_raw_buyer_channel_hits(ast.parse(raw))) == 2
        multiline = (
            "logger.warning(\n"
            '    f"delivery to {config.url} returned status {status_code} "\n'
            '    f"(attempt: {attempt})"\n'
            ")"
        )
        assert len(_raw_buyer_channel_hits(ast.parse(multiline))) == 1
        scrubbed = 'logger.warning(f"delivery to {scrub_control_chars(config.url)}: {scrub_control_chars(str(e))}")'
        assert _raw_buyer_channel_hits(ast.parse(scrubbed)) == []

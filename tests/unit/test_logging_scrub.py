"""Unit tests for log-sanitization helpers in src.core.logging_config.

Buyer-controlled strings (callback URLs, SSRF rejection details) are
interpolated into log records; unscrubbed control characters would let one
request forge adjacent records in plain-text log mode (#1546 re-review item 4).
"""

import pytest

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

    def test_unicode_line_separators_are_escaped(self):
        """U+2028/U+2029 split on str.splitlines(), so they must be escaped too."""
        raw = "a b c"
        scrubbed = scrub_control_chars(raw)
        assert " " not in scrubbed and " " not in scrubbed
        assert len(scrubbed.splitlines()) == 1, "scrubbed value must be a single log line"

    def test_none_and_non_string_are_str_wrapped_not_raised(self):
        """Called inside except blocks — a None/non-str must not raise TypeError."""
        assert scrub_control_chars(None) == "None"
        assert scrub_control_chars(42) == "42"


class TestControlCharClassCompleteness:
    """``_CONTROL_CHARS`` matches everything ``str.splitlines()`` breaks on.

    That equivalence is the class's stated contract, and it is the whole
    defense: any character that splits a line but is not escaped lets one
    buyer-controlled value forge an adjacent plain-text log record. It was
    asserted only in a docstring — narrowing the class from ``\\x1f`` to
    ``\\x1b`` silently drops FS/GS/RS/US and leaves the entire unit suite
    green. Derived from ``splitlines()`` itself rather than re-listing the
    literals, so the test cannot drift from the claim it grades.
    """

    def test_every_line_breaking_char_is_escaped(self):
        candidates = [chr(code) for code in range(0x100)] + ["\u2028", "\u2029"]
        breakers = [char for char in candidates if len(f"a{char}b".splitlines()) > 1]
        assert breakers, "splitlines() split nothing - the probe itself is broken"

        # After scrubbing, the value must occupy exactly ONE line.
        unescaped = [char for char in breakers if len(scrub_control_chars(f"a{char}b").splitlines()) > 1]
        assert not unescaped, (
            "these characters split a log record but survive scrubbing, so a buyer value "
            f"containing one can forge an adjacent record: {[hex(ord(c)) for c in unescaped]}"
        )


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

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_any_non_empty_production_value_enables_json_logs(self, monkeypatch, value):
        """PRODUCTION is deliberately read as ANY non-empty value.

        The helper's own comment warns that tightening this to ``== "true"``
        "would silently flip a PRODUCTION=1 deploy back to plain-text logs" —
        but no test set PRODUCTION at all (it appeared only in the teardown
        list), so applying exactly that tightening left the whole unit suite
        green. This is the oracle for the documented looseness.
        """
        self._clear(monkeypatch)
        monkeypatch.setenv("PRODUCTION", value)
        assert _is_production_log_env() is True, (
            f"PRODUCTION={value!r} must enable JSON logs — the loose read is deliberate legacy compat"
        )


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


# Names that carry buyer-controlled text into a log record.
_EXCEPTION_NAMES = frozenset({"e", "exc", "err", "ex", "error", "exception"})
_PAYLOAD_NAMES = frozenset({"payload", "parameters", "params", "body", "assignments", "detail"})
_BUYER_ATTRS = frozenset({"url", "text", "body"})
# Calls whose result cannot carry raw buyer bytes into the record.
_SANITIZING_OR_SAFE_CALLS = frozenset(
    {"scrub_control_chars", "log_safe", "len", "type", "id", "bool", "int", "float", "sorted", "list"}
)
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})


def _raw_buyer_channel_hits(tree) -> list[str]:
    """Buyer-controlled values reaching a ``logger.*`` call unsanitized.

    A DENY-RAW rule, not a name allowlist. The earlier matcher recognized only
    the identifier ``e`` and only ``config``/``target`` as attribute bases, so
    it was blind to ~10 of the 11 shapes that actually occur — ``exc``/``err``,
    ``str(e)``, ``{payload}``, an aliased local, ``self.config.url``,
    ``extra={}`` values, the ``%``-operator, ``.format()``, and a
    ``self.logger`` base. A raw site inside its own scan set left it green,
    which is the failure mode this rewrite closes.

    Recognized channels: any name bound by an enclosing ``except ... as X``,
    the conventional exception spellings, the request-payload spellings, and
    ``<anything>.url`` / ``.text`` / ``.body``. ``str(...)``/``repr(...)`` are
    unwrapped first — they format, they do not sanitize. A value wrapped in
    ``scrub_control_chars(...)`` / ``log_safe(...)`` is NOT flagged, and neither
    are shape-only reads (``len(...)``, ``sorted(x.keys())``), which is the
    sanctioned way to log a buyer dict.
    """
    import ast

    exception_bindings = {h.name for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and h.name}

    def unwrap(expr):
        # str(x) / repr(x) format the value; they do not sanitize it.
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in {"str", "repr"}:
            return expr.args[0] if expr.args else expr
        return expr

    def is_raw_buyer_channel(expr) -> str | None:
        expr = unwrap(expr)
        if isinstance(expr, ast.Call):
            func = expr.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            # A sanitizing/shape-only call is clean; any other call is opaque to
            # this matcher and is deliberately not flagged (it would be noise).
            return None if name in _SANITIZING_OR_SAFE_CALLS else None
        if isinstance(expr, ast.Attribute):
            return f"<...>.{expr.attr}" if expr.attr in _BUYER_ATTRS else None
        if isinstance(expr, ast.Name):
            if expr.id in exception_bindings or expr.id in _EXCEPTION_NAMES:
                return expr.id
            if expr.id in _PAYLOAD_NAMES:
                return expr.id
        return None

    def logger_args(call):
        """Positional args plus ``extra={...}`` values — both reach the record."""
        args = list(call.args)
        for kw in call.keywords:
            if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                args.extend(kw.value.values)
        return args

    hits: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _LOGGER_METHODS:
            continue
        base = node.func.value
        # `logger.x(...)`, `log.x(...)`, `self.logger.x(...)`, `self.log.x(...)`
        base_is_logger = (isinstance(base, ast.Name) and base.id in {"logger", "log"}) or (
            isinstance(base, ast.Attribute) and base.attr in {"logger", "log"}
        )
        if not base_is_logger:
            continue
        for arg in logger_args(node):
            if isinstance(arg, ast.JoinedStr):
                for value in arg.values:
                    if isinstance(value, ast.FormattedValue):
                        raw = is_raw_buyer_channel(value.value)
                        if raw is not None:
                            hits.append(f"line {value.value.lineno}: raw {{{raw}}} in a logger f-string")
            elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
                # "...%s..." % value
                raw = is_raw_buyer_channel(arg.right)
                if raw is not None:
                    hits.append(f"line {arg.lineno}: raw {raw} via the %-operator")
            elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format":
                for fmt_arg in arg.args:
                    raw = is_raw_buyer_channel(fmt_arg)
                    if raw is not None:
                        hits.append(f"line {arg.lineno}: raw {raw} via .format()")
            else:
                # A bare positional arg (the value a %s/%.1f format string consumes).
                raw = is_raw_buyer_channel(arg)
                if raw is not None:
                    hits.append(f"line {arg.lineno}: raw {raw} as a positional logger arg")
    return hits


def _webhook_log_modules() -> list["Path"]:  # noqa: F821 - Path imported by callers
    """The buyer-facing log-seam modules, DERIVED — not hand-enumerated.

    The previous 2-entry literal named exactly the two modules a prior round
    happened to touch, while ``delivery_webhook_scheduler.py`` sat outside it
    with six raw sites the matcher flags on sight. Deriving the set means a new
    webhook module joins the guard by existing, not by being remembered.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    modules = sorted((root / "src" / "services").glob("*webhook*.py"))
    # Buyer log seams outside src/services that also call the scrubber.
    modules.append(root / "src" / "a2a_server" / "adcp_a2a_server.py")
    modules.append(root / "src" / "core" / "webhook_validator.py")
    return [m for m in modules if m.exists()]


class TestWebhookLogScrubCoverage:
    """Every buyer-URL / exception-text interpolation in the webhook modules is scrubbed.

    The AST oracle sees what a grep cannot: multi-line f-string calls AND the
    ``%s``-positional form, across every module in ``_WEBHOOK_LOG_MODULES``. Any
    ``config.url`` / ``target.url`` / bare exception name in a ``logger.*`` call
    must route through ``scrub_control_chars``.
    """

    def test_no_raw_buyer_channel_in_webhook_log_calls(self):
        import ast

        modules = _webhook_log_modules()
        assert modules, (
            "the derived scan set is EMPTY — the glob or the tree layout moved, and an empty "
            "scan set makes this guard pass vacuously"
        )
        violations: list[str] = []
        for module in modules:
            for hit in _raw_buyer_channel_hits(ast.parse(module.read_text(), filename=str(module))):
                violations.append(f"{module.name}: {hit}")
        assert not violations, (
            "Buyer-controlled channels must route through scrub_control_chars in logger calls "
            "(wrap the value, e.g. scrub_control_chars(str(exc)); log a dict's shape with "
            "sorted(x.keys()) rather than its content):\n  " + "\n  ".join(violations)
        )

    def test_scan_set_covers_every_module_that_calls_the_scrubber(self):
        """A module that needs the scrubber must be inside the guard's scan set.

        The scan-set escape is the failure this pins: code placed in a module
        the guard does not read is a silent exemption, and the guard reports
        green because it never looked.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        scanned = {m.resolve() for m in _webhook_log_modules()}
        callers = {
            path.resolve()
            for path in (root / "src").rglob("*.py")
            if "scrub_control_chars(" in path.read_text() and path.name != "logging_config.py"
        }
        unscanned = sorted(str(p.relative_to(root)) for p in callers - scanned)
        assert not unscanned, (
            "These modules sanitize log values but are OUTSIDE the guard's scan set, so a new raw "
            "site in them would not redden it — add them to _webhook_log_modules():\n  " + "\n  ".join(unscanned)
        )

    @pytest.mark.parametrize(
        ("form", "source", "expected"),
        [
            ("f-string, url + exception", 'logger.warning(f"delivery to {config.url} failed: {e}")', 2),
            (
                "multi-line f-string",
                'logger.warning(\n    f"delivery to {config.url} returned status {status_code} "\n    f"(attempt: {attempt})"\n)',
                1,
            ),
            ("%s-positional", 'logger.warning("delivery to %s exceeded %.1fs", target.url, DEADLINE)', 1),
            # Every form below was INVISIBLE to the previous name-allowlist matcher.
            ("positional exception named exc", 'logger.error("not valid JSON for %s: %s", url, exc)', 1),
            ("positional exception named err", 'logger.error("failed: %s", err)', 1),
            ("str(e) inside an f-string", 'logger.error(f"failed: {str(e)}")', 1),
            ("raw payload dict", 'logger.debug(f"no url configured, payload: {payload}")', 1),
            ("raw parameters dict", 'logger.info("skill %s params: %s", skill_name, parameters)', 1),
            ("nested attribute base", 'logger.warning(f"delivery to {self.config.url}")', 1),
            ("aliased attribute base", 'logger.warning(f"posting to {push_config.url}")', 1),
            ("extra={} dict value", 'logger.info("delivering", extra={"target": target.url})', 1),
            ("%-operator", 'logger.warning("delivery to %s failed" % target.url)', 1),
            (".format()", 'logger.warning("delivery to {} failed".format(target.url))', 1),
            ("self.logger base", 'self.logger.warning("failed: %s", exc)', 1),
            (
                "except-as binding with an unconventional name",
                'try:\n    pass\nexcept Exception as boom:\n    logger.error("failed: %s", boom)',
                1,
            ),
            # Clean forms must NOT be flagged, or the guard becomes unusable.
            (
                "scrubbed f-string",
                'logger.warning(f"delivery to {scrub_control_chars(config.url)}: {scrub_control_chars(str(e))}")',
                0,
            ),
            ("scrubbed positional", 'logger.warning("delivery to %s", scrub_control_chars(target.url))', 0),
            ("shape-only dict read", 'logger.info("skill %s keys: %s", name, sorted(parameters.keys()))', 0),
            ("count-only read", 'logger.info("delivering %s items", len(payload))', 0),
            ("non-logger call", 'notifier.warning(f"delivery to {config.url}")', 0),
        ],
    )
    def test_oracle_matcher_recognizes_every_interpolation_form(self, form, source, expected):
        """Known-bad self-test, one case per shape the matcher must model.

        Parametrized so a future narrowing of the matcher names the exact form
        it stopped seeing, instead of collapsing several shapes into one
        ambiguous count assertion.
        """
        import ast

        assert len(_raw_buyer_channel_hits(ast.parse(source))) == expected, f"matcher is blind to: {form}"

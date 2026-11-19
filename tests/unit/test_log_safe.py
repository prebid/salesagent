"""Guard: interpolating a value into a log line cannot forge a second line.

CodeQL flagged four sites in this repo (CWE-117, log injection) where a value
that originates outside the process reached a ``logger.warning`` unescaped: a
tenant identifier, a provider exception's text, and a ``ValidationError`` whose
message embeds the stored value it rejected.
"""

import logging

import pytest

from src.core.logging_config import log_safe

# Every character ``str.splitlines()`` treats as a line boundary, built from code
# points so this file carries none of them literally. One list for every test here.
_ALL_BREAKS = "".join(chr(c) for c in (0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029))


class TestLogSafe:
    """log_safe escapes every character that can start a new log line."""

    @pytest.mark.parametrize(
        "char",
        list(_ALL_BREAKS),
        ids=["lf", "cr", "vt", "ff", "fs", "gs", "rs", "nel", "ls", "ps"],
    )
    def test_line_breaking_characters_are_escaped(self, char):
        """A naive strip of \\n and \\r leaves seven of these ten intact.

        Python's own line splitting treats all ten as line boundaries, and so do
        the log readers that matter, so escaping only the two obvious ones still
        lets a forged line through.
        """
        assert char not in log_safe(f"before{char}after")
        assert len(log_safe(f"before{char}after").splitlines()) == 1

    def test_the_forged_line_is_visible_rather_than_deleted(self):
        """Escaped, not dropped: an operator must see that something was there."""
        result = log_safe("tenant-a\nWARNING [GET_PRODUCTS] everything is fine")
        assert "\\n" in result
        assert "everything is fine" in result, "the payload stays readable, it just cannot span lines"

    def test_non_string_values_are_accepted(self):
        """Call sites pass exceptions and identifiers, not only strings."""
        assert log_safe(ValueError("bad\nvalue")) == "bad\\nvalue"
        assert log_safe(None) == "None"
        assert log_safe(42) == "42"

    def test_output_matches_a_unicode_escape_of_each_line_break(self):
        """The ``.replace`` chain produces what the earlier ``str.translate`` table did."""
        breaks = _ALL_BREAKS
        table = str.maketrans({c: c.encode("unicode_escape").decode("ascii") for c in breaks})
        sample = "a" + "b".join(breaks) + "\\n\r\n" + "z" + breaks[::-1]
        assert log_safe(sample) == sample.translate(table)

    def test_ordinary_text_is_unchanged(self):
        assert log_safe("tenant_blick") == "tenant_blick"
        assert log_safe("a tab\tsurvives") == "a tab\tsurvives", "a tab cannot start a line"


class TestCallSites:
    """The sites CodeQL named actually route through it."""

    def test_malformed_ai_config_warning_cannot_forge_a_line(self, caplog, tenant_row):
        """src/services/ai/config.py: the ValidationError embeds the rejected value."""
        from src.services.ai.config import TenantAIConfig

        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.from_tenant(
                tenant_row(ai_config={"provider": "x\nWARNING forged", "settings": {"temperature": 99}})
            )

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1
        assert len(records[0].getMessage().splitlines()) == 1

    def test_the_tenant_label_in_that_warning_cannot_forge_a_line(self, caplog, tenant_row):
        """The SECOND value reaching that log line, through ``source``.

        ``from_tenant`` builds the label as ``f"tenant ai_config for tenant {id!r}"``
        and hands it to the parse, which interpolates it. Escaping only the exception
        left this path open; CodeQL found it after the first fix and the test above
        does not cover it, because it forges a value INSIDE the config dict rather
        than the tenant identifier.
        """
        from src.services.ai.config import TenantAIConfig

        tenant = tenant_row(tenant_id="acme\nWARNING forged", ai_config={"settings": {"temperature": 99}})
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.from_tenant(tenant)

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1, f"expected one WARNING, got {[r.getMessage() for r in records]}"
        message = records[0].getMessage()
        assert len(message.splitlines()) == 1, f"the tenant identifier forged a line: {message!r}"
        assert "forged" in message, "escaped, not deleted"

    def test_a_non_string_tenant_identifier_cannot_forge_a_line(self, caplog, tenant_row):
        """The case ``!r`` does NOT cover, which is why the label is escaped.

        ``repr()`` of a ``str`` already escapes its line breaks, so a forged string
        identifier comes out single-line either way. The ORM attribute is not checked
        against its column type until a flush, though, and ``repr`` of an arbitrary object
        is whatever that object's ``__repr__`` returns — line breaks included. Without the
        escape this is the input that forges the line, so it is the one that grades the fix.
        """
        from src.services.ai.config import TenantAIConfig

        class Sneaky:
            def __repr__(self) -> str:
                return "acme\nWARNING forged"

        tenant = tenant_row(tenant_id=Sneaky(), ai_config={"settings": {"temperature": 99}})
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.from_tenant(tenant)

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1, f"expected one WARNING, got {[r.getMessage() for r in records]}"
        message = records[0].getMessage()
        assert len(message.splitlines()) == 1, f"a non-str __repr__ forged a line: {message!r}"


_PROPAGATING_LOGGERS = ("adcp.audit", "adcp.oauth")
_LIBRARY_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "fastmcp", "starlette")


@pytest.fixture
def configured_logging(monkeypatch):
    """Run the real logging setup in one mode, and put the global logging state back after."""
    from unittest.mock import PropertyMock

    from src.core.config import get_settings
    from src.core.logging_config import setup_oauth_logging, setup_structured_logging

    root = logging.getLogger()
    saved = {
        name: (lg.handlers[:], lg.propagate, lg.level)
        for name, lg in ((n, logging.getLogger(n)) for n in ("", *_LIBRARY_LOGGERS))
    }

    def configure(structured: bool) -> None:
        import src.core.audit_logger  # noqa: F401 - attaches the adcp.audit handlers

        monkeypatch.setattr(type(get_settings()), "structured_logging", PropertyMock(return_value=structured))
        setup_structured_logging()
        setup_oauth_logging()

    yield configure

    for name, (handlers, propagate, level) in saved.items():
        lg = root if name == "" else logging.getLogger(name)
        lg.handlers = handlers
        lg.propagate = propagate
        lg.setLevel(level)


def _record(name: str) -> logging.LogRecord:
    try:
        raise RuntimeError(f"remote said{_ALL_BREAKS}FORGED")
    except RuntimeError as exc:
        return logging.LogRecord(
            name,
            logging.ERROR,
            __file__,
            1,
            f"op{_ALL_BREAKS}%s",
            (f"arg{_ALL_BREAKS}FORGED",),
            (type(exc), exc, None),
        )


class TestEveryHandlerWritesOneLinePerRecord:
    """A record is one line in every handler it reaches, following propagation to root.

    The record goes through ``Logger.handle`` after the real setup, in both modes, so
    the check covers whatever handlers that setup attaches: the root handler (JSON in
    production, text in development) and each logger's own handlers.
    """

    @pytest.mark.parametrize("structured", [True, False], ids=["production", "development"])
    @pytest.mark.parametrize("logger_name", _PROPAGATING_LOGGERS)
    def test_a_record_with_every_line_break_renders_as_one_line(
        self, configured_logging, monkeypatch, structured, logger_name
    ):
        configured_logging(structured)
        reached: list[logging.Handler] = []
        logger: logging.Logger | None = logging.getLogger(logger_name)
        while logger is not None:
            reached += logger.handlers
            logger = logger.parent if logger.propagate else None
        assert reached, "nothing to check"

        written: list[tuple[logging.Handler, str]] = []
        for handler in reached:
            monkeypatch.setattr(handler, "emit", lambda r, h=handler: written.append((h, h.format(r))))

        logging.getLogger(logger_name).handle(_record(logger_name))

        assert {h for h, _ in written} == set(reached), "every handler on the path wrote the record"
        for handler, rendered in written:
            assert len(rendered.splitlines()) == 1, (handler, rendered)
            assert "FORGED" in rendered, "escaped, not dropped"

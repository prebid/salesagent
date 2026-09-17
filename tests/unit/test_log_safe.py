"""Guard: interpolating a value into a log line cannot forge a second line.

CodeQL flagged four sites in this repo (CWE-117, log injection) where a value
that originates outside the process reached a ``logger.warning`` unescaped: a
tenant identifier, a provider exception's text, and a ``ValidationError`` whose
message embeds the stored value it rejected.
"""

import logging

import pytest

from src.core.utils.log_safe import log_safe


class TestLogSafe:
    """log_safe escapes every character that can start a new log line."""

    @pytest.mark.parametrize(
        "char",
        ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " "],
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

    def test_ordinary_text_is_unchanged(self):
        assert log_safe("tenant_blick") == "tenant_blick"
        assert log_safe("a tab\tsurvives") == "a tab\tsurvives", "a tab cannot start a line"


class TestCallSites:
    """The sites CodeQL named actually route through it."""

    def test_malformed_ai_config_warning_cannot_forge_a_line(self, caplog):
        """src/services/ai/config.py: the ValidationError embeds the rejected value."""
        from src.services.ai.config import TenantAIConfig

        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.coerce({"provider": "x\nWARNING forged", "settings": {"temperature": 99}})

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1
        assert len(records[0].getMessage().splitlines()) == 1

    def test_the_tenant_label_in_that_warning_cannot_forge_a_line(self, caplog):
        """The SECOND value reaching that log line, through ``source``.

        ``from_tenant`` builds the label as ``f"tenant ai_config for tenant {id!r}"``
        and hands it to ``coerce``, which interpolates it. Escaping only the exception
        left this path open; CodeQL found it after the first fix and the test above
        does not cover it, because it forges a value INSIDE the config dict rather
        than the tenant identifier.
        """
        from src.services.ai.config import TenantAIConfig

        tenant = {
            "tenant_id": "acme\nWARNING forged",
            "ai_config": {"settings": {"temperature": 99}},
        }
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.from_tenant(tenant)

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1, f"expected one WARNING, got {[r.getMessage() for r in records]}"
        message = records[0].getMessage()
        assert len(message.splitlines()) == 1, f"the tenant identifier forged a line: {message!r}"
        assert "forged" in message, "escaped, not deleted"

    def test_a_non_string_tenant_identifier_cannot_forge_a_line(self, caplog):
        """The case ``!r`` does NOT cover, which is why the label is escaped.

        ``repr()`` of a ``str`` already escapes its line breaks, so a forged string
        identifier comes out single-line either way. ``_read_tenant_field`` is typed
        ``Any``, though, and ``repr`` of an arbitrary object is whatever that object's
        ``__repr__`` returns — line breaks included. Without the escape this is the
        input that forges the line, so it is the one that grades the fix.
        """
        from src.services.ai.config import TenantAIConfig

        class Sneaky:
            def __repr__(self) -> str:
                return "acme\nWARNING forged"

        tenant = {"tenant_id": Sneaky(), "ai_config": {"settings": {"temperature": 99}}}
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            TenantAIConfig.from_tenant(tenant)

        records = [r for r in caplog.records if r.name == "src.services.ai.config"]
        assert len(records) == 1, f"expected one WARNING, got {[r.getMessage() for r in records]}"
        message = records[0].getMessage()
        assert len(message.splitlines()) == 1, f"a non-str __repr__ forged a line: {message!r}"

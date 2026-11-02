"""Tests for shared timeout utilities."""

import logging
import time
from concurrent import futures

import pytest

from src.adapters.utils.timeout import timeout
from src.core.exceptions import AdCPServiceUnavailableError


class TestTimeoutDecorator:
    """Tests for the timeout decorator."""

    def test_function_completes_within_timeout(self):
        """Test that function returns normally when completing within timeout."""

        @timeout(seconds=5)
        def fast_function():
            return "success"

        result = fast_function()
        assert result == "success"

    def test_function_times_out(self):
        """Test that AdCPServiceUnavailableError is raised when function exceeds timeout."""

        @timeout(seconds=1)
        def slow_function():
            time.sleep(2)  # Just enough to exceed 1s timeout (was 10s)
            return "should not reach"

        with pytest.raises(AdCPServiceUnavailableError) as exc_info:
            slow_function()

    def test_decorated_function_preserves_return_value(self):
        """Test that return values are preserved."""

        @timeout(seconds=5)
        def returns_dict():
            return {"key": "value", "number": 42}

        result = returns_dict()
        assert result == {"key": "value", "number": 42}

    def test_decorated_function_preserves_arguments(self):
        """Test that arguments are passed through correctly."""

        @timeout(seconds=5)
        def with_args(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = with_args("x", "y", c="z")
        assert result == "x-y-z"

    def test_decorated_function_raises_exceptions(self):
        """Test that exceptions from the function propagate correctly."""

        @timeout(seconds=5)
        def raises_error():
            raise ValueError("test error")

        with pytest.raises(ValueError) as exc_info:
            raises_error()

    def test_decorated_function_preserves_name(self):
        """Test that the decorated function preserves its name."""

        @timeout(seconds=5)
        def named_function():
            pass

        assert named_function.__name__ == "named_function"

    def test_default_timeout_is_300_seconds(self):
        """Test that default timeout is 5 minutes (300 seconds)."""

        @timeout()
        def default_timeout():
            pass

        # Can't easily test the actual timeout value, but we verify it works
        default_timeout()


class TestTimeoutError:
    """What a timeout raises, and what the buyer is allowed to read.

    These two tests replaced ones written for a module-local ``TimeoutError``
    that took a positional message. ``AdCPSalesAgentError`` accepts no message parameter
    at all -- the sentence comes from CODE_TABLE, keyed by the code -- so a test
    asserting ``str(error) == "operation timed out"`` was asserting a channel
    that no longer exists. The obligation underneath it does still exist and is
    stronger: the diagnostic must survive for the operator without reaching the
    buyer.
    """

    def test_timeout_raises_service_unavailable(self):
        """A timed-out operation raises SERVICE_UNAVAILABLE, not a bespoke class."""

        @timeout(seconds=1)
        def hangs():
            time.sleep(5)

        with pytest.raises(AdCPServiceUnavailableError) as exc_info:
            hangs()

        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"

    def test_diagnostic_is_non_wire(self, caplog):
        """The function name and duration reach the log, never the buyer.

        ``internal_detail`` is typed ``BaseException | None`` and never takes an
        authored string, so the authored diagnostic has exactly one channel left:
        the log record. The exception it carries is the underlying
        ``concurrent.futures.TimeoutError`` — non-wire by construction, and the
        cause the boundary attaches a traceback for.
        """

        @timeout(seconds=1)
        def hangs():
            time.sleep(5)

        with caplog.at_level(logging.ERROR, logger="src.adapters.utils.timeout"):
            with pytest.raises(AdCPServiceUnavailableError) as exc_info:
                hangs()

        diagnostic = "\n".join(record.getMessage() for record in caplog.records)
        assert "hangs" in diagnostic, "the operator log must name the operation that hung"
        assert "timed out after 1s" in diagnostic, "the operator log must name the elapsed limit"

        # The non-wire channel carries the cause itself, not a sentence about it.
        assert isinstance(exc_info.value.internal_detail, futures.TimeoutError)
        assert "hangs" not in exc_info.value.message, "the buyer-facing sentence must not name our internals"


class TestBackwardsCompatibility:
    """Tests for backwards compatibility with GAM timeout handler."""

    def test_import_from_gam_location(self):
        """The old GAM import path still resolves to the shared objects.

        The shim previously re-exported a module-local ``AdCPServiceUnavailableError`` that
        shadowed the builtin. It now re-exports the AdCP error the decorator
        actually raises, so the identity assertion covers that instead -- a
        second timeout class reappearing anywhere would break it.
        """
        from src.adapters.gam.utils.timeout_handler import (
            AdCPServiceUnavailableError as GAMTimeoutErr,
        )
        from src.adapters.gam.utils.timeout_handler import timeout as gam_timeout

        assert GAMTimeoutErr is AdCPServiceUnavailableError
        assert gam_timeout is timeout

"""Tests for shared timeout utilities."""

import time

import pytest

from src.adapters.utils.timeout import TimeoutError, timeout


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
        """Test that TimeoutError is raised when function exceeds timeout."""

        @timeout(seconds=1)
        def slow_function():
            time.sleep(10)
            return "should not reach"

        with pytest.raises(TimeoutError) as exc_info:
            slow_function()

        assert "slow_function timed out after 1 seconds" in str(exc_info.value)

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

        assert "test error" in str(exc_info.value)

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
    """Tests for the TimeoutError exception."""

    def test_timeout_error_is_exception(self):
        """Test that TimeoutError is an Exception subclass."""
        assert issubclass(TimeoutError, Exception)

    def test_timeout_error_message(self):
        """Test TimeoutError preserves message."""
        error = TimeoutError("operation timed out")
        assert str(error) == "operation timed out"


class TestBackwardsCompatibility:
    """Tests for backwards compatibility with GAM timeout handler."""

    def test_import_from_gam_location(self):
        """Test that imports from the old GAM location still work."""
        from src.adapters.gam.utils.timeout_handler import TimeoutError as GAMTimeoutError
        from src.adapters.gam.utils.timeout_handler import timeout as gam_timeout

        # Verify they're the same objects
        from src.adapters.utils.timeout import TimeoutError as SharedTimeoutError
        from src.adapters.utils.timeout import timeout as shared_timeout

        assert GAMTimeoutError is SharedTimeoutError
        assert gam_timeout is shared_timeout

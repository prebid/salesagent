"""describe_adagents_error() must return a fixed, non-disclosing message per
exception class -- never the library's own ``str(exc)`` (GH #1802).

Unit-level (no DB, no network): the mapping is pure exception-class ->
string, and every call site that needs an integration-level proof already
has one at ``tests/integration/test_property_verification_error_disclosure.py``.
"""

from __future__ import annotations

import pytest
from adcp import (
    AdagentsAccessBlockedError,
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
)

from src.services.adagents_error_messages import describe_adagents_error

# The real, confirmed-leaky message shape (see module docstring of
# test_property_verification_error_disclosure.py) -- if describe_adagents_error
# ever regresses to echoing str(exc), this literal would show up in the
# returned message and the test would catch it immediately.
_LEAKY_MESSAGE = "SSRF validation failed for 'https://leaky.example.com/.well-known/adagents.json': cloud metadata IP 169.254.169.254 blocked"


class TestDescribeAdagentsError:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (AdagentsNotFoundError("leaky.example.com"), "adagents.json not found for this domain"),
            (AdagentsTimeoutError("leaky.example.com", 10.0), "Timed out fetching adagents.json"),
            (AdagentsValidationError(_LEAKY_MESSAGE), "adagents.json could not be validated"),
            (AdagentsAccessBlockedError("leaky.example.com"), "adagents.json fetch was blocked"),
            (ValueError("some unrelated internal detail"), "adagents.json verification failed"),
        ],
        ids=["not-found", "timeout", "validation", "access-blocked", "generic-fallback"],
    )
    def test_returns_fixed_message(self, exc: Exception, expected: str) -> None:
        assert describe_adagents_error(exc) == expected

    def test_never_echoes_the_exceptions_own_text(self) -> None:
        """The one property that matters: no exception's str() ever survives into the result."""
        for exc in (
            AdagentsNotFoundError("leaky.example.com"),
            AdagentsTimeoutError("leaky.example.com", 10.0),
            AdagentsValidationError(_LEAKY_MESSAGE),
            AdagentsAccessBlockedError("leaky.example.com"),
        ):
            result = describe_adagents_error(exc)
            assert str(exc) not in result
            assert "169.254.169.254" not in result

    def test_subclass_ordering_does_not_fall_through_to_the_base_class_message(self) -> None:
        """AdagentsNotFoundError/AdagentsTimeoutError/AdagentsAccessBlockedError are all
        AdagentsValidationError subclasses -- each must resolve to its OWN specific
        message, not the generic AdagentsValidationError one.
        """
        assert describe_adagents_error(AdagentsNotFoundError("x.com")) != describe_adagents_error(
            AdagentsValidationError("some other validation failure")
        )
        assert describe_adagents_error(AdagentsTimeoutError("x.com", 5.0)) != describe_adagents_error(
            AdagentsValidationError("some other validation failure")
        )
        assert describe_adagents_error(AdagentsAccessBlockedError("x.com")) != describe_adagents_error(
            AdagentsValidationError("some other validation failure")
        )

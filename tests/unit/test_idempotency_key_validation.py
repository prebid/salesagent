"""Shared AdCP idempotency-key shape regressions."""

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.schemas._base import validate_idempotency_key_shape


def test_trailing_newline_is_not_accepted_by_charset_anchor() -> None:
    """Python ``$`` matches before a final newline; full validation must not."""
    with pytest.raises(AdCPValidationError, match="outside"):
        validate_idempotency_key_shape("1234567890abcdef\n")

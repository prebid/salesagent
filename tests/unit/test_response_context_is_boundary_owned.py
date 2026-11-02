"""The response envelope refuses a ``context`` from anyone but the boundary.

Two refusals on ``AdcpResponse`` (salesagent-3cs7o.3), each proven by breaking it:
constructing a response with a non-None ``context`` fails validation, and assigning one
afterwards fails too. ``_boundary._served`` is the one writer, through ``object.__setattr__``,
and the third case proves that bypass still lands the echo -- a guard that also blocked the
boundary would read exactly like one that works.
"""

from __future__ import annotations

import pytest
from adcp.types import ContextObject
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas._base import AdcpErrorResponse
from src.core.tools._boundary import _served

pytestmark = pytest.mark.arch_guard


def _failure() -> AdcpErrorResponse:
    return AdcpErrorResponse.of(AdCPValidationError(field="x"))


def test_constructing_a_response_with_a_context_is_refused() -> None:
    with pytest.raises(ValidationError, match="boundary"):
        AdcpErrorResponse.model_validate({**_failure().model_dump(), "context": {"correlation_id": "c1"}})


def test_assigning_a_context_after_construction_is_refused() -> None:
    response = _failure()
    with pytest.raises(AttributeError, match="boundary"):
        response.context = ContextObject.model_validate({"correlation_id": "c1"})
    assert response.context is None


def test_the_boundary_still_stamps_the_echo() -> None:
    echo = ContextObject.model_validate({"correlation_id": "c1"})
    response = _served(echo, _failure())
    assert response.context is echo
    assert response.model_dump()["context"] == {"correlation_id": "c1"}

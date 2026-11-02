"""The boundary's idempotency decisions, graded directly.

``src/core/tools/_boundary.py`` is the one path from a validated request to a response, and
naming the response's type is a pure function of the implementation. It is exercised
end-to-end by the integration suite against a real database; this module grades it where it
is decided, so a regression names the rule it broke instead of surfacing as a replay that
did not happen three layers away.

Each rule cites the pinned prose it comes from: ``docs/building/by-layer/L1/security.mdx``,
"Idempotency", in the adcontextprotocol/adcp repo at the version this repo pins.

Rule 2 -- the seller stores "the inner response payload (not the protocol envelope)" -- used
to be graded here by three asserts over a ``_cacheable_body`` / ``_is_task_envelope`` pair.
Both are gone, and so is what they chose between: a response IS its protocol envelope now
(``AdcpResponse``), so there is no wrapper to unwrap and no second shape to store.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.core.tools._boundary import _response_model_for
from src.core.tools.registry import TOOLS


class TestTheResponseModelIsReadOffTheImplementation:
    """The cache can only revive an envelope it can name a type for."""

    def test_every_registered_tool_declares_a_model_return(self) -> None:
        missing = sorted(name for name, spec in TOOLS.items() if _response_model_for(spec.impl) is None)
        assert missing == [], (
            f"{missing} declare no BaseModel return annotation, so a cached response for them "
            f"can never be revived and every retry re-executes silently"
        )

    def test_an_unreadable_callable_degrades_to_no_model(self) -> None:
        """A raise here would turn an un-annotatable callable into a failed request."""

        class NotAModel:
            pass

        def unannotated(req, identity=None):  # noqa: ANN001, ANN202
            return None

        def wrong_return(req: BaseModel, identity: None = None) -> NotAModel:
            return NotAModel()

        assert _response_model_for(unannotated) is None
        assert _response_model_for(wrong_return) is None

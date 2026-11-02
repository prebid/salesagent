"""An adapter refusal reaches the buyer as fields, not as four sentences.

``AdServerAdapter.validate_media_buy_request`` returned ``list[str]``, and the
pricing-compatibility default built each string by interpolating structured facts
into an f-string and throwing the structure away::

    f"Adapter does not support '{pricing_model}' pricing. "
    f"Supported pricing models: {sorted_supported}. "
    f"The requested pricing model ('{pricing_model}') is not available. "
    f"Please choose a product with compatible pricing."

Four authored sentences on the buyer's wire. The last is a SUGGESTION — the field
this epic made a function of the code, so it was a second and divergent copy of
what ``CODE_TABLE`` already says. ``sorted_supported`` was a joined string where
the pin's canonical ``accepted_values`` is an array, so a buyer had to split on
``", "`` to learn what the seller does support. And the package id was discarded
entirely: with three packages, nothing said which one was refused.

Covers salesagent-rys3u.4 acceptance 3, which requires this graded ON THE WIRE
across mcp/a2a/rest — the codes and details are what a buyer actually parses, and
an ``_impl``-level assertion cannot see the envelope the transports build.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WIRE_TRANSPORTS = [Transport.MCP, Transport.A2A, Transport.REST]


def _drive_real_prevalidation(env) -> None:
    """Make the mocked adapter run the REAL pre-validation, refusing the seeded model.

    ``env.mock["adapter"]`` is a MagicMock, so calling validate_media_buy_request on
    it returns another MagicMock and the method under test never executes. Binding
    the real ``AdServerAdapter.validate_media_buy_request`` with the mock standing in
    for ``self`` runs the actual default implementation while
    ``get_supported_pricing_models`` is controlled — which is the only thing the
    method reads off self.

    Supported is set to ``{"cpc"}`` so the seeded ``cpm`` package is refused. Without
    that the request succeeds and there is nothing to grade.
    """
    from src.adapters.base import AdServerAdapter

    adapter = env.mock["adapter"].return_value
    adapter.get_supported_pricing_models.return_value = {"cpc"}
    adapter.validate_media_buy_request = lambda *args, **kwargs: AdServerAdapter.validate_media_buy_request(
        adapter, *args, **kwargs
    )


@pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_unsupported_pricing_reaches_the_buyer_as_structured_problems(integration_db, transport):
    """The refusal carries the rejected value and the supported set AS FIELDS."""
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    now = datetime.now(UTC)
    with MediaBuyCreateEnv() as env:
        env.setup_media_buy_data()
        _drive_real_prevalidation(env)

        result = env.call_via(
            transport,
            brand={"domain": "prevalidation.example.com"},
            start_time=(now + timedelta(days=1)).isoformat(),
            end_time=(now + timedelta(days=8)).isoformat(),
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            idempotency_key=f"prevalidate-{uuid4().hex}",
        )

        assert result.is_error, (
            f"[{transport.value}] pre-validation must refuse a pricing model the adapter "
            f"does not support; got {getattr(result, 'wire_response', None) or result.payload!r}"
        )

        result.assert_wire_error("VALIDATION_ERROR", recovery="correctable")

        problems = (result.wire_error_details("VALIDATION_ERROR") or {}).get("problems")
        assert problems, f"[{transport.value}] the refusal carried no problems[]: {result.wire_error_envelope}"

        # An INDEXED pointer (packages[0].pricing_option_id), built by
        # package_field_path -- the shape the pinned contract grades, and the only one
        # that says WHICH package on a multi-package request.
        pricing = [p for p in problems if str(p.get("field", "")).endswith(".pricing_option_id")]
        assert pricing, f"[{transport.value}] no problem names the pricing field: {problems}"
        problem = pricing[0]

        # accepted_values is an ARRAY, which is the specific thing acceptance 3 names.
        # It was a string joined on ", " that a buyer had to split.
        assert isinstance(problem.get("accepted_values"), list), (
            f"[{transport.value}] accepted_values must be an array, got {problem.get('accepted_values')!r}"
        )
        assert problem.get("rejected_value"), f"[{transport.value}] the refused value is not carried: {problem}"
        # WHICH package was refused -- discarded entirely by the old message.
        assert problem.get("subject_id"), f"[{transport.value}] no package is named: {problem}"
        # Indexed, not the empty-bracket form the guard forbids.
        assert problem["field"].startswith("packages["), f"[{transport.value}] {problem['field']!r}"
        assert "packages[]" not in problem["field"], (
            f"[{transport.value}] the pointer must name WHICH package: {problem['field']!r}"
        )


@pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_no_authored_sentence_reaches_the_buyer_from_pre_validation(integration_db, transport):
    """The deleted suggestion must not come back through any channel.

    Scans the WHOLE envelope rather than one field: the point is not that a
    particular key is clean but that the adapter layer has no way to put a
    sentence on the wire at all. ``ErrorProblem`` has no free-text field, so this
    holds by construction — and this test is what would notice if someone added one.
    """
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    now = datetime.now(UTC)
    with MediaBuyCreateEnv() as env:
        env.setup_media_buy_data()
        _drive_real_prevalidation(env)

        result = env.call_via(
            transport,
            brand={"domain": "prevalidation-prose.example.com"},
            start_time=(now + timedelta(days=1)).isoformat(),
            end_time=(now + timedelta(days=8)).isoformat(),
            packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            idempotency_key=f"prevalidate-prose-{uuid4().hex}",
        )

        assert result.is_error, f"[{transport.value}] expected a refusal to scan"

        rendered = str(result.wire_error_envelope)
        for authored in (
            "Please choose a product with compatible pricing",
            "Adapter does not support",
            "is not available",
        ):
            assert authored not in rendered, f"[{transport.value}] an authored sentence reached the wire: {authored!r}"

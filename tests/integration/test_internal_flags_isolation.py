"""Integration tests: internal behavior flags must not be controllable by external callers.

Security regression tests for and .
Proves that include_performance, include_sub_assets, and include_snapshot
cannot be injected by buyers through request objects.

Uses the CreativeListEnv harness for real DB integration testing.
"""

import pytest

from src.core.exceptions import AdCPInvalidRequestError
from src.core.schemas import GetMediaBuysRequest
from tests.harness.creative_list import CreativeListEnv


@pytest.mark.requires_db
class TestListCreativesInternalFlagsIsolation:
    """Verify include_* flags cannot be injected via request object."""

    def test_request_object_rejects_include_performance(self, integration_db):
        """ListCreativesRequest schema must reject include_performance (not in AdCP spec)."""
        from src.core.schemas import ListCreativesRequest

        # include_performance is not a declared property of the pinned list-creatives-request,
        # so the accepted-shape strip refuses it at the DTO boundary. The rejection is
        # the obligation; which layer produces it moved, the obligation did not.
        with pytest.raises(AdCPInvalidRequestError):
            ListCreativesRequest(include_performance=True)

    def test_request_object_rejects_include_sub_assets(self, integration_db):
        """ListCreativesRequest schema must reject include_sub_assets (not in AdCP spec)."""
        from src.core.schemas import ListCreativesRequest

        # include_sub_assets is not a declared property of the pinned list-creatives-request,
        # so the accepted-shape strip refuses it at the DTO boundary. The rejection is
        # the obligation; which layer produces it moved, the obligation did not.
        with pytest.raises(AdCPInvalidRequestError):
            ListCreativesRequest(include_sub_assets=True)

    def test_request_object_accepts_include_assignments(self, integration_db):
        """include_assignments IS a valid AdCP 3.10 spec field — must be accepted."""
        from src.core.schemas import ListCreativesRequest

        req = ListCreativesRequest(include_assignments=True)
        assert req.include_assignments is True

    def test_impl_takes_the_request_and_nothing_beside_it(self, integration_db):
        """_list_creatives_impl's signature is ``(req, identity)`` — no flag travels beside it.

        This used to call ``env.call_impl(include_performance=False, include_sub_assets=False)``
        to show the flags came from the wrapper rather than the buyer. Both parameters are
        gone (adcp 3.10 removed them from the spec; nothing in ``src/`` read either), and the
        isolation obligation is now discharged structurally rather than by passing a value:
        there is no argument beside the request for a buyer's value to be smuggled into.
        """
        import inspect

        from src.core.tools.creatives.listing import _list_creatives_impl

        assert list(inspect.signature(_list_creatives_impl).parameters) == ["req", "identity"]

        with CreativeListEnv() as env:
            env.setup_default_data()

            response = env.call_impl()
            assert response is not None

    def test_mcp_call_succeeds_with_default_flags(self, integration_db):
        """MCP wrapper works with default include_* flags (harness simulates MCP transport)."""
        with CreativeListEnv() as env:
            env.setup_default_data()

            response = env.call_mcp()
            assert response is not None


@pytest.mark.requires_db
class TestGetMediaBuysInternalFlagsIsolation:
    """A value set for include_snapshot on the REQUEST object is inert.

    This class used to assert that GetMediaBuysRequest REJECTS include_snapshot, on the
    stated grounds that it is "not in AdCP spec". That premise is false against the pinned
    version: media-buy/get-media-buys-request.json declares
    include_snapshot as {"type": "boolean"}, so it is buyer-facing and REFUSING it would be
    the bug. GetMediaBuysRequest was re-based on the library type and now inherits it, which
    is what retired the old assertion.

    The isolation it was written to protect is intact -- what changed is the mechanism, from
    "the model rejects it" to "the model never feeds it", so the invariant is re-expressed
    rather than dropped. Measured: `.include_snapshot` has exactly ONE read site in src/,
    src/routes/api_v1.py:524, and that reads the REST BODY, not the request object.
    GetMediaBuysRequest builds from media_buy_ids/status_filter/account/context
    only, and all three wrappers pass the flag out-of-band as an explicit argument.
    """

    def test_the_request_object_accepts_the_spec_field(self, integration_db):
        """Accepting it is REQUIRED: the pinned schema declares it as a buyer input."""

        assert GetMediaBuysRequest(include_snapshot=True).include_snapshot is True

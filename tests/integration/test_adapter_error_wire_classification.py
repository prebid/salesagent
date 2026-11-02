"""An adapter classifies its failure, then nothing converts the classification.

The GAM adapter computes a precise diagnosis for every upstream fault --
``map_gam_exception`` (src/adapters/gam/utils/error_handler.py) sorts a SOAP fault
into quota, permission, not-found, auth, validation, network, timeout, or
duplicate. It returns a ``GAMError``, a class the rest of the system has never
heard of: no ``except GAMError`` exists outside the module that defines it, so
the diagnosis dies inside the adapter.

The buyer whose campaign failed on a GAM quota is told the seller is
unavailable. So is the buyer who lacks permission on the advertiser, and the
buyer who named an ad unit that does not exist. Three different remedies -- wait
and retry, contact the seller, fix the reference -- collapse into one.

Two tests, at the two different loci this defect spans:

``TestTypedAdapterErrorReachesTheBuyer`` grades the WIRE. It passes today, and
that is the point: the tool layer already re-raises a typed ``AdCPSalesAgentError``
untouched (``except AdCPSalesAgentError as adcp_err: raise`` at
src/core/tools/media_buy_create.py:4214, ahead of the ``except Exception`` that
collapses everything else to SERVICE_UNAVAILABLE). So the buyer-facing half of
the contract is sound and the fix does not need to touch it -- this test pins
that, because deleting the passthrough branch would silently undo the whole fix.

``TestRawFaultIsClassified`` grades the ADAPTER, and is the reproduction. It runs
the real ``GAMOrdersManager`` over a mocked GAM client, so the fault travels the
production path. A mocked adapter cannot grade this: mocking the adapter replaces
the very code that must do the classifying.

The seam ABOVE the manager -- ``GoogleAdManager.create_media_buy`` -- is pinned by
``TestGAMAdapterSeamPreservesClassification`` in
tests/unit/test_gam_workflow_packages.py, which reuses that file's adapter wiring
instead of duplicating it here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.core.exceptions import (
    AdCPAdapterResourceNotFoundError,
    AdCPAuthorizationError,
    AdCPRateLimitError,
)
from tests.harness.transport import Transport
from tests.helpers.envelope_assertions import envelope_for

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WIRE_TRANSPORTS = [Transport.MCP, Transport.A2A, Transport.REST]


class GoogleAdsServerFault(Exception):
    """Stand-in for googleads.errors.GoogleAdsServerFault.

    The real class carries SOAP fault text and nothing this codebase controls.
    Only its type name and message reach the classifier, which is all a real
    fault guarantees and all this test relies on.
    """


class TestTypedAdapterErrorReachesTheBuyer:
    """A typed adapter error survives the tool layer with its own code."""

    _TYPED = [
        pytest.param(AdCPRateLimitError(retry_after=30), "RATE_LIMITED", id="rate_limited"),
        pytest.param(AdCPAuthorizationError(), "PERMISSION_DENIED", id="permission_denied"),
        pytest.param(AdCPAdapterResourceNotFoundError(), "REFERENCE_NOT_FOUND", id="not_found"),
    ]

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    @pytest.mark.parametrize("error,expected_code", _TYPED)
    def test_typed_error_is_not_flattened(self, integration_db, transport, error, expected_code):
        """The buyer reads the adapter's code, not the tool's catch-all."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        now = datetime.now(UTC)
        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            env.mock["adapter"].return_value.create_media_buy.side_effect = error

            result = env.call_via(
                transport,
                brand={"domain": "adapter-fault.example.com"},
                start_time=(now + timedelta(days=1)).isoformat(),
                end_time=(now + timedelta(days=8)).isoformat(),
                packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                idempotency_key=f"adapter-typed-{uuid4().hex}",
            )

            assert result.is_error, (
                "an adapter error must fail the request, got "
                f"{getattr(result, 'wire_response', None) or result.payload!r}"
            )
            result.assert_wire_error(expected_code, recovery=None)


class TestRawFaultIsClassified:
    """A raw upstream fault out of the GAM API becomes the typed AdCP error."""

    # (fault, the AdCP class the adapter must raise) -- one row per remedy.
    _FAULTS = [
        pytest.param(
            GoogleAdsServerFault("QuotaError.EXCEEDED_QUOTA: too many requests"),
            AdCPRateLimitError,
            id="quota_exceeded",
        ),
        pytest.param(
            GoogleAdsServerFault("PermissionError.PERMISSION_DENIED on advertiser 12345"),
            AdCPAuthorizationError,
            id="permission_denied",
        ),
        pytest.param(
            GoogleAdsServerFault("NotFoundError: ad unit 99999 not found"),
            AdCPAdapterResourceNotFoundError,
            id="not_found",
        ),
    ]

    @pytest.mark.parametrize("fault,expected_class", _FAULTS)
    def test_order_creation_fault_is_classified(self, fault, expected_class):
        """``createOrders`` refusing must raise the AdCP class for that refusal.

        The classification is the adapter's job and this is where it must happen.
        Graded on the type raised out of the real manager, over a mocked GAM
        client -- the boundary the fix changes.
        """
        from src.adapters.gam.managers.orders import GAMOrdersManager

        order_service = MagicMock()
        order_service.createOrders.side_effect = fault
        client_manager = MagicMock()
        client_manager.get_service.return_value = order_service

        manager = GAMOrdersManager(
            client_manager=client_manager,
            advertiser_id="12345",
            trafficker_id="67890",
        )

        with pytest.raises(expected_class):
            manager.create_order(
                order_name="test-order",
                total_budget=5000.0,
                start_time=datetime.now(UTC) + timedelta(days=1),
                end_time=datetime.now(UTC) + timedelta(days=8),
            )


class TestNoUpstreamTextOnTheWire:
    """The classification reaches the buyer; the SOAP fault's text does not.

    The classifier is the one place that sees a third party's fault string, and
    AdCP 3.1.1 transport-errors.mdx forbids putting it on the wire (Security
    Considerations; restated for CONFIGURATION_ERROR as "MUST NOT include
    credentials, connection strings, full file paths, or stack traces").

    Before the fix that rule held by accident: the tool layer's catch-all threw
    the whole error away, text included. Now the error is carried deliberately,
    so the rule needs grading -- otherwise the fix is exactly the change that
    could start publishing upstream text.
    """

    def test_fault_text_is_not_published(self):
        """The wire carries no fragment of the upstream fault."""
        from src.adapters.gam.utils.error_handler import map_gam_exception

        secret = "advertiser-98765-token-abcdef"
        fault = GoogleAdsServerFault(f"PermissionError.PERMISSION_DENIED [{secret}]")

        error = map_gam_exception(fault)
        # Built the way the boundary builds it (AdcpErrorResponse.of + to_wire),
        # so this is exactly the body the buyer receives -- not a reconstruction.
        envelope = envelope_for(error)
        wire_text = str(envelope)

        assert secret not in wire_text, f"the upstream fault's text reached the wire envelope: {wire_text[:400]}"
        assert "PERMISSION_DENIED" in wire_text, "the classification itself must reach the buyer"
        # The diagnostic still exists for the operator, off the wire.
        assert secret in str(error.internal_detail), "internal_detail must keep the fault for the server-side log"


class TestEveryClassifierRow:
    """Every row of ``map_gam_exception``, not just the three the acceptance names.

    The raise-site coverage guard walks ``raise`` statements. This classifier
    ``return``s its error and callers raise the result, so the guard cannot see
    these rows at all -- they carry no test obligation and would otherwise have no
    test. The blindness is tracked separately; the rows are graded here regardless,
    because an unclassified row silently falls through to INTERNAL_ERROR and the
    buyer loses the diagnosis exactly as they did before this fix.
    """

    _ROWS = [
        pytest.param("AuthError", "bad credentials", "CONFIGURATION_ERROR", id="auth_is_seller_config"),
        pytest.param("PermissionError", "denied", "PERMISSION_DENIED", id="permission"),
        pytest.param("QuotaError", "over limit", "RATE_LIMITED", id="quota"),
        pytest.param("NotFoundError", "missing", "REFERENCE_NOT_FOUND", id="not_found"),
        pytest.param("DuplicateError", "already exists", "CONFLICT", id="duplicate"),
        pytest.param("NetworkError", "unreachable", "SERVICE_UNAVAILABLE", id="network"),
        pytest.param("SomeTimeoutError", "timeout", "SERVICE_UNAVAILABLE", id="timeout_shares_network_code"),
        pytest.param("WeirdFault", "no idea what this is", "INTERNAL_ERROR", id="unclassifiable"),
        # OAuth vocabulary, which names neither "AuthError" nor "authentication".
        # `RefreshError: invalid_grant` is Google's canonical expired- or
        # revoked-refresh-token failure and the most common credential fault this
        # adapter sees. It contains "invalid", so on keyword order alone it landed
        # on the validation row and told the buyer their request was malformed
        # while the SELLER's token sat expired (salesagent-dpxo2).
        pytest.param(
            "RefreshError",
            "invalid_grant: Token has been expired or revoked.",
            "CONFIGURATION_ERROR",
            id="expired_refresh_token_is_sellers",
        ),
        pytest.param(
            "RefreshError", "invalid_client: Unauthorized", "CONFIGURATION_ERROR", id="bad_oauth_client_is_sellers"
        ),
        # The counter-case that keeps the row above honest: a field the BUYER
        # supplied really is the buyer's to fix, and must stay VALIDATION_ERROR.
        pytest.param(
            "GoogleAdsServerFault",
            "CommonError.INVALID_ARGUMENT: invalid value for field startDateTime",
            "VALIDATION_ERROR",
            id="bad_buyer_field_stays_the_buyers",
        ),
    ]

    @pytest.mark.parametrize("type_name,text,expected_code", _ROWS)
    def test_row_yields_its_code(self, type_name, text, expected_code):
        """The fault's family decides the code, by type name or message."""
        from src.adapters.gam.utils.error_handler import map_gam_exception

        fault = type(type_name, (Exception,), {})(text)
        error = map_gam_exception(fault)

        assert error.error_code == expected_code, (
            f"{type_name}({text!r}) classified as {error.error_code}, expected {expected_code}"
        )
        # Every row keeps the fault for the operator and off the wire.
        assert text in str(error.internal_detail)

    def test_unknown_row_is_not_retryable(self):
        """An unclassifiable fault must not land in ``with_retry``'s retry set.

        AdCPAdapterError would have: it is SERVICE_UNAVAILABLE, which the default
        ``retry_on`` includes. Routing UNKNOWN there would retry every mystery
        fault three times with backoff for no reason.
        """
        from src.adapters.gam.utils.error_handler import map_gam_exception
        from src.core.exceptions import AdCPRateLimitError, AdCPServiceUnavailableError

        error = map_gam_exception(Exception("no idea"))

        retry_on = (AdCPServiceUnavailableError, AdCPRateLimitError)
        assert not isinstance(error, retry_on), (
            f"{type(error).__name__} is in the default retry set; an unclassifiable fault must not be retried"
        )

"""Integration wire pins: schema-invalid filter fields on get_media_buys.

``GetMediaBuysBody`` (src/routes/api_v1.py) deliberately types ``media_buy_ids``
and ``status_filter`` as ``Any`` rather than their concrete AdCP shapes
(``list[str] | None`` / ``MediaBuyStatus | list[MediaBuyStatus] | None``). The
in-code comment states why: typing them concretely would make FastAPI reject a
wrong-typed value during body parsing, BEFORE the shared
``GetMediaBuysRequest`` validation boundary MCP/A2A go through — producing
``INVALID_REQUEST`` on REST while MCP/A2A produce ``VALIDATION_ERROR`` for the
identical buyer mistake. Staying ``Any`` lets the raw value reach the SAME
shared boundary on every transport, so all three converge on
``VALIDATION_ERROR`` instead of diverging (contrast with ``revision`` on
``update_media_buy``, which IS concretely typed and accepts that divergence
for a different reason — see test_update_media_buy_revision_validation_wire.py).

``context`` and ``account`` do not carry this deviation (typed concretely,
per the sibling REST *Body models) because they have no such cross-transport
classification split to protect.

These tests pin the CLAIM that the ``Any`` typing decision actually delivers
parity, not just that it exists: if a future edit types ``media_buy_ids``/
``status_filter`` concretely (reintroducing the split this decision exists to
avoid), the REST case here goes red instead of drifting silently. The A2A case
was previously pinned alone, for a different invariant (suggestion presence,
tests/integration/test_request_validation_suggestion_parity.py); this file
completes the claim across all three wire transports.
"""

from __future__ import annotations

import pytest

from tests.factories import PrincipalFactory, TenantFactory
from tests.harness.media_buy_list import MediaBuyListEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WIRE_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


@pytest.mark.requires_db
class TestGetMediaBuysFilterValidationWire:
    """A wrong-typed ``media_buy_ids``/``status_filter`` must emit VALIDATION_ERROR
    identically on every wire transport — proving the ``Any`` typing decision in
    ``GetMediaBuysBody`` achieves parity rather than merely claiming to."""

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_wrong_type_media_buy_ids_emits_validation_error_on_every_transport(self, integration_db, transport):
        """media_buy_ids as a bare string (not an array) reaches the shared
        GetMediaBuysRequest boundary on every transport -> VALIDATION_ERROR/correctable.

        If GetMediaBuysBody.media_buy_ids is ever typed concretely (list[str] | None),
        FastAPI would reject this during REST body parsing -> INVALID_REQUEST, and
        only the REST parametrization here reddens — pinning the split, not just
        this one transport's happy case.
        """
        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            result = env.call_via(transport, media_buy_ids="not-a-list")

            assert result.is_error, (
                f"{transport.value}: malformed media_buy_ids must be rejected, got success payload: {result.payload!r}"
            )
            result.assert_wire_error("VALIDATION_ERROR", recovery="correctable")

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_wrong_type_status_filter_emits_validation_error_on_every_transport(self, integration_db, transport):
        """status_filter as an invalid-shaped value reaches the shared
        GetMediaBuysRequest boundary on every transport -> VALIDATION_ERROR/correctable.
        Same split risk as media_buy_ids if this field is ever typed concretely.
        """
        with MediaBuyListEnv(tenant_id="t2", principal_id="p2") as env:
            tenant = TenantFactory(tenant_id="t2")
            PrincipalFactory(tenant=tenant, principal_id="p2")

            result = env.call_via(transport, status_filter={"not": "a-valid-status-shape"})

            assert result.is_error, (
                f"{transport.value}: malformed status_filter must be rejected, got success payload: {result.payload!r}"
            )
            result.assert_wire_error("VALIDATION_ERROR", recovery="correctable")

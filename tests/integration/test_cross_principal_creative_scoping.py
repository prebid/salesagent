"""Cross-principal creative scoping on the media-buy create + update paths.

The creatives PK is composite ``(creative_id, tenant_id, principal_id)``, but
the buyer-path creative lookups in create_media_buy (pre-adapter validation,
manual-approval assignment block, auto-approve assignment block) and
update_media_buy (``admin_get_by_ids`` via the guard's ``admin_*`` exemption)
filter tenant-only. Principal A referencing principal B's ``creative_id``:

- passes the existence gate on B's row (B's status/format leak into A's
  CREATIVE_REJECTED text via the status/format validation that runs on B's
  creative), and
- crashes with a raw ForeignKeyViolation 500 when the assignment INSERT runs
  under A's ``principal_id``.

Expected — uniform with the sync_creatives gate fixed in 555069ffe
(salesagent-hpjq): a cross-principal creative_id resolves to NOT FOUND.
CREATIVE_REJECTED on the wire naming the id, nothing leaked from B's row,
never a 500. salesagent-ft8s.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.factories import CreativeFactory, PrincipalFactory
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape
from tests.integration.media_buy_helpers import _make_create_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_OTHER_CREATIVE_ID = "c-owned-by-other-principal"
# Markers that only exist on the OTHER principal's creative row. Their presence
# anywhere in the wire envelope means the requester read the other's fields.
_LEAK_MARKERS = ("has status", "video_640x480")


def _seed_other_principals_creative(tenant: Any, *, status: str = "approved", format: str = "display_300x250") -> Any:
    """Seed a creative under a DIFFERENT principal in the requester's tenant."""
    other = PrincipalFactory(tenant=tenant, principal_id=f"otherprincipal{uuid.uuid4().hex[:8]}")
    return CreativeFactory(
        tenant=tenant,
        principal=other,
        creative_id=_OTHER_CREATIVE_ID,
        format=format,
        agent_url="https://creative.adcontextprotocol.org",
        status=status,
        data={"url": "https://example.com/other.jpg", "width": 300, "height": 250},
    )


def _cross_principal_create_request() -> Any:
    return _make_create_request(
        packages=[
            {
                "product_id": "prod_1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "creative_ids": [_OTHER_CREATIVE_ID],
            }
        ]
    )


def _assert_not_found_and_no_leak(result: Any) -> None:
    """The wire outcome must be the uniform not-found rejection with zero leakage.

    Not a 500 (raw FK IntegrityError), not a success (using the other
    principal's creative), and no field from the other principal's row in the
    envelope text.
    """
    assert result.is_error, f"Cross-principal creative reference must be rejected, got success: {result.payload!r}"
    assert_envelope_shape(
        result.wire_error_envelope,
        "CREATIVE_REJECTED",
        recovery="correctable",
        message_substr=_OTHER_CREATIVE_ID,
    )
    envelope_text = json.dumps(result.wire_error_envelope).lower()
    assert "not found" in envelope_text, (
        f"Cross-principal creative must be reported as NOT FOUND (uniform with a "
        f"nonexistent id), got: {result.wire_error_envelope}"
    )
    for marker in _LEAK_MARKERS:
        assert marker.lower() not in envelope_text, (
            f"Envelope leaks the other principal's creative fields ({marker!r}): {result.wire_error_envelope}"
        )


class TestCreateMediaBuyCrossPrincipalCreative:
    """create_media_buy: a cross-principal creative_id must resolve to not-found."""

    def test_auto_approve_path_rejects_cross_principal_creative(self, integration_db):
        """Auto path: today B's valid creative passes pre-validation on B's row,
        then the assignment INSERT under A's principal_id violates the composite
        FK — a raw 500 instead of CREATIVE_REJECTED not-found.
        """
        with MediaBuyCreateEnv(human_review_required=False) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant)

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_manual_approval_path_rejects_cross_principal_creative(self, integration_db):
        """Manual path: same hole — pre-validation passes on B's row, then the
        assignment block reloads tenant-only and inserts under A's principal_id
        (media_buy_create.py manual-approval branch) — raw FK 500.
        """
        with MediaBuyCreateEnv(human_review_required=True) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            env.mock["adapter"].return_value.manual_approval_operations = ["create_media_buy"]
            _seed_other_principals_creative(tenant)

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_rejection_text_does_not_leak_other_principals_creative_status(self, integration_db):
        """When B's creative is in a terminal state, today pre-validation reads
        B's row and rejects with "has status 'rejected'" — leaking B's creative
        state to A. The uniform contract is NOT FOUND, indistinguishable from a
        nonexistent id.
        """
        with MediaBuyCreateEnv(human_review_required=False) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant, status="rejected")

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_rejection_text_does_not_leak_other_principals_creative_format(self, integration_db):
        """When B's creative has a format the product doesn't accept, today the
        format check runs on B's row and the rejection names B's format
        (video_640x480) — leaking it. The uniform contract is NOT FOUND.
        """
        with MediaBuyCreateEnv(human_review_required=False) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant, format="video_640x480")

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)


class TestUpdateMediaBuyCrossPrincipalCreative:
    """update_media_buy: package creative_ids referencing another principal's
    creative must resolve to not-found — not pass ``admin_get_by_ids`` and 500
    on the assignment INSERT.
    """

    def test_update_creative_ids_rejects_cross_principal_creative(self, env_with_media_buy):
        from src.core.schemas import UpdateMediaBuyRequest

        env, mb = env_with_media_buy
        _seed_other_principals_creative(env._owner_tenant)
        env._commit_factory_data()
        pkg = env._seeded_package

        req = UpdateMediaBuyRequest(
            media_buy_id=mb.media_buy_id,
            packages=[
                {
                    "package_id": pkg.package_id,
                    "creative_ids": [_OTHER_CREATIVE_ID],
                }
            ],
        )
        result = env.call_via(Transport.REST, req=req)

        _assert_not_found_and_no_leak(result)

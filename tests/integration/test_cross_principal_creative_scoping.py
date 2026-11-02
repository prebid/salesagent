"""Cross-principal creative scoping on the media-buy create + update paths.

The creatives PK is composite ``(creative_id, tenant_id, principal_id)``, but
the buyer-path creative lookups in create_media_buy (pre-adapter validation,
manual-approval assignment block, auto-approve assignment block) and
update_media_buy (``admin_get_by_ids`` via the guard's ``admin_*`` exemption)
filter tenant-only. Principal A referencing principal B's ``creative_id``:

- passes the existence gate on B's row (B's status/format leak into A's
  rejection text via the status/format validation that runs on B's
  creative), and
- crashes with a raw ForeignKeyViolation 500 when the assignment INSERT runs
  under A's ``principal_id``.

Expected — uniform with the sync_creatives gate fixed in 555069ffe: a
cross-principal creative_id resolves to ``CREATIVE_NOT_FOUND`` on the wire,
nothing leaked from B's row, never a 500.

Why that code and not ``CREATIVE_REJECTED`` (pinned 3.1.1
``enums/error-code.json``, the level-1 authority):

- ``CREATIVE_NOT_FOUND`` — "Referenced creative does not exist in the agent's
  creative library ... Sellers MUST return this code uniformly for any
  creative_id not owned by the calling account — never distinguish 'exists in
  another tenant' from 'does not exist', which would enable cross-tenant
  enumeration."
- ``CREATIVE_REJECTED`` — "Creative failed content policy review."

A cross-principal reference is not a policy-review failure, and the code is
itself part of the non-leak property these tests grade: ``CREATIVE_REJECTED``
would tell A that the id EXISTS, which is exactly the fact the two
``..._does_not_leak_...`` tests below exist to keep from A. So the uniformity is
now graded by the CODE, not only by the absence of B's field values from the
message text. Both codes carry ``recovery: "correctable"`` in the pin, so the
recovery assertion is unchanged.

Precedent (``tests/CLAUDE.md`` § THE AUTHORITY ORDER): four UC-003 scenarios
asserted ``CREATIVE_REJECTED`` for three conditions the enum codes differently
and passed for years because production emitted the same wrong code. These five
are the same family, surfacing once production was corrected.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.factories import CreativeFactory, PrincipalFactory
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_dual import MediaBuyDualEnv
from tests.harness.transport import Transport
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
    result.assert_wire_error(
        "CREATIVE_NOT_FOUND",
        recovery="correctable",
    )
    envelope_text = json.dumps(result.wire_error_envelope).lower()
    # UNIFORMITY, asserted on the machine-readable envelope rather than a phrase.
    # The claim is that a cross-principal creative is INDISTINGUISHABLE from one that
    # never existed. Production enforces it structurally: the existence check is a
    # principal-scoped `get_by_ids(requested_ids, principal_id)`, so another principal's
    # row is absent from the result exactly like a nonexistent id and takes the same
    # raise. What the buyer receives is therefore the same code and the code's own
    # sentence — asserting a "not found" PHRASE tested the old authored wording, which
    # could drift from the code while the security property held, or vice versa.
    # wire_error_details asserts the code FIRST and then returns the details block,
    # so a details oracle can never read the wrong envelope's details. Replaces a
    # hand-indexed errors[0] that resolved the protocol position itself.
    payload_details = result.wire_error_details("CREATIVE_NOT_FOUND", recovery="correctable")
    assert payload_details.get("missing_creative_ids") == [_OTHER_CREATIVE_ID], (
        "The uniform rejection must report WHICH creative_ids were unresolvable, and only those "
        f"(3.1.1 L3/error-handling.mdx permits enumerating caller-supplied elements): {payload_details}"
    )
    for marker in _LEAK_MARKERS:
        assert marker.lower() not in envelope_text, (
            f"Envelope leaks the other principal's creative fields ({marker!r}): {result.wire_error_envelope}"
        )


class TestCreateMediaBuyCrossPrincipalCreative:
    """create_media_buy: a cross-principal creative_id must resolve to not-found."""

    def test_auto_approve_path_rejects_cross_principal_creative(self, integration_db):
        """Auto path: before the fix, B's valid creative passed pre-validation on
        B's row, then the assignment INSERT under A's principal_id violated the
        composite FK — a raw 500 instead of the uniform CREATIVE_NOT_FOUND.
        """
        with MediaBuyCreateEnv(human_review_required=False) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant)

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_manual_approval_path_rejects_cross_principal_creative(self, integration_db):
        """Manual path: same hole — pre-validation passed on B's row, then the
        assignment block reloaded tenant-only and inserted under A's principal_id
        (media_buy_create.py manual-approval branch) — raw FK 500. Same uniform
        CREATIVE_NOT_FOUND as the auto path.
        """
        with MediaBuyCreateEnv(human_review_required=True) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            env.mock["adapter"].return_value.manual_approval_operations = ["create_media_buy"]
            _seed_other_principals_creative(tenant)

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_rejection_text_does_not_leak_other_principals_creative_status(self, integration_db):
        """When B's creative is in a terminal state, the unscoped lookup read B's
        row and rejected with "has status 'rejected'" — leaking B's creative state
        to A. The uniform contract is CREATIVE_NOT_FOUND, indistinguishable from a
        nonexistent id: the CODE alone must not reveal that the row exists, which
        is why CREATIVE_REJECTED (a content-policy review outcome, and one that
        implies existence) would fail this test even with a scrubbed message.
        """
        with MediaBuyCreateEnv(human_review_required=False) as env:
            tenant, _principal, _product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant, status="rejected")

            result = env.call_via(Transport.REST, req=_cross_principal_create_request())

            _assert_not_found_and_no_leak(result)

    def test_rejection_text_does_not_leak_other_principals_creative_format(self, integration_db):
        """When B's creative has a format the product doesn't accept, the format
        check ran on B's row and the rejection named B's format (video_640x480) —
        leaking it. The uniform contract is CREATIVE_NOT_FOUND: A's request never
        gets far enough to have B's format judged at all.
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

    def test_update_creative_ids_rejects_cross_principal_creative(self, integration_db):
        from src.core.schemas import UpdateMediaBuyRequest
        from tests.bdd.conftest import _setup_existing_media_buy

        with MediaBuyDualEnv() as env:
            tenant, principal, product, _po = env.setup_media_buy_data()
            _seed_other_principals_creative(tenant)
            ctx: dict = {}
            _setup_existing_media_buy(ctx, env, tenant, principal, product)
            mb = ctx["existing_media_buy"]
            pkg = ctx["existing_package"]
            env._seeded_media_buy_id = mb.media_buy_id

            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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

"""dry_run create/update_media_buy previews Success(status="completed") — behavior guard.

Regression/characterization guard for salesagent-6tc3 (third member of the adcp
5.7 -> 6.6 status-default family, alongside salesagent-5dxc and salesagent-88e2).

WHAT THIS GUARDS
    adcp 6.6 gave Create/UpdateMediaBuySuccess a default protocol-envelope
    status="completed". The dry_run branches
    (src/core/tools/media_buy_update.py:359, src/core/tools/media_buy_create.py:3503)
    return a *Success for a SIMULATED, non-applied operation, so the serialized
    wire envelope previews status="completed". These two tests pin that the
    completed-preview keeps serializing for the dry_run path, guarding it against
    an ACCIDENTAL regression.

WHY "completed" IS CORRECT HERE (grounded decision — Option a, no code change)
    Citation: AdCP spec 3.1.1 (installed adcp SDK 6.6.0, the current repo pin).
    create/update-media-buy-response.json each define exactly THREE oneOf
    response variants — Success (status="completed"), Error, and Submitted
    (status="submitted"). NONE represents a simulation, and there is no dry-run
    value in the protocol-envelope status enum. The wire has no way to mark a
    response as a simulation.

    dry_run is NOT a spec request field for create/update_media_buy: it is a
    TESTING HOOK (the spec-DEPRECATED X-Dry-Run header, sandbox.mdx:219, parsed
    into AdCPTestContext.dry_run — src/core/testing_hooks.py). Conformance:
    UNGRADED — no compliance storyboard grades create/update_media_buy dry_run
    status. The spec prose only says sellers SHOULD support dry_run "for
    validation without applying changes"; it is SILENT on what status the
    response carries.

    Per feedback_schema_silent_production_authoritative: SILENT -> production is
    authoritative -> keep the completed-preview. A dry_run buyer explicitly asked
    to simulate the WOULD-BE outcome, and the would-be outcome IS completion, so
    Success(status="completed") is a truthful preview. This is deliberately
    DISTINCT from the siblings, where the operation genuinely did NOT complete:
    salesagent-5dxc (pending human approval -> UpdateMediaBuySubmitted) and
    salesagent-88e2 (reject -> Error). For those, "completed" is a lie routable
    to an existing variant; for dry_run it is a faithful preview.

SCOPE OF THIS PIN
    This guards ACCIDENTAL regression under the CURRENT spec silence. It is NOT
    an affirmative spec obligation. Revisit if a future AdCP spec defines a
    dry-run response marker (a distinct status or envelope) — such a spec would
    legitimately update these expectations.

    Assertions are on the SERIALIZED envelope (model_dump(mode="json")): the
    status-default bug family surfaces only at serialization. Each test also
    asserts a dry_run-specific marker (the dry_run change flag / the "dry_run_"
    media_buy_id prefix) so the guard proves it exercised the dry_run branch and
    not a same-status non-dry-run path — the false-green trap flagged in the
    architect review.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_update import MediaBuyUpdateEnv
from tests.helpers.adcp_factories import create_test_package_request


def _future(days: int) -> str:
    """ISO 8601 datetime string N days in the future."""
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_update_media_buy_dry_run_reports_completed():
    """update_media_buy dry_run previews the completed outcome.

    Exercises src/core/tools/media_buy_update.py:359. The dry_run early-return
    builds UpdateMediaBuySuccess (adcp-6.6 default status="completed") for a
    simulated, non-applied update. The dry_run injection uses the harness
    first-class mechanism: MediaBuyUpdateEnv(dry_run=True) flows through
    make_identity(dry_run=True) -> testing_context.dry_run=True. No identity=
    kwarg is passed to call_impl (the harness hardwires self.identity, and an
    identity kwarg would leak into UpdateMediaBuyRequest under extra=forbid) —
    the false-green trap from the architect review.
    """
    with MediaBuyUpdateEnv(tenant_id="t1", principal_id="p1", dry_run=True) as env:
        # manual_approval_required=False (the harness default) so the dry_run
        # early-return is reached, NOT the manual-approval submitted branch.
        env.mock["adapter"].return_value.manual_approval_required = False
        env.set_media_buy(media_buy_id="mb-001", status="active")

        result = env.call_impl(
            media_buy_id="mb-001",
            packages=[{"package_id": "pkg-1", "paused": True}],
        )

    envelope = result.model_dump(mode="json")

    # Spec 3.1.1 SILENT on dry_run response status -> production authoritative:
    # the simulated update previews the would-be completed outcome.
    assert envelope["status"] == "completed", f"dry_run update must preview completed, got {envelope['status']!r}"
    # Prove the dry_run branch (media_buy_update.py:359) ran, not a same-status
    # non-dry-run path: only the dry_run branch SIMULATES affected_packages from
    # the REQUEST (package_id "pkg-1", paused=True). The non-dry-run path builds
    # affected_packages from the adapter's actual update result, which yields an
    # empty list under this harness — so a non-empty, request-mirroring package
    # is the observable signature that the simulation branch executed.
    affected = envelope["affected_packages"]
    assert affected == [{"package_id": "pkg-1", "paused": True, "canceled": False}], (
        f"guard must exercise the dry_run branch — expected the request mirrored into "
        f"affected_packages, got {affected!r}"
    )


@pytest.mark.requires_db
def test_create_media_buy_dry_run_reports_completed(integration_db):
    """create_media_buy dry_run previews the completed outcome.

    Exercises src/core/tools/media_buy_create.py:3503. The dry_run branch skips
    the adapter call and returns CreateMediaBuyResult(status="completed") wrapping
    a CreateMediaBuySuccess (adcp-6.6 default status="completed") with a
    "dry_run_"-prefixed media_buy_id. Driven through the real _create_media_buy_impl
    via MediaBuyCreateEnv (requires_db; skips cleanly under `tox -e unit` /
    make quality when DATABASE_URL is unset), so it CALLS production and reaches
    the target branch rather than asserting an SDK/Pydantic default.
    """
    with MediaBuyCreateEnv(dry_run=True) as env:
        _tenant, _principal, product, _pricing_option = env.setup_media_buy_data()

        result = env.call_impl(
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[
                create_test_package_request(
                    product_id=product.product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=10000.0,
                )
            ],
        )

    response, _status = result

    # Assert on the SERIALIZED inner Success envelope — the :3503 status default.
    response_envelope = response.model_dump(mode="json")
    assert response_envelope["status"] == "completed", (
        f"dry_run create Success must preview completed, got {response_envelope['status']!r}"
    )
    # And on the full CreateMediaBuyResult wire envelope buyers receive.
    wire_envelope = result.model_dump(mode="json")
    assert wire_envelope["status"] == "completed", (
        f"dry_run create wire envelope must be completed, got {wire_envelope['status']!r}"
    )
    # Prove the dry_run branch (media_buy_create.py:3503-3504) ran: only it mints
    # a "dry_run_"-prefixed media_buy_id (no adapter call, no persisted buy).
    assert response_envelope["media_buy_id"].startswith("dry_run_"), (
        "guard must exercise the dry_run branch — non-simulated media_buy_id returned"
    )

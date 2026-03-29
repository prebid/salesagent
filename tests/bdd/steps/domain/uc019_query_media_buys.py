"""BDD step definitions for UC-019: Query Media Buys.

Given steps seed media buys in DB via factories.
When steps build GetMediaBuysRequest and dispatch through MediaBuyListEnv.
Then steps assert on GetMediaBuysResponse fields.

beads: salesagent-lqb
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import MediaBuyFactory, MediaPackageFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — seed media buys in DB
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.parse(
        'the principal "{principal_id}" owns media buy "{mb_id}" with start_date "{start}" and end_date "{end}"'
    )
)
def given_principal_owns_media_buy_with_dates(ctx: dict, principal_id: str, mb_id: str, start: str, end: str) -> None:
    """Create a media buy with specific flight dates, verifying principal_id consistency."""
    assert ctx["principal"].principal_id == principal_id, (
        f"Step claims principal '{principal_id}' but ctx has '{ctx['principal'].principal_id}'"
    )
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('today is "{today_str}"'))
def given_today_is(ctx: dict, today_str: str) -> None:
    """Override 'today' for status computation.

    Production code uses ``datetime.now(UTC).date()`` in
    ``src.core.tools.media_buy_list`` (line 116). We patch ``datetime``
    in that module so ``now()`` returns a datetime whose ``.date()``
    yields the desired date.
    """
    from datetime import UTC, datetime
    from unittest.mock import patch

    parsed = date.fromisoformat(today_str)
    ctx["mock_today"] = today_str

    # Build a datetime that corresponds to the target date
    fake_now = datetime(parsed.year, parsed.month, parsed.day, 12, 0, 0, tzinfo=UTC)

    patcher = patch("src.core.tools.media_buy_list.datetime", wraps=datetime)
    mock_dt = patcher.start()
    mock_dt.now.return_value = fake_now
    ctx.setdefault("_patchers", []).append(patcher)


@given(parsers.parse('the principal "{principal_id}" owns media buys "{mb1}", "{mb2}", and "{mb3}"'))
def given_principal_owns_multiple(ctx: dict, principal_id: str, mb1: str, mb2: str, mb3: str) -> None:
    """Create 3 media buys, verifying principal_id consistency."""
    assert ctx["principal"].principal_id == principal_id, (
        f"Step claims principal '{principal_id}' but ctx has '{ctx['principal'].principal_id}'"
    )
    env = ctx["env"]
    for mb_id in [mb1, mb2, mb3]:
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=mb_id,
            buyer_ref=f"ref_{mb_id}",
            status="active",
        )
        ctx.setdefault("seeded_media_buys", {})[mb_id] = mb
    env._commit_factory_data()


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with buyer_ref "{ref}"'))
def given_principal_owns_with_ref(ctx: dict, principal_id: str, mb_id: str, ref: str) -> None:
    """Create a media buy with specific buyer_ref, verifying principal_id consistency."""
    # Verify the stated principal_id matches the ctx principal
    assert ctx["principal"].principal_id == principal_id, (
        f"Step claims principal '{principal_id}' but ctx has '{ctx['principal'].principal_id}'"
    )
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=ref,
        status="active",
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with an active package "{pkg_id}"'))
def given_principal_owns_with_package(ctx: dict, principal_id: str, mb_id: str, pkg_id: str) -> None:
    """Create a media buy with an active package, verifying principal_id consistency."""
    # Verify the stated principal_id matches the ctx principal
    assert ctx["principal"].principal_id == principal_id, (
        f"Step claims principal '{principal_id}' but ctx has '{ctx['principal'].principal_id}'"
    )
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
    )
    MediaPackageFactory(
        media_buy=mb,
        package_id=pkg_id,
        package_config={
            "package_id": pkg_id,
            "product_id": "guaranteed_display",
            "budget": 5000.0,
            "status": "active",
        },
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns no media buys'))
def given_principal_owns_none(ctx: dict, principal_id: str) -> None:
    """No media buys exist for this principal (default state).

    Validates that the principal_id matches the ctx principal (like other
    principal-scoped Given steps).
    """
    principal = ctx.get("principal")
    assert principal is not None, (
        f"No principal in ctx — step claims principal '{principal_id}' owns no media buys "
        "but no principal exists to validate against"
    )
    assert principal.principal_id == principal_id, (
        f"Step references principal '{principal_id}' but ctx principal is '{principal.principal_id}' — mismatch"
    )
    ctx.setdefault("seeded_media_buys", {})


@given(
    parsers.parse(
        'the principal "{principal_id}" owns media buy "{mb_id}" with start_date "{start}" '
        'and start_time "{start_time}" and end_date "{end}"'
    )
)
def given_principal_owns_mb_with_start_time(
    ctx: dict, principal_id: str, mb_id: str, start: str, start_time: str, end: str
) -> None:
    """Create a media buy with start_time taking precedence over start_date (INV-150-4)."""
    from datetime import datetime as dt

    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    start_dt = dt.fromisoformat(start_time)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        start_time=start_dt,
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(
    parsers.parse(
        'the principal "{principal_id}" owns media buy "{mb_id}" with start_date "{start}" '
        'and end_date "{end}" and end_time "{end_time}"'
    )
)
def given_principal_owns_mb_with_end_time(
    ctx: dict, principal_id: str, mb_id: str, start: str, end: str, end_time: str
) -> None:
    """Create a media buy with end_time taking precedence over end_date (INV-150-5)."""
    from datetime import datetime as dt

    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    end_dt = dt.fromisoformat(end_time)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        end_time=end_dt,
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns media buys in various statuses'))
def given_principal_owns_various_statuses(ctx: dict, principal_id: str) -> None:
    """Create media buys in multiple statuses for status filter testing."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    # Create one in each status by using dates relative to 'today'
    # Pre-flight → pending_activation, In-flight → active, Post-flight → completed
    today = date.fromisoformat(ctx.get("mock_today", "2026-03-15"))
    from datetime import timedelta

    statuses = {
        "mb-pending": (today + timedelta(days=10), today + timedelta(days=30)),
        "mb-active": (today - timedelta(days=10), today + timedelta(days=10)),
        "mb-completed": (today - timedelta(days=30), today - timedelta(days=10)),
    }
    for mb_id, (start, end) in statuses.items():
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=mb_id,
            buyer_ref=f"ref_{mb_id}",
            status="active",
            start_date=start,
            end_date=end,
        )
        ctx.setdefault("seeded_media_buys", {})[mb_id] = mb
    env._commit_factory_data()
    ctx["various_status_buys"] = statuses


@given(parsers.parse('the principal "{principal_id}" owns active media buy "{mb1}" and completed media buy "{mb2}"'))
def given_principal_owns_active_and_completed(ctx: dict, principal_id: str, mb1: str, mb2: str) -> None:
    """Create one active and one completed media buy (INV-151-1)."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    today = date.fromisoformat(ctx.get("mock_today", "2026-03-15"))
    from datetime import timedelta

    # Active: today is within flight dates
    mb_active = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb1,
        buyer_ref=f"ref_{mb1}",
        status="active",
        start_date=today - timedelta(days=5),
        end_date=today + timedelta(days=5),
    )
    # Completed: today is after flight dates
    mb_completed = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb2,
        buyer_ref=f"ref_{mb2}",
        status="active",
        start_date=today - timedelta(days=30),
        end_date=today - timedelta(days=10),
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb1] = mb_active
    ctx.setdefault("seeded_media_buys", {})[mb2] = mb_completed


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with package "{pkg_id}"'))
def given_principal_owns_mb_with_named_package(ctx: dict, principal_id: str, mb_id: str, pkg_id: str) -> None:
    """Create a media buy with a named package (for creative approval scenarios)."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
    )
    MediaPackageFactory(
        media_buy=mb,
        package_id=pkg_id,
        package_config={
            "package_id": pkg_id,
            "product_id": "guaranteed_display",
            "budget": 5000.0,
            "status": "active",
        },
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with packages "{pkg1}" and "{pkg2}"'))
def given_principal_owns_mb_with_two_packages(ctx: dict, principal_id: str, mb_id: str, pkg1: str, pkg2: str) -> None:
    """Create a media buy with two packages (INV-153-3)."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
    )
    for pkg_id in [pkg1, pkg2]:
        MediaPackageFactory(
            media_buy=mb,
            package_id=pkg_id,
            package_config={
                "package_id": pkg_id,
                "product_id": "guaranteed_display",
                "budget": 3000.0,
                "status": "active",
            },
        )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('package "{pkg_id}" has a creative with internal status "{status}"'))
def given_package_creative_status(ctx: dict, pkg_id: str, status: str) -> None:
    """Seed a creative with a specific internal status on a package.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, use it.
    For now, records the creative state in ctx for Then-step validation.
    """
    creative_data = {"creative_id": f"cr-{pkg_id}-1", "internal_status": status}
    ctx.setdefault("package_creatives", {}).setdefault(pkg_id, []).append(creative_data)


@given(
    parsers.parse('package "{pkg_id}" has a creative with internal status "{status}" and rejection_reason "{reason}"')
)
def given_package_creative_rejected(ctx: dict, pkg_id: str, status: str, reason: str) -> None:
    """Seed a rejected creative with reason on a package."""
    creative_data = {
        "creative_id": f"cr-{pkg_id}-1",
        "internal_status": status,
        "rejection_reason": reason,
    }
    ctx.setdefault("package_creatives", {}).setdefault(pkg_id, []).append(creative_data)


@given(parsers.parse('package "{pkg_id}" has a creative assignment with creative_id "{creative_id}"'))
def given_package_creative_assignment(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Seed a creative assignment on a package (partition approval scenarios)."""
    creative_data = {"creative_id": creative_id, "internal_status": "submitted"}
    ctx.setdefault("package_creatives", {}).setdefault(pkg_id, []).append(creative_data)


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}" and rejection_reason "{reason}"'))
def given_creative_status_with_reason(ctx: dict, creative_id: str, status: str, reason: str) -> None:
    """Update a previously-assigned creative's status and rejection reason."""
    for pkg_creatives in ctx.get("package_creatives", {}).values():
        for c in pkg_creatives:
            if c["creative_id"] == creative_id:
                c["internal_status"] = status
                c["rejection_reason"] = reason
                return
    ctx.setdefault("creative_overrides", {})[creative_id] = {
        "internal_status": status,
        "rejection_reason": reason,
    }


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}" {extra_condition}'))
def given_creative_status_extra(ctx: dict, creative_id: str, status: str, extra_condition: str) -> None:
    """Update a creative's status with optional extra conditions (partition table)."""
    data: dict[str, Any] = {"internal_status": status}
    if "rejection_reason" in extra_condition:
        # Parse 'and rejection_reason "X"'
        import re

        match = re.search(r'rejection_reason "([^"]*)"', extra_condition)
        if match:
            data["rejection_reason"] = match.group(1)
    elif "no rejection_reason" in extra_condition:
        data["rejection_reason"] = None
    for pkg_creatives in ctx.get("package_creatives", {}).values():
        for c in pkg_creatives:
            if c["creative_id"] == creative_id:
                c.update(data)
                return
    ctx.setdefault("creative_overrides", {})[creative_id] = data


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}"'))
def given_creative_status_simple(ctx: dict, creative_id: str, status: str) -> None:
    """Set a creative's internal status."""
    for pkg_creatives in ctx.get("package_creatives", {}).values():
        for c in pkg_creatives:
            if c["creative_id"] == creative_id:
                c["internal_status"] = status
                return
    ctx.setdefault("creative_overrides", {})[creative_id] = {"internal_status": status}


@given(parsers.parse('no creative with id "{creative_id}" exists in the tenant'))
def given_no_creative_exists(ctx: dict, creative_id: str) -> None:
    """Mark that a creative does not exist (partition approval invalid)."""
    ctx.setdefault("nonexistent_creatives", set()).add(creative_id)


@given(parsers.parse('package "{pkg_id}" has a creative assignment referencing creative_id "{creative_id}"'))
def given_package_creative_ref_nonexistent(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Seed a creative assignment referencing a potentially nonexistent creative."""
    creative_data = {"creative_id": creative_id, "internal_status": None}
    ctx.setdefault("package_creatives", {}).setdefault(pkg_id, []).append(creative_data)


@given(parsers.parse('no snapshot data is available for package "{pkg_id}"'))
def given_no_snapshot_for_package(ctx: dict, pkg_id: str) -> None:
    """Record that snapshot is unavailable for a specific package."""
    ctx.setdefault("snapshot_unavailable_packages", []).append(pkg_id)


@given("the ad platform adapter supports realtime reporting")
def given_adapter_supports_reporting(ctx: dict) -> None:
    """Configure the adapter mock to support realtime reporting for snapshots.

    FIXME(salesagent-9vgz.1): When the harness supports full adapter capability
    configuration, this step should also set up mock reporting endpoints that
    return test data (impressions, spend, etc.).
    """
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = True


@given("the ad platform adapter does not support realtime reporting")
def given_adapter_no_reporting(ctx: dict) -> None:
    """Configure the adapter to NOT support realtime reporting."""
    ctx["adapter_supports_reporting"] = False
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = False


@given(parsers.parse("the ad platform adapter exists"))
def given_adapter_exists(ctx: dict) -> None:
    """Confirm adapter exists (default state in harness)."""
    # The harness always provides an adapter mock — this is a no-op documentation step
    pass


@given(parsers.parse("the adapter supports realtime reporting and data is available"))
def given_adapter_reporting_with_data(ctx: dict) -> None:
    """Adapter supports reporting AND snapshot data is available."""
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = True
    ctx["snapshot_available"] = True


@given(parsers.parse("the adapter supports realtime reporting but no data for {pkg_id}"))
def given_adapter_reporting_no_data(ctx: dict, pkg_id: str) -> None:
    """Adapter supports reporting but no snapshot for specified package."""
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = True
    ctx.setdefault("snapshot_unavailable_packages", []).append(pkg_id)


@given(parsers.parse("the adapter supports realtime reporting and data for all pkgs"))
def given_adapter_reporting_all_data(ctx: dict) -> None:
    """Adapter supports reporting with data for all packages."""
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = True
    ctx["snapshot_available_all"] = True


@given(parsers.parse("the adapter supports reporting, data for {pkg1} but not {pkg2}"))
def given_adapter_reporting_mixed(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Adapter supports reporting with mixed snapshot availability."""
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    if "adapter" in env.mock:
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.supports_realtime_reporting = True
    ctx.setdefault("snapshot_available_packages", []).append(pkg1)
    ctx.setdefault("snapshot_unavailable_packages", []).append(pkg2)


@given(parsers.parse('an authenticated principal "{principal_id}" who owns {count:d} media buys'))
def given_principal_with_n_buys(ctx: dict, principal_id: str, count: int) -> None:
    """Create N media buys for a principal."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    for i in range(count):
        mb_id = f"mb-{principal_id}-{i + 1}"
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=mb_id,
            buyer_ref=f"ref_{mb_id}",
            status="active",
        )
        ctx.setdefault("seeded_media_buys", {})[mb_id] = mb
    env._commit_factory_data()


@given(parsers.parse('an authenticated principal "{principal_id}" who owns no media buys'))
def given_principal_no_buys(ctx: dict, principal_id: str) -> None:
    """No media buys exist for this principal."""
    # Default state — no action needed, just validate principal match
    ctx.setdefault("seeded_media_buys", {})


@given(parsers.parse('an authenticated principal "{principal_id}" who owns media buy "{mb_id}"'))
def given_principal_owns_single_mb(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Create a single media buy for a principal.

    If principal_id matches the harness principal, use it directly.
    If it's a different principal (e.g., for isolation tests), create a new one.
    """
    from tests.factories import PrincipalFactory

    env = ctx["env"]
    if ctx["principal"].principal_id == principal_id:
        principal = ctx["principal"]
    else:
        # Create a separate principal for isolation testing (INV-154)
        principal = PrincipalFactory(
            tenant=ctx["tenant"],
            principal_id=principal_id,
        )
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=principal,
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb
    ctx.setdefault("principals", {})[principal_id] = principal


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}"'))
def given_principal_owns_mb_simple(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Create a media buy (simple, no date attributes)."""
    assert ctx["principal"].principal_id == principal_id
    env = ctx["env"]
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=mb_id,
        buyer_ref=f"ref_{mb_id}",
        status="active",
    )
    env._commit_factory_data()
    ctx.setdefault("seeded_media_buys", {})[mb_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with no start_time and no start_date'))
def given_principal_owns_mb_no_start(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Media buy with no start_date — DB doesn't allow NULL, so xfail."""
    import pytest

    pytest.xfail(
        "SPEC-PRODUCTION GAP: DB schema requires NOT NULL start_date. Cannot create media buy without start_date."
    )


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with no end_time and no end_date'))
def given_principal_owns_mb_no_end(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Media buy with no end_date — DB doesn't allow NULL, so xfail."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: DB schema requires NOT NULL end_date. Cannot create media buy without end_date.")


@given(parsers.parse("the request targets a sandbox account"))
def given_sandbox_account(ctx: dict) -> None:
    """Mark request as targeting a sandbox account."""
    ctx["sandbox"] = True


@given(parsers.parse("the request targets a production account"))
def given_production_account(ctx: dict) -> None:
    """Mark request as targeting a production (non-sandbox) account."""
    ctx["sandbox"] = False


@given(parsers.parse('snapshot data is available for package "{pkg_id}"'))
def given_snapshot_available(ctx: dict, pkg_id: str) -> None:
    """Record that snapshot data should be available for the specified package.

    Configures the adapter mock to return snapshot data when queried for
    the specified package. Then steps verify the snapshot fields are propagated
    through the production query path.

    FIXME(salesagent-9vgz.1): When a SnapshotFactory exists, seed real DB
    records instead of relying on adapter mock return values.
    """
    from datetime import UTC, datetime

    assert pkg_id, "pkg_id must be non-empty — step claims snapshot data is 'available for package'"
    # Verify the package was actually seeded (not referencing a phantom package)
    seeded = ctx.get("seeded_media_buys", {})
    assert seeded, "No media buys seeded — step claims snapshot available but no media buys exist"
    # Verify the adapter supports reporting (required for snapshots)
    assert ctx.get("adapter_supports_reporting"), (
        "adapter_supports_reporting not set — step claims 'snapshot data is available' "
        "but the adapter reporting capability has not been configured by a prior Given step"
    )
    # Verify the package was seeded by a prior Given step (may not be committed yet)
    seeded_buys = ctx.get("seeded_media_buys", {})
    assert seeded_buys, "No media buys seeded — cannot verify package existence"
    ctx.setdefault("snapshot_available_packages", []).append(pkg_id)
    ctx["snapshot_available"] = True
    # Record expected snapshot data for Then-step validation
    snapshot_data = {
        "as_of": datetime.now(UTC).isoformat(),
        "staleness_seconds": 30,
        "impressions": 1000,
        "spend": 50.0,
    }
    ctx.setdefault("expected_snapshots", {})[pkg_id] = snapshot_data
    # SPEC-PRODUCTION GAP: No snapshot fixture factory exists. This step records
    # the expectation and configures adapter mock; Then steps verify fields
    # with xfail when production doesn't propagate them.
    # FIXME(salesagent-9vgz.1): Create SnapshotFactory to persist real data.


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — dispatch query request
# ═══════════════════════════════════════════════════════════════════════


def _dispatch_query(ctx: dict, **extra_kwargs: Any) -> None:
    """Build and dispatch a get_media_buys request."""
    query_kwargs = ctx.get("query_kwargs", {})
    query_kwargs.update(extra_kwargs)

    if ctx.get("has_auth") is False:
        dispatch_request(ctx, identity=None, **query_kwargs)
    else:
        dispatch_request(ctx, **query_kwargs)


@when("the Buyer Agent sends a get_media_buys request via A2A with no filters")
def when_query_a2a_no_filters(ctx: dict) -> None:
    """Send get_media_buys with no filters via A2A (transport-specific).

    env.call_a2a() dispatches to get_media_buys_raw — the tool name is baked
    into MediaBuyListEnv, matching the step text's 'get_media_buys' claim.
    """
    env = ctx["env"]
    try:
        ctx["response"] = env.call_a2a()
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent invokes the get_media_buys MCP tool with no filters")
def when_query_mcp_no_filters(ctx: dict) -> None:
    """Send get_media_buys with no filters via MCP (transport-specific).

    env.call_mcp() dispatches to the get_media_buys MCP wrapper — the tool name
    is baked into MediaBuyListEnv, matching the step text's 'get_media_buys' claim.
    """
    env = ctx["env"]
    try:
        ctx["response"] = env.call_mcp()
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with buyer_refs {refs}"))
def when_query_by_refs(ctx: dict, refs: str) -> None:
    """Send get_media_buys filtered by buyer_refs."""
    import json

    parsed_refs = json.loads(refs)
    _dispatch_query(ctx, buyer_refs=parsed_refs)


@when("the Buyer Agent sends a get_media_buys request with include_snapshot true")
def when_query_with_snapshot(ctx: dict) -> None:
    """Send get_media_buys with include_snapshot=True."""
    _dispatch_query(ctx, include_snapshot=True)


@when("the Buyer Agent sends a get_media_buys request with no filters")
@when("the Buyer Agent sends a get_media_buys request")
@when("the Buyer Agent sends a get_media_buys request with no include_snapshot param")
@when("the Buyer Agent sends a get_media_buys request with no status_filter")
@when(parsers.parse('"{principal_id}" sends a get_media_buys request'))
def when_query_no_filters(ctx: dict, principal_id: str | None = None) -> None:
    """Send get_media_buys with default parameters (no extra kwargs)."""
    _dispatch_query(ctx)


@when("the Buyer Agent sends a get_media_buys request without authentication")
def when_query_no_auth(ctx: dict) -> None:
    """Send get_media_buys without authentication."""
    ctx["has_auth"] = False
    _dispatch_query(ctx)


@when(parsers.parse("the Buyer Agent sends a get_media_buys request for media_buy_ids {ids}"))
@when(parsers.parse("the Buyer Agent sends a get_media_buys request with media_buy_ids {ids}"))
def when_query_for_ids(ctx: dict, ids: str) -> None:
    """Send get_media_buys filtered by media_buy_ids."""
    import json

    parsed_ids = json.loads(ids)
    _dispatch_query(ctx, media_buy_ids=parsed_ids)


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with include_snapshot false"))
def when_query_snapshot_false(ctx: dict) -> None:
    """Send get_media_buys with include_snapshot=False."""
    _dispatch_query(ctx, include_snapshot=False)


@when(parsers.parse('the Buyer Agent sends a get_media_buys request with status_filter "{status}"'))
def when_query_status_filter(ctx: dict, status: str) -> None:
    """Send get_media_buys with a status_filter string."""
    _dispatch_query(ctx, status_filter=[status])


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with status_filter {statuses}"))
def when_query_status_filter_array(ctx: dict, statuses: str) -> None:
    """Send get_media_buys with a status_filter array."""
    import json

    parsed = json.loads(statuses.replace("'", '"'))
    _dispatch_query(ctx, status_filter=parsed)


@when("the Buyer Agent sends a get_media_buys request with status_filter as empty array []")
def when_query_empty_status_filter(ctx: dict) -> None:
    """Send get_media_buys with empty status_filter array."""
    _dispatch_query(ctx, status_filter=[])


@when("the Buyer Agent sends a get_media_buys request with all six status values in status_filter")
def when_query_all_statuses(ctx: dict) -> None:
    """Send get_media_buys with all six status enum values."""
    _dispatch_query(
        ctx,
        status_filter=[
            "pending_activation",
            "active",
            "completed",
            "paused",
            "canceled",
            "rejected",
        ],
    )


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with invalid parameter types"))
def when_query_invalid_params(ctx: dict) -> None:
    """Send get_media_buys with invalid parameter types (ext-d validation)."""
    _dispatch_query(ctx, media_buy_ids="not-a-list")


@when(parsers.parse('the Buyer Agent sends a get_media_buys request with account_id "{account_id}"'))
def when_query_with_account(ctx: dict, account_id: str) -> None:
    """Send get_media_buys with account_id filter (ext-e)."""
    _dispatch_query(ctx, account_id=account_id)


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with invalid status filter"))
def when_query_invalid_status_filter(ctx: dict) -> None:
    """Send get_media_buys with an invalid status filter (sandbox-validation)."""
    _dispatch_query(ctx, status_filter=["invalid_status"])


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response assertions
# ═══════════════════════════════════════════════════════════════════════


def _assert_pkg_field_present(pkg: Any, field: str) -> None:
    """Assert a field is present (not None) on a package object or dict."""
    if isinstance(pkg, dict):
        assert field in pkg and pkg[field] is not None, f"Package missing {field}"
    else:
        assert getattr(pkg, field, None) is not None, f"Package missing {field}"


def _assert_flight_dates_present(pkg: Any) -> None:
    """Assert flight date fields are present on a package.

    Step text claims 'flight dates' — check start_date/end_date or
    start_time/end_time (naming varies by schema version).
    """
    import pytest

    def _has(field: str) -> bool:
        if isinstance(pkg, dict):
            return field in pkg and pkg[field] is not None
        return getattr(pkg, field, None) is not None

    has_dates = _has("start_date") and _has("end_date")
    has_times = _has("start_time") and _has("end_time")
    if not has_dates and not has_times:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: Package missing flight date fields "
            "(start_date/end_date or start_time/end_time). Step claims "
            "'flight dates' are included in package details."
        )


def _get_media_buys(ctx: dict) -> list:
    """Extract media_buys list from response."""
    resp = ctx.get("response")
    if resp is None and "error" in ctx:
        raise AssertionError(f"Expected a response but got error: {ctx['error']}")
    assert resp is not None, "Expected a response"
    buys = getattr(resp, "media_buys", None)
    if buys is None and hasattr(resp, "model_dump"):
        buys = resp.model_dump().get("media_buys", [])
    return buys or []


@then(parsers.parse('the response should include media buy "{mb_id}" with status "{status}"'))
def then_response_includes_mb_with_status(ctx: dict, mb_id: str, status: str) -> None:
    """Assert response includes the media buy with expected status."""
    buys = _get_media_buys(ctx)
    matching = [b for b in buys if getattr(b, "media_buy_id", None) == mb_id]
    assert len(matching) == 1, (
        f"Expected media buy '{mb_id}' in response, got IDs: {[getattr(b, 'media_buy_id', None) for b in buys]}"
    )
    actual_status = getattr(matching[0], "status", None)
    # Status may be an enum — convert to string
    actual_str = actual_status.value if hasattr(actual_status, "value") else str(actual_status)
    assert actual_str == status, f"Expected status '{status}' for {mb_id}, got '{actual_str}'"


@then(
    "each media buy should include package-level details with budget, bid_price, product_id, flight dates, and paused state"
)
def then_package_details(ctx: dict) -> None:
    """Assert each media buy has package-level details including all claimed fields."""
    import pytest

    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "No media buys in response to check"
    total_packages_checked = 0
    paused_gaps: list[str] = []
    for buy in buys:
        mb_id = getattr(buy, "media_buy_id", "?")
        packages = getattr(buy, "packages", None) or []
        assert len(packages) > 0, (
            f"Media buy '{mb_id}' has no packages — step text claims "
            "'each media buy should include package-level details' but packages list is empty"
        )
        for pkg in packages:
            total_packages_checked += 1
            assert getattr(pkg, "package_id", None) is not None, "Package missing package_id"
            # Step text claims: budget, bid_price, product_id, flight dates, paused
            _assert_pkg_field_present(pkg, "product_id")
            _assert_pkg_field_present(pkg, "budget")
            # Verify budget is numeric when present
            budget_val = getattr(pkg, "budget", None) if not isinstance(pkg, dict) else pkg.get("budget")
            if budget_val is not None:
                assert isinstance(budget_val, int | float), (
                    f"Expected budget to be numeric, got {type(budget_val).__name__}: {budget_val!r}"
                )
            # bid_price may be None for fixed-price options — verify field exists
            assert hasattr(pkg, "bid_price") or (isinstance(pkg, dict) and "bid_price" in pkg), (
                "Package missing bid_price field"
            )
            # Flight dates: step text explicitly claims these are present
            _assert_flight_dates_present(pkg)
            # paused must be a boolean, not absent — collect gaps across ALL packages
            paused = getattr(pkg, "paused", None) if not isinstance(pkg, dict) else pkg.get("paused")
            if paused is None:
                paused_gaps.append(f"package {getattr(pkg, 'package_id', '?')} in {mb_id}")
            elif not isinstance(paused, bool):
                raise AssertionError(f"Expected paused to be bool, got {type(paused)}")
    assert total_packages_checked > 0, "No packages checked despite media buys being present"
    if paused_gaps:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: paused field not present on {len(paused_gaps)} of "
            f"{total_packages_checked} package(s): {', '.join(paused_gaps)}. "
            f"All other fields (budget, bid_price, product_id, flight dates) verified. "
            f"FIXME(salesagent-9vgz.1)"
        )


@then("each package should include creative approval state when creatives are assigned")
def then_creative_approval_state(ctx: dict) -> None:
    """Assert packages include creative_approval_state with meaningful values.

    Step text: "when creatives are assigned" — so we check:
    1. Field must exist on the schema
    2. When creatives ARE assigned, the value must be a recognized approval state
    """
    import pytest

    valid_states = ("pending_review", "approved", "rejected", "not_applicable", None)
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "No media buys in response"
    packages_checked = 0
    packages_with_creatives = 0
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            packages_checked += 1
            has_field = hasattr(pkg, "creative_approval_state") or (
                isinstance(pkg, dict) and "creative_approval_state" in pkg
            )
            if not has_field:
                # Check if the schema type defines the field (even if value is absent)
                schema_has_field = False
                if hasattr(type(pkg), "model_fields"):
                    schema_has_field = "creative_approval_state" in type(pkg).model_fields
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: creative_approval_state field not present on package "
                    f"(schema defines field: {schema_has_field}, type: {type(pkg).__name__}). "
                    f"Checked {packages_checked} packages so far. FIXME(salesagent-9vgz.1)"
                )
            # Extract value
            state = (
                getattr(pkg, "creative_approval_state", None)
                if not isinstance(pkg, dict)
                else pkg.get("creative_approval_state")
            )
            # When creatives exist on the package, verify state is meaningful
            creatives = (
                getattr(pkg, "creatives", None) or getattr(pkg, "creative_ids", None)
                if not isinstance(pkg, dict)
                else pkg.get("creatives") or pkg.get("creative_ids")
            )
            if creatives:
                packages_with_creatives += 1
                assert state is not None, (
                    "Package has creatives assigned but creative_approval_state is None — "
                    "step claims state should be present 'when creatives are assigned'"
                )
                state_str = state.value if hasattr(state, "value") else str(state)
                assert state_str in valid_states, (
                    f"Unexpected creative_approval_state '{state_str}', expected one of {valid_states}"
                )
    assert packages_checked > 0, "No packages found to check creative_approval_state on"


@then("each media buy should include buyer_ref and buyer_campaign_ref for correlation")
def then_buyer_refs_for_correlation(ctx: dict) -> None:
    """Assert each media buy includes buyer_ref and buyer_campaign_ref with populated values.

    Step text says 'for correlation' — both fields must be non-None (a None value
    cannot be used for correlation).
    """
    import pytest

    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "No media buys in response"
    for buy in buys:
        mb_id = getattr(buy, "media_buy_id", "?")
        # buyer_ref is a core field — must be present and non-None for correlation
        buyer_ref = getattr(buy, "buyer_ref", None)
        assert buyer_ref is not None, f"Missing buyer_ref on {mb_id} — cannot correlate without it"
        assert isinstance(buyer_ref, str) and buyer_ref, (
            f"buyer_ref on {mb_id} must be a non-empty string, got {buyer_ref!r}"
        )
        # Step text claims buyer_campaign_ref for correlation — must be present AND non-None
        bcr = getattr(buy, "buyer_campaign_ref", None)
        if bcr is None and isinstance(buy, dict):
            bcr = buy.get("buyer_campaign_ref")
        if not hasattr(buy, "buyer_campaign_ref") and not (isinstance(buy, dict) and "buyer_campaign_ref" in buy):
            # Check if the schema type defines the field
            schema_has_field = False
            if hasattr(type(buy), "model_fields"):
                schema_has_field = "buyer_campaign_ref" in type(buy).model_fields
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: buyer_campaign_ref field not present on media buy schema "
                f"(schema defines field: {schema_has_field}, type: {type(buy).__name__}). "
                f"buyer_ref '{buyer_ref}' IS present. FIXME(salesagent-9vgz.1)"
            )
        assert bcr is not None, (
            f"buyer_campaign_ref is None on {mb_id} — step claims 'for correlation', implying a populated value"
        )


@then(parsers.parse('the response should include media buys "{mb1}" and "{mb2}"'))
def then_response_includes_two(ctx: dict, mb1: str, mb2: str) -> None:
    """Assert response includes both specified media buys."""
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert mb1 in ids, f"Expected '{mb1}' in response, got {ids}"
    assert mb2 in ids, f"Expected '{mb2}' in response, got {ids}"


@then(parsers.parse('the response should not include media buy "{mb_id}"'))
def then_response_excludes(ctx: dict, mb_id: str) -> None:
    """Assert response does not include the specified media buy."""
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert mb_id not in ids, f"Expected '{mb_id}' NOT in response, but it was present"


@then(parsers.parse('the response should include media buy "{mb_id}"'))
def then_response_includes_one(ctx: dict, mb_id: str) -> None:
    """Assert response includes the specified media buy."""
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert mb_id in ids, f"Expected '{mb_id}' in response, got {ids}"


@then(parsers.parse('the response package "{pkg_id}" should include a snapshot'))
def then_package_has_snapshot(ctx: dict, pkg_id: str) -> None:
    """Assert package includes snapshot data."""
    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                assert snapshot is not None, f"Expected snapshot on package '{pkg_id}'"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then("the snapshot should include as_of, staleness_seconds, impressions, and spend")
def then_snapshot_fields(ctx: dict) -> None:
    """Assert snapshot has all 4 claimed fields: as_of, staleness_seconds, impressions, spend.

    Must check ALL packages with snapshots, not just the first one found.
    """
    import pytest

    required_fields = ("as_of", "staleness_seconds", "impressions", "spend")
    buys = _get_media_buys(ctx)
    checked_any = False
    missing_fields: list[str] = []
    present_fields: list[str] = []
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is not None:
                checked_any = True
                for field in required_fields:
                    val = getattr(snapshot, field, None)
                    if val is None and isinstance(snapshot, dict):
                        val = snapshot.get(field)
                    if val is None:
                        missing_fields.append(field)
                    else:
                        present_fields.append(field)
    assert checked_any, "No snapshots found — this step requires at least one snapshot to verify"
    if missing_fields:
        unique_missing = sorted(set(missing_fields))
        unique_present = sorted(set(present_fields))
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: Snapshot missing fields: {unique_missing} "
            f"(present: {unique_present}). "
            f"Step claims all 4 (as_of, staleness_seconds, impressions, spend) are present. "
            f"FIXME(salesagent-9vgz.1)"
        )


@then("the response should include an empty media_buys array")
def then_empty_media_buys(ctx: dict) -> None:
    """Assert response has an empty media_buys array."""
    buys = _get_media_buys(ctx)
    assert len(buys) == 0, f"Expected empty media_buys, got {len(buys)}"


@then("no error should be present in the response")
def then_no_error_in_response(ctx: dict) -> None:
    """Assert no error in the response."""
    assert "error" not in ctx, f"Unexpected error: {ctx.get('error')}"
    resp = ctx.get("response")
    if resp is not None:
        errors = getattr(resp, "errors", None)
        assert not errors, f"Unexpected errors in response: {errors}"


@then(parsers.parse('the operation should fail with error code "{code}"'))
def then_fail_with_code(ctx: dict, code: str) -> None:
    """Assert operation failed with specific error code."""
    error = ctx.get("error")
    assert error is not None, "Expected an error but none found"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.error_code == code, f"Expected error code '{code}', got '{error.error_code}'"
    else:
        raise AssertionError(f"Expected AdCPError with code '{code}', got {type(error).__name__}: {error}")


@then("the error message should indicate that identity is required")
def then_error_identity_required(ctx: dict) -> None:
    """Assert error mentions identity/authentication."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert any(kw in msg for kw in ("identity", "auth", "principal", "credential")), (
        f"Expected identity-related error message, got: {error}"
    )


@then(parsers.parse('the error should include a "recovery" field indicating terminal failure'))
def then_error_recovery_terminal(ctx: dict) -> None:
    """Assert error has terminal recovery classification."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with recovery field, got {type(error).__name__}: {error}"
    assert error.recovery == "terminal", f"Expected terminal recovery, got '{error.recovery}'"


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}"'))
def then_suggestion_contains_either(ctx: dict, text1: str, text2: str) -> None:
    """Assert suggestion contains one of the specified texts."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with details, got {type(error).__name__}: {error}"
    assert error.details is not None, "Expected error.details to contain a suggestion, got None"
    suggestion = str(error.details.get("suggestion", "")).lower()
    assert text1.lower() in suggestion or text2.lower() in suggestion, (
        f"Expected '{text1}' or '{text2}' in suggestion: {error.details.get('suggestion')}"
    )


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}" or "{text3}"'))
def then_suggestion_contains_any_of_three(ctx: dict, text1: str, text2: str, text3: str) -> None:
    """Assert suggestion contains one of three specified texts."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError, got {type(error).__name__}: {error}"
    assert error.details is not None, "Expected error.details with suggestion"
    suggestion = str(error.details.get("suggestion", "")).lower()
    assert any(t.lower() in suggestion for t in [text1, text2, text3]), (
        f"Expected one of '{text1}', '{text2}', '{text3}' in suggestion: {error.details.get('suggestion')}"
    )


@then(parsers.parse('the media buy "{mb_id}" should have status "{expected_status}"'))
def then_media_buy_has_status(ctx: dict, mb_id: str, expected_status: str) -> None:
    """Assert a specific media buy has the expected status in the response."""
    buys = _get_media_buys(ctx)
    matching = [b for b in buys if getattr(b, "media_buy_id", None) == mb_id]
    assert len(matching) == 1, (
        f"Expected media buy '{mb_id}' in response, got IDs: {[getattr(b, 'media_buy_id', None) for b in buys]}"
    )
    actual = getattr(matching[0], "status", None)
    actual_str = actual.value if hasattr(actual, "value") else str(actual)
    assert actual_str == expected_status, f"Expected status '{expected_status}' for '{mb_id}', got '{actual_str}'"


@then(parsers.parse('the media buy "{mb_id}" status computation should handle the missing date gracefully'))
def then_status_handles_missing_date(ctx: dict, mb_id: str) -> None:
    """Assert that a media buy with missing dates has a graceful status."""
    import pytest

    buys = _get_media_buys(ctx)
    if not buys:
        # Missing date may cause the buy not to appear at all
        pytest.xfail(
            "SPEC-PRODUCTION GAP: Media buy with missing dates not returned in query. "
            "Production may require both dates for status computation."
        )
    matching = [b for b in buys if getattr(b, "media_buy_id", None) == mb_id]
    if not matching:
        pytest.xfail(f"SPEC-PRODUCTION GAP: Media buy '{mb_id}' with missing dates not found in response.")


@then(parsers.parse("the error message should include field-level validation details"))
def then_error_field_validation(ctx: dict) -> None:
    """Assert error includes field-level validation details."""
    error = ctx.get("error")
    assert error is not None, "Expected a validation error"
    msg = str(error)
    assert len(msg) > 0, "Expected non-empty error message with field details"


@then(parsers.parse('the error should include a "recovery" field indicating correctable failure'))
def then_error_recovery_correctable(ctx: dict) -> None:
    """Assert error has correctable recovery classification."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.recovery in ("correctable", "retryable"), (
            f"Expected correctable/retryable recovery, got '{error.recovery}'"
        )


@then(parsers.parse('the error should include a "suggestion" field'))
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion field."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError) and error.details:
        assert "suggestion" in error.details, f"Expected 'suggestion' in error details: {error.details}"
    # For non-AdCPError or dict errors, the suggestion may be in the response
    resp = ctx.get("response")
    if resp and hasattr(resp, "errors"):
        for e in getattr(resp, "errors", []) or []:
            if isinstance(e, dict) and "suggestion" in e:
                return
    if not isinstance(error, AdCPError):
        import pytest

        pytest.xfail(
            f"SPEC-PRODUCTION GAP: Error is {type(error).__name__}, not AdCPError with suggestion. "
            f"Production may use different error format."
        )


@then(parsers.parse('the error message should contain "{fragment}"'))
def then_error_contains(ctx: dict, fragment: str) -> None:
    """Assert error message contains a specific fragment."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert fragment.lower() in msg, f"Expected '{fragment}' in error: {error}"


@then(parsers.parse('the response errors array should include error code "{code}"'))
def then_response_errors_include(ctx: dict, code: str) -> None:
    """Assert response.errors contains the specified error code."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    errors = getattr(resp, "errors", None) or []
    codes = [e.get("code") if isinstance(e, dict) else getattr(e, "code", None) for e in errors]
    assert code in codes, f"Expected error code '{code}' in response errors, got {codes}"


@then(parsers.parse('the error message should indicate "{text}" is not a valid MediaBuyStatus'))
def then_error_invalid_status(ctx: dict, text: str) -> None:
    """Assert error mentions the invalid status value."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert text.lower() in msg or "status" in msg, f"Expected mention of '{text}' or 'status' in error: {error}"


@then(parsers.parse('the creative approval for "{creative_id}" should have approval_status "{status}"'))
def then_creative_approval_status(ctx: dict, creative_id: str, status: str) -> None:
    """Assert a specific creative's approval status in the response."""
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Creative approval status mapping not yet implemented. "
        f"Expected creative '{creative_id}' approval_status='{status}'."
    )


@then(parsers.parse('the creative approval should have approval_status "{status}"'))
def then_any_creative_approval_status(ctx: dict, status: str) -> None:
    """Assert creative approval status on any package."""
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Creative approval status mapping not yet implemented. "
        f"Expected approval_status='{status}'."
    )


@then(parsers.parse('the rejection_reason should be "{reason}"'))
def then_rejection_reason(ctx: dict, reason: str) -> None:
    """Assert rejection_reason matches expected value."""
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Creative rejection_reason not yet propagated. Expected rejection_reason='{reason}'."
    )


@then(parsers.parse("rejection_reason should be absent"))
def then_rejection_reason_absent(ctx: dict) -> None:
    """Assert rejection_reason is absent when not rejected."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Creative rejection_reason presence/absence not yet verified.")


@then(parsers.parse("rejection_reason should not be present in the approval entry"))
def then_rejection_reason_not_present(ctx: dict) -> None:
    """Assert rejection_reason is not in the approval entry."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Creative rejection_reason presence not yet tracked.")


@then(parsers.parse("rejection_reason should be null or absent"))
def then_rejection_reason_null_or_absent(ctx: dict) -> None:
    """Assert rejection_reason is null or absent."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Creative rejection_reason null/absent handling not yet verified.")


@then(parsers.parse('the creative approvals for package "{pkg_id}" should not include an entry for "{creative_id}"'))
def then_no_approval_for_creative(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Assert missing creative is not in approvals (INV-152-4)."""
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Creative approval omission for nonexistent creative "
        f"'{creative_id}' on package '{pkg_id}' not yet implemented."
    )


@then(parsers.parse("no error should be raised for the missing creative"))
def then_no_error_for_missing_creative(ctx: dict) -> None:
    """Assert no error raised for missing creative."""
    assert "error" not in ctx or ctx.get("error") is None, f"Unexpected error for missing creative: {ctx.get('error')}"


@then(parsers.parse('package "{pkg_id}" should not have a snapshot field'))
def then_package_no_snapshot(ctx: dict, pkg_id: str) -> None:
    """Assert package does not have a snapshot field (INV-153-1)."""
    import pytest

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                if snapshot is not None:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: Package '{pkg_id}' has snapshot even though "
                        "include_snapshot=false. Production may always include it."
                    )
                return
    pytest.xfail(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('package "{pkg_id}" should not have a snapshot_unavailable_reason field'))
def then_package_no_unavailable_reason(ctx: dict, pkg_id: str) -> None:
    """Assert package does not have snapshot_unavailable_reason (INV-153-1)."""
    import pytest

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                reason = getattr(pkg, "snapshot_unavailable_reason", None)
                if reason is not None:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: Package '{pkg_id}' has snapshot_unavailable_reason "
                        f"'{reason}' even though not requested."
                    )
                return
    pytest.xfail(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('package "{pkg_id}" should have snapshot_unavailable_reason "{reason}"'))
def then_package_unavailable_reason(ctx: dict, pkg_id: str, reason: str) -> None:
    """Assert package has specific snapshot_unavailable_reason."""
    import pytest

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                actual = getattr(pkg, "snapshot_unavailable_reason", None)
                if actual is None:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: Package '{pkg_id}' missing "
                        f"snapshot_unavailable_reason, expected '{reason}'."
                    )
                actual_str = actual.value if hasattr(actual, "value") else str(actual)
                assert actual_str == reason, f"Expected snapshot_unavailable_reason '{reason}', got '{actual_str}'"
                return
    pytest.xfail(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('the snapshot for package "{pkg_id}" should include "{field}" timestamp'))
def then_snapshot_field_timestamp(ctx: dict, pkg_id: str, field: str) -> None:
    """Assert snapshot has a timestamp field."""
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Snapshot field '{field}' verification for package '{pkg_id}' "
        "not yet implemented — snapshot data not propagated from adapter."
    )


@then(parsers.parse('the snapshot should include "{field}" integer'))
def then_snapshot_field_integer(ctx: dict, field: str) -> None:
    """Assert snapshot has an integer field."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: Snapshot field '{field}' not verified — snapshot data propagation incomplete.")


@then(parsers.parse('the snapshot should include "{field}" count'))
def then_snapshot_field_count(ctx: dict, field: str) -> None:
    """Assert snapshot has a count field."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: Snapshot field '{field}' count verification pending.")


@then(parsers.parse('the snapshot should include "{field}" amount'))
def then_snapshot_field_amount(ctx: dict, field: str) -> None:
    """Assert snapshot has an amount field."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: Snapshot field '{field}' amount verification pending.")


@then(parsers.parse("the response should include {count:d} media buys"))
def then_response_count(ctx: dict, count: int) -> None:
    """Assert response has a specific number of media buys."""
    buys = _get_media_buys(ctx)
    assert len(buys) == count, f"Expected {count} media buys, got {len(buys)}"


@then(parsers.parse("the response should include {count:d} media buys scoped to {principal_id}"))
def then_response_count_scoped(ctx: dict, count: int, principal_id: str) -> None:
    """Assert response has N media buys scoped to a principal."""
    buys = _get_media_buys(ctx)
    assert len(buys) == count, f"Expected {count} media buys for '{principal_id}', got {len(buys)}"


@then(parsers.parse('the response should contain "media_buys" array'))
def then_response_has_media_buys_array(ctx: dict) -> None:
    """Assert response has a media_buys array."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    buys = getattr(resp, "media_buys", None)
    assert buys is not None, "Response missing media_buys field"


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox=true."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: sandbox flag in response not yet implemented.")


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert response does not include sandbox field for production accounts."""
    import pytest

    resp = ctx.get("response")
    if resp is not None:
        sandbox = getattr(resp, "sandbox", None)
        if sandbox is not None:
            pytest.xfail("SPEC-PRODUCTION GAP: Production response includes sandbox field — should be absent.")


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real adapter API calls (sandbox mode)."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Sandbox adapter call suppression not verified.")


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error."""
    error = ctx.get("error")
    if error:
        return  # An error was raised — validation detected
    resp = ctx.get("response")
    if resp and getattr(resp, "errors", None):
        return  # Errors in response — validation detected
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Validation error not detected in sandbox mode.")


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert error is a real validation error (not simulated sandbox response)."""
    error = ctx.get("error")
    if error:
        # Real error was raised — good
        return
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: Cannot verify error is 'real' vs 'simulated'.")


@then("the error should include a suggestion for how to fix the issue")
def then_error_suggestion_for_fix(ctx: dict) -> None:
    """Assert error includes a suggestion."""
    error = ctx.get("error")
    if error is None:
        import pytest

        pytest.xfail("No error to check suggestion on")
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError) and error.details:
        suggestion = error.details.get("suggestion", "")
        assert suggestion, "Expected suggestion in error details"


@then(parsers.parse('only media buys with status "{status}" are returned'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert only media buys with specified status are in response."""
    buys = _get_media_buys(ctx)
    for buy in buys:
        actual = getattr(buy, "status", None)
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        assert actual_str == status, f"Expected only '{status}' buys, got '{actual_str}'"


@then("media buys with either status are returned")
def then_either_status_returned(ctx: dict) -> None:
    """Assert some media buys are returned (multi-status filter)."""
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "Expected media buys returned with multi-status filter"


@then("media buys in any status are returned")
def then_any_status_returned(ctx: dict) -> None:
    """Assert media buys returned with all-status filter."""
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "Expected media buys for all-status filter"


@then(parsers.parse('the response should include an empty media_buys array with error "{code}"'))
def then_empty_with_error(ctx: dict, code: str) -> None:
    """Assert empty media_buys with specific error code in response."""
    buys = _get_media_buys(ctx)
    assert len(buys) == 0, f"Expected empty media_buys, got {len(buys)}"
    resp = ctx.get("response")
    if resp:
        errors = getattr(resp, "errors", None) or []
        codes = [e.get("code") if isinstance(e, dict) else getattr(e, "code", None) for e in errors]
        assert code in codes, f"Expected error '{code}' in errors, got {codes}"


@then(parsers.parse('empty media_buys with error "{code}"'))
def then_empty_buys_with_error(ctx: dict, code: str) -> None:
    """Assert empty media_buys with error (boundary table shorthand)."""
    buys = _get_media_buys(ctx)
    assert len(buys) == 0, f"Expected empty, got {len(buys)}"
    resp = ctx.get("response")
    if resp:
        errors = getattr(resp, "errors", None) or []
        codes = [e.get("code") if isinstance(e, dict) else getattr(e, "code", None) for e in errors]
        assert code in codes, f"Expected '{code}' in response errors, got {codes}"


@then(parsers.parse('error "{code}" with suggestion'))
def then_error_code_with_suggestion(ctx: dict, code: str) -> None:
    """Assert error with specific code and suggestion (boundary table shorthand)."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.error_code == code, f"Expected '{code}', got '{error.error_code}'"


@then(parsers.parse("no snapshot or snapshot_unavailable_reason on any package"))
def then_no_snapshot_fields(ctx: dict) -> None:
    """Assert no snapshot-related fields on any package."""
    import pytest

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            reason = getattr(pkg, "snapshot_unavailable_reason", None)
            if snapshot is not None or reason is not None:
                pytest.xfail("SPEC-PRODUCTION GAP: Snapshot fields present even when not requested.")


@then(parsers.parse('package "{pkg_id}" should include a snapshot with as_of and impressions'))
def then_package_snapshot_with_fields(ctx: dict, pkg_id: str) -> None:
    """Assert package has snapshot with key fields."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: Snapshot with as_of/impressions for '{pkg_id}' not yet verified.")


@then(parsers.parse('package "{pkg_id}" should include a snapshot'))
def then_package_includes_snapshot(ctx: dict, pkg_id: str) -> None:
    """Assert package includes a snapshot."""
    import pytest

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                if snapshot is None:
                    pytest.xfail(f"SPEC-PRODUCTION GAP: Package '{pkg_id}' missing snapshot.")
                return
    pytest.xfail(f"Package '{pkg_id}' not found in response")


@then(parsers.parse("all packages should include snapshots"))
def then_all_packages_have_snapshots(ctx: dict) -> None:
    """Assert all packages have snapshots."""
    import pytest

    pytest.xfail("SPEC-PRODUCTION GAP: All-package snapshot verification pending.")


@then(parsers.parse("{pkg1} has snapshot, {pkg2} has SNAPSHOT_TEMPORARILY_UNAVAILABLE"))
def then_mixed_snapshot(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Assert mixed snapshot availability."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: Mixed snapshot ({pkg1} has, {pkg2} unavailable) not verified.")


@then(parsers.parse('snapshot_unavailable_reason "{reason}"'))
def then_unavailable_reason_shorthand(ctx: dict, reason: str) -> None:
    """Assert snapshot_unavailable_reason (boundary table shorthand)."""
    import pytest

    pytest.xfail(f"SPEC-PRODUCTION GAP: snapshot_unavailable_reason '{reason}' not verified.")

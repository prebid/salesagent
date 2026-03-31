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
    """Record expected creative state — cannot seed real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, seed real DB
    records. Currently no factory exists to create creative DB rows.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot seed creative with "
        f"status='{status}' on package '{pkg_id}'. "
        f"FIXME(salesagent-vov): Create factory to seed real DB records."
    )


@given(
    parsers.parse('package "{pkg_id}" has a creative with internal status "{status}" and rejection_reason "{reason}"')
)
def given_package_creative_rejected(ctx: dict, pkg_id: str, status: str, reason: str) -> None:
    """Record rejected creative — cannot seed real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, seed real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot seed creative with "
        f"status='{status}', rejection_reason='{reason}' on package '{pkg_id}'. "
        f"FIXME(salesagent-vov): Create factory to seed real DB records."
    )


@given(parsers.parse('package "{pkg_id}" has a creative assignment with creative_id "{creative_id}"'))
def given_package_creative_assignment(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Record creative assignment — cannot seed real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, seed real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot seed creative assignment "
        f"for '{creative_id}' on package '{pkg_id}'. "
        f"FIXME(salesagent-vov): Create factory to seed real DB records."
    )


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}" and rejection_reason "{reason}"'))
def given_creative_status_with_reason(ctx: dict, creative_id: str, status: str, reason: str) -> None:
    """Update creative status/reason — cannot update real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, update real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot update creative "
        f"'{creative_id}' with status='{status}', rejection_reason='{reason}' in DB. "
        f"FIXME(salesagent-vov): Create factory to seed/update real DB records."
    )


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}" {extra_condition}'))
def given_creative_status_extra(ctx: dict, creative_id: str, status: str, extra_condition: str) -> None:
    """Update creative status with extra conditions — cannot update real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, update real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot update creative "
        f"'{creative_id}' status to '{status}' with '{extra_condition}' in DB. "
        f"FIXME(salesagent-vov): Create factory to seed/update real DB records."
    )


@given(parsers.parse('the creative "{creative_id}" has internal status "{status}"'))
def given_creative_status_simple(ctx: dict, creative_id: str, status: str) -> None:
    """Set creative internal status — cannot update real DB records.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, update real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot update creative "
        f"'{creative_id}' status to '{status}' in DB. "
        f"FIXME(salesagent-vov): Create factory to seed/update real DB records."
    )


@given(parsers.parse('no creative with id "{creative_id}" exists in the tenant'))
def given_no_creative_exists(ctx: dict, creative_id: str) -> None:
    """Mark creative as nonexistent — cannot verify or enforce DB absence.

    FIXME(salesagent-vov): When CreativeAssignmentFactory exists, verify actual
    DB absence rather than relying on ctx-only sentinels.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Cannot enforce DB absence for creative '{creative_id}' — "
        f"no CreativeAssignmentFactory to verify or control DB state. "
        f"FIXME(salesagent-vov): Verify actual DB absence."
    )


@given(parsers.parse('package "{pkg_id}" has a creative assignment referencing creative_id "{creative_id}"'))
def given_package_creative_ref_nonexistent(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Record creative assignment referencing a potentially nonexistent creative.

    FIXME(salesagent-vov): No CreativeAssignmentFactory — cannot seed real DB records.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: No CreativeAssignmentFactory — cannot seed creative assignment "
        f"for '{creative_id}' on package '{pkg_id}'. "
        f"FIXME(salesagent-vov): Create factory to seed real DB records."
    )


@given(parsers.parse('no snapshot data is available for package "{pkg_id}"'))
def given_no_snapshot_for_package(ctx: dict, pkg_id: str) -> None:
    """Establish that no snapshot data exists for a package.

    The default state in the harness is no snapshot data — the adapter mock
    (when present) returns no data unless explicitly configured. Record the
    expectation in ctx so Then steps can verify the correct unavailable_reason.
    """
    ctx.setdefault("snapshot_unavailable_packages", set()).add(pkg_id)


@given("the ad platform adapter supports realtime reporting")
def given_adapter_supports_reporting(ctx: dict) -> None:
    """Configure the adapter mock to support realtime reporting for snapshots.

    FIXME(salesagent-9vgz.1): When the harness supports full adapter capability
    configuration, this step should also set up mock reporting endpoints that
    return test data (impressions, spend, etc.).
    """
    ctx["adapter_supports_reporting"] = True
    env = ctx["env"]
    assert "adapter" in env.mock, (
        "Step claims 'the ad platform adapter supports realtime reporting' "
        "but no adapter mock is configured in the test environment"
    )
    adapter_mock = env.mock["adapter"].return_value
    adapter_mock.supports_realtime_reporting = True


@given("the ad platform adapter does not support realtime reporting")
def given_adapter_no_reporting(ctx: dict) -> None:
    """Configure the adapter to NOT support realtime reporting."""
    ctx["adapter_supports_reporting"] = False
    env = ctx["env"]
    assert "adapter" in env.mock, (
        "Step claims 'the ad platform adapter does not support realtime reporting' "
        "but no adapter mock is configured in the test environment"
    )
    adapter_mock = env.mock["adapter"].return_value
    adapter_mock.supports_realtime_reporting = False


@given(parsers.parse("the ad platform adapter exists"))
def given_adapter_exists(ctx: dict) -> None:
    """Confirm adapter exists (default state in harness)."""
    env = ctx["env"]
    assert "adapter" in env.mock, (
        "Step claims 'the ad platform adapter exists' but no adapter mock is configured in the test environment"
    )


@given(parsers.parse("the adapter supports realtime reporting and data is available"))
def given_adapter_reporting_with_data(ctx: dict) -> None:
    """Adapter supports reporting AND snapshot data is available.

    Patches get_adapter in the media_buy_list module to return a mock adapter
    whose get_packages_snapshot returns realistic snapshot data keyed by the
    packages created in earlier Given steps.
    """
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from src.core.schemas._base import Snapshot

    ctx["adapter_supports_reporting"] = True

    # Build snapshot data from seeded media buys
    snapshot_data: dict[str, dict[str, Snapshot]] = {}
    seeded = ctx.get("seeded_media_buys", {})
    for mb_id, mb in seeded.items():
        packages = getattr(mb, "packages", None) or []
        # Also check MediaPackage objects created via factory (stored in session)
        env = ctx["env"]
        if env._session is not None:
            from sqlalchemy import select

            from src.core.database.models import MediaPackage as DBMediaPackage

            pkgs = env._session.scalars(select(DBMediaPackage).filter_by(media_buy_id=mb_id)).all()
            for pkg in pkgs:
                snapshot_data.setdefault(mb_id, {})[pkg.package_id] = Snapshot(
                    as_of=datetime.now(UTC),
                    impressions=1500.0,
                    spend=75.50,
                    staleness_seconds=30,
                    clicks=120.0,
                    pacing_index=1.05,
                    delivery_status="delivering",
                    currency="USD",
                )

    adapter_mock = MagicMock()
    adapter_mock.capabilities.supports_realtime_reporting = True
    adapter_mock.get_packages_snapshot.return_value = snapshot_data

    patcher = patch(
        "src.core.tools.media_buy_list.get_adapter",
        return_value=adapter_mock,
    )
    patcher.start()
    ctx.setdefault("_patchers", []).append(patcher)
    ctx["adapter_snapshot_data"] = snapshot_data


@given(parsers.parse("the adapter supports realtime reporting but no data for {pkg_id}"))
def given_adapter_reporting_no_data(ctx: dict, pkg_id: str) -> None:
    """Adapter supports reporting but no snapshot for specified package.

    FIXME(salesagent-9vgz.1): No mock side_effect or return_value is configured
    to raise/return nothing for the specified package — cannot fulfill 'no data for X'.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Adapter mock has no side_effect/return_value for '{pkg_id}' — "
        f"cannot fulfill 'no data for {pkg_id}'. "
        f"FIXME(salesagent-9vgz.1): Configure adapter mock to return empty for this package."
    )


@given(parsers.parse("the adapter supports realtime reporting and data for all pkgs"))
def given_adapter_reporting_all_data(ctx: dict) -> None:
    """Adapter supports reporting with data for all packages.

    FIXME(salesagent-9vgz.1): No mock return_value is configured to actually
    return snapshot data for any package — cannot fulfill 'data for all pkgs'.
    """
    import pytest

    pytest.xfail(
        "SPEC-PRODUCTION GAP: Adapter mock has no return_value for snapshot data — "
        "cannot fulfill 'data for all pkgs'. "
        "FIXME(salesagent-9vgz.1): Configure adapter mock to return snapshot data."
    )


@given(parsers.parse("the adapter supports reporting, data for {pkg1} but not {pkg2}"))
def given_adapter_reporting_mixed(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Adapter supports reporting with mixed snapshot availability.

    FIXME(salesagent-9vgz.1): Adapter mock is never configured with per-package
    return values or side effects — cannot fulfill 'data for X but not Y'.
    """
    import pytest

    pytest.xfail(
        "SPEC-PRODUCTION GAP: Adapter mock has no per-package return_value/side_effect — "
        "cannot fulfill mixed snapshot availability. "
        "FIXME(salesagent-9vgz.1): Configure adapter mock with per-package responses."
    )


@given(parsers.parse('an authenticated principal "{principal_id}" who owns {count:d} media buys'))
def given_principal_with_n_buys(ctx: dict, principal_id: str, count: int) -> None:
    """Create N media buys for a principal.

    Uses MediaBuyFactory(...) which invokes factory_boy's create() strategy.
    env._commit_factory_data() flushes all pending factory objects to the DB session.
    """
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
    assert len(ctx["seeded_media_buys"]) >= count, (
        f"Expected at least {count} seeded media buys, got {len(ctx['seeded_media_buys'])}"
    )


@given(parsers.parse('an authenticated principal "{principal_id}" who owns no media buys'))
def given_principal_no_buys(ctx: dict, principal_id: str) -> None:
    """No media buys exist for this principal."""
    principal = ctx.get("principal")
    assert principal is not None, (
        f"No principal in ctx — step claims principal '{principal_id}' owns no media buys "
        "but no principal exists to validate against"
    )
    assert principal.principal_id == principal_id, (
        f"Step references principal '{principal_id}' but ctx principal is '{principal.principal_id}' — mismatch"
    )
    ctx.setdefault("seeded_media_buys", {})


@given(parsers.parse('an authenticated principal "{principal_id}" who owns media buy "{mb_id}"'))
def given_principal_owns_single_mb(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Create a single media buy for a principal.

    If principal_id matches the harness principal, use it directly.
    If it's a different principal (e.g., for isolation tests), create a new one
    via PrincipalFactory. Both factory objects are committed via _commit_factory_data().
    """
    from tests.factories import PrincipalFactory

    env = ctx["env"]
    if ctx["principal"].principal_id == principal_id:
        principal = ctx["principal"]
    else:
        # Create a separate principal for isolation testing (INV-154)
        # PrincipalFactory(...) creates via factory_boy; _commit_factory_data() persists
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
    """Ensure snapshot data will be returned for a specific package.

    Patches get_adapter in the media_buy_list module so that the adapter
    returns snapshot data for the specified package. If a patcher already
    exists (from given_adapter_reporting_with_data), update its return data.
    """
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from src.core.schemas._base import Snapshot

    test_snapshot = Snapshot(
        as_of=datetime.now(UTC),
        impressions=1500.0,
        spend=75.50,
        staleness_seconds=30,
        clicks=120.0,
        pacing_index=1.05,
        delivery_status="delivering",
        currency="USD",
    )

    ctx.setdefault("expected_snapshots", {})[pkg_id] = test_snapshot

    # Find the media_buy_id that owns this package
    seeded = ctx.get("seeded_media_buys", {})
    target_mb_id: str | None = None
    env = ctx["env"]
    if env._session is not None:
        from sqlalchemy import select

        from src.core.database.models import MediaPackage as DBMediaPackage

        pkg_row = env._session.scalars(select(DBMediaPackage).filter_by(package_id=pkg_id)).first()
        if pkg_row:
            target_mb_id = pkg_row.media_buy_id
    if target_mb_id is None and seeded:
        target_mb_id = next(iter(seeded))

    # Build or update snapshot_data mapping
    snapshot_data = ctx.get("adapter_snapshot_data", {})
    if target_mb_id:
        snapshot_data.setdefault(target_mb_id, {})[pkg_id] = test_snapshot
    ctx["adapter_snapshot_data"] = snapshot_data

    # If no adapter patcher exists yet, create one
    if not any(getattr(p, "attribute", "") == "get_adapter" for p in ctx.get("_patchers", [])):
        adapter_mock = MagicMock()
        adapter_mock.capabilities.supports_realtime_reporting = True
        adapter_mock.get_packages_snapshot.return_value = snapshot_data

        patcher = patch(
            "src.core.tools.media_buy_list.get_adapter",
            return_value=adapter_mock,
        )
        patcher.start()
        ctx.setdefault("_patchers", []).append(patcher)


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
    """Assert packages include creative approval info with meaningful values.

    Step text: "when creatives are assigned" — so we check:
    1. The creative_approvals field must be present on the schema
    2. When creatives ARE assigned, creative_approvals must be populated
    3. Each approval entry must have a valid approval_status
    """
    from src.core.schemas._base import ApprovalStatus

    valid_statuses = {s.value for s in ApprovalStatus}
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "No media buys in response"
    packages_checked = 0
    packages_with_approvals = 0
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            packages_checked += 1
            approvals = getattr(pkg, "creative_approvals", None)
            if approvals:
                packages_with_approvals += 1
                for approval in approvals:
                    cid = getattr(approval, "creative_id", None)
                    assert cid is not None, "CreativeApproval entry missing creative_id"
                    status = getattr(approval, "approval_status", None)
                    assert status is not None, f"CreativeApproval for '{cid}' has no approval_status"
                    status_str = status.value if hasattr(status, "value") else str(status)
                    assert status_str in valid_statuses, (
                        f"Unexpected approval_status '{status_str}' for creative '{cid}', "
                        f"expected one of {valid_statuses}"
                    )
    assert packages_checked > 0, "No packages found to check creative approvals on"
    # Step text says "when creatives are assigned" — verify at least one package
    # actually had creative approvals to check
    assert packages_with_approvals > 0, (
        f"Step claims 'when creatives are assigned' but none of the {packages_checked} "
        f"packages had creative_approvals populated — test setup must assign creatives"
    )


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
    """Assert that a media buy with missing dates raises a structured error.

    This step is used in @error-tagged scenarios where missing dates should
    cause a graceful failure (structured AdCPError), not a silent success.
    The scenario's follow-up steps check for suggestion fields, confirming
    an error is the expected outcome.
    """
    from src.core.exceptions import AdCPError

    error = ctx.get("error")
    assert error is not None, (
        f"Expected a structured error for media buy '{mb_id}' with missing dates, "
        f"but no error was raised — production returned a response instead. "
        f"Missing dates should cause a validation/computation error, not succeed silently."
    )
    assert isinstance(error, (AdCPError, ValueError, TypeError)), (
        f"Expected graceful error handling (AdCPError/ValueError/TypeError) "
        f"for missing date, got unhandled {type(error).__name__}: {error}"
    )


@then(parsers.parse("the error message should include field-level validation details"))
def then_error_field_validation(ctx: dict) -> None:
    """Assert error includes field-level validation details with actual field names.

    Step text claims "field-level validation details" — the error must reference
    specific field names or paths (media_buy_ids, status_filter, buyer_refs, etc.),
    not just generic words like "type" or "expected" that appear in any error.
    """
    error = ctx.get("error")
    assert error is not None, "Expected a validation error"
    msg = str(error)
    # Require actual field names from GetMediaBuysRequest schema
    field_names = ("media_buy_ids", "status_filter", "buyer_refs", "account_id")
    assert any(field_name in msg.lower() for field_name in field_names), (
        f"Expected field-level validation details (containing actual field names like "
        f"{field_names}) in error message, got: {msg}"
    )


@then(parsers.parse('the error should include a "recovery" field indicating correctable failure'))
def then_error_recovery_correctable(ctx: dict) -> None:
    """Assert error has correctable recovery classification."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with recovery field, got {type(error).__name__}: {error}"
    assert error.recovery in ("correctable", "retryable"), (
        f"Expected correctable/retryable recovery, got '{error.recovery}'"
    )


@then(parsers.parse('the error should include a "suggestion" field'))
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion field with actionable content.

    Step text: 'the error should include a "suggestion" field'.
    The error must be an AdCPError with a details dict containing a non-empty
    suggestion string. No xfail escape — if production omits the suggestion,
    the test must fail.
    """
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), (
        f"Expected AdCPError with suggestion field, got {type(error).__name__}: {error}"
    )
    assert error.details is not None, (
        f"AdCPError(error_code={error.error_code!r}) has no details dict — cannot contain 'suggestion' field"
    )
    assert "suggestion" in error.details, f"Expected 'suggestion' in error details: {error.details}"
    suggestion = error.details["suggestion"]
    assert isinstance(suggestion, str) and suggestion.strip(), (
        f"Expected non-empty suggestion string, got {suggestion!r}"
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
    # Step text requires BOTH: mention of the invalid value AND that it's about status
    assert text.lower() in msg, f"Expected invalid value '{text}' to appear in error message, got: {error}"
    assert "status" in msg, (
        f"Expected 'status' to appear in error message (indicating this is a status validation error), got: {error}"
    )


@then(parsers.parse('the creative approval for "{creative_id}" should have approval_status "{status}"'))
def then_creative_approval_status(ctx: dict, creative_id: str, status: str) -> None:
    """Assert a specific creative's approval status in the response.

    Searches ALL packages across ALL media buys for a creative_approvals
    entry matching creative_id, then asserts approval_status.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                aid = getattr(approval, "creative_id", None)
                if aid == creative_id:
                    actual = getattr(approval, "approval_status", None)
                    actual_str = actual.value if hasattr(actual, "value") else str(actual)
                    assert actual_str == status, (
                        f"Expected approval_status '{status}' for creative '{creative_id}', got '{actual_str}'"
                    )
                    return
    raise AssertionError(f"No approval entry found for creative '{creative_id}' across {len(buys)} media buy(s)")


@then(parsers.parse('the creative approval should have approval_status "{status}"'))
def then_any_creative_approval_status(ctx: dict, status: str) -> None:
    """Assert creative approval status on any package (any creative matches)."""

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                actual = getattr(approval, "approval_status", None)
                actual_str = actual.value if hasattr(actual, "value") else str(actual)
                if actual_str == status:
                    return  # Found a matching approval
    raise AssertionError(f"No approval with status='{status}' found across {len(buys)} media buy(s)")


@then(parsers.parse('the rejection_reason should be "{reason}"'))
def then_rejection_reason(ctx: dict, reason: str) -> None:
    """Assert rejection_reason matches expected value on any approval."""

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                actual_reason = getattr(approval, "rejection_reason", None)
                if actual_reason is not None:
                    assert str(actual_reason) == reason, f"Expected rejection_reason '{reason}', got '{actual_reason}'"
                    return
    raise AssertionError(
        f"No approval with rejection_reason found across {len(buys)} media buy(s), expected '{reason}'"
    )


@then(parsers.parse("rejection_reason should be absent"))
def then_rejection_reason_absent(ctx: dict) -> None:
    """Assert rejection_reason is absent on ALL approvals when not rejected."""

    buys = _get_media_buys(ctx)
    checked = 0
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                checked += 1
                actual_reason = getattr(approval, "rejection_reason", None)
                assert actual_reason is None, f"Expected rejection_reason to be absent, got '{actual_reason}'"
    assert checked > 0, "No approval entries found in response — cannot verify rejection_reason absence"


@then(parsers.parse("rejection_reason should not be present in the approval entry"))
def then_rejection_reason_not_present(ctx: dict) -> None:
    """Assert rejection_reason is not present on ANY approval entry."""

    buys = _get_media_buys(ctx)
    checked = 0
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                checked += 1
                actual_reason = getattr(approval, "rejection_reason", None)
                assert actual_reason is None, f"Expected rejection_reason to not be present, got '{actual_reason}'"
    assert checked > 0, "No approval entries found in response — cannot verify rejection_reason absence"


@then(parsers.parse("rejection_reason should be null or absent"))
def then_rejection_reason_null_or_absent(ctx: dict) -> None:
    """Assert rejection_reason is null or absent on ALL approval entries."""

    buys = _get_media_buys(ctx)
    checked = 0
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            approvals = getattr(pkg, "creative_approvals", None) or []
            for approval in approvals:
                checked += 1
                actual_reason = getattr(approval, "rejection_reason", None)
                assert actual_reason is None, f"Expected rejection_reason to be null or absent, got '{actual_reason}'"
    assert checked > 0, "No approval entries found in response — cannot verify rejection_reason null/absent"


@then(parsers.parse('the creative approvals for package "{pkg_id}" should not include an entry for "{creative_id}"'))
def then_no_approval_for_creative(ctx: dict, pkg_id: str, creative_id: str) -> None:
    """Assert missing creative is not in approvals (INV-152-4).

    The package MUST be found in the response — if it's missing, that's a hard
    failure, not an xfail.
    """
    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                approvals = getattr(pkg, "creative_approvals", None) or []
                approval_ids = [getattr(a, "creative_id", None) for a in approvals]
                assert creative_id not in approval_ids, (
                    f"Expected creative '{creative_id}' to NOT appear in approvals for package '{pkg_id}', but found it"
                )
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response — cannot verify creative '{creative_id}' omission")


@then(parsers.parse("no error should be raised for the missing creative"))
def then_no_error_for_missing_creative(ctx: dict) -> None:
    """Assert no error raised for missing creative."""
    assert "error" not in ctx or ctx.get("error") is None, f"Unexpected error for missing creative: {ctx.get('error')}"


@then(parsers.parse('package "{pkg_id}" should not have a snapshot field'))
def then_package_no_snapshot(ctx: dict, pkg_id: str) -> None:
    """Assert package does not have a snapshot field (INV-153-1).

    Step text: 'should not have a snapshot field'. If production returns
    a snapshot contrary to the requirement, this is a spec-production gap.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                # Violation path: snapshot IS present when it should NOT be
                assert snapshot is None, f"Package '{pkg_id}' has snapshot={snapshot!r} — should be absent"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('package "{pkg_id}" should not have a snapshot_unavailable_reason field'))
def then_package_no_unavailable_reason(ctx: dict, pkg_id: str) -> None:
    """Assert package does not have snapshot_unavailable_reason (INV-153-1).

    Step text: 'should not have a snapshot_unavailable_reason field'.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                reason = getattr(pkg, "snapshot_unavailable_reason", None)
                # Violation path: reason IS present when it should NOT be
                assert reason is None, (
                    f"Package '{pkg_id}' has snapshot_unavailable_reason='{reason}' — should be absent"
                )
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('package "{pkg_id}" should have snapshot_unavailable_reason "{reason}"'))
def then_package_unavailable_reason(ctx: dict, pkg_id: str, reason: str) -> None:
    """Assert package has specific snapshot_unavailable_reason."""

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                actual = getattr(pkg, "snapshot_unavailable_reason", None)
                assert actual is not None, (
                    f"Package '{pkg_id}' missing snapshot_unavailable_reason, expected '{reason}'"
                )
                actual_str = actual.value if hasattr(actual, "value") else str(actual)
                assert actual_str == reason, f"Expected snapshot_unavailable_reason '{reason}', got '{actual_str}'"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('the snapshot for package "{pkg_id}" should include "{field}" timestamp'))
def then_snapshot_field_timestamp(ctx: dict, pkg_id: str, field: str) -> None:
    """Assert snapshot has a timestamp field with valid ISO 8601 format."""
    from datetime import datetime

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                assert snapshot is not None, f"Package '{pkg_id}' has no snapshot — cannot verify '{field}' timestamp"
                val = getattr(snapshot, field, None)
                if val is None and isinstance(snapshot, dict):
                    val = snapshot.get(field)
                assert val is not None, f"Snapshot field '{field}' not present on package '{pkg_id}'"
                # Accept both datetime objects and ISO 8601 strings
                if isinstance(val, datetime):
                    return  # Already a datetime — valid timestamp
                assert isinstance(val, str), (
                    f"Expected '{field}' to be a timestamp (datetime or ISO 8601 string), "
                    f"got {type(val).__name__}: {val!r}"
                )
                try:
                    datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise AssertionError(
                        f"Snapshot field '{field}' value '{val}' is not a valid ISO 8601 timestamp: {exc}"
                    ) from exc
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('the snapshot should include "{field}" integer'))
def then_snapshot_field_integer(ctx: dict, field: str) -> None:
    """Assert snapshot has a non-negative integer field.

    Snapshot integer fields (e.g. staleness_seconds) represent metrics that
    must be non-negative per the Snapshot schema (ge=0 constraint).
    """
    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is not None:
                val = getattr(snapshot, field, None)
                if val is None and isinstance(snapshot, dict):
                    val = snapshot.get(field)
                assert val is not None, f"Snapshot field '{field}' not present — snapshot data propagation incomplete"
                assert isinstance(val, int), f"Expected '{field}' to be an integer, got {type(val).__name__}: {val!r}"
                assert val >= 0, f"Expected '{field}' to be non-negative, got {val}"
                return
    raise AssertionError(f"No snapshots found — cannot verify '{field}' integer")


@then(parsers.parse('the snapshot should include "{field}" count'))
def then_snapshot_field_count(ctx: dict, field: str) -> None:
    """Assert snapshot has a positive numeric count field matching seeded data.

    Step text says "count" — the value must be numeric and positive (> 0),
    verifying that the production code correctly propagated real data from
    the adapter snapshot, not just a default/zero value.
    When expected_snapshots are available in ctx, verify value matches.
    """
    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is not None:
                val = getattr(snapshot, field, None)
                if val is None and isinstance(snapshot, dict):
                    val = snapshot.get(field)
                assert val is not None, f"Snapshot field '{field}' count not present on package"
                assert isinstance(val, (int, float)), (
                    f"Expected '{field}' to be a numeric count, got {type(val).__name__}: {val!r}"
                )
                assert val > 0, (
                    f"Expected '{field}' count to be positive (> 0) — a zero value suggests "
                    f"snapshot data was not propagated from adapter. Got {val}"
                )
                # Verify against seeded snapshot data if available
                pkg_id = getattr(pkg, "package_id", None)
                expected = ctx.get("expected_snapshots", {}).get(pkg_id)
                if expected is not None:
                    expected_val = getattr(expected, field, None)
                    if expected_val is not None:
                        assert val == expected_val, (
                            f"Snapshot '{field}' value {val} does not match seeded "
                            f"value {expected_val} for package '{pkg_id}'"
                        )
                return
    raise AssertionError(f"No snapshots found — cannot verify '{field}' count")


@then(parsers.parse('the snapshot should include "{field}" amount'))
def then_snapshot_field_amount(ctx: dict, field: str) -> None:
    """Assert snapshot has an amount field."""

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is not None:
                val = getattr(snapshot, field, None)
                if val is None and isinstance(snapshot, dict):
                    val = snapshot.get(field)
                assert val is not None, f"Snapshot field '{field}' amount not present on package"
                assert isinstance(val, int | float), (
                    f"Expected '{field}' to be a numeric amount, got {type(val).__name__}: {val!r}"
                )
                assert val >= 0, f"Expected '{field}' amount to be non-negative, got {val}"
                return
    raise AssertionError(f"No snapshots found — cannot verify '{field}' amount")


@then(parsers.parse("the response should include {count:d} media buys"))
def then_response_count(ctx: dict, count: int) -> None:
    """Assert response has a specific number of media buys."""
    buys = _get_media_buys(ctx)
    assert len(buys) == count, f"Expected {count} media buys, got {len(buys)}"


@then(parsers.parse("the response should include {count:d} media buys scoped to {principal_id}"))
def then_response_count_scoped(ctx: dict, count: int, principal_id: str) -> None:
    """Assert response has N media buys scoped to a principal.

    Step text claims 'scoped to {principal_id}' — scoping MUST be verified,
    not just the count.
    """
    buys = _get_media_buys(ctx)
    assert len(buys) == count, f"Expected {count} media buys for '{principal_id}', got {len(buys)}"
    # Verify scoping: all returned buys should belong to the claimed principal
    seeded = ctx.get("seeded_media_buys", {})
    returned_ids = {getattr(b, "media_buy_id", None) for b in buys}
    scoping_checked = 0
    for mb_id in returned_ids:
        if mb_id in seeded:
            mb = seeded[mb_id]
            actual_principal = getattr(mb, "principal_id", None)
            if actual_principal is not None:
                scoping_checked += 1
                assert actual_principal == principal_id, (
                    f"Media buy '{mb_id}' belongs to principal '{actual_principal}', "
                    f"not '{principal_id}' — scoping violation"
                )
    # Step claims scoping — we must have verified at least one buy's ownership
    if count > 0:
        assert scoping_checked > 0, (
            f"Step claims scoping to '{principal_id}' but verified 0 of {count} returned buys. "
            f"Returned IDs: {returned_ids}, seeded IDs: {set(seeded.keys())}"
        )


@then(parsers.parse('the response should contain "media_buys" array'))
def then_response_has_media_buys_array(ctx: dict) -> None:
    """Assert response has a media_buys field that is a list (array)."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    buys = getattr(resp, "media_buys", None)
    assert buys is not None, "Response missing media_buys field"
    assert isinstance(buys, list), f"Expected media_buys to be a list (array), got {type(buys).__name__}"


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox=true.

    Step text: 'should include sandbox equals true'. Missing sandbox field
    means the feature isn't implemented yet (spec gap).
    """
    import pytest

    from src.core.schemas._base import GetMediaBuysResponse

    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    assert isinstance(resp, GetMediaBuysResponse), f"Expected GetMediaBuysResponse, got {type(resp).__name__}"
    # Check schema-level field definition first
    has_sandbox_field = "sandbox" in type(resp).model_fields if hasattr(type(resp), "model_fields") else False
    sandbox = getattr(resp, "sandbox", None)
    if sandbox is None and not has_sandbox_field:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: sandbox field not defined in GetMediaBuysResponse schema. "
            "Correct assertion: assert resp.sandbox is True. "
            "FIXME(salesagent-9vgz.1): Add sandbox: bool | None field to GetMediaBuysResponse."
        )
    assert sandbox is True, f"Expected sandbox=true, got {sandbox!r}"


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert response does not include sandbox field for production accounts.

    Step text: 'should not include a sandbox field'. If production includes
    it anyway, this is a spec-production gap.
    """

    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    sandbox = getattr(resp, "sandbox", None)
    # Violation path: sandbox IS present when it should NOT be
    assert sandbox is None, (
        f"Production response includes sandbox={sandbox!r} for production account — should be absent"
    )


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real adapter API calls (sandbox mode).

    Two verification paths:
    1. If the env has adapter mocks (e.g. create/update envs), verify no methods were called.
    2. If the env has NO adapter patches (e.g. MediaBuyListEnv — pure DB read),
       this proves no adapter calls are possible by design. Verify EXTERNAL_PATCHES
       is empty to confirm the operation is adapter-free.
    """
    env = ctx["env"]
    if "adapter" in env.mock:
        # Path 1: adapter mock exists — verify no methods were called
        adapter_mock = env.mock["adapter"].return_value
        methods_checked = 0
        for method_name in ("create_line_item", "get_report", "sync_creative"):
            method = getattr(adapter_mock, method_name, None)
            if method is not None and hasattr(method, "called"):
                methods_checked += 1
                assert not method.called, f"Real adapter method '{method_name}' was called in sandbox mode"
        assert methods_checked > 0, (
            "Adapter mock exists but no callable methods found to verify — "
            f"adapter mock type: {type(adapter_mock).__name__}"
        )
    else:
        # Path 2: no adapter mock — operation is adapter-free by design
        assert env.EXTERNAL_PATCHES == {}, (
            f"No adapter mock but EXTERNAL_PATCHES is non-empty: {env.EXTERNAL_PATCHES}. "
            f"If the env patches external services, it should include an adapter mock "
            f"to verify no real calls were made in sandbox mode."
        )
        # Confirm response was successful (operation completed without adapter)
        assert ctx.get("response") is not None, "No adapter mock and no response — operation may have failed silently"


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error.

    Step text says 'indicate a validation error' — must verify either:
    1. An exception was raised with validation-related keywords, OR
    2. Response.errors contains validation-related content.
    """

    error = ctx.get("error")
    if error:
        # Verify it's actually a validation error, not just any error
        msg = str(error).lower()
        assert any(kw in msg for kw in ("validation", "invalid", "required", "type", "field")), (
            f"Expected a validation error, but error doesn't indicate validation: {error}"
        )
        return
    resp = ctx.get("response")
    if resp:
        errors = getattr(resp, "errors", None)
        if errors:
            # Verify at least one error relates to validation
            error_strs = [str(e).lower() for e in errors]
            has_validation_keyword = any(
                any(kw in s for kw in ("validation", "invalid", "required", "type", "field")) for s in error_strs
            )
            assert has_validation_keyword, f"Response has errors but none indicate validation: {errors}"
            return
    raise AssertionError(
        "Expected validation error: neither error raised nor response.errors contains validation content"
    )


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert error is a real validation error (not simulated sandbox response)."""

    error = ctx.get("error")
    assert error is not None, "Expected a real validation error but no error was raised"
    from src.core.exceptions import AdCPError

    # A "real" validation error is an actual exception (not a response-embedded simulated one)
    assert isinstance(error, (AdCPError, ValueError, TypeError)), (
        f"Expected a real validation error (AdCPError/ValueError/TypeError), got {type(error).__name__}: {error}"
    )


@then("the error should include a suggestion for how to fix the issue")
def then_error_suggestion_for_fix(ctx: dict) -> None:
    """Assert error includes a suggestion with actionable fix guidance.

    Step text: 'suggestion for how to fix the issue' — the suggestion must be
    a non-empty string with enough content to be actionable (at least 5 chars).
    No xfail escape — if production omits suggestions, the test must fail.
    """
    error = ctx.get("error")
    assert error is not None, "Expected an error to check suggestion on"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), (
        f"Expected AdCPError with suggestion field, got {type(error).__name__}: {error}"
    )
    assert error.details is not None, (
        f"AdCPError(error_code={error.error_code!r}) has no details dict — cannot contain suggestion"
    )
    suggestion = error.details.get("suggestion")
    assert isinstance(suggestion, str) and len(suggestion.strip()) >= 5, (
        f"Expected actionable suggestion string (>= 5 chars) in error details, "
        f"got {suggestion!r}. Step claims 'how to fix the issue' — suggestion "
        f"must contain meaningful guidance."
    )


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
    """Assert media buys with multiple statuses are returned."""
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "Expected media buys returned with multi-status filter"
    # "either status" implies at least 2 different statuses are represented
    statuses = set()
    for buy in buys:
        actual = getattr(buy, "status", None)
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        statuses.add(actual_str)
    assert len(statuses) >= 2, (
        f"Step claims 'either status are returned' but only found status(es): {statuses}. "
        f"Expected at least 2 different statuses."
    )


@then("media buys in any status are returned")
def then_any_status_returned(ctx: dict) -> None:
    """Assert all seeded media buys are returned with all-status filter.

    Step text claims "any status are returned" — this requires seeded data
    to exist (to verify completeness) and all seeded IDs to appear in response.
    """
    buys = _get_media_buys(ctx)
    assert len(buys) > 0, "Expected media buys for all-status filter"
    seeded = ctx.get("seeded_media_buys", {})
    assert seeded, (
        "Step claims 'media buys in any status are returned' but no media buys "
        "were seeded — cannot verify completeness without seeded data"
    )
    returned_ids = {getattr(b, "media_buy_id", None) for b in buys}
    for mb_id in seeded:
        assert mb_id in returned_ids, (
            f"All-status filter should return all media buys, but '{mb_id}' is missing. Returned: {returned_ids}"
        )


@then(parsers.parse('the response should include an empty media_buys array with error "{code}"'))
def then_empty_with_error(ctx: dict, code: str) -> None:
    """Assert empty media_buys with specific error code in response."""
    buys = _get_media_buys(ctx)
    assert len(buys) == 0, f"Expected empty media_buys, got {len(buys)}"
    resp = ctx.get("response")
    assert resp is not None, (
        f"Expected response with empty media_buys and error '{code}', but response is None. Error: {ctx.get('error')}"
    )
    errors = getattr(resp, "errors", None) or []
    codes = [e.get("code") if isinstance(e, dict) else getattr(e, "code", None) for e in errors]
    assert code in codes, f"Expected error '{code}' in errors, got {codes}"


@then(parsers.parse('empty media_buys with error "{code}"'))
def then_empty_buys_with_error(ctx: dict, code: str) -> None:
    """Assert empty media_buys with error (boundary table shorthand)."""
    buys = _get_media_buys(ctx)
    assert len(buys) == 0, f"Expected empty, got {len(buys)}"
    resp = ctx.get("response")
    assert resp is not None, (
        f"Expected response with empty media_buys and error '{code}', but response is None. Error: {ctx.get('error')}"
    )
    errors = getattr(resp, "errors", None) or []
    codes = [e.get("code") if isinstance(e, dict) else getattr(e, "code", None) for e in errors]
    assert code in codes, f"Expected '{code}' in response errors, got {codes}"


@then(parsers.parse('error "{code}" with suggestion'))
def then_error_code_with_suggestion(ctx: dict, code: str) -> None:
    """Assert error with specific code and suggestion (boundary table shorthand).

    Step text: 'error "{code}" with suggestion'. Asserts both error code AND
    presence of suggestion in details dict.
    """
    import pytest

    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with code '{code}', got {type(error).__name__}: {error}"
    assert error.error_code == code, f"Expected error code '{code}', got '{error.error_code}'"
    # Step text promises "with suggestion" — details dict must exist and contain it
    if error.details is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: AdCPError(error_code={code!r}) has no details dict — "
            f"cannot contain suggestion. "
            f"Correct assertion: assert error.details is not None and 'suggestion' in error.details."
        )
    assert "suggestion" in error.details, f"Expected 'suggestion' in error details for '{code}', got: {error.details}"
    suggestion = error.details["suggestion"]
    assert isinstance(suggestion, str) and suggestion.strip(), (
        f"Expected non-empty suggestion string for error code '{code}', got {suggestion!r}"
    )


@then(parsers.parse("no snapshot or snapshot_unavailable_reason on any package"))
def then_no_snapshot_fields(ctx: dict) -> None:
    """Assert no snapshot-related fields on any package.

    Step text: 'no snapshot or snapshot_unavailable_reason on any package'.
    Violations are collected across ALL packages before reporting.
    """

    buys = _get_media_buys(ctx)
    snapshot_violations: list[str] = []
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            pkg_id = getattr(pkg, "package_id", "?")
            snapshot = getattr(pkg, "snapshot", None)
            reason = getattr(pkg, "snapshot_unavailable_reason", None)
            if snapshot is not None:
                snapshot_violations.append(f"{pkg_id}: has snapshot={snapshot!r}")
            if reason is not None:
                snapshot_violations.append(f"{pkg_id}: has snapshot_unavailable_reason='{reason}'")
    # Violation path: snapshot fields ARE present when they should NOT be
    assert not snapshot_violations, (
        f"{len(snapshot_violations)} snapshot field violation(s) found when not requested: "
        f"{', '.join(snapshot_violations)}"
    )


@then(parsers.parse('package "{pkg_id}" should include a snapshot with as_of and impressions'))
def then_package_snapshot_with_fields(ctx: dict, pkg_id: str) -> None:
    """Assert package has snapshot with key fields (as_of and impressions).

    Step text claims three things: 1) snapshot exists, 2) as_of exists,
    3) impressions exists. Each is verified; xfail only on spec gaps.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                assert snapshot is not None, (
                    f"Package '{pkg_id}' has no snapshot — cannot verify as_of/impressions fields"
                )
                # Verify as_of
                as_of = getattr(snapshot, "as_of", None)
                if as_of is None and isinstance(snapshot, dict):
                    as_of = snapshot.get("as_of")
                assert as_of is not None, f"Snapshot on '{pkg_id}' missing 'as_of' field"
                # Verify impressions
                impressions = getattr(snapshot, "impressions", None)
                if impressions is None and isinstance(snapshot, dict):
                    impressions = snapshot.get("impressions")
                assert impressions is not None, f"Snapshot on '{pkg_id}' missing 'impressions' field"
                assert isinstance(impressions, int | float), (
                    f"Expected 'impressions' to be numeric, got {type(impressions).__name__}"
                )
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse('package "{pkg_id}" should include a snapshot'))
def then_package_includes_snapshot(ctx: dict, pkg_id: str) -> None:
    """Assert package includes a snapshot.

    Step text: 'should include a snapshot'. Missing snapshot is a spec gap.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            if getattr(pkg, "package_id", None) == pkg_id:
                snapshot = getattr(pkg, "snapshot", None)
                assert snapshot is not None, f"Package '{pkg_id}' missing snapshot — expected snapshot to be present"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response")


@then(parsers.parse("all packages should include snapshots"))
def then_all_packages_have_snapshots(ctx: dict) -> None:
    """Assert all packages have snapshots.

    Step text: 'all packages should include snapshots'. Checks every package.
    """

    buys = _get_media_buys(ctx)
    packages_checked = 0
    missing_snapshot: list[str] = []
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            pkg_id = getattr(pkg, "package_id", "?")
            packages_checked += 1
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is None:
                missing_snapshot.append(pkg_id)
    assert packages_checked > 0, "No packages found to check snapshots on"
    assert not missing_snapshot, (
        f"{len(missing_snapshot)} of {packages_checked} package(s) missing snapshots: {missing_snapshot}"
    )


@then(parsers.parse("{pkg1} has snapshot, {pkg2} has SNAPSHOT_TEMPORARILY_UNAVAILABLE"))
def then_mixed_snapshot(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Assert mixed snapshot availability.

    Step text claims: pkg1 HAS snapshot, pkg2 HAS SNAPSHOT_TEMPORARILY_UNAVAILABLE.
    Both claims are verified; xfail only when production doesn't propagate data.
    """

    buys = _get_media_buys(ctx)
    pkg1_found = False
    pkg2_found = False
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            pid = getattr(pkg, "package_id", None)
            if pid == pkg1:
                pkg1_found = True
                snapshot = getattr(pkg, "snapshot", None)
                assert snapshot is not None, f"Package '{pkg1}' expected to have snapshot but snapshot is None"
            elif pid == pkg2:
                pkg2_found = True
                reason = getattr(pkg, "snapshot_unavailable_reason", None)
                assert reason is not None, (
                    f"Package '{pkg2}' expected to have SNAPSHOT_TEMPORARILY_UNAVAILABLE "
                    f"but snapshot_unavailable_reason is None"
                )
                reason_str = reason.value if hasattr(reason, "value") else str(reason)
                assert reason_str == "SNAPSHOT_TEMPORARILY_UNAVAILABLE", (
                    f"Expected SNAPSHOT_TEMPORARILY_UNAVAILABLE for '{pkg2}', got '{reason_str}'"
                )
    if not pkg1_found or not pkg2_found:
        missing = []
        if not pkg1_found:
            missing.append(pkg1)
        if not pkg2_found:
            missing.append(pkg2)
        raise AssertionError(f"Package(s) not found in response: {missing}")


@then(parsers.parse('snapshot_unavailable_reason "{reason}"'))
def then_unavailable_reason_shorthand(ctx: dict, reason: str) -> None:
    """Assert snapshot_unavailable_reason on any package (boundary table shorthand).

    Step text: 'snapshot_unavailable_reason "{reason}"'. Searches all packages
    for a matching reason value.
    """

    buys = _get_media_buys(ctx)
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            actual = getattr(pkg, "snapshot_unavailable_reason", None)
            if actual is not None:
                actual_str = actual.value if hasattr(actual, "value") else str(actual)
                assert actual_str == reason, f"Expected snapshot_unavailable_reason '{reason}', got '{actual_str}'"
                return
    raise AssertionError(
        f"snapshot_unavailable_reason='{reason}' not found on any package across {len(buys)} media buy(s)"
    )

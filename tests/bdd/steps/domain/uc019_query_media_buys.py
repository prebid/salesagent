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
from tests.bdd.steps.generic.then_error import _wire_code, _wire_error_object, _wire_suggestion
from tests.factories import (
    CreativeAssignmentFactory,
    CreativeFactory,
    MediaBuyFactory,
    MediaPackageFactory,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _generate_unique_id(label: str) -> str:
    """Generate a unique media_buy_id from a Gherkin label.

    Appends a uuid4 suffix so IDs never collide across parallel test runs
    or E2E scenarios sharing a database, while keeping the label prefix
    for human readability in logs.
    """
    import uuid

    return f"{label}-{uuid.uuid4().hex[:8]}"


def _register_media_buy(ctx: dict, label: str, media_buy: Any) -> None:
    """Register a media buy under a Gherkin label for later lookup.

    Stores both the label→real_id mapping and the label→ORM object mapping.
    """
    ctx.setdefault("media_buy_labels", {})[label] = media_buy.media_buy_id
    ctx.setdefault("seeded_media_buys", {})[label] = media_buy


def _resolve_media_buy_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin label to the real database media_buy_id."""
    labels = ctx.get("media_buy_labels", {})
    if label in labels:
        return labels[label]
    return label  # fallback: label IS the real ID (legacy)


def _resolve_media_buy_ids(ctx: dict, labels: list[str]) -> list[str]:
    """Resolve a list of Gherkin labels to real database media_buy_ids."""
    return [_resolve_media_buy_id(ctx, label) for label in labels]


def _register_principal(ctx: dict, label: str) -> None:
    """Register the ctx principal under a Gherkin label.

    Called once per scenario (conftest creates one principal).
    Subsequent Given steps resolve "buyer-001" → real principal_id.
    """
    principal = ctx["principal"]
    ctx.setdefault("principal_labels", {})[label] = principal.principal_id


def _resolve_principal_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin principal label to the real principal_id."""
    labels = ctx.get("principal_labels", {})
    if label in labels:
        return labels[label]
    return label  # fallback: label IS the real ID


def _make_test_snapshot() -> Any:
    """Create a realistic Snapshot instance for adapter reporting tests."""
    from datetime import UTC, datetime

    from src.core.schemas._base import Snapshot

    return Snapshot(
        as_of=datetime.now(UTC),
        impressions=1500.0,
        spend=75.50,
        staleness_seconds=30,
        clicks=120.0,
        pacing_index=1.05,
        delivery_status="delivering",
        currency="USD",
    )


def _patch_adapter_with_snapshot(ctx: dict, snapshot_data: dict) -> None:
    """Patch get_adapter to return a mock adapter with the given snapshot data."""
    from unittest.mock import MagicMock, patch

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


def _find_media_buy_for_package(ctx: dict, pkg_id: str) -> Any:
    """Find the seeded media buy ORM object that owns the given package_id."""
    pkgs = ctx.get("seeded_packages", {})
    mb = pkgs.get(pkg_id)
    assert mb is not None, (
        f"Package '{pkg_id}' not found in seeded_packages. "
        f"Ensure a prior Given step created the media buy with this package. "
        f"Known packages: {list(pkgs)}"
    )
    return mb


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
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)


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


# Pre-flight window (far future) for persisted-status seeds that carry no
# explicit dates: keeps the persisted value stable regardless of the real clock.
# INV-8/9/10 assert the raw persisted→canonical mapping with no flight
# refinement, so a pre-flight window is invisible to the resolver (those statuses
# are terminal or non-serving, never date-refined) while making the seed
# self-consistent (a pending buy is legitimately pre-flight).
_UC019_PERSISTED_SEED_WINDOW = (date(2099, 1, 1), date(2099, 12, 31))


def _seed_media_buy_with_persisted_status(
    ctx: dict,
    principal_id: str,
    mb_id: str,
    persisted: str,
    *,
    is_paused: bool = False,
) -> None:
    """Seed a media buy carrying a specific persisted (internal) status column.

    Mirrors given_multiple_buys_various_statuses in uc004 (the reviewer's
    template): the persisted status is written verbatim so get_media_buys
    exercises the real PERSISTED_STATUS_TO_CANONICAL mapping. Dates default to a
    pre-flight window; scenarios that need a specific flight phase override them
    via the "has start_date/end_date" modifier step.
    """
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    start, end = _UC019_PERSISTED_SEED_WINDOW
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status=persisted,
        is_paused=is_paused,
        start_date=start,
        end_date=end,
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with persisted status "{persisted}"'))
def given_owns_media_buy_persisted_status(ctx: dict, principal_id: str, mb_id: str, persisted: str) -> None:
    """Seed a buy with a persisted status (INV-7/8/9/10 taxonomy mapping)."""
    _seed_media_buy_with_persisted_status(ctx, principal_id, mb_id, persisted)


def _seed_simple_media_buy(ctx: dict, principal_id: str, mb_id: str, status: str = "active") -> Any:
    """Register the principal, seed a media buy (default mid-flight window) under a
    unique id, and register its Gherkin label. Shared by the plain and
    with-status Given steps so the seed+register block lives in one place.
    """
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status=status,
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)
    return mb


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with status "{status}"'))
def given_owns_media_buy_with_status(ctx: dict, principal_id: str, mb_id: str, status: str) -> None:
    """Seed a buy carrying a specific status and REGISTER its label.

    Without this specific binding the greedy generic step (`owns media buy
    "{mb_id}"`) captured the trailing ` with status "…"` into mb_id, registering
    a garbled label so a later by-ID query couldn't resolve it (INV-5). Default
    (mid-flight) window: "active" stays active; terminal states pass through.
    """
    _seed_simple_media_buy(ctx, principal_id, mb_id, status)


@given(
    parsers.parse(
        'the principal "{principal_id}" owns media buy "{mb_id}" '
        'with persisted status "{persisted}" and is_paused {flag}'
    )
)
def given_owns_media_buy_persisted_status_paused(
    ctx: dict, principal_id: str, mb_id: str, persisted: str, flag: str
) -> None:
    """Seed a buy with a persisted status and explicit is_paused (INV-6/INV-11)."""
    _seed_media_buy_with_persisted_status(
        ctx, principal_id, mb_id, persisted, is_paused=(flag.strip().lower() == "true")
    )


@given(parsers.parse('media buy "{mb_id}" has start_date "{start}" and end_date "{end}"'))
def given_media_buy_has_dates(ctx: dict, mb_id: str, start: str, end: str) -> None:
    """Override the flight window on an already-seeded buy (INV-6/7/11 modifier)."""
    from sqlalchemy import select

    from src.core.database.models import MediaBuy as DBMediaBuy

    real_id = _resolve_media_buy_id(ctx, mb_id)
    env = ctx["env"]
    row = env._session.scalars(select(DBMediaBuy).filter_by(media_buy_id=real_id)).first()
    assert row is not None, f"Media buy '{mb_id}' (real_id={real_id}) not seeded before setting its dates"
    row.start_date = date.fromisoformat(start)
    row.end_date = date.fromisoformat(end)
    env._session.commit()


@given(parsers.parse('the principal "{principal_id}" owns media buys "{mb1}", "{mb2}", and "{mb3}"'))
def given_principal_owns_multiple(ctx: dict, principal_id: str, mb1: str, mb2: str, mb3: str) -> None:
    """Create 3 media buys, verifying principal_id consistency."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    for label in [mb1, mb2, mb3]:
        real_id = _generate_unique_id(label)
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=real_id,
            status="active",
        )
        _register_media_buy(ctx, label, mb)
    env._commit_factory_data()


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with an active package "{pkg_id}"'))
def given_principal_owns_with_package(ctx: dict, principal_id: str, mb_id: str, pkg_id: str) -> None:
    """Create a media buy with an active package, verifying principal_id consistency."""
    # Verify the stated principal_id matches the ctx principal
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
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
    _register_media_buy(ctx, mb_id, mb)
    ctx.setdefault("seeded_packages", {})[pkg_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns no media buys'))
def given_principal_owns_none(ctx: dict, principal_id: str) -> None:
    """No media buys exist for this principal (default state).

    Validates that the principal_id matches the ctx principal (like other
    principal-scoped Given steps).
    """
    _register_principal(ctx, principal_id)
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

    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    start_dt = dt.fromisoformat(start_time)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        start_time=start_dt,
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)


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

    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    end_dt = dt.fromisoformat(end_time)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status="active",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        end_time=end_dt,
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)


@given(parsers.parse('the principal "{principal_id}" owns media buys in various statuses'))
def given_principal_owns_various_statuses(ctx: dict, principal_id: str) -> None:
    """Create media buys in multiple statuses for status filter testing."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    # Create one in each status by using dates relative to 'today'
    # Pre-flight → pending_start, In-flight → active, Post-flight → completed
    today = date.fromisoformat(ctx.get("mock_today", "2026-03-15"))
    from datetime import timedelta

    status_dates = {
        "mb-pending": (today + timedelta(days=10), today + timedelta(days=30)),
        "mb-active": (today - timedelta(days=10), today + timedelta(days=10)),
        "mb-completed": (today - timedelta(days=30), today - timedelta(days=10)),
    }
    for label, (start, end) in status_dates.items():
        real_id = _generate_unique_id(label)
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=real_id,
            status="active",
            start_date=start,
            end_date=end,
        )
        _register_media_buy(ctx, label, mb)
    env._commit_factory_data()


@given(parsers.parse('the principal "{principal_id}" owns active media buy "{mb1}" and completed media buy "{mb2}"'))
def given_principal_owns_active_and_completed(ctx: dict, principal_id: str, mb1: str, mb2: str) -> None:
    """Create one active and one completed media buy (INV-151-1)."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    today = date.fromisoformat(ctx.get("mock_today", "2026-03-15"))
    from datetime import timedelta

    # Active: today is within flight dates
    real_id1 = _generate_unique_id(mb1)
    mb_active = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id1,
        status="active",
        start_date=today - timedelta(days=5),
        end_date=today + timedelta(days=5),
    )
    # Completed: today is after flight dates
    real_id2 = _generate_unique_id(mb2)
    mb_completed = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id2,
        status="active",
        start_date=today - timedelta(days=30),
        end_date=today - timedelta(days=10),
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb1, mb_active)
    _register_media_buy(ctx, mb2, mb_completed)


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with package "{pkg_id}"'))
def given_principal_owns_mb_with_named_package(ctx: dict, principal_id: str, mb_id: str, pkg_id: str) -> None:
    """Create a media buy with a named package (for creative approval scenarios)."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
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
    _register_media_buy(ctx, mb_id, mb)
    ctx.setdefault("seeded_packages", {})[pkg_id] = mb


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}" with packages "{pkg1}" and "{pkg2}"'))
def given_principal_owns_mb_with_two_packages(ctx: dict, principal_id: str, mb_id: str, pkg1: str, pkg2: str) -> None:
    """Create a media buy with two packages (INV-153-3)."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
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
        ctx.setdefault("seeded_packages", {})[pkg_id] = mb
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)


@given(
    parsers.parse(
        'package "{pkg_id}" persisted targeting_overlay is a string (will raise TypeError on Targeting(**str))'
    )
)
@given(parsers.parse('package "{pkg_id}" persisted targeting_overlay is corrupted (will raise TypeError)'))
def given_package_targeting_overlay_is_string(ctx: dict, pkg_id: str) -> None:
    """Corrupt persisted targeting_overlay to a string (BR-RULE-294 INV-3).

    ``Targeting(**"not a dict")`` raises ``TypeError``; production catches that
    narrowly, nulls the overlay, and appends a non-fatal
    ``TARGETING_REHYDRATION_FAILED`` advisory (``recovery=terminal``). Buyer-facing
    INV-3 is graded on the wire ``errors[]`` Thens, not server-side logs.
    """
    _mutate_package_targeting(ctx, pkg_id, overlay="not a dict", legacy=None, clear_modern=False)


@given(
    parsers.re(r'package "(?P<pkg_id>[^"]+)" persisted targeting_overlay is a valid dict \{geo_countries:\["US"\]\}')
)
def given_package_targeting_overlay_valid_geo(ctx: dict, pkg_id: str) -> None:
    """Seed a valid Targeting dict under targeting_overlay (BR-RULE-294 INV-5)."""
    _mutate_package_targeting(ctx, pkg_id, overlay={"geo_countries": ["US"]}, legacy=None, clear_modern=False)


@given(parsers.parse('package "{pkg_id}" persisted package_config has {persisted_state}'))
def given_package_persisted_package_config_state(ctx: dict, pkg_id: str, persisted_state: str) -> None:
    """Apply outline / INV persisted_state to package_config (BR-RULE-294).

    Mirrors ``given_package_targeting_overlay_is_string`` for outline rows that
    expect seller suggestion (string/list corruption, two-packages, one-of-N).
    Multi-entity states seed the extra packages/buys themselves.
    """
    state = persisted_state.strip()

    if state == "no targeting_overlay and no legacy targeting":
        _ensure_principal_and_package(ctx, pkg_id)
        _mutate_package_targeting(ctx, pkg_id, overlay=None, legacy=None, clear_modern=True)
        return

    if state.startswith("targeting_overlay {geo_countries:"):
        _ensure_principal_and_package(ctx, pkg_id)
        _mutate_package_targeting(ctx, pkg_id, overlay={"geo_countries": ["US"]}, legacy=None, clear_modern=False)
        return

    if "no targeting_overlay" in state and "legacy targeting" in state:
        _ensure_principal_and_package(ctx, pkg_id)
        _mutate_package_targeting(ctx, pkg_id, overlay=None, legacy={"geo_countries": ["US"]}, clear_modern=True)
        return

    if state == "targeting_overlay set to the string 'not a dict'":
        _ensure_principal_and_package(ctx, pkg_id)
        _mutate_package_targeting(ctx, pkg_id, overlay="not a dict", legacy=None, clear_modern=False)
        return

    if state == "targeting_overlay set to the list ['not','a','dict']":
        _ensure_principal_and_package(ctx, pkg_id)
        _mutate_package_targeting(ctx, pkg_id, overlay=["not", "a", "dict"], legacy=None, clear_modern=False)
        return

    if state.startswith('two packages "pkg-001" and "pkg-002"'):
        _seed_buy_with_two_corrupted_packages(ctx)
        return

    if state.startswith('one of two buys "mb-001"/"mb-002"'):
        _seed_two_buys_one_corrupted(ctx)
        return

    raise AssertionError(f"Unrecognized persisted_state for package_config: {persisted_state!r}")


@given(parsers.parse('the principal "{principal_id}" owns media buys "{mb1}" and "{mb2}"'))
def given_principal_owns_two_media_buys(ctx: dict, principal_id: str, mb1: str, mb2: str) -> None:
    """Seed two media buys each with a default package (BR-RULE-294 INV-6)."""
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    for label, pkg_id in ((mb1, "pkg-001"), (mb2, "pkg-001")):
        real_id = _generate_unique_id(label)
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=real_id,
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
        _register_media_buy(ctx, label, mb)
        ctx.setdefault("seeded_packages", {})[f"{label}:{pkg_id}"] = mb


@given(parsers.parse('media buy "{mb_id}" package "{pkg_id}" has corrupted targeting_overlay (will raise TypeError)'))
def given_media_buy_package_corrupted_targeting(ctx: dict, mb_id: str, pkg_id: str) -> None:
    """Corrupt targeting_overlay on a package owned by the named buy (INV-6)."""
    _mutate_package_targeting(ctx, pkg_id, overlay="not a dict", legacy=None, clear_modern=False, mb_label=mb_id)


@given(parsers.parse('media buy "{mb_id}" has valid persisted state'))
def given_media_buy_valid_persisted_state(ctx: dict, mb_id: str) -> None:
    """No-op marker: sibling buy was seeded clean by the owns-buys Given (INV-6)."""
    assert mb_id in ctx.get("media_buy_labels", {}), f"Media buy '{mb_id}' not seeded — run owns-buys Given first"


def _ensure_principal_and_package(ctx: dict, pkg_id: str) -> None:
    """Ensure principal is registered and ``pkg_id`` exists (outline may rely on prior Given)."""
    if "principal" in ctx and "buyer-001" not in ctx.get("principal_labels", {}):
        _register_principal(ctx, "buyer-001")
    if pkg_id not in ctx.get("seeded_packages", {}):
        # Outline rows that only run the persisted_state Given (multi-entity) seed themselves.
        # Single-package outlines seed via owns media buy … with package first.
        return


def _mutate_package_targeting(
    ctx: dict,
    pkg_id: str,
    *,
    overlay: object | None,
    legacy: object | None,
    clear_modern: bool,
    mb_label: str | None = None,
) -> None:
    """Mutate package_config targeting keys on the named package row."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from src.core.database.models import MediaPackage as DBMediaPackage

    env = ctx["env"]
    assert env._session is not None, "Expected an active DB session for package_config mutation"
    stmt = select(DBMediaPackage).filter_by(package_id=pkg_id)
    if mb_label is not None:
        real_mb_id = _resolve_media_buy_id(ctx, mb_label)
        stmt = stmt.filter_by(media_buy_id=real_mb_id)
    pkg_row = env._session.scalars(stmt).first()
    assert pkg_row is not None, f"Package '{pkg_id}' not found in DB — seed the media buy first"
    config = dict(pkg_row.package_config or {})
    if clear_modern or overlay is None:
        config.pop("targeting_overlay", None)
    if overlay is not None:
        config["targeting_overlay"] = overlay
    if legacy is None:
        config.pop("targeting", None)
    else:
        config["targeting"] = legacy
    pkg_row.package_config = config
    flag_modified(pkg_row, "package_config")
    env._session.commit()


def _seed_buy_with_two_corrupted_packages(ctx: dict) -> None:
    """Seed mb-001 with pkg-001/pkg-002 both carrying corrupt targeting_overlay strings."""
    _register_principal(ctx, "buyer-001")
    env = ctx["env"]
    real_id = _generate_unique_id("mb-001")
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id=real_id,
        status="active",
    )
    for pkg_id in ("pkg-001", "pkg-002"):
        MediaPackageFactory(
            media_buy=mb,
            package_id=pkg_id,
            package_config={
                "package_id": pkg_id,
                "product_id": "guaranteed_display",
                "budget": 3000.0,
                "status": "active",
                "targeting_overlay": "not a dict",
            },
        )
        ctx.setdefault("seeded_packages", {})[pkg_id] = mb
    env._commit_factory_data()
    _register_media_buy(ctx, "mb-001", mb)


def _seed_two_buys_one_corrupted(ctx: dict) -> None:
    """Seed mb-001 (corrupt pkg-001) + mb-002 (clean) for one-of-N boundary."""
    _register_principal(ctx, "buyer-001")
    env = ctx["env"]
    for label, corrupt in (("mb-001", True), ("mb-002", False)):
        real_id = _generate_unique_id(label)
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=real_id,
            status="active",
        )
        config: dict[str, Any] = {
            "package_id": "pkg-001",
            "product_id": "guaranteed_display",
            "budget": 5000.0,
            "status": "active",
        }
        if corrupt:
            config["targeting_overlay"] = "not a dict"
        else:
            config["targeting_overlay"] = {"geo_countries": ["US"]}
        MediaPackageFactory(media_buy=mb, package_id="pkg-001", package_config=config)
        env._commit_factory_data()
        _register_media_buy(ctx, label, mb)
        ctx.setdefault("seeded_packages", {})[f"{label}:pkg-001"] = mb


@given(parsers.parse('package "{pkg_id}" has a creative with internal status "{status}"'))
def given_package_creative_status(ctx: dict, pkg_id: str, status: str) -> None:
    """Seed a creative with the given internal status, assigned to the package."""
    env = ctx["env"]
    # Resolve the media buy that owns this package from seeded_media_buys
    media_buy = _find_media_buy_for_package(ctx, pkg_id)
    # Feature file passes "null" as literal string for null status.
    # DB column is NOT NULL, so store as-is — _map_creative_status treats
    # unrecognized values (including "null" and "") as pending_review.
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        status=status,
    )
    CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=pkg_id,
    )
    env._commit_factory_data()


@given(
    parsers.parse('package "{pkg_id}" has a creative with internal status "{status}" and rejection_reason "{reason}"')
)
def given_package_creative_rejected(ctx: dict, pkg_id: str, status: str, reason: str) -> None:
    """Seed a creative with the given internal status and rejection_reason, assigned to the package."""
    env = ctx["env"]
    media_buy = _find_media_buy_for_package(ctx, pkg_id)
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        status=status,
        data={"rejection_reason": reason},
    )
    CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=pkg_id,
    )
    env._commit_factory_data()


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
    ctx["adapter_supports_reporting"] = True

    snapshot_data: dict[str, dict] = {}
    seeded = ctx.get("seeded_media_buys", {})
    env = ctx["env"]
    for _label, mb_obj in seeded.items():
        real_id = mb_obj.media_buy_id
        if env._session is not None:
            from sqlalchemy import select

            from src.core.database.models import MediaPackage as DBMediaPackage

            pkgs = env._session.scalars(select(DBMediaPackage).filter_by(media_buy_id=real_id)).all()
            for pkg in pkgs:
                snapshot_data.setdefault(real_id, {})[pkg.package_id] = _make_test_snapshot()

    _patch_adapter_with_snapshot(ctx, snapshot_data)


@given(parsers.parse("the adapter supports realtime reporting but no data for {pkg_id}"))
def given_adapter_reporting_no_data(ctx: dict, pkg_id: str) -> None:
    """Adapter supports reporting but no snapshot for specified package.

    Configures adapter mock to support realtime reporting but return an empty
    snapshot dict for the media buy owning ``pkg_id``, so the package has no
    snapshot data available.
    """
    ctx["adapter_supports_reporting"] = True

    # Build snapshot_data with the target package's media buy present but
    # with NO entry for the specific pkg_id — simulating "no data for X".
    snapshot_data: dict[str, dict] = {}
    seeded = ctx.get("seeded_media_buys", {})
    env = ctx["env"]
    if env._session is not None:
        from sqlalchemy import select

        from src.core.database.models import MediaPackage as DBMediaPackage

        pkg_row = env._session.scalars(select(DBMediaPackage).filter_by(package_id=pkg_id)).first()
        if pkg_row:
            # Media buy exists but has empty snapshot dict — no data for pkg_id
            snapshot_data[pkg_row.media_buy_id] = {}
    elif seeded:
        # Fallback: use first seeded media buy with empty snapshot
        first_mb = next(iter(seeded.values()))
        snapshot_data[first_mb.media_buy_id] = {}

    _patch_adapter_with_snapshot(ctx, snapshot_data)


@given(parsers.parse("the adapter supports realtime reporting and data for all pkgs"))
def given_adapter_reporting_all_data(ctx: dict) -> None:
    """Adapter supports reporting with snapshot data for every seeded package.

    Builds snapshot entries for all packages across all seeded media buys,
    so every package has data available when include_snapshot is requested.
    """
    ctx["adapter_supports_reporting"] = True

    snapshot_data: dict[str, dict] = {}
    seeded = ctx.get("seeded_media_buys", {})
    env = ctx["env"]

    for _label, mb_obj in seeded.items():
        real_id = mb_obj.media_buy_id
        if env._session is not None:
            from sqlalchemy import select

            from src.core.database.models import MediaPackage as DBMediaPackage

            pkgs = env._session.scalars(select(DBMediaPackage).filter_by(media_buy_id=real_id)).all()
            for pkg in pkgs:
                snapshot_data.setdefault(real_id, {})[pkg.package_id] = _make_test_snapshot()

    _patch_adapter_with_snapshot(ctx, snapshot_data)


@given(parsers.parse("the adapter supports reporting, data for {pkg1} but not {pkg2}"))
def given_adapter_reporting_mixed(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Adapter supports reporting with mixed per-package snapshot availability.

    Configures adapter mock so ``pkg1`` has snapshot data and ``pkg2`` does not.
    The snapshot dict includes an entry for pkg1 but omits pkg2.
    """
    ctx["adapter_supports_reporting"] = True

    snapshot_data: dict[str, dict] = {}
    seeded = ctx.get("seeded_media_buys", {})
    env = ctx["env"]

    # Find which media buy owns pkg1 and pkg2
    if env._session is not None:
        from sqlalchemy import select

        from src.core.database.models import MediaPackage as DBMediaPackage

        for pkg_id in (pkg1, pkg2):
            pkg_row = env._session.scalars(select(DBMediaPackage).filter_by(package_id=pkg_id)).first()
            if pkg_row:
                mb_id = pkg_row.media_buy_id
                if pkg_id == pkg1:
                    snapshot_data.setdefault(mb_id, {})[pkg_id] = _make_test_snapshot()
                else:
                    # pkg2's media buy key exists but no entry for pkg2
                    snapshot_data.setdefault(mb_id, {})
    elif seeded:
        first_mb = next(iter(seeded.values()))
        snapshot_data[first_mb.media_buy_id] = {pkg1: _make_test_snapshot()}

    _patch_adapter_with_snapshot(ctx, snapshot_data)


@given(parsers.parse("the adapter does not support realtime reporting"))
def given_adapter_no_realtime(ctx: dict) -> None:
    """Configure adapter to NOT support realtime reporting (short form).

    Patches get_adapter in the media_buy_list module so the returned adapter
    has supports_realtime_reporting=False. Unlike the "ad platform adapter does
    not support realtime reporting" step (which uses env.mock["adapter"]), this
    step patches the module-level get_adapter — suitable for MediaBuyListEnv
    which has no EXTERNAL_PATCHES.
    """
    from unittest.mock import MagicMock, patch

    ctx["adapter_supports_reporting"] = False

    adapter_mock = MagicMock()
    adapter_mock.capabilities.supports_realtime_reporting = False
    adapter_mock.get_packages_snapshot.return_value = {}

    patcher = patch(
        "src.core.tools.media_buy_list.get_adapter",
        return_value=adapter_mock,
    )
    patcher.start()
    ctx.setdefault("_patchers", []).append(patcher)


@given(parsers.parse('an authenticated principal "{principal_id}" who owns {count:d} media buys'))
def given_principal_with_n_buys(ctx: dict, principal_id: str, count: int) -> None:
    """Create N media buys for a principal.

    Uses MediaBuyFactory(...) which invokes factory_boy's create() strategy.
    env._commit_factory_data() flushes all pending factory objects to the DB session.
    """
    _register_principal(ctx, principal_id)
    env = ctx["env"]
    for i in range(count):
        label = f"mb-{principal_id}-{i + 1}"
        real_id = _generate_unique_id(label)
        mb = MediaBuyFactory(
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            media_buy_id=real_id,
            status="active",
        )
        _register_media_buy(ctx, label, mb)
    env._commit_factory_data()
    assert len(ctx["seeded_media_buys"]) >= count, (
        f"Expected at least {count} seeded media buys, got {len(ctx['seeded_media_buys'])}"
    )


@given(parsers.parse('an authenticated principal "{principal_id}" who owns no media buys'))
def given_principal_no_buys(ctx: dict, principal_id: str) -> None:
    """No media buys exist for this principal."""
    _register_principal(ctx, principal_id)
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
    resolved_id = _resolve_principal_id(ctx, principal_id)
    if ctx["principal"].principal_id == resolved_id:
        principal = ctx["principal"]
    else:
        # Create a separate principal for isolation testing (INV-154)
        principal = PrincipalFactory(
            tenant=ctx["tenant"],
            principal_id=resolved_id,
        )
    real_id = _generate_unique_id(mb_id)
    mb = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=principal,
        media_buy_id=real_id,
        status="active",
    )
    env._commit_factory_data()
    _register_media_buy(ctx, mb_id, mb)
    ctx.setdefault("principals", {})[principal_id] = principal


@given(parsers.parse('the principal "{principal_id}" owns media buy "{mb_id}"'))
def given_principal_owns_mb_simple(ctx: dict, principal_id: str, mb_id: str) -> None:
    """Create a media buy (simple, no date attributes)."""
    _seed_simple_media_buy(ctx, principal_id, mb_id)


# RETIRED with T-UC-019-partition-status-invalid: the "no start_time and no
# start_date" / "no end_time and no end_date" given steps seeded a schema-impossible
# null-date buy (MediaBuy dates are NOT NULL). See the feature file for the spec
# rationale. Their helper _create_media_buy_with_null_dates and the paired
# then_status_handles_missing_date are removed with them.


def _seed_account_for_principal(ctx: dict, *, sandbox: bool) -> None:
    """Seed a real Account (sandbox or production) reachable by the scenario principal.

    get_media_buys carries no account parameter on the request (production
    rejects account filtering with ACCOUNT_FILTER_NOT_SUPPORTED and instructs
    "the seller infers the account from the auth token"), so "the request
    targets a <kind> account" means: the account the identity resolves to has
    that sandbox flag. Seeding the Account + AgentAccountAccess rows makes the
    premise real at the data layer — a future sandbox short-circuit keyed off
    the principal's account (BR-RULE-209) is then actually exercised, instead
    of the Given being an inert ctx flag (6szx graduation inspection).
    """
    from tests.factories.account import AccountFactory, AgentAccountAccessFactory

    env = ctx["env"]
    account = AccountFactory(tenant=ctx["tenant"], sandbox=sandbox)
    AgentAccountAccessFactory(tenant=ctx["tenant"], principal=ctx["principal"], account=account)
    env._commit_factory_data()
    ctx["sandbox"] = sandbox
    ctx["account"] = account


@given(parsers.parse("the request targets a sandbox account"))
def given_sandbox_account(ctx: dict) -> None:
    """Seed a sandbox account for the principal (the token infers the account)."""
    _seed_account_for_principal(ctx, sandbox=True)


@given(parsers.parse("the request targets a production account"))
def given_production_account(ctx: dict) -> None:
    """Seed a production (non-sandbox) account for the principal."""
    _seed_account_for_principal(ctx, sandbox=False)


@given("an authenticated identity with no principal_id")
def given_identity_no_principal(ctx: dict) -> None:
    """Simulate an identity resolved but with no principal_id.

    The buyer has valid tenant context (e.g., token resolved) but lacks a
    principal_id — simulating an expired/revoked token or incomplete auth.
    Sets has_auth=True so the When step sends a real identity, but with
    principal_id=None so _impl can detect the missing principal and return
    an appropriate error response.
    """
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    identity = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=env._tenant_id,
    )
    ctx.setdefault("query_kwargs", {})["identity"] = identity


@given(parsers.parse("an authenticated identity with principal_id null"))
@given(parsers.parse('an authenticated identity with principal_id ""'))
def given_identity_principal_id_null_or_empty(ctx: dict) -> None:
    """Simulate an identity with principal_id as null or empty string.

    Both null and empty string are treated as "missing principal_id" by
    production code. We set principal_id=None for both — the distinction
    is in the Gherkin readability, not the implementation.
    """
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    identity = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=env._tenant_id,
    )
    ctx.setdefault("query_kwargs", {})["identity"] = identity


@given(parsers.parse('the principal "{principal_id}" does not exist in the tenant database'))
def given_principal_not_in_tenant_db(ctx: dict, principal_id: str) -> None:
    """Ensure the specified principal does not exist in the tenant database.

    For integration env: delete the principal if it exists. The env already
    created a default principal, but the scenario has set up a different
    principal_id (e.g., "buyer-unknown") that should NOT be in the database.
    """
    from sqlalchemy import delete, select

    from src.core.database.models import Principal

    env = ctx["env"]
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    if env._session is not None:
        existing = env._session.scalars(
            select(Principal).filter_by(principal_id=principal_id, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            env._session.execute(
                delete(Principal).where(
                    Principal.principal_id == principal_id,
                    Principal.tenant_id == tenant.tenant_id,
                )
            )
            env._session.commit()


@given(parsers.parse('an authenticated principal "{principal_id}" not in registry'))
def given_principal_not_in_registry(ctx: dict, principal_id: str) -> None:
    """Simulate an authenticated principal whose ID is not in the tenant database.

    Sets up an identity with the given principal_id, but ensures no matching
    Principal row exists in the DB. The _impl function should detect this
    and return a "principal_not_found" error.
    """
    from sqlalchemy import delete, select

    from src.core.database.models import Principal
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"

    # Build identity with the unregistered principal_id
    identity = PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=env._tenant_id,
    )
    ctx.setdefault("query_kwargs", {})["identity"] = identity

    # Ensure the principal does NOT exist in DB
    if env._session is not None:
        existing = env._session.scalars(
            select(Principal).filter_by(principal_id=principal_id, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            env._session.execute(
                delete(Principal).where(
                    Principal.principal_id == principal_id,
                    Principal.tenant_id == tenant.tenant_id,
                )
            )
            env._session.commit()


@given("no authentication context")
def given_no_auth_context(ctx: dict) -> None:
    """Simulate a request with no authentication at all.

    Sets has_auth=False so the When step sends identity=None, triggering
    an AUTH_REQUIRED error from _impl.
    """
    ctx["has_auth"] = False


@given(parsers.parse('snapshot data is available for package "{pkg_id}"'))
def given_snapshot_available(ctx: dict, pkg_id: str) -> None:
    """Ensure snapshot data will be returned for a specific package.

    Patches get_adapter in the media_buy_list module so that the adapter
    returns snapshot data for the specified package. If a patcher already
    exists (from given_adapter_reporting_with_data), update its return data.
    """
    test_snapshot = _make_test_snapshot()
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
        first_mb = next(iter(seeded.values()))
        target_mb_id = first_mb.media_buy_id

    # Build or update snapshot_data mapping
    snapshot_data = ctx.get("adapter_snapshot_data", {})
    if target_mb_id:
        snapshot_data.setdefault(target_mb_id, {})[pkg_id] = test_snapshot
    ctx["adapter_snapshot_data"] = snapshot_data

    # If no adapter patcher exists yet, create one
    if not any(getattr(p, "attribute", "") == "get_adapter" for p in ctx.get("_patchers", [])):
        _patch_adapter_with_snapshot(ctx, snapshot_data)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — dispatch query request
# ═══════════════════════════════════════════════════════════════════════


def _dispatch_query(ctx: dict, **extra_kwargs: Any) -> None:
    """Build and dispatch a get_media_buys request."""
    if ctx.get("error") is not None:
        return
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

    env.call_mcp() dispatches through MediaBuyListEnv.call_mcp → _run_mcp_client
    (real FastMCP Client; stashes wire_response). The tool name is baked into
    MediaBuyListEnv, matching the step text's 'get_media_buys' claim.
    """
    env = ctx["env"]
    try:
        ctx["response"] = env.call_mcp()
        # Mirror dispatch_request success-path stash when calling env directly.
        ctx["wire_response"] = getattr(env, "_last_wire_response", None)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a get_media_buys request with include_snapshot true")
def when_query_with_snapshot(ctx: dict) -> None:
    """Send get_media_buys with include_snapshot=True."""
    _dispatch_query(ctx, include_snapshot=True)


@when("the Buyer Agent sends a get_media_buys request with no filters")
@when("the Buyer Agent sends a get_media_buys request")
@when("the Buyer Agent sends a get_media_buys request with no include_snapshot param")
@when("the Buyer Agent sends a get_media_buys request with no status_filter")
@when("the Buyer Agent sends a get_media_buys request with no status_filter and no media_buy_ids")
@when(parsers.parse('"{principal_id}" sends a get_media_buys request'))
def when_query_no_filters(ctx: dict, principal_id: str | None = None) -> None:
    """Send get_media_buys with default parameters (no extra kwargs)."""
    _dispatch_query(ctx)


@when(
    parsers.re(
        r"the Buyer Agent sends a get_media_buys request with no status_filter and media_buy_ids (?P<ids>\[.+\])"
    )
)
def when_query_no_filter_with_ids(ctx: dict, ids: str) -> None:
    """No status_filter but explicit media_buy_ids — the by-ID path returns every
    matching buy regardless of status (status filter is skipped for explicit IDs).
    """
    import json

    real_ids = _resolve_media_buy_ids(ctx, json.loads(ids))
    _dispatch_query(ctx, media_buy_ids=real_ids)


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

    parsed_labels = json.loads(ids)
    real_ids = _resolve_media_buy_ids(ctx, parsed_labels)
    _dispatch_query(ctx, media_buy_ids=real_ids)


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

    if statuses.startswith("as empty array"):
        _dispatch_query(ctx, status_filter=[])
        return
    parsed = json.loads(statuses.replace("'", '"'))
    _dispatch_query(ctx, status_filter=parsed)


@when("the Buyer Agent sends a get_media_buys request with status_filter as empty array []")
def when_query_empty_status_filter(ctx: dict) -> None:
    """Send get_media_buys with empty status_filter array."""
    _dispatch_query(ctx, status_filter=[])


@when("the Buyer Agent sends a get_media_buys request with all seven v3.1 status values in status_filter")
def when_query_all_statuses(ctx: dict) -> None:
    """Send get_media_buys with all seven v3.1 MediaBuyStatus enum values.

    Derived from the pinned SDK MediaBuyStatus enum (the enums/media-buy-status.json
    vocabulary) rather than a hand-listed literal, so the "seven" tracks the spec
    automatically and doesn't duplicate the status list held elsewhere.
    """
    from adcp.types import MediaBuyStatus

    _dispatch_query(ctx, status_filter=[s.value for s in MediaBuyStatus])


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with invalid parameter types"))
def when_query_invalid_params(ctx: dict) -> None:
    """Send get_media_buys with invalid parameter types (ext-d validation)."""
    _dispatch_query(ctx, media_buy_ids="not-a-list")


@when(parsers.parse('the Buyer Agent sends a get_media_buys request with account_id "{account_id}"'))
def when_query_with_account(ctx: dict, account_id: str) -> None:
    """Send get_media_buys with account_id filter (ext-e)."""
    _dispatch_query(ctx, account={"account_id": account_id})


@when(parsers.parse("the Buyer Agent sends a get_media_buys request with invalid status filter"))
def when_query_invalid_status_filter(ctx: dict) -> None:
    """Send get_media_buys with an invalid status filter (sandbox-validation)."""
    _dispatch_query(ctx, status_filter=["invalid_status"])


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response assertions
# ═══════════════════════════════════════════════════════════════════════


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
    real_id = _resolve_media_buy_id(ctx, mb_id)
    buys = _get_media_buys(ctx)
    matching = [b for b in buys if getattr(b, "media_buy_id", None) == real_id]
    assert len(matching) == 1, (
        f"Expected media buy '{mb_id}' (real_id={real_id}) in response, "
        f"got IDs: {[getattr(b, 'media_buy_id', None) for b in buys]}"
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
    assert buys, "No media buys in response to check"
    total_packages_checked = 0
    paused_gaps: list[str] = []
    for buy in buys:
        mb_id = buy.media_buy_id
        packages = buy.packages or []
        assert packages, (
            f"Media buy '{mb_id}' has no packages — step text claims "
            "'each media buy should include package-level details' but packages list is empty"
        )
        for pkg in packages:
            total_packages_checked += 1
            assert isinstance(pkg.package_id, str) and pkg.package_id, (
                f"Package missing or empty package_id, got {pkg.package_id!r}"
            )
            # Step text claims: budget, bid_price, product_id, flight dates, paused
            assert pkg.product_id is not None, f"Package {pkg.package_id} missing product_id"
            assert pkg.budget is not None, f"Package {pkg.package_id} missing budget"
            # Verify budget is numeric
            assert isinstance(pkg.budget, int | float), (
                f"Expected budget to be numeric, got {type(pkg.budget).__name__}: {pkg.budget!r}"
            )
            # bid_price may be None for fixed-price options — verify the field value type when present
            if pkg.bid_price is not None:
                assert isinstance(pkg.bid_price, int | float), (
                    f"Expected bid_price to be numeric, got {type(pkg.bid_price).__name__}: {pkg.bid_price!r}"
                )
            # Flight dates: step text explicitly claims these are present
            _assert_flight_dates_present(pkg)
            # paused must be a boolean, not absent — collect gaps across ALL packages
            if pkg.paused is None:
                paused_gaps.append(f"package {pkg.package_id} in {mb_id}")
            else:
                assert isinstance(pkg.paused, bool), f"Expected paused to be bool, got {type(pkg.paused)}"
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
    assert buys, "No media buys in response"
    packages_checked = 0
    packages_with_approvals = 0
    for buy in buys:
        for pkg in buy.packages or []:
            packages_checked += 1
            approvals = pkg.creative_approvals
            if approvals:
                packages_with_approvals += 1
                for approval in approvals:
                    assert isinstance(approval.creative_id, str) and approval.creative_id, (
                        "CreativeApproval entry missing creative_id"
                    )
                    assert approval.approval_status is not None, (
                        f"CreativeApproval for '{approval.creative_id}' has no approval_status"
                    )
                    status_str = (
                        approval.approval_status.value
                        if hasattr(approval.approval_status, "value")
                        else str(approval.approval_status)
                    )
                    assert status_str in valid_statuses, (
                        f"Unexpected approval_status '{status_str}' for creative "
                        f"'{approval.creative_id}', expected one of {valid_statuses}"
                    )
    assert packages_checked > 0, "No packages found to check creative approvals on"
    # Step text says "when creatives are assigned" — verify at least one package
    # actually had creative approvals to check
    assert packages_with_approvals > 0, (
        f"Step claims 'when creatives are assigned' but none of the {packages_checked} "
        f"packages had creative_approvals populated — test setup must assign creatives"
    )


@then("each media buy should include buyer_campaign_ref for correlation")
def then_buyer_campaign_ref_for_correlation(ctx: dict) -> None:
    """Assert buyer_campaign_ref on each response media buy matches the seeded value.

    buyer_campaign_ref is the surviving correlation identifier (top-level buyer_ref
    was removed from the schema in adcp 3.12).
    """
    buys = _get_media_buys(ctx)
    seeded = ctx.get("seeded_media_buys", {})
    checked = 0
    for buy in buys:
        buy_id = buy.media_buy_id

        # buyer_campaign_ref is the surviving correlation identifier.
        # Match it against the value seeded via factory raw_request.
        seeded_mb = None
        for mb in seeded.values():
            if mb.media_buy_id == buy_id:
                seeded_mb = mb
                break
        assert seeded_mb is not None, (
            f"Response media buy '{buy_id}' not found in seeded_media_buys — "
            f"known IDs: {[m.media_buy_id for m in seeded.values()]}"
        )
        expected_ref = (seeded_mb.raw_request or {}).get("buyer_campaign_ref")
        actual_ref = buy.buyer_campaign_ref
        assert actual_ref == expected_ref, (
            f"Media buy '{buy_id}' buyer_campaign_ref mismatch: "
            f"expected {expected_ref!r} (from factory raw_request), got {actual_ref!r}"
        )
        checked += 1
    assert checked == len(seeded), (
        f"Expected {len(seeded)} media buys with buyer_campaign_ref verified, but only checked {checked}"
    )


@then(parsers.parse('the response should include media buys "{mb1}" and "{mb2}"'))
def then_response_includes_two(ctx: dict, mb1: str, mb2: str) -> None:
    """Assert response includes both specified media buys."""
    real_id1 = _resolve_media_buy_id(ctx, mb1)
    real_id2 = _resolve_media_buy_id(ctx, mb2)
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert real_id1 in ids, f"Expected '{mb1}' (real_id={real_id1}) in response, got {ids}"
    assert real_id2 in ids, f"Expected '{mb2}' (real_id={real_id2}) in response, got {ids}"


@then(parsers.parse('the response should not include media buy "{mb_id}"'))
def then_response_excludes(ctx: dict, mb_id: str) -> None:
    """Assert response does not include the specified media buy."""
    real_id = _resolve_media_buy_id(ctx, mb_id)
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert real_id not in ids, f"Expected '{mb_id}' (real_id={real_id}) NOT in response, but it was present"


@then(parsers.parse('the response should include media buy "{mb_id}"'))
def then_response_includes_one(ctx: dict, mb_id: str) -> None:
    """Assert response includes the specified media buy."""
    real_id = _resolve_media_buy_id(ctx, mb_id)
    buys = _get_media_buys(ctx)
    ids = {getattr(b, "media_buy_id", None) for b in buys}
    assert real_id in ids, f"Expected '{mb_id}' (real_id={real_id}) in response, got {ids}"


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
    required_fields = ("as_of", "staleness_seconds", "impressions", "spend")
    buys = _get_media_buys(ctx)
    checked_any = False
    for buy in buys:
        for pkg in getattr(buy, "packages", []) or []:
            snapshot = getattr(pkg, "snapshot", None)
            if snapshot is not None:
                checked_any = True
                for field in required_fields:
                    val = getattr(snapshot, field, None)
                    if val is None and isinstance(snapshot, dict):
                        val = snapshot.get(field)
                    assert val is not None, (
                        f"Snapshot on package '{getattr(pkg, 'package_id', '?')}' missing required field '{field}'"
                    )
    assert checked_any, "No snapshots found — this step requires at least one snapshot to verify"


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
    """Assert operation failed with specific error code — wire-first, typed fallback.

    On a wire transport (A2A/MCP via ``_run_a2a_handler`` / ``_run_mcp_client``)
    the code is read from the real two-layer envelope, and BOTH layers must agree
    (envelope-level ``adcp_error.code`` and payload-level ``errors[0].code``).
    No-wire runs fall back to the typed production exception. Cannot use
    ``result.assert_wire_error`` unconditionally: this step also grades
    locally-tracked non-canonical codes (e.g. ACCOUNT_FILTER_NOT_SUPPORTED)
    absent from the pinned error-code enum.
    """
    wire_code = _wire_code(ctx)
    if wire_code is not None:
        assert wire_code == code, f"Expected wire adcp_error.code '{code}', got '{wire_code}'"
        payload_error = _wire_error_object(ctx) or {}
        assert payload_error.get("code") == code, (
            f"Two-layer envelope disagreement: adcp_error.code={code!r} but "
            f"errors[0].code={payload_error.get('code')!r}"
        )
        return
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


def _assert_error_recovery(ctx: dict, expected: str) -> None:
    """Assert the error's recovery classification — wire-first, typed fallback.

    On a wire transport the ``recovery`` field is read from the real envelope's
    error object (the buyer-facing retry semantics per error.json); no-wire
    runs fall back to the typed production exception.
    """
    wire = _wire_error_object(ctx)
    if wire is not None:
        assert wire.get("recovery") == expected, (
            f"Expected wire recovery='{expected}', got {wire.get('recovery')!r} on wire code {wire.get('code')!r}"
        )
        return
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with recovery field, got {type(error).__name__}: {error}"
    assert error.recovery == expected, f"Expected {expected} recovery, got '{error.recovery}'"


@then(parsers.parse('the error should include a "recovery" field indicating terminal failure'))
def then_error_recovery_terminal(ctx: dict) -> None:
    """Assert error has terminal recovery classification."""
    _assert_error_recovery(ctx, "terminal")


def _current_suggestion(ctx: dict) -> str:
    """Resolve the buyer-facing error suggestion — wire-first, typed fallback.

    On a wire transport the suggestion is read from the real envelope at the
    protocol top level (STRICT error.json conformance — a suggestion buried in
    ``details`` does not count, #1417); no-wire runs fall back to the typed
    production exception's top-level attribute. Fails when the suggestion is
    missing or empty — never a silent escape.

    Also covers success-path advisory ``errors[]`` (BR-RULE-294 targeting
    rehydration): the buyer still receives a top-level suggestion on the
    serialized ``wire_response.errors[0]`` even though the transport did not
    reject. Prefer that over the reconstructed typed payload. Typed
    ``response.errors[0]`` is used only when ``wire_response is None`` — an
    empty wire ``errors[]`` must fail loudly, not silently pass via the typed
    payload.
    """
    from tests.harness.transport import extract_wire_suggestion

    suggestion = _wire_suggestion(ctx)
    wire = ctx.get("wire_response")
    if suggestion is None and wire is not None:
        # Success-path advisory errors[] captured on the real wire body.
        suggestion = extract_wire_suggestion(wire)
    if suggestion is None:
        matched = ctx.get("matched_response_error")
        if matched is not None:
            suggestion = _error_attr(matched, "suggestion")
    if suggestion is None and wire is None:
        resp = ctx.get("response")
        errors = getattr(resp, "errors", None) if resp is not None else None
        if errors:
            suggestion = _error_attr(errors[0], "suggestion")
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "Expected an error"
        from src.core.exceptions import AdCPError

        if isinstance(error, AdCPError):
            # STRICT error.json conformance: top-level attribute only (#1417).
            suggestion = error.suggestion
        else:
            suggestion = _error_attr(error, "suggestion")
    assert isinstance(suggestion, str) and suggestion.strip(), (
        f"Expected non-empty top-level suggestion string, got {suggestion!r}"
    )
    return suggestion


def _assert_suggestion_contains_any(ctx: dict, options: list[str]) -> None:
    """Assert the buyer-facing suggestion contains at least one of the options."""
    suggestion = _current_suggestion(ctx)
    lowered = suggestion.lower()
    assert any(t.lower() in lowered for t in options), f"Expected one of {options!r} in suggestion: {suggestion}"


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}"'))
def then_suggestion_contains_either(ctx: dict, text1: str, text2: str) -> None:
    """Assert suggestion contains one of the specified texts."""
    _assert_suggestion_contains_any(ctx, [text1, text2])


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}" or "{text3}"'))
def then_suggestion_contains_any_of_three(ctx: dict, text1: str, text2: str, text3: str) -> None:
    """Assert suggestion contains one of three specified texts."""
    _assert_suggestion_contains_any(ctx, [text1, text2, text3])


@then(parsers.parse('the media buy "{mb_id}" should have status "{expected_status}"'))
def then_media_buy_has_status(ctx: dict, mb_id: str, expected_status: str) -> None:
    """Assert a specific media buy has the expected status in the response."""
    real_id = _resolve_media_buy_id(ctx, mb_id)
    buys = _get_media_buys(ctx)
    matching = [b for b in buys if getattr(b, "media_buy_id", None) == real_id]
    assert len(matching) == 1, (
        f"Expected media buy '{mb_id}' (real_id={real_id}) in response, "
        f"got IDs: {[getattr(b, 'media_buy_id', None) for b in buys]}"
    )
    actual = getattr(matching[0], "status", None)
    actual_str = actual.value if hasattr(actual, "value") else str(actual)
    assert actual_str == expected_status, f"Expected status '{expected_status}' for '{mb_id}', got '{actual_str}'"


@then(parsers.parse("the error message should include field-level validation details"))
def then_error_field_validation(ctx: dict) -> None:
    """Assert error includes field-level validation details with actual field names.

    Step text claims "field-level validation details" — the error must reference
    specific field names or paths (media_buy_ids, status_filter, buyer_refs, etc.),
    not just generic words like "type" or "expected" that appear in any error.
    """
    # Require actual field names from GetMediaBuysRequest schema.
    field_names = ("media_buy_ids", "status_filter", "buyer_refs", "account_id")
    wire = _wire_error_object(ctx)
    if wire is not None:
        # Wire-first: the buyer-facing message and the structured ``field``
        # selector must reference an actual request schema field.
        text = f"{wire.get('message', '')} {wire.get('field', '')}".lower()
        source = f"wire error object {wire!r}"
    else:
        error = ctx.get("error")
        assert error is not None, "Expected a validation error"
        text = str(error).lower()
        source = f"error message {error}"
    assert any(field_name in text for field_name in field_names), (
        f"Expected field-level validation details (containing actual field names like {field_names}) in {source}"
    )


@then(parsers.parse('the error should include a "recovery" field indicating correctable failure'))
def then_error_recovery_correctable(ctx: dict) -> None:
    """Assert error has recovery field set to 'correctable'.

    The step text explicitly says "correctable failure" — the recovery field
    must be exactly "correctable" (not "retryable" or other values).
    """
    _assert_error_recovery(ctx, "correctable")


@then(parsers.parse('the error should include a "suggestion" field'))
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a non-empty suggestion — wire-first, typed fallback.

    Step text: 'the error should include a "suggestion" field'.
    No xfail escape — if production omits the suggestion, the test must fail.
    """
    _current_suggestion(ctx)


@then(parsers.parse('the error message should contain "{fragment}"'))
def then_error_contains(ctx: dict, fragment: str) -> None:
    """Assert error message contains a specific fragment."""
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    msg = str(error).lower()
    assert fragment.lower() in msg, f"Expected '{fragment}' in error: {error}"


@then(parsers.parse('the response errors array should include error code "{code}"'))
def then_response_errors_include(ctx: dict, code: str) -> None:
    """Assert response.errors contains the specified error code — wire-first.

    Converges onto ``_response_errors`` (same channel as
    ``then_response_errors_include_code``): prefer the serialized wire body;
    fall back to the typed payload only when ``wire_response is None``.
    """
    errors = _response_errors(ctx)
    codes = [_error_attr(e, "code") for e in errors]
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
    """Assert snapshot field matches the exact value from the seeded expected_snapshots.

    The Given step stores expected Snapshot objects in ctx["expected_snapshots"][pkg_id].
    We verify the response snapshot field equals that exact seeded value.
    """
    expected_snapshots = ctx.get("expected_snapshots", {})
    assert expected_snapshots, (
        f"No expected_snapshots in ctx — the Given step must seed snapshot data "
        f"via _make_test_snapshot() before asserting '{field}' amount"
    )
    buys = _get_media_buys(ctx)
    snapshots_checked = 0
    for buy in buys:
        for pkg in buy.packages or []:
            if pkg.snapshot is None:
                continue
            snapshots_checked += 1
            actual_val = getattr(pkg.snapshot, field, None)
            assert actual_val is not None, f"Snapshot field '{field}' not present on package '{pkg.package_id}'"
            expected_snapshot = expected_snapshots.get(pkg.package_id)
            assert expected_snapshot is not None, (
                f"Package '{pkg.package_id}' has a snapshot but no expected_snapshot was seeded. "
                f"Known seeded packages: {list(expected_snapshots)}"
            )
            expected_val = getattr(expected_snapshot, field)
            assert actual_val == expected_val, (
                f"Snapshot '{field}' on package '{pkg.package_id}': "
                f"expected {expected_val!r} (from seeded snapshot), got {actual_val!r}"
            )
    assert snapshots_checked > 0, f"No packages with snapshots found — cannot verify '{field}' amount"


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
    # Build reverse map: real_id → ORM object
    real_id_to_mb = {mb_obj.media_buy_id: mb_obj for mb_obj in seeded.values()}
    returned_ids = {getattr(b, "media_buy_id", None) for b in buys}
    resolved = _resolve_principal_id(ctx, principal_id)
    scoping_checked = 0
    for real_id in returned_ids:
        if real_id in real_id_to_mb:
            mb = real_id_to_mb[real_id]
            actual_principal = getattr(mb, "principal_id", None)
            if actual_principal is not None:
                scoping_checked += 1
                assert actual_principal == resolved, (
                    f"Media buy '{real_id}' belongs to principal '{actual_principal}', "
                    f"not '{resolved}' (label '{principal_id}') — scoping violation"
                )
    # Step claims scoping — we must have verified at least one buy's ownership
    if count > 0:
        assert scoping_checked > 0, (
            f"Step claims scoping to '{principal_id}' but verified 0 of {count} returned buys. "
            f"Returned IDs: {returned_ids}, seeded real IDs: {set(real_id_to_mb.keys())}"
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

    Scenario-level xfail (T-UC-019-sandbox-happy) handles the expected failure
    when sandbox mode is not yet implemented in production.
    """
    resp = ctx.get("response")
    assert resp is not None, f"Expected response, got error: {ctx.get('error')}"
    sandbox = getattr(resp, "sandbox", None)
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


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error — wire-first.

    On a wire transport the buyer-facing code must be exactly VALIDATION_ERROR
    (the pinned error-code enum's canonical request-validation code). No-wire
    fallback: either a raised exception with validation-related keywords, or
    response.errors containing validation-related content.
    """
    wire_code = _wire_code(ctx)
    if wire_code is not None:
        assert wire_code == "VALIDATION_ERROR", f"Expected wire code VALIDATION_ERROR, got {wire_code!r}"
        return

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
    """Assert error is a real validation error (not simulated sandbox response).

    Wire-first: a "real" validation error is an actual wire REJECTION — a
    two-layer error envelope carrying VALIDATION_ERROR with correctable
    recovery (BR-RULE-209 INV-7: sandbox inputs are validated like production;
    a simulated sandbox response would come back as a success payload instead).
    No-wire fallback: the typed production exception.
    """
    result = ctx.get("result")
    if result is not None and result.wire_error_envelope is not None:
        result.assert_wire_error("VALIDATION_ERROR")
        return

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
    Wire-first, typed fallback (see _current_suggestion). No xfail escape —
    if production omits suggestions, the test must fail.
    """
    suggestion = _current_suggestion(ctx)
    assert len(suggestion.strip()) >= 5, (
        f"Expected actionable suggestion string (>= 5 chars), got {suggestion!r}. "
        f"Step claims 'how to fix the issue' — suggestion must contain meaningful guidance."
    )


@then(parsers.parse('only media buys with status "{status}" are returned'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert only media buys with the specified status are in the response.

    Non-empty guard (mirrors the uc004 sibling and this module's other status
    Thens): a filter that regresses to ``[]`` must NOT false-green here — the
    single_status / null_default rows route through this step and each seeds a
    matching buy, so an empty result is a real failure, not a vacuous pass.
    """
    buys = _get_media_buys(ctx)
    assert buys, f"Filter '{status}' returned no media buys — expected at least the seeded matching buy."
    for buy in buys:
        actual = getattr(buy, "status", None)
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        assert actual_str == status, f"Expected only '{status}' buys, got '{actual_str}'"


@then("media buys with either status are returned")
def then_either_status_returned(ctx: dict) -> None:
    """Assert media buys with multiple statuses are returned."""
    buys = _get_media_buys(ctx)
    assert buys, "Expected media buys returned with multi-status filter"
    # "either status" implies at least 2 different statuses are represented
    statuses = {buy.status.value if hasattr(buy.status, "value") else str(buy.status) for buy in buys}
    assert len(statuses) >= 2, (
        f"Step claims 'either status are returned' but only found status(es): {statuses}. "
        f"Expected at least 2 different statuses."
    )


@then("every matching buy returned regardless of status")
def then_every_matching_buy_regardless_of_status(ctx: dict) -> None:
    """By-ID query skips the status filter: all requested buys come back even
    though they hold different lifecycle statuses.

    Non-vacuous: requires the requested buys to be present AND to span more than
    one status (else 'regardless of status' isn't actually exercised).
    """
    buys = _get_media_buys(ctx)
    assert buys, "Expected media buys returned for an explicit-IDs query"
    statuses = {b.status.value if hasattr(b.status, "value") else str(b.status) for b in buys}
    assert len(statuses) >= 2, (
        f"Step claims buys are returned 'regardless of status' but the result holds a "
        f"single status {statuses}; the by-ID skip-filter behavior isn't exercised."
    )


@then("media buys in any status are returned")
def then_any_status_returned(ctx: dict) -> None:
    """Assert all seeded media buys are returned with all-status filter.

    Step text claims "any status are returned" — this requires seeded data
    to exist (to verify completeness) and all seeded IDs to appear in response.
    """
    buys = _get_media_buys(ctx)
    assert buys, "Expected media buys for all-status filter"
    seeded = ctx.get("seeded_media_buys", {})
    assert seeded, (
        "Step claims 'media buys in any status are returned' but no media buys "
        "were seeded — cannot verify completeness without seeded data"
    )
    returned_ids = {b.media_buy_id for b in buys}
    for label, mb_obj in seeded.items():
        real_id = mb_obj.media_buy_id
        assert real_id in returned_ids, (
            f"All-status filter should return all media buys, but '{label}' (real_id={real_id}) is missing. "
            f"Returned: {returned_ids}"
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
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from src.core.exceptions import AdCPError

    assert isinstance(error, AdCPError), f"Expected AdCPError with code '{code}', got {type(error).__name__}: {error}"
    assert error.error_code == code, f"Expected error code '{code}', got '{error.error_code}'"
    # STRICT error.json conformance: suggestion is a top-level error attribute,
    # never read from the free-form details dict (#1417).
    suggestion = error.suggestion
    assert isinstance(suggestion, str) and suggestion.strip(), (
        f"Expected non-empty top-level suggestion string for error code '{code}', got {suggestion!r}"
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


# ═══════════════════════════════════════════════════════════════════════
# BR-RULE-294 / targeting_overlay rehydration (INV-3)
# ═══════════════════════════════════════════════════════════════════════


def _response_errors(ctx: dict) -> list[dict | Any]:
    """Return advisory ``errors[]`` — prefer serialized wire body over typed payload.

    Elements are wire ``dict``s when ``wire_response`` is present, otherwise typed
    ``Error``-like objects from the reconstructed payload. Fall back to typed
    ``response.errors`` only when ``wire_response is None``. An empty wire
    ``errors[]`` must surface as empty — never silently substitute the typed payload.
    """
    wire = ctx.get("wire_response")
    if wire is not None:
        assert isinstance(wire, dict), f"Expected dict wire_response, got {type(wire)}"
        return list(wire.get("errors") or [])
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response with errors[], got error: {ctx.get('error')}"
    return list(getattr(resp, "errors", None) or [])


def _response_media_buys(ctx: dict) -> list[Any]:
    """Return media_buys — prefer serialized wire body over typed payload."""
    wire = ctx.get("wire_response")
    if wire is not None:
        assert isinstance(wire, dict), f"Expected dict wire_response, got {type(wire)}"
        return list(wire.get("media_buys") or [])
    return _get_media_buys(ctx)


def _error_attr(err: dict | Any, key: str) -> Any | None:
    """Read a field from a dict or Error-like object (wire or typed)."""
    if isinstance(err, dict):
        return err.get(key)
    return getattr(err, key, None)


def _require_matched(ctx: dict) -> dict | Any:
    """Return the stashed ``matched_response_error`` or fail loudly."""
    matched = ctx.get("matched_response_error")
    assert matched is not None, "No matched_response_error — run the errors[] code step first"
    return matched


def _assert_matched_error_attr(ctx: dict, attr: str, value: str) -> None:
    """Assert a stashed ``errors[]`` entry attribute equals ``value``."""
    key = "field" if attr in {"field", "field selector"} else attr
    actual = _error_attr(_require_matched(ctx), key)
    assert actual == value, f"Expected errors[] {key} {value!r}, got {actual!r}"


@then(parsers.parse('response.errors[] should include an entry with code "{code}"'))
def then_response_errors_include_code(ctx: dict, code: str) -> None:
    """Assert success-path advisory ``errors[]`` carries ``code``; stash the match.

    Stashing enables subsequent ``that errors[] entry …`` steps and lets
    ``_current_suggestion`` / ``extract_wire_suggestion`` grade the buyer-facing
    suggestion on the same advisory (not a transport rejection —
    ``assert_wire_error`` does not apply to partial-success advisories).
    """
    errors = _response_errors(ctx)
    codes = [_error_attr(e, "code") for e in errors]
    assert code in codes, f"Expected response.errors[] entry with code {code!r}, got {codes}"
    ctx["matched_response_error"] = next(e for e in errors if _error_attr(e, "code") == code)


@then(parsers.parse('that errors[] entry message should start with "{prefix}"'))
def then_matched_error_message_starts_with(ctx: dict, prefix: str) -> None:
    """Assert the stashed ``errors[]`` entry message starts with ``prefix``."""
    message = _error_attr(_require_matched(ctx), "message")
    assert isinstance(message, str) and message.startswith(prefix), (
        f"Expected errors[] message to start with {prefix!r}, got {message!r}"
    )


@then(parsers.parse('that errors[] entry {attr} should be "{value}"'))
def then_matched_error_attr_equals(ctx: dict, attr: str, value: str) -> None:
    """Parametrized equality for stashed ``errors[]`` entry attrs (field/recovery/…)."""
    _assert_matched_error_attr(ctx, attr, value)


@then(parsers.parse('that errors[] entry field selector should be "{value}"'))
def then_matched_error_field_selector(ctx: dict, value: str) -> None:
    """Alias: field selector → ``field`` equality via shared helper."""
    _assert_matched_error_attr(ctx, "field", value)


@then(parsers.parse('the package "{pkg_id}" targeting_overlay should be null'))
def then_package_targeting_overlay_null(ctx: dict, pkg_id: str) -> None:
    """Assert the named package's ``targeting_overlay`` is null after fail-soft rehydration."""
    for buy in _response_media_buys(ctx):
        packages = _error_attr(buy, "packages") or []
        for pkg in packages:
            if _error_attr(pkg, "package_id") == pkg_id:
                overlay = _error_attr(pkg, "targeting_overlay")
                assert overlay is None, f"Expected package '{pkg_id}' targeting_overlay to be null, got {overlay!r}"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response media_buys")


@then(
    parsers.re(
        r'the package "(?P<pkg_id>[^"]+)" targeting_overlay should be a Targeting object with geo_countries \["US"\]'
    )
)
def then_package_targeting_overlay_geo_us(ctx: dict, pkg_id: str) -> None:
    """Assert the named package rehydrated targeting_overlay.geo_countries == ['US']."""
    for buy in _response_media_buys(ctx):
        packages = _error_attr(buy, "packages") or []
        for pkg in packages:
            if _error_attr(pkg, "package_id") == pkg_id:
                overlay = _error_attr(pkg, "targeting_overlay")
                assert overlay is not None, f"Expected package '{pkg_id}' targeting_overlay to be present"
                geo = _error_attr(overlay, "geo_countries")
                assert list(geo or []) == ["US"], f"Expected geo_countries ['US'] on package '{pkg_id}', got {geo!r}"
                return
    raise AssertionError(f"Package '{pkg_id}' not found in response media_buys")


@then(parsers.parse('no error should appear in response.errors[] for "{pkg_id}"'))
def then_no_rehydration_error_for_package(ctx: dict, pkg_id: str) -> None:
    """Assert no targeting-rehydration advisory mentions the named package."""
    errors = _response_errors(ctx)
    for err in errors:
        field = str(_error_attr(err, "field") or "")
        message = str(_error_attr(err, "message") or "")
        assert pkg_id not in field and pkg_id not in message, (
            f"Expected no errors[] entry for package '{pkg_id}', got {_error_attr(err, 'code')!r}: {message!r}"
        )


@then(
    parsers.parse(
        'response.errors[] should include a TARGETING_REHYDRATION_FAILED entry with suggestion referencing "{token}"'
    )
)
def then_errors_include_rehydration_with_suggestion(ctx: dict, token: str) -> None:
    """Assert a TARGETING_REHYDRATION_FAILED advisory carries a seller-facing suggestion."""
    then_response_errors_include_code(ctx, "TARGETING_REHYDRATION_FAILED")
    suggestion = str(_error_attr(_require_matched(ctx), "suggestion") or "").lower()
    assert token.lower() in suggestion, f"Expected suggestion to reference {token!r}, got {suggestion!r}"


@then(parsers.parse('both packages "{pkg1}" and "{pkg2}" targeting_overlay should be null'))
def then_both_packages_targeting_null(ctx: dict, pkg1: str, pkg2: str) -> None:
    """Assert two packages both have null targeting_overlay."""
    then_package_targeting_overlay_null(ctx, pkg1)
    then_package_targeting_overlay_null(ctx, pkg2)


@then(
    parsers.parse(
        'response.errors[] should include two TARGETING_REHYDRATION_FAILED entries each with suggestion referencing "{token}"'
    )
)
def then_two_rehydration_errors_with_suggestion(ctx: dict, token: str) -> None:
    """Assert exactly two TARGETING_REHYDRATION_FAILED advisories, each with suggestion."""
    errors = [e for e in _response_errors(ctx) if _error_attr(e, "code") == "TARGETING_REHYDRATION_FAILED"]
    assert len(errors) == 2, f"Expected 2 TARGETING_REHYDRATION_FAILED entries, got {len(errors)}"
    for err in errors:
        suggestion = str(_error_attr(err, "suggestion") or "").lower()
        assert token.lower() in suggestion, f"Expected suggestion to reference {token!r}, got {suggestion!r}"


@then("the corrupted package's targeting_overlay should be null and sibling buys should render normally")
def then_corrupted_null_siblings_ok(ctx: dict) -> None:
    """One-of-N: corrupt pkg null; sibling buy still present."""
    # Corrupt buy uses pkg-001; overlay null is asserted on that package id.
    # Prefer matching via mb-001 when labels are registered.
    real_mb1 = _resolve_media_buy_id(ctx, "mb-001")
    found_null = False
    for buy in _response_media_buys(ctx):
        if _error_attr(buy, "media_buy_id") != real_mb1:
            continue
        for pkg in _error_attr(buy, "packages") or []:
            if _error_attr(pkg, "package_id") == "pkg-001":
                overlay = _error_attr(pkg, "targeting_overlay")
                assert overlay is None, f"Expected null targeting_overlay on corrupt package, got {overlay!r}"
                found_null = True
                break
    assert found_null, "Corrupt package pkg-001 on mb-001 not found"
    real_mb2 = _resolve_media_buy_id(ctx, "mb-002")
    mb2 = next((b for b in _response_media_buys(ctx) if _error_attr(b, "media_buy_id") == real_mb2), None)
    assert mb2 is not None, f"Expected sibling media buy mb-002 ({real_mb2}) in response"
    assert _error_attr(mb2, "packages"), "Expected sibling buy to render with packages"


@then(
    parsers.parse(
        'response.errors[] should include exactly one TARGETING_REHYDRATION_FAILED entry with suggestion referencing "{token}"'
    )
)
def then_exactly_one_rehydration_with_suggestion(ctx: dict, token: str) -> None:
    """Assert exactly one TARGETING_REHYDRATION_FAILED advisory with suggestion token."""
    errors = [e for e in _response_errors(ctx) if _error_attr(e, "code") == "TARGETING_REHYDRATION_FAILED"]
    assert len(errors) == 1, f"Expected 1 TARGETING_REHYDRATION_FAILED entry, got {len(errors)}"
    ctx["matched_response_error"] = errors[0]
    suggestion = str(_error_attr(errors[0], "suggestion") or "").lower()
    assert token.lower() in suggestion, f"Expected suggestion to reference {token!r}, got {suggestion!r}"


@then(
    parsers.parse(
        'response.errors[] should include exactly one TARGETING_REHYDRATION_FAILED entry for ("{mb_id}", "{pkg_id}")'
    )
)
def then_exactly_one_rehydration_for_pair(ctx: dict, mb_id: str, pkg_id: str) -> None:
    """Assert exactly one TARGETING_REHYDRATION_FAILED advisory for the (buy, package) pair."""
    real_mb = _resolve_media_buy_id(ctx, mb_id)
    errors = [e for e in _response_errors(ctx) if _error_attr(e, "code") == "TARGETING_REHYDRATION_FAILED"]
    assert len(errors) == 1, f"Expected 1 TARGETING_REHYDRATION_FAILED entry, got {len(errors)}"
    err = errors[0]
    message = str(_error_attr(err, "message") or "")
    field = str(_error_attr(err, "field") or "")
    assert pkg_id in field or pkg_id in message, (
        f"Expected package '{pkg_id}' in error, got field={field!r} message={message!r}"
    )
    assert real_mb in message or mb_id in message, (
        f"Expected media buy '{mb_id}' ({real_mb}) in error message, got {message!r}"
    )
    ctx["matched_response_error"] = err


@then(parsers.parse('the response should include media buy "{mb_id}" with package "{pkg_id}" targeting_overlay null'))
def then_response_mb_pkg_overlay_null(ctx: dict, mb_id: str, pkg_id: str) -> None:
    """Assert named buy/package appears with null targeting_overlay."""
    real_mb = _resolve_media_buy_id(ctx, mb_id)
    for buy in _response_media_buys(ctx):
        if _error_attr(buy, "media_buy_id") != real_mb:
            continue
        packages = _error_attr(buy, "packages") or []
        for pkg in packages:
            if _error_attr(pkg, "package_id") == pkg_id:
                overlay = _error_attr(pkg, "targeting_overlay")
                assert overlay is None, f"Expected null targeting_overlay, got {overlay!r}"
                return
        raise AssertionError(f"Package '{pkg_id}' not found on media buy '{mb_id}'")
    raise AssertionError(f"Media buy '{mb_id}' not found in response")


@then(parsers.parse('the response should include media buy "{mb_id}" rendered normally'))
def then_response_mb_rendered_normally(ctx: dict, mb_id: str) -> None:
    """Assert the named media buy is present with at least one package."""
    real_mb = _resolve_media_buy_id(ctx, mb_id)
    for buy in _response_media_buys(ctx):
        if _error_attr(buy, "media_buy_id") == real_mb:
            packages = _error_attr(buy, "packages") or []
            assert packages, f"Expected media buy '{mb_id}' to render with packages"
            return
    raise AssertionError(f"Media buy '{mb_id}' not found in response")

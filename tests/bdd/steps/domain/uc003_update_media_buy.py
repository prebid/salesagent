"""BDD step definitions for UC-003: Update Media Buy.

Given steps build ctx["update_kwargs"], assembled into UpdateMediaBuyRequest
in the When step. Background steps set up the existing media buy via
conftest's _harness_env.

"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._harness_db import db_session
from tests.bdd.steps._outcome_helpers import (
    WIRE_MISSING,
    assert_wire_rejection,
    payload_or_none,
    require_payload,
    wire_absent,
    wire_dict,
    wire_entry,
    wire_field,
    wire_lookup,
)
from tests.bdd.steps.generic._auth import authenticate_env_as
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic._table import as_bool, drop_header_if
from tests.bdd.steps.generic.given_media_buy import _resolve_date_token
from tests.factories.mint import mint
from tests.factories.request import OMIT
from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY

#: The package budget an update scenario asks for. Deliberately NOT the 5000 the
#: Background's package is seeded with (``_setup_existing_media_buy``): an update to the
#: seeded value leaves the row unchanged whether or not the seller wrote it, so every
#: persistence assertion would hold vacuously.
_UPDATED_BUDGET = 7500.0

#: The value cell that leaves its field out of the update request (``a valid
#: update_media_buy request with:``), for an outline row that omits a field the others send.
_OMITTED = "(omitted)"

# ═══════════════════════════════════════════════════════════════════════
# Label mapping — Gherkin package labels → real package_ids
# ═══════════════════════════════════════════════════════════════════════
#
# Gherkin scenarios refer to packages with stable labels like "pkg_001",
# but MediaPackageFactory uses a Sequence (pkg_0000, pkg_0001, ...). Step
# definitions must resolve the label to the real database package_id
# before comparing or operating on packages. See UC-019 principal_id
# pattern for the same approach.


def _register_package(ctx: dict, label: str, package: Any) -> None:
    """Register a package under a Gherkin label.

    Called by conftest after setup_update_data() and by given_package_exists()
    when a new package is created. Subsequent step resolvers translate the
    label to the real factory-generated package_id.
    """
    ctx.setdefault("package_labels", {})[label] = package.package_id


def _resolve_package_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin package label to the real package_id.

    Falls back to returning the label itself so legacy scenarios (where the
    label and the real ID happen to coincide) continue to work.
    """
    return ctx.get("package_labels", {}).get(label, label)


def _resolve_media_buy_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin media buy label to the real media_buy_id.

    Falls back to returning the label itself for scenarios where the label
    matches the real ID (e.g. when the conftest doesn't set media_buy_labels).
    """
    return ctx.get("media_buy_labels", {}).get(label, label)


def _existing_media_buy(ctx: dict, label: str | None = None) -> Any:
    """The Background's media buy, checked against *label* when a step names one.

    A sentence that names the buy (``the media buy "mb_existing" ...``) and one that does
    not (``the media buy ...``) mean the same row; a label that resolves to some other id
    is a scenario naming a buy the env never seeded, and acting on the Background's buy
    instead would grade the wrong row.
    """
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — the route's seed builds it"
    if label is not None:
        resolved = _resolve_media_buy_id(ctx, label)
        assert resolved == mb.media_buy_id, (
            f"step names media buy {label!r} (resolves to {resolved!r}), but the seeded buy is {mb.media_buy_id!r}"
        )
    return mb


def _ensure_principal(ctx: dict, principal_id: str) -> None:
    """Create *principal_id* in the scenario's tenant unless the row already exists.

    Read first, not guessed from ``ctx["principal"]``: a second step naming the same
    principal (the ownership outlines name it as owner AND as caller) would otherwise
    insert it twice.
    """
    from sqlalchemy import select

    from src.core.database.models import Principal
    from tests.factories import PrincipalFactory

    tenant = ctx["tenant"]
    with db_session(ctx) as session:
        exists = session.scalars(
            select(Principal).filter_by(tenant_id=tenant.tenant_id, principal_id=principal_id)
        ).first()
    if exists is None:
        PrincipalFactory(principal_id=principal_id, tenant=tenant)
        ctx["env"]._commit_factory_data()


def _invoice_recipient(legal_name: str) -> dict[str, str]:
    """A BusinessEntity naming only *legal_name*, the one member core/business-entity.json requires."""
    return {"legal_name": legal_name}


def _expand_length_placeholder(value: str) -> str:
    """Expand ``<N character string>`` / ``<N char string>`` to N characters; else return *value*."""
    import re

    match = re.fullmatch(r"<(\d+)\s*char(?:acter)?\s*string>", value.strip())
    return "x" * int(match.group(1)) if match else value


def _stored_package(ctx: dict, label: str) -> tuple[Any, dict[str, Any]]:
    """``(budget, package_config)`` of the persisted package *label*, read through the repository.

    For the Thens whose claim is what the seller RECORDED: a response can echo a value the
    seller never wrote, so those read the row. Read through ``MediaBuyUoW`` (tenant-scoped,
    and pointed at the live server's database on e2e_rest) rather than the env's session.
    """
    from src.core.database.repositories.uow import MediaBuyUoW

    mb = _existing_media_buy(ctx)
    package_id = _resolve_package_id(ctx, label)
    with MediaBuyUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.media_buys is not None
        row = uow.media_buys.get_package(mb.media_buy_id, package_id)
        assert row is not None, f"no persisted package {package_id!r} (label {label!r}) on {mb.media_buy_id!r}"
        return row.budget, dict(row.package_config or {})


def _stored_assignment_routing(ctx: dict, label: str) -> dict[str, list[str] | None]:
    """``{creative_id: placement_ids}`` of the assignments persisted on package *label*.

    Read through ``MediaBuyUoW`` for the reason ``_stored_package`` gives: the claim is
    what the seller recorded, and on e2e_rest only the live server's database holds it.
    """
    from src.core.database.repositories.uow import MediaBuyUoW

    mb = _existing_media_buy(ctx)
    package_id = _resolve_package_id(ctx, label)
    with MediaBuyUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.assignments is not None
        rows = uow.assignments.get_by_media_buy_and_package(mb.media_buy_id, package_id)
        return {row.creative_id: row.placement_ids for row in rows}


def _original_flight(ctx: dict) -> tuple[datetime, datetime]:
    """The flight the Given 'the existing media buy has start_time ... and end_time ...' stored."""
    original_start = ctx.get("original_start_time")
    original_end = ctx.get("original_end_time")
    assert original_start is not None and original_end is not None, (
        "original_start_time/original_end_time not in ctx — "
        "missing prior Given step 'the existing media buy has start_time ... and end_time ...'"
    )
    return original_start, original_end


def _assert_stored_flight(stored: SimpleNamespace, *, start: datetime, end: datetime) -> None:
    """The seller's record of the buy (``uc002_invariants._stored_buy``) holds the flight *start*..*end*.

    The response does not carry the flight at all, so the stored record is the only place a
    flight change, or its absence, can be read.
    """
    from tests.bdd.steps.domain.uc002_invariants import _as_utc

    for field, expected in (("start_time", start), ("end_time", end)):
        actual = getattr(stored, field)
        assert actual is not None and _as_utc(actual) == expected, f"stored {field} is {actual}, expected {expected}"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — Background + request construction
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer owns an existing media buy")
@given("the media buy exists in the seller catalog")
def given_buyer_owns_media_buy(ctx: dict) -> None:
    """Verify the existing media buy is in ctx AND persisted in DB.

    Step text says 'Buyer owns an existing media buy' — verify both ctx state
    AND database persistence to prevent phantom media buys that exist only in
    test state.  Uses MediaBuyRepository (not raw select) per repository pattern.
    """
    _verify_existing_media_buy(ctx)


@given(parsers.parse('the Buyer owns an existing media buy with media_buy_id "{media_buy_id}"'))
def given_buyer_owns_media_buy_by_id(ctx: dict, media_buy_id: str) -> None:
    """Verify the existing media buy is in ctx, persisted in DB, and register its label.

    The media_buy_id from Gherkin (e.g. "mb_existing") is a label — the actual
    factory-generated ID may differ (label mechanism), or the UC-003 harness may
    seed the literal id (PR #1567 MediaBuyDualEnv), in which case the label maps
    to itself. This step registers the label mapping so subsequent steps
    (update_kwargs, assertions) can use the Gherkin label, and the shared verify
    checks DB persistence by the real id so a mis-seeded / phantom media buy
    fails loudly here rather than deep in the update path.
    """
    _verify_existing_media_buy(ctx)
    mb = ctx["existing_media_buy"]
    # Register the Gherkin label → real media_buy_id mapping
    ctx.setdefault("media_buy_labels", {})[media_buy_id] = mb.media_buy_id


def _verify_existing_media_buy(ctx: dict) -> None:
    """Shared verification: existing_media_buy is in ctx and persisted in DB."""
    from src.core.database.repositories.media_buy import MediaBuyRepository

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — conftest setup_update_data() failed"
    # Verify DB persistence — step claims media buy "exists", not just "is in ctx"
    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify media buy ownership"
    repo = MediaBuyRepository(env._session, tenant.tenant_id)
    db_mb = repo.get_by_id(mb.media_buy_id)
    assert db_mb is not None, (
        f"Media buy '{mb.media_buy_id}' not found in DB for tenant '{tenant.tenant_id}' — "
        "step claims 'Buyer owns an existing media buy' but it is not persisted"
    )


def _assert_wire_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Assert a REAL wire field equals the expected value (strict equality).

    Reads ctx['wire_response'] (the buyer-facing body), not the reconstructed
    payload. Shared dumb value comparator for the wire value-pin steps."""
    wire = wire_dict(ctx)
    actual = wire.get(field)
    assert actual == expected, f"Expected wire {field} '{expected}', got {actual!r} (wire keys: {sorted(wire)})"


@then(parsers.parse('the wire media_buy_status should be "{status}"'))
def then_wire_media_buy_status_value(ctx: dict, status: str) -> None:
    """Assert the REAL wire ``media_buy_status`` equals the expected DOMAIN status.

    Used to pin that a persisted status whose name differs from its AdCP
    value (e.g. 'scheduled') is normalized to the correct domain MediaBuyStatus on
    the update response (#1417)."""
    _assert_wire_field_equals(ctx, "media_buy_status", status)


@then(parsers.parse('the wire status should be "{status}"'))
def then_wire_status_value(ctx: dict, status: str) -> None:
    """Assert the REAL wire top-level ``status`` equals the expected PROTOCOL TaskStatus.

    The GA 3.1.0 storyboard pending_creatives_to_start.yaml grades top-level
    ``status`` = field_value 'completed' on synchronous create/update success
    (protocol-envelope.json required: [status]) — a different namespace from
    the domain ``media_buy_status``."""
    _assert_wire_field_equals(ctx, "status", status)


@then(parsers.parse('the wire valid_actions should include "{action}"'))
def then_wire_valid_actions_include(ctx: dict, action: str) -> None:
    """Assert the REAL wire ``valid_actions`` list contains the expected action.

    valid_actions must be derived from the NORMALIZED AdCP status, so a persisted
    'scheduled' buy reports pending_start's actions (not [] from the raw string)
    (#1417)."""
    wire = wire_dict(ctx)
    actions = wire.get("valid_actions") or []
    assert action in actions, f"Expected '{action}' in wire valid_actions, got {actions!r}"


@then(parsers.parse('the wire valid_actions should not include "{action}"'))
def then_wire_valid_actions_exclude(ctx: dict, action: str) -> None:
    """Assert the REAL wire ``valid_actions`` list is present and does not contain *action*.

    Present and non-empty, because an absent or empty list omits every action and so would
    pass for a seller that advertised nothing; the scenarios using this step pair it with an
    "include" line that names an action the buy's status does allow.
    """
    wire = wire_dict(ctx)
    actions = wire.get("valid_actions")
    assert actions, f"Expected a non-empty wire valid_actions, got {actions!r}"
    assert action not in actions, f"Expected '{action}' absent from wire valid_actions, got {actions!r}"


@given(parsers.parse('the media buy is in "{status}" status'))
def given_media_buy_status(ctx: dict, status: str) -> None:
    """Set precondition: mutate the existing media buy to the specified status.

    This is a Given step (precondition setup), NOT a Then assertion.
    The phrasing "is in X status" describes the desired precondition state,
    not an assertion on production code output.  The function sets mb.status
    and commits so that subsequent When/Then steps operate against a media
    buy in the specified status.
    """
    mb = ctx.get("existing_media_buy")
    assert mb is not None, (
        "No existing_media_buy in ctx — step claims 'the media buy is in "
        f'"{status}" status\' but no media buy exists to set status on'
    )
    # Precondition mutation: set status and persist to DB
    mb.status = status
    # A pre-start status must be internally consistent with the flight window:
    # the shared status taxonomy (#1545) date-refines generic serving aliases,
    # so a "scheduled" buy whose factory-default flight already started would
    # honestly refine to active. Move the window to the future so the seeded
    # precondition means what the scenario says (awaiting start).
    if status in ("scheduled", "pending_start"):
        from datetime import date, timedelta

        mb.start_date = date.today() + timedelta(days=7)
        mb.end_date = date.today() + timedelta(days=37)
        # start_time/end_time take precedence over the dates in the shared
        # resolver — clear any seeded past timestamps so the future window holds.
        mb.start_time = None
        mb.end_time = None
    env = ctx["env"]
    env._commit_factory_data()


@given(parsers.parse('the existing media buy has start_time "{start_time}" and end_time "{end_time}"'))
def given_existing_mb_start_end_time(ctx: dict, start_time: str, end_time: str) -> None:
    """Set start_time and end_time on the existing media buy ORM model.

    Stores the original values in ctx for later comparison by the Then step
    'the existing start_time and end_time should remain unchanged'.
    """
    from datetime import datetime

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set start_time/end_time"
    parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    mb.start_time = parsed_start
    mb.end_time = parsed_end
    env = ctx["env"]
    env._commit_factory_data()
    # Store originals for the Then step that checks they remain unchanged
    ctx["original_start_time"] = parsed_start.astimezone(UTC)
    ctx["original_end_time"] = parsed_end.astimezone(UTC)


@given(parsers.parse('the existing media buy has start_time "{start_time}"'))
def given_existing_mb_start_time(ctx: dict, start_time: str) -> None:
    """Set start_time on the existing media buy ORM model (end_time unchanged).

    Used by ext-e scenarios where end_time is set via the update request,
    not pre-existing on the media buy.
    """
    from datetime import datetime

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set start_time"
    parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    mb.start_time = parsed_start
    env = ctx["env"]
    env._commit_factory_data()


@given(parsers.parse("a valid update_media_buy request with:"))
def given_update_request_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Build update request kwargs from a data table."""
    import json

    _supported_fields = {
        "media_buy_id",
        "paused",
        "canceled",
        "start_time",
        "end_time",
        "packages",
        "budget",
        "idempotency_key",
        "invoice_recipient",
    }
    kwargs = _ensure_update_defaults(ctx)
    clock = ctx["env"].clock
    # Skip header row (pytest-bdd datatables include the header as first row)
    rows = drop_header_if(datatable, "field")
    # Track which fields the table explicitly sets
    table_fields = {row[0].strip() for row in rows}
    for row in rows:
        field, value = row[0].strip(), row[1].strip()
        assert field in _supported_fields, (
            f"Unrecognized update field '{field}' in datatable — "
            f"step silently drops it. Supported: {sorted(_supported_fields)}. "
            f"Add handling for '{field}' if it's a valid UpdateMediaBuyRequest field."
        )
        if value == _OMITTED:
            # An Examples cell that leaves the field out of the request altogether.
            kwargs.pop(field, None)
        elif field == "media_buy_id":
            # Resolve Gherkin label (e.g. "mb_existing") to real factory ID
            kwargs["media_buy_id"] = _resolve_media_buy_id(ctx, value)
        elif field == "paused":
            kwargs["paused"] = as_bool(value)
        elif field == "canceled":
            kwargs["canceled"] = as_bool(value)
        elif field == "start_time":
            kwargs["start_time"] = _resolve_date_token(value, clock)
        elif field == "end_time":
            kwargs["end_time"] = _resolve_date_token(value, clock)
        elif field == "budget":
            kwargs["budget"] = float(value)
        elif field == "packages":
            kwargs["packages"] = json.loads(value)
        elif field == "idempotency_key":
            kwargs["idempotency_key"] = _expand_length_placeholder(value)
        elif field == "invoice_recipient":
            # The authorization check is a production gap (#1417); T-UC-003-ext-t is xfailed.
            kwargs["invoice_recipient"] = _invoice_recipient(value)


@given(parsers.parse('the invoice_recipient "{recipient}" is not authorized for this account'))
def given_invoice_recipient_not_authorized(ctx: dict, recipient: str) -> None:
    """Precondition: the invoice_recipient is not authorized for the account.

    No authorization record links this recipient to the account, so an
    authorized-invoice-recipient check would reject the update. Production does
    not yet implement that check (BR-RULE-214, #1417), so the scenario
    is xfailed (T-UC-003-ext-t) until production catches up. Assert the request
    carries the override so the resolution path is exercised when implemented.
    """
    assert ctx.get("update_kwargs", {}).get("invoice_recipient") is not None, (
        "update_kwargs must carry invoice_recipient for the authorization check"
    )


@given("the request does NOT include start_time, end_time, or paused fields")
def given_request_omits_start_end_paused(ctx: dict) -> None:
    """Declarative guard — ensure start_time, end_time, paused are NOT in update_kwargs.

    The default update_kwargs only contains media_buy_id, so these fields are already
    absent. This step explicitly strips them in case prior Given steps added them.
    """
    kwargs = _ensure_update_defaults(ctx)
    for field in ("start_time", "end_time", "paused"):
        kwargs.pop(field, None)


@given("the request does NOT include an idempotency_key")
def given_request_omits_idempotency_key(ctx: dict) -> None:
    """Send NO idempotency_key, which 3.1.1 makes a rejection.

    ``media-buy/update-media-buy-request.json`` lists ``idempotency_key`` in ``/required``,
    so the absence is INVALID_REQUEST — see the version-cited note on
    @T-UC-003-idempotency-absent in the feature file.

    This sentence used to live in ``uc002_create_media_buy.py`` as "canonical, shared
    across UC-002/003", writing ``ctx["idempotency_key"] = None`` — a key no UC-003 step
    reads, so the scenario dispatched WITH the key and graded the opposite of what it says.
    The sentence appears on exactly one feature line, this use case's, and UC-002 bound it
    to none; ownership moves to the bag it describes, which is what the note it replaces
    already prescribed ("create/update behaviours genuinely differ").

    The sentinel rather than a pop: see ``_ensure_update_defaults``.
    """
    _ensure_update_defaults(ctx)["idempotency_key"] = OMIT_IDEMPOTENCY_KEY


@given("the request does NOT include an account field")
def given_request_omits_account(ctx: dict) -> None:
    """Send NO account, which 3.1.1 makes a rejection.

    ``media-buy/update-media-buy-request.json`` lists ``account`` in ``/required``
    (v3.1 added it, for governance checks and account resolution), so the absence is
    INVALID_REQUEST — which is what @T-UC-003-account-absent asserts.

    The sentence had NO definition at all, so the scenario raised
    StepDefinitionNotFoundError and its xfail recorded a spec/production gap where the
    real cause was missing wiring.
    """
    _ensure_update_defaults(ctx)["account"] = OMIT_ACCOUNT


@given("the request does not include any updatable fields")
def given_request_no_updatable_fields(ctx: dict) -> None:
    """Ensure the update request contains only media_buy_id — no updatable fields.

    Strips packages, paused, start_time, end_time, and any other
    fields that _update_media_buy_impl treats as updatable.
    """
    kwargs = _ensure_update_defaults(ctx)
    # Keep only media_buy_id, remove everything else
    media_buy_id = kwargs["media_buy_id"]
    kwargs.clear()
    kwargs["media_buy_id"] = media_buy_id


@given(parsers.parse("the request includes 1 package update with:"))
def given_package_update_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Add a package update to the request from a data table."""
    import json

    # product_id is intentionally accepted here so the immutable-field override
    # reaches production. AdCPPackageUpdate forbids it (extra=forbid), so it is
    # rejected — currently as VALIDATION_ERROR rather than the spec-expected
    # INVALID_REQUEST (BR-RULE-198), so T-UC-003-ext-w is xfailed (#1417).
    # canceled and start_time are package-update.json properties (adcp v3.2.7), sent as
    # written: start_time's literal "asap" is the value the package-scope row exists to
    # show refused (``not: {const: asap}``), so it is not resolved as a date token.
    _supported_pkg_fields = {
        "package_id",
        "budget",
        "paused",
        "targeting_overlay",
        "catalogs",
        "product_id",
        "canceled",
        "start_time",
    }
    kwargs = _ensure_update_defaults(ctx)
    pkg_update: dict[str, Any] = {}
    # Skip header row if present (pytest-bdd datatables include header as first row)
    rows = drop_header_if(datatable, "field")
    for row in rows:
        field, value = row[0].strip(), row[1].strip()
        assert field in _supported_pkg_fields, (
            f"Unrecognized package field '{field}' in datatable — "
            f"supported: {sorted(_supported_pkg_fields)}. "
            f"Add handling for '{field}' if it's a valid package update field."
        )
        if field == "package_id":
            pkg_update["package_id"] = value
        elif field == "budget":
            pkg_update["budget"] = float(value)
        elif field in ("paused", "canceled"):
            pkg_update[field] = as_bool(value)
        elif field in ("targeting_overlay", "catalogs"):
            pkg_update[field] = json.loads(value)
        elif field in ("product_id", "start_time"):
            pkg_update[field] = value
    assert pkg_update, "Datatable produced empty package update — check table format"
    kwargs["packages"] = [pkg_update]


@given(parsers.parse('the package "{package_id}" exists in the media buy'))
def given_package_exists(ctx: dict, package_id: str) -> None:
    """Verify or create the package in the existing media buy.

    `package_id` is a Gherkin label — resolve it against ctx['package_labels']
    (populated by conftest from the factory-seeded existing_package) before
    deciding whether a new package needs to be created.
    """
    pkg = ctx.get("existing_package")
    resolved = _resolve_package_id(ctx, package_id)
    if pkg is not None and pkg.package_id == resolved:
        return  # Label already maps to the existing package
    # Create the package if it doesn't exist or doesn't match
    from tests.factories import MediaPackageFactory

    assert ctx.get("existing_media_buy") is not None, "No existing_media_buy in ctx — cannot create package"
    env = ctx["env"]
    new_pkg = MediaPackageFactory(
        media_buy=ctx["existing_media_buy"],
        package_config={
            "product_id": "guaranteed_display",
            "budget": 5000.0,
        },
    )
    env._commit_factory_data()
    ctx["existing_package"] = new_pkg
    _register_package(ctx, package_id, new_pkg)


@given(parsers.parse('the package "{package_id}" has stored targeting_overlay {overlay}'))
def given_package_stored_overlay(ctx: dict, package_id: str, overlay: str) -> None:
    """Persist *overlay* (JSON) as the package's current targeting, then prove it took.

    The prior value is what lets a replacement be told apart from a seller that left the
    package alone: an update to an empty overlay reads the same either way.
    """
    import json

    pkg = ctx.get("existing_package")
    assert pkg is not None and pkg.package_id == _resolve_package_id(ctx, package_id), (
        f"package {package_id!r} is not the seeded existing package"
    )
    seeded = json.loads(overlay)
    pkg.package_config = {**(pkg.package_config or {}), "targeting_overlay": seeded}
    ctx["env"]._commit_factory_data()
    _budget, config = _stored_package(ctx, package_id)
    assert config.get("targeting_overlay") == seeded, (
        f"seeding package {package_id!r} with targeting_overlay {seeded!r} did not take: {config!r}"
    )


@given("the updated daily spend does not exceed max_daily_package_spend")
def given_daily_spend_ok(ctx: dict) -> None:
    """Declarative guard — verifies budgets are within daily spend limits.

    Checks that each package's budget is positive AND does not exceed the tenant's
    max_daily_package_spend (if configured). When update_kwargs has no packages,
    falls back to verifying the existing media buy's packages satisfy the constraint.
    """
    kwargs = ctx.get("update_kwargs", {})
    packages_to_check = kwargs.get("packages", [])

    # If no packages in update, verify existing packages satisfy the constraint
    if not packages_to_check:
        existing_mb = ctx.get("existing_media_buy")
        assert existing_mb is not None, (
            "No packages in update_kwargs AND no existing_media_buy — "
            "step claims 'daily spend does not exceed max_daily_package_spend' "
            "but there is no budget data to validate"
        )
        # Verify existing packages actually satisfy the constraint (not just assumed)
        existing_pkgs = getattr(existing_mb, "packages", None) or []
        assert len(existing_pkgs) > 0, (
            "existing_media_buy has no packages — step claims 'daily spend does not "
            "exceed max_daily_package_spend' but there are no packages to validate"
        )
    else:
        # Only verify budgets that are positive — zero/negative budgets are expected
        # to fail via the When/Then outcome, not via this guard step.
        pass

    # Verify against tenant's max_daily_package_spend if available
    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims 'does not exceed max_daily_package_spend' "
        "but cannot check the limit without a tenant"
    )
    max_daily = getattr(tenant, "max_daily_package_spend", None)
    if max_daily is not None:
        # Check update packages when present (only positive budgets — zero/negative
        # are expected to fail at budget validation, not at daily spend cap).
        for pkg in packages_to_check:
            budget = pkg.get("budget")
            if budget is not None and budget > 0:
                assert budget <= max_daily, (
                    f"Package budget {budget} exceeds tenant max_daily_package_spend {max_daily} — "
                    "step claims 'does not exceed max_daily_package_spend'"
                )
        # Also check existing packages when no update packages specified —
        # the step claims the constraint holds, so existing packages must satisfy it too.
        if not packages_to_check:
            existing_mb = ctx.get("existing_media_buy")
            for pkg in getattr(existing_mb, "packages", None) or []:
                budget = getattr(pkg, "budget", None)
                if budget is not None:
                    assert float(budget) <= float(max_daily), (
                        f"Existing package budget {budget} exceeds tenant max_daily_package_spend "
                        f"{max_daily} — step claims 'does not exceed max_daily_package_spend' "
                        "but existing packages violate the constraint"
                    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative_assignments / inline creatives on package updates
# ═══════════════════════════════════════════════════════════════════════


@given("the package update includes creative_assignments with:")
def given_package_update_creative_assignments(ctx: dict, datatable: list[list[str]]) -> None:
    """Add creative_assignments to the first package update from a data table.

    Handles variable column counts:
    - 1 col: creative_id only (error scenarios)
    - 2 cols: creative_id + placement_ids (placement error scenarios)
    - 3 cols: creative_id + weight + placement_ids (full happy path)
    First row is the header (skipped).
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    # Detect column layout from header
    header = [h.strip().lower() for h in datatable[0]]
    assignments = []
    for row in datatable[1:]:  # skip header row
        creative_id = row[0].strip()
        assignment: dict[str, Any] = {"creative_id": creative_id}
        if "weight" in header and len(row) > header.index("weight"):
            assignment["weight"] = float(row[header.index("weight")].strip())
        else:
            assignment["weight"] = 1.0  # default weight
        if "placement_ids" in header and len(row) > header.index("placement_ids"):
            placement_ids = [p.strip() for p in row[header.index("placement_ids")].strip().split(",") if p.strip()]
            if placement_ids:
                assignment["placement_ids"] = placement_ids
        assignments.append(assignment)
    kwargs["packages"][0]["creative_assignments"] = assignments
    # Track referenced creative_ids for later guard steps
    ctx["referenced_creative_ids"] = [a["creative_id"] for a in assignments]
    ctx["referenced_placement_ids"] = [pid for a in assignments for pid in (a.get("placement_ids") or [])]


@given(parsers.parse('the package update references creative "{creative_id}" via {array}'))
def given_package_update_references_creative(ctx: dict, creative_id: str, array: str) -> None:
    """Reference one creative through the named ARRAY parameter, and only that one.

    ``creative_ids`` and ``creative_assignments`` are the two request members that
    name creatives, they reach the same validation helper, and the only thing that
    differs on the wire is which array ``error.field`` points at. One step so an
    outline can grade both from one row each, instead of two scenarios that drift.

    Creates nothing: the scenarios using this are the not-found and bad-state paths,
    where a companion Given says what the library does or does not hold.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    if array == "creative_ids":
        pkg["creative_ids"] = [creative_id]
    elif array == "creative_assignments":
        pkg["creative_assignments"] = [{"creative_id": creative_id, "weight": 1.0}]
    else:
        raise AssertionError(
            f"{array!r} is not a request member that references creatives. The two are "
            "'creative_ids' and 'creative_assignments'; a third spelling means the "
            "scenario names something update_media_buy does not accept."
        )
    ctx["referenced_creative_ids"] = [creative_id]


@given("all referenced creative_ids exist in the creative library")
def given_creatives_exist_in_library(ctx: dict) -> None:
    """Create DB Creative records for all creative_ids referenced by creative_assignments."""
    from tests.factories.creative import CreativeFactory

    creative_ids = ctx.get("referenced_creative_ids", [])
    for cid in creative_ids:
        CreativeFactory.create(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250_image",
            approved=True,
            data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
        )


@given("all referenced creatives are in valid state (not error or rejected)")
def given_creatives_in_valid_state(ctx: dict) -> None:
    """Declarative guard — creatives created by the previous step are already approved.

    CreativeFactory with approved=True sets status='approved'. This step verifies
    the referenced creative_ids exist and were created with valid status by the
    prior 'all referenced creative_ids exist in the creative library' step.
    """
    ids = ctx.get("referenced_creative_ids")
    assert ids and len(ids) > 0, "No referenced creative_ids — missing prior step"
    # Prior step (given_creatives_exist_in_library) creates with approved=True.
    # Verify the status is not error/rejected via DB query.
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims 'all referenced creatives are in valid state' "
        "but cannot verify without tenant context"
    )
    invalid_statuses = ("error", "rejected")
    with db_session(ctx) as session:
        for cid in ids:
            cr = session.scalars(select(CreativeModel).filter_by(creative_id=cid, tenant_id=tenant.tenant_id)).first()
            assert cr is not None, (
                f"Creative {cid} not found in DB for tenant {tenant.tenant_id} — "
                "step claims creatives are 'in valid state' but creative doesn't exist"
            )
            assert cr.status not in invalid_statuses, (
                f"Creative {cid} is in '{cr.status}' state — step claims 'not error or rejected'"
            )


def ensure_product_declares_placements(ctx: dict, product: Any, placement_ids: list[str]) -> None:
    """Give *product* a seller-inline placement per id in *placement_ids*, if it declares none.

    A placement id is valid for a product only when the product declares that placement:
    core/creative-assignment.json calls ``placement_ids`` "routing IDs within
    already-purchased inventory", and core/product.json's ``placements`` is that
    inventory ("Creative assignments route creatives only after placement inventory is
    purchased"). A product declaring no placements offers none to route to. A product that
    already declares some is left as it is, so the caller judges ids against what it holds.
    """
    from tests.factories import StoredPlacementFactory

    if not product.placements:
        product.placements = [StoredPlacementFactory(placement_id=pid) for pid in placement_ids]
        ctx["env"]._commit_factory_data()


@given("all placement_ids are valid for the product")
def given_placement_ids_valid(ctx: dict) -> None:
    """The product declares every placement the assignments route to.

    The harness product declares no placements, which makes every placement id invalid for
    it, so the sentence is made true here rather than assumed.
    """
    pids = ctx.get("referenced_placement_ids")
    assert pids, "No referenced placement_ids — missing prior step"
    product = ctx.get("default_product")
    assert product is not None, (
        "No product in ctx under 'default_product' — "
        "step claims placements are 'valid for the product' but no product exists to validate against"
    )
    ensure_product_declares_placements(ctx, product, pids)
    allowed = {p.get("placement_id") for p in product.placements}
    invalid = [p for p in pids if p not in allowed]
    assert not invalid, (
        f"Placement IDs {invalid} are not in product's placements {allowed} — "
        "step claims 'all placement_ids are valid for the product'"
    )


@given("the package update includes inline creatives with valid content")
def given_package_update_inline_creatives(ctx: dict) -> None:
    """Add inline creative objects to the first package update.

    The sentence promises VALID content, and the hand-built asset map did not
    deliver it: ``{"primary": {url, width, height}}`` carries no ``asset_type``
    discriminator, so ``AdCPPackageUpdate.creatives`` rejects the item with
    ``assets.primary.AssetVariant Unable to extract tag using discriminator
    'asset_type' [type=union_tag_not_found]`` and the update never reaches the
    behaviour under test. Built through ``image_spec`` now, which is the same
    correction ``uc003_ext_error_scenarios.given_package_update_inline_creatives_bare``
    already carries with the same reasoning. The invalidity was never this
    scenario's subject, so it is fixed rather than declared malformed.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    kwargs["packages"][0]["creatives"] = [_inline_creative_payload("inline-cr-001", "Inline Creative 1")]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — optimization_goals on package updates (partition/boundary)
# ═══════════════════════════════════════════════════════════════════════


@given("the package update includes optimization_goals:")
def given_package_update_optimization_goals_default(ctx: dict) -> None:
    """Set default optimization_goals on the first package update (alt-flow scenario).

    This is the NO-DATATABLE variant (step text ends with ':'). The parameterized
    variant ``given_package_update_optimization_goals`` handles explicit goals values.
    Hardcodes a representative single-metric goal (clicks) for replacement semantics tests.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. Used by the alt-flow replacement semantics scenario.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    # Default: single metric goal (clicks) — representative for replacement semantics test.
    # The parameterized variant (with goals_value) handles scenario-specific goals.
    kwargs["packages"][0]["optimization_goals"] = json.loads('[{"kind": "metric", "metric": "clicks", "priority": 1}]')


@given(parsers.parse("the package update includes optimization_goals: {goals_value}"))
def given_package_update_optimization_goals(ctx: dict, goals_value: str) -> None:
    """Set optimization_goals on the first package update.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. UpdateMediaBuyRequest's package updates will reject the field.
    All scenarios are expected to xfail via conftest.py tag-based xfail.

    The goals_value is either a JSON array or the literal '<not provided>'.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]

    if goals_value.strip() == "<not provided>":
        # "<not provided>" means "omit the field" — tests preservation semantics.
        # Step text "includes optimization_goals: <not provided>" is a Scenario Outline
        # convention: the field slot exists in the template but this row omits the value.
        kwargs["packages"][0].pop("optimization_goals", None)
        assert "optimization_goals" not in kwargs["packages"][0], (
            "optimization_goals should be absent after '<not provided>' — preservation test requires omission"
        )
    else:
        kwargs["packages"][0]["optimization_goals"] = json.loads(goals_value)


@given("no targeting_overlay.keyword_targets is present in the same package update")
def given_no_keyword_targets_in_update(ctx: dict) -> None:
    """Ensure the package update does not include keyword_targets.

    This step is a declarative guard — it confirms that the package update
    doesn't have keyword_targets set (which would conflict with keyword_targets_add).
    """
    kwargs = _ensure_update_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in update_kwargs — 'no targeting_overlay.keyword_targets is present' "
        "requires a prior step that configures at least one package update. "
        "The context is missing expected structure."
    )
    pkg = kwargs["packages"][0]
    overlay = pkg.get("targeting_overlay")
    if isinstance(overlay, dict):
        overlay.pop("keyword_targets", None)
    elif overlay is not None and hasattr(overlay, "keyword_targets"):
        # Handle Pydantic model overlays (same pattern as negative_keywords guard)
        overlay.keyword_targets = None


@given("no targeting_overlay.negative_keywords is present in the same package update")
def given_no_negative_keywords_in_update(ctx: dict) -> None:
    """Ensure the package update does not include negative_keywords in targeting_overlay.

    Declarative guard — analogous to the keyword_targets guard above. Prevents
    conflict with negative_keywords_add (BR-RULE-083).

    Requires packages to exist in the update — this step is always preceded by
    a 'the request includes 1 package update with:' step in the Gherkin.
    """
    kwargs = _ensure_update_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in update_kwargs — 'no targeting_overlay.negative_keywords is present' "
        "requires a prior step that configures at least one package update. "
        "The context is missing expected structure."
    )
    pkg = kwargs["packages"][0]
    overlay = pkg.get("targeting_overlay")
    if overlay is None:
        return  # No overlay → negative_keywords trivially absent
    if isinstance(overlay, dict):
        overlay.pop("negative_keywords", None)
    elif hasattr(overlay, "negative_keywords"):
        # Handle Pydantic model overlays
        overlay.negative_keywords = None


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — keyword operations on package updates
# ═══════════════════════════════════════════════════════════════════════


def _set_keyword_field_on_package(ctx: dict, field: str, default_value: list[dict[str, Any]]) -> None:
    """Set a keyword operation field on the first package update.

    Shared helper for keyword_targets_add, keyword_targets_remove,
    negative_keywords_add, and negative_keywords_remove steps.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    kwargs["packages"][0][field] = default_value


@given("the package update includes keyword_targets_add:")
def given_package_update_keyword_targets_add(ctx: dict) -> None:
    """Set default keyword_targets_add on the first package update (alt-flow scenario)."""
    _set_keyword_field_on_package(ctx, "keyword_targets_add", [{"keyword": "shoes", "match_type": "broad"}])


@given("the package update includes keyword_targets_remove:")
def given_package_update_keyword_targets_remove(ctx: dict) -> None:
    """Set default keyword_targets_remove on the first package update (alt-flow scenario)."""
    _set_keyword_field_on_package(ctx, "keyword_targets_remove", [{"keyword": "shoes", "match_type": "broad"}])


@given("the package update includes negative_keywords_add:")
def given_package_update_negative_keywords_add(ctx: dict) -> None:
    """Set negative_keywords_add on the first package update (alt-flow scenario).

    Note: Step text ends with ':' (Gherkin table indicator) but this function uses
    hardcoded defaults. Feature files using this step do not provide a DataTable;
    the ':' is part of the step text pattern matching the Gherkin scenario phrasing.

    FIXME: Accept datatable parameter when feature files provide one.
    """
    _set_keyword_field_on_package(ctx, "negative_keywords_add", [{"keyword": "cheap", "match_type": "exact"}])


@given("the package update includes negative_keywords_remove:")
def given_package_update_negative_keywords_remove(ctx: dict) -> None:
    """Set negative_keywords_remove on the first package update (alt-flow scenario).

    Note: Step text ends with ':' (Gherkin table indicator) but this function uses
    hardcoded defaults. Feature files using this step do not provide a DataTable;
    the ':' is part of the step text pattern matching the Gherkin scenario phrasing.

    FIXME: Accept datatable parameter when feature files provide one.
    """
    _set_keyword_field_on_package(ctx, "negative_keywords_remove", [{"keyword": "cheap", "match_type": "exact"}])


# ═══════════════════════════════════════════════════════════════════════
# WHEN step — dispatch update request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the update_media_buy request")
def when_send_update_request(ctx: dict) -> None:
    """Build UpdateMediaBuyRequest and dispatch through harness."""

    update_kwargs = ctx.get("update_kwargs", {})
    # Resolve Gherkin package_id labels ("pkg_001") to real factory-generated
    # package_ids before sending the request to production code. See
    # _resolve_package_id / _register_package above.
    packages = update_kwargs.get("packages")
    if packages:
        for pkg in packages:
            if isinstance(pkg, dict) and "package_id" in pkg:
                pkg["package_id"] = _resolve_package_id(ctx, pkg["package_id"])
    # Dispatch the RAW flat bag, not a locally-constructed model. Building
    # UpdateMediaBuyRequest here meant a payload the schema rejects never reached a
    # transport: the ValidationError was raised in the TEST process and stored as
    # ctx["error"], so every "malformed input is rejected with X" scenario graded the
    # harness's own exception -- keys ['code','message'], no suggestion -- instead of the
    # wire envelope production actually emits, which does carry one. Such a test cannot fail
    # when the server stops rejecting the payload, because the server was never asked.
    #
    # The harness already supports this form; _is_update_request's docstring says the raw
    # dispatch exists precisely "for scenarios whose payload the LOCAL UpdateMediaBuyRequest
    # must reject". The step simply was not using it.
    #
    # The required fields are LITERALS no longer. `account={"account_id": "acct_test"}` and
    # `idempotency_key="test-idem-key-0001"` used to be written into the bag at this point,
    # which put them beyond the reach of every Given that means to remove one: "the request
    # does NOT include an account field" and the `<not provided>` Examples rows all popped a
    # key this step then put straight back, so those rows graded a request carrying the
    # field they say is absent. `apply_required_update_fields` setdefaults them instead, so
    # a Given's OMIT sentinel survives to the wire — and it runs HERE as well as in
    # `_ensure_update_defaults` because this sentence is also UC-026's and the dual-emit
    # feature's When, and UC-026 builds its bag with its own `_ensure_update_kwargs`.
    #
    # NO `identity=` EITHER. A no-auth scenario used to dispatch `identity=None`, which is
    # not a request field: `_flatten_update_request` passes it into the flat wire params, so
    # the DTO rejected it under extra="forbid" and all three transports answered
    # INVALID_REQUEST to an auth row asserting AUTH_MISSING. The credential is the channel —
    # `given_buyer_no_auth` stashes a token-less one in ctx["credential"] and
    # `dispatch_request` presents it, so the REAL resolver refuses a real request. UC-019
    # removed this exact shape from its own dispatch (_dispatch_query); this was the last copy.
    #
    # The defaults are written INTO the scenario's own bag, not a copy, so the minted key is
    # the same on a second dispatch within one scenario — what an idempotent-replay row needs.
    dispatch_request(ctx, **apply_required_update_fields(ctx, update_kwargs))


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — update-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the existing start_time and end_time should remain unchanged")
def then_start_end_time_unchanged(ctx: dict) -> None:
    """Assert the media buy's start_time and end_time were not altered by the update.

    Reloads the media buy from DB and compares against the values stored in ctx
    by the Given step 'the existing media buy has start_time ... and end_time ...'.
    """
    from tests.bdd.steps.domain.uc002_invariants import _stored_buy

    start, end = _original_flight(ctx)
    _assert_stored_flight(_stored_buy(ctx, _existing_media_buy(ctx).media_buy_id), start=start, end=end)


@then("the media buy's stored start_time should be the instant the update took effect")
def then_stored_start_is_applied_instant(ctx: dict) -> None:
    """A start_time of "asap" is stored as the instant the update took effect.

    core/start-timing.json: "asap" is "Start campaign as soon as possible", and
    update-media-buy-response.json ``implementation_date`` is the "ISO 8601 timestamp when
    changes take effect". Both instants come from the seller's clock, so the comparison
    holds on e2e_rest too. The scenario seeds a start far from now, so a seller that kept
    the prior start, or stored anything but the applied instant, falls outside the tolerance.
    """
    from tests.bdd.steps.domain.uc002_invariants import _assert_start_resolved_to

    applied = datetime.fromisoformat(str(wire_field(ctx, "implementation_date")).replace("Z", "+00:00"))
    _assert_start_resolved_to(_stored_existing_buy(ctx).start_time, applied, "the update's implementation_date")


@then("the response should contain media_buy_id")
def then_response_has_media_buy_id(ctx: dict) -> None:
    """Assert response contains media_buy_id matching the existing media buy."""
    resp = require_payload(ctx)
    actual = getattr(resp, "media_buy_id", None)
    assert actual is not None, f"Expected media_buy_id in response, got {actual!r}"
    mb = ctx.get("existing_media_buy")
    if mb is not None:
        expected = mb.media_buy_id
        assert actual == expected, (
            f"media_buy_id mismatch: expected '{expected}' from existing media buy, got '{actual}'"
        )


@then(parsers.parse('the response should contain media_buy_id "{label}"'))
def then_response_has_media_buy_id_label(ctx: dict, label: str) -> None:
    """Assert the WIRE media_buy_id is the id the Gherkin label names.

    The label resolves to the seeded buy's real id; the value compared is the one the
    buyer received, so an update that answered for some other buy fails here.
    """
    _assert_wire_field_equals(ctx, "media_buy_id", _resolve_media_buy_id(ctx, label))


@then(parsers.parse("the package budget should be persisted as {budget:d}"))
def then_package_budget_persisted(ctx: dict, budget: int) -> None:
    """Assert the updated package's STORED budget is *budget*.

    Persistence is the claim (BR-RULE-020: adapter success persists the change), and a
    response can report a budget the seller never wrote, so the row is read. The package
    is the one the update targeted, ``pkg_001``.

    Moved here from ``generic/then_media_buy.py``, which is not a registered step module,
    so the sentence had no live definition at all; that copy also looked the package up by
    id alone, outside any tenant scope.
    """
    stored, _config = _stored_package(ctx, "pkg_001")
    assert stored is not None and float(stored) == float(budget), (
        f"Expected the stored package budget to be {budget}, got {stored!r}"
    )


@then("the response should contain implementation_date that is null")
def then_implementation_date_null(ctx: dict) -> None:
    """Assert response has a null implementation_date (pending approval)."""
    resp = require_payload(ctx)
    assert resp.implementation_date is None, (
        f"Expected implementation_date to be None (pending approval), got {resp.implementation_date!r}"
    )


@then("the response should contain an implementation_date that is not null")
def then_implementation_date_not_null(ctx: dict) -> None:
    """Assert response has a non-null implementation_date.

    Step text: "the response should contain an implementation_date that is not null"
    Contract: implementation_date MUST be present and non-null on success responses.
    If production doesn't populate it, that's a SPEC-PRODUCTION GAP (xfail).
    """
    from datetime import datetime

    resp = require_payload(ctx)
    # Guard: this step only makes sense on a success response, not an error
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check implementation_date on error"
    impl_date = resp.implementation_date
    # Step text claims "not null" unconditionally — hard assert.
    # If production doesn't populate this, the SCENARIO should be xfailed in conftest.py,
    # not the step body.
    assert impl_date is not None, (
        "implementation_date is None in response — step text claims 'not null' unconditionally"
    )
    # impl_date is not None — verify it's a meaningful datetime
    if isinstance(impl_date, str):
        parsed = datetime.fromisoformat(impl_date.replace("Z", "+00:00"))
        assert parsed.year >= 2020, f"implementation_date parsed to implausible date: {parsed!r}"
        assert parsed.year <= 2100, f"implementation_date is implausibly far in the future: {parsed!r}"
    else:
        assert isinstance(impl_date, datetime), (
            f"implementation_date should be datetime or ISO string, got {type(impl_date).__name__}: {impl_date!r}"
        )
        assert impl_date.year >= 2020, f"implementation_date has implausible year: {impl_date!r}"
        assert impl_date.year <= 2100, f"implementation_date is implausibly far in the future: {impl_date!r}"


@then(parsers.parse('the response should contain affected_packages including "{package_id}"'))
def then_affected_packages_include(ctx: dict, package_id: str) -> None:
    """Assert affected_packages contains the specified package.

    `package_id` is a Gherkin label — resolve it to the real package_id
    produced by the factory before comparing against the production response.
    """
    resp = require_payload(ctx)
    affected = getattr(resp, "affected_packages", None) or []
    pkg_ids = [
        getattr(p, "package_id", None) or (p.get("package_id") if isinstance(p, dict) else None) for p in affected
    ]
    resolved = _resolve_package_id(ctx, package_id)
    assert resolved in pkg_ids, f"Expected '{resolved}' (label '{package_id}') in affected_packages, got {pkg_ids}"


@then("the response should list no affected packages")
def then_no_affected_packages(ctx: dict) -> None:
    """Assert the WIRE names no package as modified: ``affected_packages`` absent or ``[]``.

    update-media-buy-response.json ``/oneOf/0/properties/affected_packages`` holds "each
    modified package" and is optional; "Campaign-level updates that do not modify packages
    may return an empty array". So an update that modifies no package lists none, in
    either spelling, and any entry is a package the seller claims to have changed.
    """
    affected = wire_lookup(ctx, "affected_packages")
    assert affected is WIRE_MISSING or affected == [], (
        f"the update modified no package, but the wire lists affected_packages {affected!r}"
    )


@then("the response should contain affected_packages")
def then_affected_packages_present(ctx: dict) -> None:
    """Assert affected_packages contains the package(s) belonging to the media buy.

    Step text: "the response should contain affected_packages"
    Contract: affected_packages MUST contain the package_id of the existing
    package on the media buy being updated. Presence alone (len > 0) is not
    enough — the specific package_id from ctx["existing_package"] must appear.
    """
    resp = require_payload(ctx)
    assert "error" not in ctx, f"Update errored ({ctx.get('error')}) — cannot check affected_packages on error"
    affected = getattr(resp, "affected_packages", None)
    assert affected is not None, (
        f"affected_packages is None on response (type: {type(resp).__name__}) — "
        "step text claims response should contain affected_packages"
    )
    assert isinstance(affected, list), (
        f"affected_packages should be a list, got {type(affected).__name__}: {affected!r}"
    )
    existing_pkg = ctx.get("existing_package")
    assert existing_pkg is not None, (
        "Test harness did not register ctx['existing_package'] — cannot verify "
        "affected_packages contents without knowing which package was updated"
    )
    expected_pkg_id = getattr(existing_pkg, "package_id", None)
    assert expected_pkg_id is not None, f"existing_package has no package_id (type: {type(existing_pkg).__name__})"
    actual_pkg_ids = {
        getattr(p, "package_id", None) or (p.get("package_id") if isinstance(p, dict) else None) for p in affected
    }
    assert expected_pkg_id in actual_pkg_ids, (
        f"Expected package '{expected_pkg_id}' in affected_packages, got {actual_pkg_ids}"
    )


@then(parsers.parse("the affected package should show the updated budget of {budget:d}"))
def then_affected_package_budget(ctx: dict, budget: int) -> None:
    """Assert the specific affected package shows the updated budget value.

    Step text: "the affected package should show the updated budget of {budget}"
    Contract: the AffectedPackage matching ctx["existing_package"].package_id
    MUST echo the requested budget. If production doesn't echo budget, that's
    a SPEC-PRODUCTION GAP (xfail the scenario).
    """
    resp = require_payload(ctx)
    # Guard: this step only makes sense on a success response
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check budget on error"
    affected = getattr(resp, "affected_packages", None) or []
    existing_pkg = ctx.get("existing_package")
    assert existing_pkg is not None, (
        "Test harness did not register ctx['existing_package'] — cannot identify which affected package to check"
    )
    expected_pkg_id = getattr(existing_pkg, "package_id", None)
    assert expected_pkg_id is not None, f"existing_package has no package_id (type: {type(existing_pkg).__name__})"
    # Locate the specific package we updated — not just "the first one".
    pkg = next(
        (
            p
            for p in affected
            if (getattr(p, "package_id", None) or (p.get("package_id") if isinstance(p, dict) else None))
            == expected_pkg_id
        ),
        None,
    )
    assert pkg is not None, (
        f"Expected package '{expected_pkg_id}' in affected_packages, got "
        f"{[getattr(p, 'package_id', None) or (p.get('package_id') if isinstance(p, dict) else None) for p in affected]}"
    )
    actual_budget = getattr(pkg, "budget", None)
    if actual_budget is None and isinstance(pkg, dict):
        actual_budget = pkg.get("budget")
    # Step text claims "updated budget of {budget}" unconditionally — hard assert.
    # If production doesn't echo budget, the SCENARIO should be xfailed in conftest.py.
    #
    assert actual_budget is not None, (
        f"affected package '{expected_pkg_id}' budget is None — step text claims "
        f"'updated budget of {budget}' unconditionally"
    )
    # actual_budget is not None — validate type and value
    assert isinstance(actual_budget, (int, float)), (
        f"Expected budget to be numeric, got {type(actual_budget).__name__}: {actual_budget!r}"
    )
    assert float(actual_budget) == float(budget), (
        f"Expected budget {budget} on affected package '{expected_pkg_id}', got {actual_budget}"
    )


@then("the response envelope should include a sandbox flag")
def then_response_has_sandbox(ctx: dict) -> None:
    """Assert response includes sandbox information.

    Step text: "the response envelope should include a sandbox flag"
    Contract: sandbox MUST be present as a boolean on the response envelope.
    If production doesn't include it, that's a SPEC-PRODUCTION GAP (xfail).
    """
    from pydantic import BaseModel

    resp = require_payload(ctx)
    # Guard: this step only makes sense on a success response, not an error
    assert "error" not in ctx, f"Update errored ({ctx.get('error')}) — cannot check sandbox flag on an error response"
    # Guard: response must be a Pydantic model (not a raw dict/string) — the
    # transport dispatch always yields a typed response on success.
    assert isinstance(resp, BaseModel), (
        f"Response is not a Pydantic model (type: {type(resp).__name__}) — cannot inspect for sandbox flag"
    )
    # sandbox may live on the response directly or on a wrapper envelope
    sandbox = getattr(resp, "sandbox", None)
    if sandbox is None:
        dumped = resp.model_dump()
        sandbox = dumped.get("sandbox")
    # Step text claims "should include a sandbox flag" unconditionally — hard assert.
    # If production doesn't include sandbox, the SCENARIO should be xfailed in conftest.py.
    #
    assert sandbox is not None, (
        f"sandbox flag not present on response (type: {type(resp).__name__}) — "
        "step text claims envelope 'should include' it unconditionally"
    )
    # sandbox is not None — verify it's a boolean (not just any truthy/falsy value)
    # Step text claims "should include a sandbox flag" — presence + type, not a specific value.
    assert isinstance(sandbox, bool), f"Expected sandbox to be bool, got {type(sandbox).__name__}: {sandbox!r}"


@then('the response should NOT contain an "errors" field')
@then('the response should NOT have an "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert the response does not contain an 'errors' field at all.

    Step text says 'NOT contain' — the key must be ABSENT, not merely null: an empty list
    or a serialized null both mean the field exists. wire_absent encodes that distinction.

    Asserted on the WIRE rather than on resp.model_dump(): a round-trip through the model
    proves the serializer is self-consistent, not what the buyer actually received.
    """
    wire_absent(ctx, "errors")


# Step 'the response should contain an "errors" array' is owned by
# tests/bdd/steps/generic/then_media_buy.py, which grades the WIRE rejection
# (``ctx["result"].assert_wire_error_is_schema_conformant()`` against pinned
# core/error.json). The copy that lived here read ctx["error_response"] — a key
# _promote_update_errors set from an in-process UpdateMediaBuyError that the test
# process itself had reconstructed, so it could not fail when the wire envelope
# regressed. Both the promoter and the key are gone; the generic step subsumes it.


@then("the response should contain a task_id")
def then_response_contains_task_id(ctx: dict) -> None:
    """Assert the submitted envelope carries a non-empty task_id on the real wire.

    POST-S8: the buyer receives a task_id to poll tasks/get. Graded on the
    serialized wire (ctx['wire_response']) so an A2A/MCP/REST regression that
    drops task_id is caught — not on the coerced typed payload.
    """
    data = wire_dict(ctx)
    task_id = data.get("task_id")
    assert isinstance(task_id, str) and task_id, (
        f"Submitted response must carry a non-empty task_id on the wire, got {task_id!r} (wire keys: {sorted(data)})"
    )


def _assert_a2a_submitted_task_has_no_artifacts(ctx: dict) -> None:
    """Defense-in-depth for the A2A submitted case: grade the REAL protobuf Task.

    On A2A a submitted outcome is conveyed via the Task state and the transport
    clears artifacts (``del task.artifacts[:]``), so the NOT-contain checks over
    the synthesized wire dict are architecturally vacuous on that transport.
    Assert on ``env.last_a2a_task`` (the actual transport object, stashed by the
    dispatcher) that no artifact leaked — mirroring the update path's guard
    (test_a2a_update_media_buy_submitted_guard.py). PR #1567 round-3.
    """
    from tests.harness.transport import Transport

    # The A2A branch below is a LEGITIMATE transport-aware assertion (see the
    # docstring): on A2A the wire-dict NOT-contain checks are vacuous, so the real
    # Task is graded instead. What is NOT legitimate is reaching it with the
    # transport unset — `None is not Transport.A2A` silently reads as "some other
    # transport" and returns, grading nothing on the only transport this guard
    # exists for. An unset transport is a wiring bug; report it (salesagent-n78j0.1.5).
    transport = ctx.get("transport")
    if transport is None:
        raise AssertionError(
            "A2A submitted-artifact guard reached with ctx['transport'] unset — the "
            "check would silently downgrade to 'not A2A' and grade nothing. Dispatch "
            "through dispatch_request (which raises on an unset transport) or set "
            "ctx['transport'] explicitly."
        )
    if transport is not Transport.A2A:
        return
    if wire_dict(ctx).get("status") != "submitted":
        return
    env = ctx.get("env")
    task = getattr(env, "last_a2a_task", None)
    assert task is not None, "A2A submitted case must stash the real Task (env.last_a2a_task)"
    assert not task.artifacts, (
        f"A2A submitted Task must carry NO artifacts (submitted is conveyed via the Task "
        f"state; an artifact would leak a premature result), got {task.artifacts!r}"
    )


@then(parsers.parse('the response should NOT contain "{field_name}" field'))
def then_response_not_contain_field(ctx: dict, field_name: str) -> None:
    """Assert the response does NOT contain a given field.

    BR-RULE-018 INV-1/INV-2: Success responses must not contain error fields,
    and error responses must not contain success-specific fields. Both directions read
    the REAL serialized wire — ``ctx["wire_response"]`` on success, the two-layer error
    envelope on failure — never ``model_dump()``: media_buy_id and implementation_date
    are not declared on UpdateMediaBuySubmitted, so a model-level check passes vacuously
    and can never catch a wire regression (e.g. the A2A submitted payload leaking a
    field). Absent-or-null on the wire satisfies "does NOT contain" (a null field is
    not conveyed); a real value is a contract violation.
    """
    # Success-path response — assert against the buyer-facing serialized wire.
    response = payload_or_none(ctx)
    if response is not None:
        data = wire_dict(ctx)
        assert data.get(field_name) is None, (
            f"Response should NOT contain '{field_name}' field on the wire (BR-RULE-018), "
            f"but found: {data.get(field_name)!r}"
        )
        _assert_a2a_submitted_task_has_no_artifacts(ctx)
        return
    # Error-path response (BR-RULE-018 INV-2) — the envelope the buyer received.
    envelope = ctx["result"].error_envelope()
    assert envelope.get(field_name) is None, (
        f"Error envelope should NOT contain '{field_name}' field "
        f"(BR-RULE-018 INV-2), but found: {envelope.get(field_name)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — creative replacement assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the package "{package_id}" should have stored targeting_overlay {dimension:w} {value}'))
def then_package_stored_overlay_dimension(ctx: dict, package_id: str, dimension: str, value: str) -> None:
    """The package's STORED targeting_overlay carries *value* (JSON) for *dimension*.

    core/targeting-input.json: on update "a non-null value replaces that dimension".
    ``affected_packages`` does not carry the overlay (``_affected_package`` says why), so
    the record is read, the way ``get_media_buys`` would serve it.
    """
    import json

    _budget, config = _stored_package(ctx, package_id)
    overlay = config.get("targeting_overlay")
    assert isinstance(overlay, dict), f"package {package_id!r} stores no targeting_overlay: {config!r}"
    expected = json.loads(value)
    assert overlay.get(dimension) == expected, (
        f"stored targeting_overlay.{dimension} is {overlay.get(dimension)!r}, expected {expected!r}"
    )


@then(parsers.parse('the package "{package_id}" should have creative assignments [{expected_ids}]'))
def then_package_has_creative_assignments(ctx: dict, package_id: str, expected_ids: str) -> None:
    """Assert the package's creative assignments match the expected set after update.

    BR-RULE-024: creative_assignments on update replaces all existing.
    Reads the package from DB and verifies the creative assignments list
    contains exactly the expected creative IDs. An entry written ``cr_new_1:70`` also
    pins that assignment's stored weight.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    tenant = ctx["tenant"]
    real_pkg_id = _resolve_package_id(ctx, package_id)

    with db_session(ctx) as session:
        assignments = session.scalars(
            select(CreativeAssignment).filter_by(
                media_buy_id=mb.media_buy_id,
                package_id=real_pkg_id,
                tenant_id=tenant.tenant_id,
            )
        ).all()
        actual_weights = {a.creative_id: a.weight for a in assignments}

    entries = [entry.strip().partition(":") for entry in expected_ids.split(",")]
    expected = {cid.strip() for cid, _, _ in entries}
    assert set(actual_weights) == expected, (
        f"Package '{package_id}' creative assignments mismatch. "
        f"Expected: {sorted(expected)}, actual: {sorted(actual_weights)}"
    )
    for cid, _, weight in entries:
        if weight:
            assert actual_weights[cid.strip()] == int(weight), (
                f"Package '{package_id}' assignment {cid.strip()!r} stored weight "
                f"{actual_weights[cid.strip()]!r}, the update sent {weight}"
            )


@then(parsers.parse("the old assignments [{old_ids}] should be removed"))
def then_old_assignments_removed(ctx: dict, old_ids: str) -> None:
    """Assert the old creative assignments are no longer present on the package.

    Complements the 'should have creative assignments' step by explicitly
    verifying the old IDs were removed, not just that new ones were added.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    tenant = ctx["tenant"]
    pkg_obj = ctx.get("existing_package")
    assert pkg_obj is not None, "No existing_package in ctx — cannot verify old assignments removed"

    with db_session(ctx) as session:
        assignments = session.scalars(
            select(CreativeAssignment).filter_by(
                media_buy_id=mb.media_buy_id,
                package_id=pkg_obj.package_id,
                tenant_id=tenant.tenant_id,
            )
        ).all()
        actual_ids = {a.creative_id for a in assignments}

    removed_ids = {cid.strip() for cid in old_ids.split(",")}
    still_present = removed_ids & actual_ids
    assert not still_present, (
        f"Old creative assignments should be removed but still present: {sorted(still_present)}. "
        f"Current assignments: {sorted(actual_ids)}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Inline creatives on a package update (package-update.json /properties/creatives)
# ═══════════════════════════════════════════════════════════════════════


def _inline_creative_payload(creative_id: str, name: str) -> dict[str, Any]:
    """One valid inline display creative, as a package update's ``creatives`` item carries it.

    The ``asset_type`` discriminator comes from ``image_spec``: a hand-built asset map
    without it is refused by ``AdCPPackageUpdate.creatives`` before the update runs.
    """
    from tests.factories.creative_asset import named_format_assets
    from tests.factories.request import CreativeAssetRequestFactory

    return CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=name,
        format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        assets=named_format_assets("display_300x250_image", media_url=f"https://example.com/{creative_id}.png"),
    )


def _approved_display_creative(ctx: dict, creative_id: str) -> Any:
    """An approved display creative in the buyer's library, compatible with the display product."""
    from tests.factories.creative import CreativeFactory

    return CreativeFactory(
        creative_id=creative_id,
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        format="display_300x250_image",
        approved=True,
        data={"assets": {"primary": {"url": f"https://example.com/{creative_id}.png", "width": 300, "height": 250}}},
    )


def _send_earlier_update(ctx: dict, package_label: str, **package_fields: Any) -> None:
    """Put the package's prior state (creatives, catalogs) on the record the way a buyer does: an update.

    Sent over the scenario's own transport through ``dispatch_request``, which gates and
    records the payload like every other dispatch. That entry writes the dispatch-result
    contract into the ctx it is handed, so this update gets a ctx of its own: the scenario's
    ``ctx["result"]`` is the request it grades, and this update is only its precondition.
    The own ctx carries no ``credential`` override, so the env presents its own, as the
    graded request's principal. Its own bag, so it carries its own idempotency key rather
    than the graded request's.
    """
    bag = apply_required_update_fields(
        ctx,
        {
            "media_buy_id": _existing_media_buy(ctx).media_buy_id,
            "packages": [{"package_id": _resolve_package_id(ctx, package_label), **package_fields}],
        },
    )
    earlier: dict = {"env": ctx["env"], "transport": ctx["transport"]}
    dispatch_request(earlier, **bag)
    result = earlier["result"]
    assert result.is_success, (
        f"the earlier update_media_buy that sets up {package_label!r} was refused: {result.error!r}"
    )


@given(parsers.parse('an earlier update gave package "{package_id}" the inline creative "{creative_id}"'))
def given_earlier_inline_creative(ctx: dict, package_id: str, creative_id: str) -> None:
    """The package's prior creative arrived inline, as in the pin's storyboard
    (inline_creatives_without_sync: an inline creative, then a replacement inline update)."""
    _send_earlier_update(ctx, package_id, creatives=[_inline_creative_payload(creative_id, "Earlier inline creative")])


@given(parsers.parse('an earlier update gave package "{package_id}" the targeting_overlay {overlay}'))
def given_earlier_targeting_overlay(ctx: dict, package_id: str, overlay: str) -> None:
    """The package's stored overlay before the graded update, put there by an update.

    Recorded in ``ctx["prior_targeting_overlay"]``: an update that omits a dimension leaves it
    as this one set it, which is what makes an absent or empty overlay row observable.
    """
    import json

    prior = json.loads(overlay)
    _send_earlier_update(ctx, package_id, targeting_overlay=prior)
    ctx["prior_targeting_overlay"] = prior


@given(parsers.parse('an earlier update assigned library creative "{creative_id}" to package "{package_id}"'))
def given_earlier_library_assignment(ctx: dict, creative_id: str, package_id: str) -> None:
    """The package's prior creative is a library creative assigned through ``creative_assignments``."""
    _approved_display_creative(ctx, creative_id)
    ctx["env"]._commit_factory_data()
    _send_earlier_update(ctx, package_id, creative_assignments=[{"creative_id": creative_id, "weight": 1.0}])


@given(parsers.parse('an earlier update gave package "{package_id}" the catalogs {catalogs}'))
def given_earlier_catalogs(ctx: dict, package_id: str, catalogs: str) -> None:
    """The package's stored catalogs arrived the way a buyer sets them: a catalogs replacement.

    media-buy/package-update.json catalogs: "the provided array replaces the current list".
    A refusal that leaves these in place is distinguishable from one that clears them.
    """
    import json

    _send_earlier_update(ctx, package_id, catalogs=json.loads(catalogs))


@then(parsers.parse('the stored catalogs of package "{package_id}" should be {catalogs}'))
def then_stored_catalogs(ctx: dict, package_id: str, catalogs: str) -> None:
    """The persisted package's catalogs are exactly *catalogs*.

    Read off the row, not the response: after a refusal there is no package on the wire, and
    the claim is what the seller kept (update-media-buy-response.json's error arm: "operation
    failed, no changes applied").
    """
    import json

    _budget, config = _stored_package(ctx, package_id)
    expected = json.loads(catalogs)
    assert config.get("catalogs") == expected, (
        f"package {package_id!r} stores catalogs {config.get('catalogs')!r}, expected {expected!r}"
    )


@when(parsers.parse('the Buyer Agent reads media buy "{media_buy_id}" back with get_media_buys'))
def when_read_back_media_buy(ctx: dict, media_buy_id: str) -> None:
    """The buyer-visible readback of the package's creatives: get_media_buys by id.

    The pin's inline_creatives_without_sync storyboard reads the replacement back the same
    way, from ``media_buys[].packages[].creative_approvals[].creative_id``.
    """
    from src.core.schemas._base import GetMediaBuysRequest

    dispatch_request(ctx, req=GetMediaBuysRequest(media_buy_ids=[_existing_media_buy(ctx, media_buy_id).media_buy_id]))


@then(parsers.parse('the package "{package_id}" should report creative approvals for exactly {creative_ids}'))
def then_package_reports_exactly(ctx: dict, package_id: str, creative_ids: str) -> None:
    """The package's ``creative_approvals`` on the get_media_buys wire name exactly *creative_ids*."""
    import json

    from tests.bdd.steps.domain.uc019_query_media_buys import _reported_creative_ids

    expected = sorted(json.loads(creative_ids))
    reported = _reported_creative_ids(ctx, _resolve_package_id(ctx, package_id))
    assert reported == expected, f"package {package_id!r} reports creatives {reported} on the wire, expected {expected}"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: idempotency_key
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.re(r"the idempotency_key is set to (?P<value>.*)"))
def given_idempotency_key(ctx: dict, value: str) -> None:
    """Set or omit idempotency_key on the update request.

    '<not provided>' means omit the field — recorded as the OMIT sentinel rather than by
    popping the key, because a later Given's `_ensure_update_defaults` call would default a
    popped key straight back and the row would grade a request carrying the key it says is
    absent. `_flatten_update_request` drops the sentinel at the wire.
    Empty string means set to '' (for boundary validation of empty keys).
    Any other value sets it as-is. Handles length placeholders like
    '<255 character string>' by generating a string of the described length.

    Uses parsers.re instead of parsers.parse to match empty values
    (partition: empty_string where the value after 'set to' is empty).
    """
    kwargs = _ensure_update_defaults(ctx)
    if value.strip() == "<not provided>":
        kwargs["idempotency_key"] = OMIT_IDEMPOTENCY_KEY
        return
    # A length placeholder expands; an empty string or any other value is sent as-is.
    kwargs["idempotency_key"] = _expand_length_placeholder(value)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: daily spend cap
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the tenant max_daily_package_spend is {cap_config}"))
def given_tenant_max_daily_spend(ctx: dict, cap_config: str) -> None:
    """Configure the tenant's max_daily_package_spend for daily spend cap tests.

    Accepts a numeric value or 'not set' (which clears the limit).
    """
    from sqlalchemy import select

    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot configure max_daily_package_spend"

    stripped = cap_config.strip()
    with db_session(ctx) as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id)).first()
        if stripped.lower() == "not set":
            if cl is not None:
                cl.max_daily_package_spend = None
                session.commit()
        else:
            assert cl is not None, (
                f"No CurrencyLimit for tenant {tenant.tenant_id} — cannot set max_daily_package_spend"
            )
            cl.max_daily_package_spend = float(stripped)
            session.commit()


@given(parsers.parse("the media buy flight duration is {flight_days} days"))
def given_media_buy_flight_duration(ctx: dict, flight_days: str) -> None:
    """Set the media buy flight duration by adjusting start_time and end_time.

    Sets start_time to now and end_time to now + flight_days.
    Given step must set up exactly what the step text says — no silent flooring.
    0-day flights (start == end) are valid test inputs for validation error scenarios.
    """
    from datetime import datetime, timedelta

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set flight duration"
    days = int(flight_days)
    now = datetime.now(tz=UTC)
    mb.start_time = now
    mb.end_time = now + timedelta(days=days)
    env = ctx["env"]
    env._commit_factory_data()

    # Verify persistence: re-read from DB to confirm flight duration was committed
    from sqlalchemy import select

    from src.core.database.models import MediaBuy as MediaBuyModel

    with db_session(ctx) as session:
        persisted = session.scalars(
            select(MediaBuyModel).filter_by(media_buy_id=mb.media_buy_id, tenant_id=mb.tenant_id)
        ).first()
        assert persisted is not None, f"Media buy {mb.media_buy_id} not found in DB after _commit_factory_data()"
        actual_days = (persisted.end_time - persisted.start_time).days
        assert actual_days == days, (
            f"Flight duration not persisted: expected {days} days, "
            f"got {actual_days} (start={persisted.start_time}, end={persisted.end_time})"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: media buy identification
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("a valid update_media_buy request with identification: {id_config}"))
def given_update_request_with_identification(ctx: dict, id_config: str) -> None:
    """Build update request with specific identification fields.

    id_config formats:
    - 'media_buy_id=<label>' — the buy the label names, resolved as the table Given resolves it
      (the Background registers ``mb_existing`` for the seeded buy)
    - '<none>' — send no media_buy_id (expect error)
    """
    kwargs: dict[str, Any] = {}
    stripped = id_config.strip()

    if stripped == "<none>":
        # The OMIT sentinel, not an absent key. ``MediaBuyDualEnv`` picks the TOOL from the
        # bag's content, and ``media_buy_id`` is the key it reads: a bag without one was sent
        # to create_media_buy, which refused the update's package (no product_id) at
        # ``packages[0].package_id``. The rows asserting INVALID_REQUEST on media_buy_id then
        # failed on a field the update was never asked about. The key present with OMIT routes
        # the bag to update_media_buy, and ``_flatten_update_request`` strips it at the wire.
        kwargs["media_buy_id"] = OMIT
    else:
        for part in stripped.split(","):
            key, _, val = part.strip().partition("=")
            assert key.strip() == "media_buy_id", f"unrecognized identification field {key!r} in {id_config!r}"
            kwargs["media_buy_id"] = _resolve_media_buy_id(ctx, val.strip())

    ctx["update_kwargs"] = kwargs


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: frequency_cap
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the package targeting_overlay includes frequency_cap: {freq_cap_config}"))
def given_frequency_cap_config(ctx: dict, freq_cap_config: str) -> None:
    """Set frequency_cap on the first package update's targeting_overlay.

    The parameter is the WHOLE core/frequency-cap.json object (JSON), sent as written:
    ``{"suppress": {"interval": 60, "unit": "minutes"}}``, ``{"suppress_minutes": 60}``.
    A bare Duration is not a frequency cap, and the step does not wrap one.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    overlay = pkg.setdefault("targeting_overlay", {})
    if isinstance(overlay, str):
        overlay = json.loads(overlay)
        pkg["targeting_overlay"] = overlay
    parsed_config = json.loads(freq_cap_config)
    overlay["frequency_cap"] = parsed_config


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: parameterized keyword operations
# ═══════════════════════════════════════════════════════════════════════


def _set_keyword_field_from_param(ctx: dict, field: str, raw_value: str) -> None:
    """Set a keyword operation field from a parameterized scenario value.

    Handles JSON arrays and the overlay sentinels. ``<with targeting_overlay.<dim> present>``
    puts that overlay dimension beside the operation, so a row can name the conflicting
    dimension or the other one (the cross-dimension rows); ``<with overlay present>`` means
    the operation's own dimension.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = raw_value.strip()

    if stripped.startswith("<with"):
        overlay = pkg.setdefault("targeting_overlay", {})
        if isinstance(overlay, str):
            overlay = json.loads(overlay)
            pkg["targeting_overlay"] = overlay
        named = re.fullmatch(r"<with targeting_overlay\.(keyword_targets|negative_keywords) present>", stripped)
        own = "keyword_targets" if field.startswith("keyword_targets") else "negative_keywords"
        assert named or stripped == "<with overlay present>", f"unrecognised overlay sentinel {stripped!r}"
        dimension = named.group(1) if named else own
        overlay[dimension] = [{"keyword": "conflict", "match_type": "broad"}]
        pkg[field] = [{"keyword": "shoes", "match_type": "broad"}]
    else:
        pkg[field] = json.loads(stripped)


@given(parsers.parse("the package update includes keyword_targets_add: {kw_value}"))
def given_keyword_targets_add_param(ctx: dict, kw_value: str) -> None:
    """Set keyword_targets_add on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "keyword_targets_add", kw_value)


@given(parsers.parse("the package update includes keyword_targets_remove: {kw_value}"))
def given_keyword_targets_remove_param(ctx: dict, kw_value: str) -> None:
    """Set keyword_targets_remove on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "keyword_targets_remove", kw_value)


@given(parsers.parse("the package update includes negative_keywords_add: {nk_value}"))
def given_negative_keywords_add_param(ctx: dict, nk_value: str) -> None:
    """Set negative_keywords_add on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "negative_keywords_add", nk_value)


@given(parsers.parse("the package update includes negative_keywords_remove: {nk_value}"))
def given_negative_keywords_remove_param(ctx: dict, nk_value: str) -> None:
    """Set negative_keywords_remove on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "negative_keywords_remove", nk_value)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: targeting_overlay
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the package targeting_overlay is set to: {overlay_value}"))
def given_targeting_overlay(ctx: dict, overlay_value: str) -> None:
    """Set the full targeting_overlay on the first package update.

    Accepts a JSON object or '<not provided>' (omit the field).
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = overlay_value.strip()

    if stripped == "<not provided>":
        pkg.pop("targeting_overlay", None)
    else:
        pkg["targeting_overlay"] = json.loads(stripped)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: start_time/end_time guards
# ═══════════════════════════════════════════════════════════════════════


@given("the existing end_time is in the future")
def given_existing_end_time_future(ctx: dict) -> None:
    """Ensure the existing media buy's end_time is in the future.

    Sets end_time to 90 days from now if it's not already in the future.
    """
    from datetime import datetime, timedelta

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify end_time"
    now = datetime.now(tz=UTC)
    if mb.end_time is None or mb.end_time.replace(tzinfo=UTC) <= now:
        mb.end_time = now + timedelta(days=90)
        env = ctx["env"]
        env._commit_factory_data()


@given("the new end_time is after the existing start_time")
def given_new_end_time_after_existing_start(ctx: dict) -> None:
    """Declarative guard — verify the requested end_time is after the existing start_time.

    Validates that the update_kwargs end_time value (from the datatable) is
    chronologically after the existing media buy's start_time. If the existing
    media buy has no start_time, sets one in the past so the guard holds.
    """
    from datetime import datetime, timedelta

    kwargs = _ensure_update_defaults(ctx)
    end_time_str = kwargs.get("end_time")
    assert end_time_str is not None, (
        "update_kwargs has no 'end_time' — step claims 'the new end_time is after the "
        "existing start_time' but no end_time was set by a prior Given step"
    )
    new_end = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify start_time"
    # Ensure existing media buy has a start_time (set one in the past if missing)
    if mb.start_time is None:
        mb.start_time = new_end - timedelta(days=30)
        env = ctx["env"]
        env._commit_factory_data()
    existing_start = mb.start_time
    if existing_start.tzinfo is None:
        existing_start = existing_start.replace(tzinfo=UTC)
    assert new_end > existing_start, (
        f"New end_time {new_end} is not after existing start_time {existing_start} — step precondition violated"
    )


@given(parsers.parse("the budget {amount:d} is greater than zero"))
def given_budget_greater_than_zero(ctx: dict, amount: int) -> None:
    """Declarative guard — the budget the package update carries is *amount*, and positive.

    The request's budget is the package update's: the pinned
    ``media-buy/update-media-buy-request.json`` (adcp v3.2.7) declares no top-level
    ``budget`` (its aggregate is ``total_budget``, an ``{amount, currency}`` object), and
    ``media-buy/package-update.json`` declares ``budget``. This guard read a top-level
    ``budget`` key no Given of its one scenario writes, so it refused every run before
    dispatch.
    """
    assert amount > 0, f"Budget {amount} is not greater than zero — step precondition violated"
    assert _requested_budget(ctx) == float(amount), (
        f"the package update carries budget {_requested_budget(ctx)}, not the step's {amount}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: creative replacement
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the package "{package_id}" has existing creative assignments [{assignments}]'))
def given_package_existing_creatives(ctx: dict, package_id: str, assignments: str) -> None:
    """Assign the named creatives to the package, as CreativeAssignment rows.

    Parses comma-separated creative IDs (e.g., 'cr_old_1, cr_old_2') and creates each
    Creative and its assignment to the Background's buy and package. The rows are the
    state the seller reads and replaces (``_replace_package_assignments`` deletes the
    rows a replacement does not name); a list written into ``package_config`` is read by
    nothing, so a seller that appended instead of replacing passed every replacement Then.
    """
    from tests.factories.creative import CreativeAssignmentFactory

    env = ctx["env"]
    creative_ids = [cid.strip() for cid in assignments.split(",")]
    pkg = ctx.get("existing_package")
    assert pkg is not None, (
        f"No 'existing_package' in ctx — step claims package '{package_id}' has creative assignments "
        "but no package was set up by a prior Given step"
    )
    # package_id is a Gherkin label; register it against the existing package
    # so later resolver calls can translate label → real package_id.
    _register_package(ctx, package_id, pkg)
    for cid in creative_ids:
        CreativeAssignmentFactory(
            creative=_approved_display_creative(ctx, cid),
            media_buy=_existing_media_buy(ctx),
            package_id=pkg.package_id,
        )
    env._commit_factory_data()
    ctx["existing_creative_ids"] = creative_ids


@given(parsers.parse("the package creative update mode is: {mode}"))
def given_creative_update_mode(ctx: dict, mode: str) -> None:
    """Set the creative update mode on the first package update.

    Parses mode formats:
    - 'creative_ids=[cr_new_1, cr_new_2]' — set creative_ids array
    - 'creative_assignments=[{cr_new_1, weight:70}]' — set creative_assignments
    """
    import re

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = mode.strip()

    if stripped.startswith("creative_ids="):
        # Parse creative_ids=[id1, id2, ...]
        ids_match = re.search(r"\[([^\]]*)\]", stripped)
        assert ids_match, f"Cannot parse creative_ids from: {stripped}"
        creative_ids = [cid.strip() for cid in ids_match.group(1).split(",") if cid.strip()]
        pkg["creative_ids"] = creative_ids
        # Create Creative records for new IDs
        env = ctx["env"]
        for cid in creative_ids:
            if cid not in (ctx.get("existing_creative_ids") or []):
                _approved_display_creative(ctx, cid)
        env._commit_factory_data()
        ctx["referenced_creative_ids"] = creative_ids
    elif stripped.startswith("creative_assignments="):
        # Parse creative_assignments=[{cr_new_1, weight:70}, {cr_new_2}]. An entry that names
        # no weight sends none: creative-assignment.json requires only creative_id.
        assignments = []
        env = ctx["env"]
        # Find all {id[, weight:N]} blocks
        for match in re.finditer(r"\{([^}]+)\}", stripped):
            parts = [p.strip() for p in match.group(1).split(",")]
            cid = parts[0]
            assignment: dict[str, Any] = {"creative_id": cid}
            for part in parts[1:]:
                if part.startswith("weight:"):
                    assignment["weight"] = float(part.split(":")[1])
            assignments.append(assignment)
            if cid not in (ctx.get("existing_creative_ids") or []):
                _approved_display_creative(ctx, cid)
        env._commit_factory_data()
        pkg["creative_assignments"] = assignments
        ctx["referenced_creative_ids"] = [a["creative_id"] for a in assignments]
    else:
        raise ValueError(
            f"Unrecognized creative update mode: '{stripped}'. "
            f"Expected format: 'creative_ids=[id1, id2]' or "
            f"'creative_assignments=[{{id, weight:N}}]'. "
            f"Check the scenario step text."
        )


@given("all referenced creatives are valid")
def given_all_creatives_valid(ctx: dict) -> None:
    """Ensure all referenced creatives exist in DB and are in valid state.

    Given steps are precondition setup: create any missing creatives as approved,
    then verify none are in error/rejected state.
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel
    from tests.factories.creative import CreativeFactory

    ids = ctx.get("referenced_creative_ids") or ctx.get("existing_creative_ids")
    assert ids and len(ids) > 0, "No referenced or existing creative_ids — missing prior step"

    env = ctx["env"]
    env._commit_factory_data()

    tenant = ctx["tenant"]
    with db_session(ctx) as session:
        # Find which creatives already exist
        db_creatives = session.scalars(
            select(CreativeModel).filter(
                CreativeModel.tenant_id == tenant.tenant_id,
                CreativeModel.creative_id.in_(ids),
            )
        ).all()
        found_ids = {c.creative_id for c in db_creatives}
        missing = set(ids) - found_ids

    # Create any missing creatives as approved
    if missing:
        for cid in sorted(missing):
            CreativeFactory.create(
                creative_id=cid,
                tenant=ctx["tenant"],
                principal=ctx["principal"],
                format="display_300x250_image",
                approved=True,
            )

    # Re-query to verify all exist and none are in invalid state
    with db_session(ctx) as session:
        db_creatives = session.scalars(
            select(CreativeModel).filter(
                CreativeModel.tenant_id == tenant.tenant_id,
                CreativeModel.creative_id.in_(ids),
            )
        ).all()
        found_ids = {c.creative_id for c in db_creatives}
        still_missing = set(ids) - found_ids
        assert not still_missing, f"Failed to create creatives {sorted(still_missing)} for tenant '{tenant.tenant_id}'"
        invalid_states = {"error", "rejected", "failed"}
        for creative in db_creatives:
            status = getattr(creative, "status", None)
            if status and status in invalid_states:
                raise AssertionError(
                    f"Creative '{creative.creative_id}' has status '{status}' — "
                    f"step claims 'all referenced creatives are valid' but this creative "
                    f"is in an invalid state"
                )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: creative state validation
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.parse("the package update includes creative_assignments referencing creative in state: {creative_state}")
)
def given_creative_assignments_with_state(ctx: dict, creative_state: str) -> None:
    """Add creative_assignments referencing a creative with a specific state.

    States: 'approved', 'error', 'wrong_format'.
    Creates a Creative record with the appropriate state/format.
    """
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]

    state = creative_state.strip()
    cid = f"cr_{state}_001"
    if state == "approved":
        _approved_display_creative(ctx, cid)
    elif state == "error":
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250_image",
            approved=False,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}},
        )
        # Set status to error after creation
        from sqlalchemy import select

        from src.core.database.models import Creative as CreativeModel

        env._commit_factory_data()
        with db_session(ctx) as session:
            cr = session.scalars(
                select(CreativeModel).filter_by(creative_id=cid, tenant_id=ctx["tenant"].tenant_id)
            ).first()
            assert cr is not None, (
                f"Creative {cid} not found in DB after _commit_factory_data() — "
                f"factory did not persist the creative for tenant {ctx['tenant'].tenant_id}"
            )
            cr.status = "error"
            session.commit()
            # Verify the status was persisted
            session.refresh(cr)
            assert cr.status == "error", f"Creative {cid} status not persisted as 'error', got '{cr.status}'"
    elif state == "wrong_format":
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            # Incompatible with the display product, which is the whole of what this
            # fixture needs. ``video_vast_4`` stood here and is an invented id the
            # registry's v1 -> v2 mapping does not carry, so seeding the row was refused
            # before the scenario's own subject ran. ``video_640x360_vast`` is the
            # registry's own VAST glob and projects to ``video_vast`` with no catalog --
            # still a video against a display product, so the incompatibility is intact.
            format="video_640x360_vast",
            approved=True,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.mp4"}}},
        )
    else:
        raise ValueError(f"Unknown creative state: {state}")

    env._commit_factory_data()
    pkg["creative_assignments"] = [{"creative_id": cid, "weight": 1.0}]
    ctx["referenced_creative_ids"] = [cid]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: placement_id validation
# ═══════════════════════════════════════════════════════════════════════


#: The placements the package's product declares on the placement-validation rows; the
#: '(valid)' row names exactly these, the '(not in product)' row names one outside them.
_PRODUCT_PLACEMENT_IDS = ("plc_a", "plc_b")


@given(
    parsers.parse("the package update includes creative_assignments with placement configuration: {placement_config}")
)
def given_creative_assignments_with_placements(ctx: dict, placement_config: str) -> None:
    """Add creative_assignments with specific placement configuration.

    Formats:
    - 'placement_ids=[plc_a, plc_b] (valid)' — valid placement IDs
    - 'no placement_ids specified' — no placement_ids
    - 'placement_ids=[plc_invalid] (not in product)' — invalid IDs
    - 'placement_ids=[plc_a] (product unsupported)' — product doesn't support placements

    The PRODUCT is part of every row's fixture, because the rows differ in it: the
    package's product declares the placements ``plc_a`` and ``plc_b`` on every row but
    '(product unsupported)', where it declares none. Seeded only on that row, the product
    had no placements anywhere, so '(valid)' and '(not in product)' were refused
    UNSUPPORTED_FEATURE and the unsupported row passed whether or not its own fixture ran.
    The placements are ``mode: included``: pinned core/placement.json (v3.2.7) makes an
    included placement "fixed product inventory" that every package buys, and creative
    assignments "may then route creatives only within the purchased set" — a
    ``targetable`` one is purchased only through placement_selection.
    """
    import re

    from tests.factories.product import StoredPlacementFactory

    env = ctx["env"]
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = placement_config.strip()

    cid = "cr_placement_test"
    _approved_display_creative(ctx, cid)
    env._commit_factory_data()

    assignment: dict[str, Any] = {"creative_id": cid, "weight": 1.0}
    if "no placement_ids" in stripped:
        # Step text: "no placement_ids specified" — key ABSENT, not empty list.
        # An empty list (key present, value=[]) is semantically different from
        # key absent (field not specified). Do NOT set placement_ids at all.
        pass
    else:
        ids_match = re.search(r"\[([^\]]*)\]", stripped)
        if ids_match:
            placement_ids = [pid.strip() for pid in ids_match.group(1).split(",") if pid.strip()]
            assignment["placement_ids"] = placement_ids
            ctx["referenced_placement_ids"] = placement_ids

    # The package's product: the one the update path reads placements from.
    product = ctx["default_product"]
    package_product_id = (ctx["existing_package"].package_config or {}).get("product_id")
    assert package_product_id == product.product_id, (
        f"pkg_001 sells product {package_product_id!r}, not the seeded default product "
        f"{product.product_id!r} whose placements this step declares"
    )
    product.placements = (
        None
        if "product unsupported" in stripped
        else [StoredPlacementFactory(placement_id=pid, mode="included") for pid in _PRODUCT_PLACEMENT_IDS]
    )
    env._commit_factory_data()

    pkg["creative_assignments"] = [assignment]
    ctx["referenced_creative_ids"] = [cid]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: adapter dispatch & persistence
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.re(
        r"the request includes (?P<update_fields>"
        r"(?!\d+ packages with valid)"
        r"(?!a reporting_webhook)"
        r"(?!a push_notification_config)"
        r"(?!new_packages )"
        r"(?!1 package update where )"
        r"(?!1 package update containing )"
        r"(?!a package update where )"
        r"(?!1 package with budget update$)"
        r"(?!an artifact_webhook)"
        r".+[^:])$"
    )
)
def given_request_includes_fields(ctx: dict, update_fields: str) -> None:
    """Configure the update request with specific field combinations.

    Uses regex to avoid matching the datatable step 'the request includes 1 package update with:'
    and UC-002 steps like 'the request includes 2 packages with valid product_ids'.
    Also excludes registration-SSRF Givens ('a reporting_webhook…', 'a push_notification_config…')
    so those bind to their dedicated create/sync steps instead of this update catch-all.
    The negative lookahead excludes UC-002 package creation patterns (e.g. "2 packages with valid").
    The [^:] at the end ensures this step doesn't capture text ending with ':'.

    The ``new_packages …``, ``1 package update where …``, ``1 package update containing …``
    and ``a package update where …`` families, and ``1 package with budget update``, have
    their own steps and are excluded here. Each used to bind HERE as well: the new_packages variants all sent the same single
    package whatever the sentence said ("two complete package-requests", "an entry missing
    product_id"), and the package-update families fell through to the ValueError below.

    Matched patterns from adapter-dispatch partition/boundary scenarios:
    - '1 package with budget update only'
    - '1 package with budget and targeting'
    - 'packages with all updatable fields'
    - 'no updatable fields in request'

    Every package budget is :data:`_UPDATED_BUDGET`, never the 5000 the Background seeds,
    so "success with persisted records" can tell a written budget from an untouched row.
    The all-fields request pauses the buy (``paused: true``): the Background's buy is
    ``active``, where update_media_buy.mdx maps ``paused: false`` to ``resume``, an action
    that state does not offer. Its flight moves to dates still ahead of the clock.
    """
    kwargs = _ensure_update_defaults(ctx)
    stripped = update_fields.strip()

    if "no updatable fields" in stripped:
        # Keep only media_buy_id
        mid = kwargs["media_buy_id"]
        kwargs.clear()
        kwargs["media_buy_id"] = mid
    elif "budget update only" in stripped:
        kwargs["packages"] = [{"package_id": "pkg_001", "budget": _UPDATED_BUDGET}]
    elif "budget and targeting" in stripped:
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": _UPDATED_BUDGET,
                "targeting_overlay": {"geo_countries": ["US"]},
            }
        ]
    elif "all updatable fields" in stripped:
        clock = ctx["env"].clock
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": _UPDATED_BUDGET,
                "targeting_overlay": {"geo_countries": ["US"]},
                "paused": False,
            }
        ]
        kwargs["paused"] = True
        kwargs["start_time"] = clock.future_iso(1)
        kwargs["end_time"] = clock.future_iso(60)
    else:
        raise ValueError(f"Unknown update_fields pattern: {stripped}")


@given(parsers.parse('the media buy exists with status "{status}"'))
@given(parsers.parse('the media buy "{label}" exists with status "{status}"'))
def given_media_buy_exists_with_status(ctx: dict, status: str, label: str | None = None) -> None:
    """Ensure the existing media buy is in DB with the given status.

    The quoted form names the buy by its Gherkin label; it must be the Background's buy.
    """
    mb = _existing_media_buy(ctx, label)
    if mb.status != status:
        mb.status = status
        env = ctx["env"]
        env._commit_factory_data()


@given(parsers.parse("the request includes 1 package with budget update"))
def given_request_with_budget_update(ctx: dict) -> None:
    """Add a single package with a budget update to the request.

    The budget differs from the one the Background's package is seeded with, so the
    persistence-timing outcomes can tell a written update from an untouched row.
    """
    kwargs = _ensure_update_defaults(ctx)
    kwargs["packages"] = [{"package_id": "pkg_001", "budget": _UPDATED_BUDGET}]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: approval workflow flags
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the tenant human_review_required is {tenant_flag}"))
def given_tenant_human_review_flag(ctx: dict, tenant_flag: str) -> None:
    """Set the tenant's human_review_required flag for approval workflow tests.

    The sentence names one input, so "true" sets one: ``given_tenant_review_only`` raises the
    tenant column and leaves every adapter as a real one with its own flag off, in process
    and on the live server alike. It used to delegate to ``given_tenant_manual_approval``,
    which raises the adapter's flag too, so @T-UC-003-approval-tenant ("tenant requires
    manual review", BR-RULE-017 INV-2) passed on the adapter arm and graded nothing about
    the tenant. The partition and boundary approval-workflow outlines follow this Given with
    their own adapter Given; @T-UC-003-bva-approval-workflow reaches it through
    ``given_approval_configuration``, whose tenant row sets no adapter flag. Every tenant-only
    row now grades the tenant arm, which update_media_buy does not honour (#2483).
    """
    from tests.bdd.steps.generic.given_media_buy import (
        given_tenant_auto_approval,
        given_tenant_review_only,
    )

    flag = tenant_flag.strip().lower()
    if flag == "false":
        given_tenant_auto_approval(ctx)
    elif flag == "true":
        given_tenant_review_only(ctx)
    else:
        raise ValueError(f"Unknown tenant_flag: {tenant_flag}")


@given(parsers.parse("the adapter manual_approval_required is {adapter_flag}"))
def given_adapter_manual_approval_flag(ctx: dict, adapter_flag: str) -> None:
    """Set the adapter's manual_approval_required flag for approval workflow tests.

    Delegates to the existing adapter approval helpers in given_media_buy.py.
    """
    from tests.bdd.steps.generic.given_media_buy import (
        given_adapter_manual_approval,
        given_adapter_no_manual_approval,
    )

    flag = adapter_flag.strip().lower()
    if flag == "false":
        given_adapter_no_manual_approval(ctx)
    elif flag == "true":
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown adapter_flag: {adapter_flag}")


@given(parsers.parse("the tenant approval mode is {approval_mode}"))
def given_tenant_approval_mode(ctx: dict, approval_mode: str) -> None:
    """Configure the tenant's approval mode.

    'auto-approval' — tenant human_review_required=False, no adapter gates the operation
    'manual' — tenant human_review_required=True, every adapter gates create and update

    Delegates to the shared tenant-approval writers, which configure EVERY adapter the env
    exposes plus the identity's tenant and the live server's adapter row. The update
    path's approval gate reads its own adapter (``env.mock['update_adapter']``), which the
    body this replaced never touched, so its 'manual' row ran the auto path in process.
    """
    from tests.bdd.steps.generic.given_media_buy import (
        _configure_adapter_manual_approval,
        given_tenant_auto_approval,
        given_tenant_manual_approval,
    )

    stripped = approval_mode.strip()
    if stripped == "auto-approval":
        given_tenant_auto_approval(ctx)
        _configure_adapter_manual_approval(ctx["env"], required=False)
    elif stripped == "manual":
        given_tenant_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown approval mode: {stripped}")


@given(parsers.re(r"the adapter (?P<adapter_result>returns \w+|not yet called)"))
def given_adapter_result(ctx: dict, adapter_result: str) -> None:
    """Configure adapter mock behavior for persistence timing tests.

    'returns success' — adapter returns normally
    'returns error' — adapter raises an exception
    'not yet called' — adapter not invoked (manual approval path)

    Uses regex to avoid matching 'the adapter manual_approval_required is ...'
    which is a separate step for approval workflow partition/boundary scenarios.

    The adapter configured is the one the UPDATE path calls (``update_media_buy`` on
    ``env.mock['update_adapter']``). This used to set ``update_order`` -- a method no
    adapter has -- on the create-module mock, so in process the error row never failed.
    The error is the ad server's outage class, ``AdCPAdapterError``: SERVICE_UNAVAILABLE,
    the code the rows expect and the class the live server's mock adapter raises.
    """
    from src.core.exceptions import AdCPAdapterError
    from tests.bdd.steps.generic.given_media_buy import set_update_adapter_outcome

    stripped = adapter_result.strip()
    env = ctx["env"]
    assert "update_adapter" in env.mock, (
        f"Step claims 'the adapter {stripped}' but the env registers no update-path adapter "
        f"(env.mock['update_adapter']); {type(env).__name__} does not drive update_media_buy."
    )

    if stripped == "returns success":
        set_update_adapter_outcome(ctx, None)
    elif stripped == "returns error":
        set_update_adapter_outcome(ctx, AdCPAdapterError())
    elif stripped == "not yet called":
        # Manual approval — adapter won't be called
        pass
    else:
        raise ValueError(f"Unknown adapter result: {stripped}")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: principal ownership
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the media buy exists with owner {owner}"))
@given(parsers.parse('the media buy "{label}" exists with owner {owner}'))
@given(parsers.parse('the media buy is owned by principal "{owner}"'))
def given_media_buy_with_owner(ctx: dict, owner: str, label: str | None = None) -> None:
    """Ensure the existing media buy is owned by the specified principal.

    Creates the owner principal if needed and sets the media buy's principal_id.
    Step text claims the owner "exists" — we must guarantee the principal record
    is present in the DB, not just set the foreign key. The quoted form names the buy
    by its Gherkin label; it must be the Background's buy.
    """
    mb = _existing_media_buy(ctx, label)
    owner_id = owner.strip()
    _ensure_principal(ctx, owner_id)
    mb.principal_id = owner_id
    ctx["env"]._commit_factory_data()


@given(parsers.parse("the authenticated principal is {principal}"))
def given_authenticated_principal(ctx: dict, principal: str) -> None:
    """Authenticate as *principal*, a principal that exists and can reach the request's account.

    The ownership outlines vary ONE thing, who owns the buy against who sends the update,
    so everything else must hold for both rows. A principal row that does not exist sends
    no credential, and one without access to the account the request names is refused at
    account resolution; either answers before ``_verify_principal`` runs, so the mismatch
    row would see a refusal it is not about and the match row would fail. The principal is
    created when absent and granted the env's default account, which is the account
    ``ctx["account_ref"]`` names (``_seed_update_account_ref``).
    """
    principal_id = principal.strip()
    _ensure_principal(ctx, principal_id)
    ctx["env"].setup_default_account(principal_id=principal_id)
    authenticate_env_as(ctx, principal_id)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: immutable fields
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.re(r"the request includes 1 package update with (?P<update_content>.+[^:])$"))
def given_package_update_with_content(ctx: dict, update_content: str) -> None:
    """Configure a package update based on free-text content description.

    Uses regex to avoid matching the datatable step 'the request includes 1 package update with:'.
    The [^:] at the end ensures this step doesn't capture text ending with ':'.

    Matched patterns from immutable-fields partition/boundary scenarios:
    - 'budget and targeting updates only' — valid updatable fields
    - 'product_id=prod_new (immutable)' — attempt to set immutable product_id
    - 'format_ids=[fmt_new] (immutable)' — attempt to set immutable format_ids
    - 'pricing_option_id=po_new (immutable)' — attempt to set immutable pricing_option_id
    """
    kwargs = _ensure_update_defaults(ctx)
    stripped = update_content.strip()

    if "budget and targeting" in stripped:
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": 5000.0,
                "targeting_overlay": {"geo_countries": ["US"]},
            }
        ]
    elif stripped.startswith("product_id="):
        product_id = stripped.split("=")[1].split()[0]
        kwargs["packages"] = [{"package_id": "pkg_001", "product_id": product_id, "budget": 5000.0}]
    elif stripped.startswith("format_ids="):
        kwargs["packages"] = [{"package_id": "pkg_001", "format_ids": ["fmt_new"], "budget": 5000.0}]
    elif stripped.startswith("pricing_option_id="):
        po_id = stripped.split("=")[1].split()[0]
        kwargs["packages"] = [{"package_id": "pkg_001", "pricing_option_id": po_id, "budget": 5000.0}]
    else:
        raise ValueError(f"Unknown update_content pattern: {stripped}")


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def apply_required_update_fields(ctx: dict, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Default the two fields update-media-buy-request.json lists in /required besides the id.

    3.1.1 declares ``required: ["idempotency_key", "account", "media_buy_id"]``, so a bag
    carrying only ``media_buy_id`` is a request the pin rejects. ONE function with two
    callers: :func:`_ensure_update_defaults`, so the Given that says "a VALID
    update_media_buy request" builds a valid bag; and the When step, so a use case with its
    own bag builder (UC-026's ``_ensure_update_kwargs``) dispatches a valid request too.

    ``setdefault``, so a Given that names a field wins — including a Given that means to
    send NONE, which writes the harness's ``OMIT_*`` sentinel rather than popping the key.
    A pop would be undone by the next Given's call through here; the sentinel survives both
    this function and the next, and ``MediaBuyDualEnv._flatten_update_request`` strips it at
    the wire (the payload artifact records it as ``<omit:idempotency_key>`` /
    ``<omit:account>``).

    ``idempotency_key`` is minted per scenario and spec-shaped
    (``^[A-Za-z0-9_.:-]{16,255}$``): unique, because a key reused across scenarios replays
    the first one's response instead of performing the update; stable within a scenario, so
    a row that dispatches the same request twice hits the idempotency cache on purpose.
    Same two-policy split as ``given_media_buy._ensure_request_defaults`` on the create
    side, for the same reason.

    ``account`` must RESOLVE, not merely be present: the transport boundary looks the
    reference up, so a literal id answers ACCOUNT_NOT_FOUND and every scenario that is not
    about accounts fails on resolution before reaching what it grades. It is taken from
    ``ctx["account_ref"]`` — the one key in this tree meaning "the account this request
    names", filled in by the env route's seed — and only seeded here when no route named
    one. That preference is load-carrying, not tidiness: ``setup_default_account`` goes
    through ``setup_default_data``, which RE-CREATES a missing principal, and
    @T-UC-003-ext-a-unknown deletes its principal on purpose two Givens earlier. Seeding
    unconditionally resurrected the identity that scenario had just removed, and all three
    transports answered PERMISSION_DENIED instead of the refusal the row grades.
    """
    kwargs.setdefault("idempotency_key", mint(f"bdd-upd-key-{uuid.uuid4().hex}"))
    kwargs.setdefault(
        "account",
        ctx.get("account_ref") or {"account_id": ctx["env"].setup_default_account().account_id},
    )
    return kwargs


def _ensure_update_defaults(ctx: dict) -> dict[str, Any]:
    """Ensure ctx['update_kwargs'] holds an update request that is VALID BY CONSTRUCTION.

    3.1.1 ``media-buy/update-media-buy-request.json`` declares
    ``required: ["idempotency_key", "account", "media_buy_id"]``, so a bag carrying only
    ``media_buy_id`` is a request the pin rejects — and the step that calls this says "a
    VALID update_media_buy request", which then held for no scenario using it: every one
    dispatched a body production answers with INVALID_REQUEST naming a field the scenario
    never meant to test. The auth rows were the visible case (AUTH_MISSING expected,
    INVALID_REQUEST received), and the same defect silently re-pointed every other row.

    ``media_buy_id`` comes from the Background's buy; the other two are
    :func:`apply_required_update_fields`, which the When step also runs so a bag this
    function never touched still dispatches a valid request.
    """
    if "update_kwargs" not in ctx:
        mb = ctx.get("existing_media_buy")
        assert mb is not None, (
            "No existing_media_buy in ctx — _ensure_update_defaults requires "
            "the Background step to have created a media buy"
        )
        ctx["update_kwargs"] = {
            "media_buy_id": mb.media_buy_id,
        }
    return apply_required_update_fields(ctx, ctx["update_kwargs"])


# ═══════════════════════════════════════════════════════════════════════
# BR-RULE-215: revision as the buyer's optimistic-concurrency token
# ═══════════════════════════════════════════════════════════════════════
# These four steps wake the two revision scenarios, which had no step definitions
# at all — the reason they sat dormant behind the UC-003 harness xfail. The
# obligation they carry is the one the whole revision surface rests on: a mutating
# update advances the token and REPORTS the advanced value, so a buyer can take it
# from the response and hand it straight back on the next call.


@given(parsers.parse('the media buy "{label}" is at revision {revision:d}'))
def given_media_buy_at_revision(ctx: dict, label: str, revision: int) -> None:
    """Put the persisted row at *revision*, then prove it took.

    Written through the repository''s own column rather than a factory rebuild, so the
    scenario starts from a row the update path will really read. The read-back is not
    ceremony: seeding a distinctive value is what separates "reports the post-write
    value" from "reports a plausible constant", and a seed that silently did not land
    would turn the whole scenario green for the wrong reason.
    """
    from sqlalchemy import update as sa_update

    from src.core.database.models import MediaBuy

    env = ctx["env"]
    real_id = _resolve_media_buy_id(ctx, label)
    session = env._session  # noqa: SLF001 — the harness's session seam, as used above
    session.execute(sa_update(MediaBuy).where(MediaBuy.media_buy_id == real_id).values(revision=revision))
    session.commit()

    seeded = session.get(MediaBuy, real_id)
    session.refresh(seeded)
    assert seeded.revision == revision, (
        f"seeding media buy {label!r} to revision {revision} did not take (column reads "
        f"{seeded.revision!r}); every assertion below would grade the wrong starting point"
    )
    ctx.setdefault("seeded_revisions", {})[label] = revision


@given("the request revision is set to <not provided>")
def given_request_revision_absent(ctx: dict) -> None:
    """Send NO revision — the last-write-wins path the spec makes optional.

    Exact text rather than the `{revision:d}` parser above, because the Examples row
    carries the literal `<not provided>` and an int parser cannot match it. Without
    this the row failed on StepDefinitionNotFoundError and the suite's non-strict
    auto-xfail absorbed it, so a row grading LWW read as dormant-for-some-reason.
    Leaving `revision` out of the kwargs IS the assertion: the request must go without
    a token, not with a null one.
    """
    _ensure_update_defaults(ctx)


@given(parsers.parse("the request revision is set to {revision:d}"))
def given_request_revision(ctx: dict, revision: int) -> None:
    """Send *revision* as the buyer''s expected-current token on the update request."""
    kwargs = _ensure_update_defaults(ctx)
    kwargs["revision"] = revision


@given(parsers.parse('the request revision is set to "{revision}"'))
def given_request_revision_wrong_type(ctx: dict, revision: str) -> None:
    """Send *revision* as a STRING — the wrong-type partition row.

    A separate step from the ``{revision:d}`` parser above for the same reason
    ``<not provided>`` needs its own: the @T-UC-003-partition-revision Examples row
    carries a QUOTED value (``"7"``), and an int parser cannot match it. ``parsers.parse``
    rather than ``parsers.re`` so the match is anchored at BOTH ends, matching the sibling
    ``{revision:d}`` step's form — an unanchored ``re`` pattern matches any line that
    merely starts with this text.

    Without this step the row failed on StepDefinitionNotFoundError, and the strict-xfail
    guard correctly refused to let that be recorded as a production/spec gap — it is test
    wiring, and the guard named it: "MISCLASSIFIED strict-xfail ... this is DORMANCY
    (test-wiring), not a graded production gap". That is the
    dormancy-misclassified-as-gap pattern ``test_architecture_bdd_xfail_reason_tokens``
    grades.

    The string is passed through UNCONVERTED. That is the whole point of the row:
    ``revision`` is typed as an integer, so 7 and ``"7"`` must NOT behave alike — the
    request must be refused as INVALID_REQUEST. Coercing it here with ``int(revision)``
    would send a well-typed 7 and grade the CONFLICT path instead (a re-run of the
    ``matches_current`` row), so the row would pass while testing nothing it names, and a
    production regression that silently accepts a string would read as green.
    """
    kwargs = _ensure_update_defaults(ctx)
    kwargs["revision"] = revision


@then(parsers.parse("the response should contain a revision with value {expected:d}"))
def then_response_revision_value(ctx: dict, expected: int) -> None:
    """Assert the WIRE revision is the post-write value.

    Read off the wire rather than the typed payload, because the regression this
    guards is a producer reporting a value it never read — a schema default reaching
    the buyer looks identical in a typed object and is only visible in what was sent.
    """
    from tests.bdd.steps._outcome_helpers import wire_dict

    actual = wire_dict(ctx).get("revision")
    assert actual == expected, (
        f"expected the update response to carry revision {expected} (the value AFTER this "
        f"write — spec 3.1.1 update-media-buy-response.json: 'Revision number after this "
        f"update'), got {actual!r}"
    )


@then("the response should contain a valid_actions array")
def then_response_valid_actions_array(ctx: dict) -> None:
    """Assert valid_actions is present AND non-empty on the wire.

    Presence alone would pass on an empty list, which is what a non-AdCP status string
    produces — the exact defect valid_actions derivation exists to prevent. INT-002:
    the buyer plans its next call from this array without a get_media_buys round-trip,
    and an empty array tells it there is nothing it may do.
    """
    from tests.bdd.steps._outcome_helpers import wire_dict

    actions = wire_dict(ctx).get("valid_actions")
    assert isinstance(actions, list), f"valid_actions must be an array on the wire, got {actions!r}"
    assert actions, (
        "valid_actions is empty; a buyer reads it to plan its next call, and an empty "
        "array is what an unnormalized status string yields"
    )


# ═══════════════════════════════════════════════════════════════════════
# Request builders — the bva-* outlines' base request and its subject
# ═══════════════════════════════════════════════════════════════════════
#
# Each bva outline starts from "a valid update_media_buy request for <label>" and then
# changes ONE thing. The base must therefore already be a request the seller accepts, so
# the row's own change is the only thing that can decide the outcome: a base carrying no
# updatable field is refused as an empty update, and every rejection row would pass on
# that refusal instead of the one it names.


def _update_request_for(ctx: dict, label: str | None) -> dict[str, Any]:
    """The base update bag: the named buy, the required fields, and one package budget change."""
    _existing_media_buy(ctx, label)
    kwargs = _ensure_update_defaults(ctx)
    kwargs["packages"] = [{"package_id": "pkg_001", "budget": _UPDATED_BUDGET}]
    return kwargs


@given(parsers.parse('a valid update_media_buy request for "{label}"'))
@given(parsers.parse('an update_media_buy request for "{label}"'))
def given_update_request_for(ctx: dict, label: str) -> None:
    """Build an accepted update of *label*: one package budget change, nothing else."""
    _update_request_for(ctx, label)


#: billing-eligibility rows -> the invoice_recipient the update overrides with. Only the
#: invoice_recipient row remains (see the feature's correction note); the entity is one no
#: Given links to the account, so the seller's authorization check must refuse it.
_BILLING_CONDITIONS: dict[str, str] = {
    "invoice_recipient supplied = business entity not authorized for this account": "acme-finance-not-on-acct",
}


@given(parsers.parse('an update_media_buy request for "{label}" with the billing condition: {condition}'))
def given_update_with_billing_condition(ctx: dict, label: str, condition: str) -> None:
    """An accepted update of *label* that also overrides invoice_recipient as the row says."""
    _update_request_for(ctx, label)["invoice_recipient"] = _invoice_recipient(_BILLING_CONDITIONS[condition])


@given(parsers.parse("the request includes 1 package update where start_time is {value}"))
def given_package_update_start_time(ctx: dict, value: str) -> None:
    """Replace the package update with one that sets only ``start_time`` to *value*.

    Sent as written: the package-scope ``asap`` row exists to show the seller refusing the
    literal (package-update.json ``start_time`` is ``not: {const: asap}``).
    """
    _ensure_update_defaults(ctx)["packages"] = [{"package_id": "pkg_001", "start_time": value}]


#: A protocol-valid value for each member package-update.json's root ``not`` forbids. Valid
#: shapes on purpose: the only defect in the request is that the member is present. The
#: canonical selector arms (format_option_refs, format_kind, params) are named beside
#: ``format_ids``; ``capability_ids`` is the pre-GA field the schema's description says "is
#: also rejected rather than accepted as an extension"; ``bidding`` and ``bid_price`` are
#: forbidden only TOGETHER, ``bidding`` being a non-null object (core/bidding-policy.json's
#: ``{automatic: true}``).
_IMMUTABLE_PACKAGE_VALUES: dict[str, Any] = {
    "product_id": "prod_other",
    "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}],
    "pricing_option_id": "po_other",
    "format_option_refs": [{"scope": "product", "format_option_id": "opt_other"}],
    "format_kind": "image",
    "params": {"width": 300, "height": 250},
    "capability_ids": ["cap_other"],
    "budget_cap_timezone": "America/New_York",
    "bidding": {"automatic": True},
    "bid_price": 5.0,
}

#: What the generated rows mean by "all three immutable fields": the three they were
#: written against, before the pin's list of six was read in full.
_ORIGINAL_THREE_IMMUTABLE = ("product_id", "format_ids", "pricing_option_id")

#: The row texts that name a package update carrying NO immutable field.
_MUTABLE_ONLY_PACKAGE_UPDATES = ("only updatable fields", "only mutable fields", "only budget")


@given(parsers.parse("the request includes a package update where {description}"))
@given(parsers.parse("the request includes 1 package update containing {description}"))
def given_package_update_with_immutable_fields(ctx: dict, description: str) -> None:
    """Send a budget update plus every immutable field *description* names.

    The immutable-field outlines (bva-immutable-field-guard, bva-package-immutable-fields,
    partition-immutable-package-field, package-update-immutable-keyword) phrase the same
    rows several ways; this reads which of the fields in :data:`_IMMUTABLE_PACKAGE_VALUES`
    a row names ("all three" names the original three). A row naming none must be one of
    the mutable-only rows, so an unrecognised phrasing fails here instead of silently
    sending a valid update.
    """
    if "all three immutable fields" in description:
        named = list(_ORIGINAL_THREE_IMMUTABLE)
    else:
        named = [name for name in _IMMUTABLE_PACKAGE_VALUES if name in description]
    assert named or any(marker in description for marker in _MUTABLE_ONLY_PACKAGE_UPDATES), (
        f"package update description {description!r} names no immutable field and is not a mutable-only row"
    )
    package = {"package_id": "pkg_001", "budget": _UPDATED_BUDGET}
    package.update({name: _IMMUTABLE_PACKAGE_VALUES[name] for name in named})
    _ensure_update_defaults(ctx)["packages"] = [package]


@then(
    parsers.parse('the update is refused INVALID_REQUEST naming the package update\'s "{name}" under keyword {keyword}')
)
def then_package_update_field_refused_by_keyword(ctx: dict, name: str, keyword: str) -> None:
    """The refusal names *name* on the one package update, with the schema keyword *keyword*.

    The code alone does not separate the named immutable-field refusal from the generic
    one an undeclared field earns in dev (``additionalProperties``), and production drops
    an undeclared field instead of refusing it. ``core/error.json``: ``issues[].keyword`` is
    the schema keyword that rejected the payload, and ``field`` MUST be ``issues[0].pointer``
    in JSONPath-lite.
    """
    _assert_package_update_refused(ctx, [name], keyword)


def _assert_package_update_refused(
    ctx: dict, names: list[str], keyword: str, *, require_suggestion: bool = False
) -> None:
    """INVALID_REQUEST whose ``issues[]`` name each of *names* on the one package update under *keyword*.

    One issue per member: ``UpdateMediaBuyRequest._refuse_forbidden_package_shapes`` raises
    one entry per forbidden shape. ``field`` is ``issues[0].pointer``, so it is pinned only
    when a single member is named; for several, which one is first is the pin's order.
    """
    ctx["result"].assert_wire_error(
        "INVALID_REQUEST",
        field=f"packages[0].{names[0]}" if len(names) == 1 else None,
        issues=[{"pointer": f"/packages/0/{name}", "keyword": keyword} for name in names],
        require_suggestion=require_suggestion,
    )


def _assert_refused_by_root_not(ctx: dict) -> None:
    """Every immutable member the package update carries is refused by package-update.json's root ``not``.

    The keyword is what separates this refusal from the generic one: in dev an undeclared
    member is refused under ``additionalProperties`` with the same code, and in production
    it is dropped and the update succeeds.
    """
    package = ctx["update_kwargs"]["packages"][0]
    named = [name for name in _IMMUTABLE_PACKAGE_VALUES if name in package]
    assert named, f"the row's package update carries no immutable member: {sorted(package)}"
    _assert_package_update_refused(ctx, named, "not", require_suggestion=True)


@then("the update is refused AMBIGUOUS_BIDDING_POLICY naming the package update itself")
def then_package_update_refused_ambiguous_bidding(ctx: dict) -> None:
    """AMBIGUOUS_BIDDING_POLICY, correctable, naming the PACKAGE.

    enums/error-code.json classifies the code correctable. Neither ``bidding`` nor
    ``bid_price`` alone is at fault, so ``field`` is the package-update object itself.
    """
    assert_wire_rejection(ctx, "AMBIGUOUS_BIDDING_POLICY", recovery="correctable", field="packages[0]")


#: idempotency_key boundary rows -> the value sent. update-media-buy-request.json requires
#: the key (``/required``) and constrains it to ``^[A-Za-z0-9_.:-]{16,255}$``; the second
#: row is long enough and carries a character the pattern excludes.
_IDEMPOTENCY_KEY_BOUNDARIES: dict[str, Any] = {
    "absent (field not provided)": OMIT_IDEMPOTENCY_KEY,
    "valid length, disallowed character (e.g. space)": "bdd update key with spaces",
}


@given(parsers.parse("the request's idempotency_key matches the boundary {boundary}"))
def given_idempotency_key_boundary(ctx: dict, boundary: str) -> None:
    """Send the idempotency_key the boundary row describes (or none, for the absent row)."""
    _ensure_update_defaults(ctx)["idempotency_key"] = _IDEMPOTENCY_KEY_BOUNDARIES[boundary]


#: collection_list overlay rows -> the targeting_overlay sent. Each reference carries the
#: two members core/collection-list-ref.json requires (agent_url, list_id).
_COLLECTION_LIST_OVERLAYS: dict[str, dict[str, Any]] = {
    "collection_list with valid agent_url and list_id": {
        "collection_list": {"agent_url": "https://collections.example.com", "list_id": "cl_include"},
    },
    "collection_list_exclude with valid agent_url and list_id": {
        "collection_list_exclude": {"agent_url": "https://collections.example.com", "list_id": "cl_exclude"},
    },
    "both collection_list and collection_list_exclude set": {
        "collection_list": {"agent_url": "https://collections.example.com", "list_id": "cl_include"},
        "collection_list_exclude": {"agent_url": "https://collections.example.com", "list_id": "cl_exclude"},
    },
}


@given(parsers.parse("the request carries a targeting_overlay where {boundary}"))
def given_collection_list_overlay(ctx: dict, boundary: str) -> None:
    """Put the row's collection-list overlay on the base request's package update."""
    _ensure_update_defaults(ctx)["packages"][0]["targeting_overlay"] = dict(_COLLECTION_LIST_OVERLAYS[boundary])


@given(parsers.parse("the tenant/adapter approval configuration is {boundary}"))
def given_approval_configuration(ctx: dict, boundary: str) -> None:
    """Route the update to manual approval through the tenant flag or the adapter flag."""
    if boundary.startswith("tenant flag true"):
        given_tenant_human_review_flag(ctx, "true")
    elif boundary.startswith("adapter flag true"):
        given_tenant_human_review_flag(ctx, "false")
        given_adapter_manual_approval_flag(ctx, "true")
    else:
        raise AssertionError(f"unknown approval configuration row: {boundary!r}")


# ═══════════════════════════════════════════════════════════════════════
# Storyboard: unknown package on a known media buy (invalid_transitions)
# ═══════════════════════════════════════════════════════════════════════


@given("the buyer references a package_id that does not belong to the media buy")
def given_unknown_package_reference(ctx: dict) -> None:
    """Choose a package_id the seeded buy does not hold."""
    ctx["unknown_package_id"] = mint(f"pkg_not_on_buy_{uuid.uuid4().hex}")


@when("the Buyer Agent sends update_media_buy targeting the unknown package")
def when_update_unknown_package(ctx: dict) -> None:
    """Send a budget update for the unknown package of the known buy, with a correlation_id.

    The correlation_id is stamped by the storyboard module's writer, so the shared
    correlation-echo Then reads the value this request carried.
    """
    from tests.bdd.steps.domain.uc003_storyboard_generic_client import stamp_correlation_id

    kwargs = _ensure_update_defaults(ctx)
    kwargs["packages"] = [{"package_id": ctx["unknown_package_id"], "budget": _UPDATED_BUDGET}]
    stamp_correlation_id(ctx, kwargs)
    when_send_update_request(ctx)


# ═══════════════════════════════════════════════════════════════════════
# Idempotency: a key already spent on a different body
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('idempotency_key "{key}" was previously used with a different request body'))
def given_idempotency_key_spent(ctx: dict, key: str) -> None:
    """SPEND *key* on a real, successful update whose body differs from the scenario's.

    The first request crosses the same transport and boundary as the second, so the hash
    the conflict is detected against is the one production stored -- the same shape as
    UC-011's ``given_key_already_spent``. Its body is a package budget change; the
    scenario's own request pauses the buy, so the two canonical payloads differ.
    """
    mb = _existing_media_buy(ctx)
    first = {
        "media_buy_id": mb.media_buy_id,
        "idempotency_key": key,
        "packages": [{"package_id": _resolve_package_id(ctx, "pkg_001"), "budget": _UPDATED_BUDGET}],
    }
    dispatch_request(ctx, **apply_required_update_fields(ctx, first))
    assert ctx.get("error") is None, f"Given: the first update on key {key!r} failed: {ctx.get('error')!r}"


# ═══════════════════════════════════════════════════════════════════════
# BR-RULE-217 / BR-RULE-010: new_packages
# ═══════════════════════════════════════════════════════════════════════
#
# This seller does not add packages to a live buy: it advertises no add_packages and refuses
# new_packages UNSUPPORTED_FEATURE (update-media-buy-request.json /properties/new_packages).
# These steps build the request the pin describes and grade the result on the wire; the
# scenarios written for a SUPPORTING seller fail at their own precondition, which reads the
# seller's advertisement rather than asserting it (salesagent-ol033.25 owns the feature).


def _product_pair(ctx: dict, product_id: str) -> tuple[str, str]:
    """``(product_id, pricing_option_id)`` for a seeded product, seeding it on first use.

    The Background's product is already seeded with its option; any other id gets its own
    product and pricing option, once, so two steps naming one id name one product.
    """
    from tests.harness.create_request import pricing_option_id

    if product_id == ctx["default_product"].product_id:
        return product_id, pricing_option_id(ctx["default_pricing_option"])
    seeded = ctx.setdefault("seeded_products", {})
    if product_id not in seeded:
        env = ctx["env"]
        product, option = env.setup_product_chain(ctx["tenant"], product_id=product_id)
        env._commit_factory_data()
        seeded[product_id] = (product.product_id, pricing_option_id(option))
    return seeded[product_id]


def _set_new_packages(ctx: dict, product_ids: list[str]) -> list[dict[str, Any]]:
    """Send one complete package-request (product, pricing option, budget) per product id."""
    from tests.harness.create_request import build_request_packages

    pairs = [_product_pair(ctx, product_id) for product_id in product_ids]
    entries = build_request_packages(pairs, budget=_UPDATED_BUDGET)
    _ensure_update_defaults(ctx)["new_packages"] = entries
    ctx["new_package_product_ids"] = [product_id for product_id, _option in pairs]
    return entries


@given("the request includes new_packages with one complete package-request")
def given_new_packages_one(ctx: dict) -> None:
    """One complete package-request for a product the buy does not hold yet."""
    _set_new_packages(ctx, ["prod_new_a"])


@given("the request includes new_packages with two complete package-requests")
def given_new_packages_two(ctx: dict) -> None:
    """Two complete package-requests for two products the buy does not hold yet."""
    _set_new_packages(ctx, ["prod_new_a", "prod_new_b"])


@given("the request includes new_packages with an entry missing product_id")
def given_new_packages_missing_product(ctx: dict) -> None:
    """One package-request without product_id, which package-request.json lists in /required."""
    entries = _set_new_packages(ctx, ["prod_new_a"])
    del entries[0]["product_id"]


@given(parsers.parse('the request includes new_packages with a package for product "{product_id}"'))
def given_new_packages_for_product(ctx: dict, product_id: str) -> None:
    """One complete package-request for *product_id*."""
    _set_new_packages(ctx, [product_id])


@given(parsers.parse('the media buy already has a package for product "{product_id}"'))
def given_existing_package_for_product(ctx: dict, product_id: str) -> None:
    """Seed a second package on the Background's buy, for a product seeded with its option."""
    from tests.factories import MediaPackageFactory

    seeded_id, _option = _product_pair(ctx, product_id)
    MediaPackageFactory(
        media_buy=_existing_media_buy(ctx),
        package_config={"product_id": seeded_id, "budget": 5000.0},
    )
    ctx["env"]._commit_factory_data()


#: product-uniqueness rows -> the product ids the update's new_packages carry. "existing"
#: stands for the product of the Background's package.
_NEW_PACKAGE_SETS: dict[str, list[str]] = {
    "update new_packages[] adds a product_id not present elsewhere": ["prod_new_a"],
    "update new_packages[] product_id duplicates an existing media buy package": ["existing"],
    "update new_packages[] contains two entries with the same product_id": ["prod_new_a", "prod_new_a"],
}


@given(parsers.parse('an update_media_buy request for "{label}" whose package set is described by {boundary}'))
def given_update_with_package_set(ctx: dict, label: str, boundary: str) -> None:
    """An update of *label* whose only change is the row's new_packages.

    No package budget change rides along: with one, a seller that ignores new_packages
    would answer every "accepted" row with a success it earned on the budget.
    """
    _existing_media_buy(ctx, label)
    existing_product = (ctx["existing_package"].package_config or {})["product_id"]
    _set_new_packages(
        ctx,
        [existing_product if product_id == "existing" else product_id for product_id in _NEW_PACKAGE_SETS[boundary]],
    )


def _advertised_actions(ctx: dict) -> list[str]:
    """The ``valid_actions`` the seller derives for the Background's buy in its seeded state.

    ``_adcp_status_and_actions`` is the one function every update response takes
    ``valid_actions`` from, so this is the list the buyer would read before the update.
    A precondition, read from production rather than set: a seller advertises what its
    state machine says, and no test input can make it advertise more or less.
    """
    from src.core.tools.media_buy_update import _adcp_status_and_actions

    _status, actions = _adcp_status_and_actions(_existing_media_buy(ctx))
    return actions


@given(parsers.parse('the media buy\'s valid_actions advertises "{action}"'))
def given_valid_actions_advertise(ctx: dict, action: str) -> None:
    """Assert the seller advertises *action* for the buy in its seeded state."""
    actions = _advertised_actions(ctx)
    assert action in actions, f"the seller does not advertise {action!r} for this buy; valid_actions={actions!r}"


@given(parsers.parse('the media buy\'s valid_actions does NOT advertise "{action}"'))
def given_valid_actions_excludes(ctx: dict, action: str) -> None:
    """Assert the seller does not advertise *action* for the buy in its seeded state.

    A non-empty list is required too: a terminal buy advertises nothing, and "add_packages
    is absent" would then hold of a buy no update could reach at all.
    """
    actions = _advertised_actions(ctx)
    assert actions, "the seeded buy advertises no action at all, so the absence of one grades nothing"
    assert action not in actions, f"the seller advertises {action!r} for this buy; valid_actions={actions!r}"


@then("both new packages should appear in the affected_packages")
def then_new_packages_affected(ctx: dict) -> None:
    """The wire affected_packages carries one new package per product the request added.

    New packages are told apart from the Background's by package_id (the seller mints
    one for each), and matched to the request by product_id.
    """
    existing_ids = {ctx["existing_package"].package_id}
    affected = wire_field(ctx, "affected_packages")
    added = sorted(entry.get("product_id") for entry in affected if entry.get("package_id") not in existing_ids)
    assert added == sorted(ctx["new_package_product_ids"]), (
        f"expected new packages for {sorted(ctx['new_package_product_ids'])} in affected_packages, got {affected!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# BR-RULE-216: cancellation
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the cancellation_reason is set to {value}"))
def given_cancellation_reason(ctx: dict, value: str) -> None:
    """Send *value* (a length placeholder expands) as the request's cancellation_reason."""
    _ensure_update_defaults(ctx)["cancellation_reason"] = _expand_length_placeholder(value)


@given(parsers.parse('the media buy "{label}" is in "{status}" status with packages "{first}" and "{second}"'))
def given_media_buy_status_with_two_packages(ctx: dict, label: str, status: str, first: str, second: str) -> None:
    """Put the named buy in *status* holding packages *first* and *second*."""
    _existing_media_buy(ctx, label)
    given_media_buy_status(ctx, status)
    given_package_exists(ctx, first)
    given_package_exists(ctx, second)


@then(parsers.parse('package "{label}" should be canceled'))
def then_package_canceled(ctx: dict, label: str) -> None:
    """The wire affected_packages entry for *label* reports canceled: true (core/package.json)."""
    entry = wire_entry(ctx, "affected_packages", package_id=_resolve_package_id(ctx, label))
    assert entry.get("canceled") is True, f"package {label!r} is not reported canceled: {entry!r}"


@then(parsers.parse('the media buy status should NOT be "{status}"'))
def then_media_buy_status_not(ctx: dict, status: str) -> None:
    """The wire media_buy_status is present and is not *status*."""
    actual = wire_field(ctx, "media_buy_status")
    assert actual != status, f"media_buy_status is {actual!r}; the update must not have made the buy {status!r}"


# ═══════════════════════════════════════════════════════════════════════
# BR-RULE-209: sandbox on the three update response shapes
# ═══════════════════════════════════════════════════════════════════════

#: The account phrases the sandbox outlines use -> whether the named account is a sandbox.
_ACCOUNT_CONTEXTS: dict[str, bool] = {"a sandbox account": True, "a production account": False}

#: ``(code, errors[0].field)`` of the refusal the terminal-failure shape provokes: a package
#: budget of 0 refused by the seller's positivity rule (sandbox.mdx: "Sandbox errors are real
#: validation errors. If a buyer sends an invalid budget using a sandbox account, return a
#: real error").
_TERMINAL_FAILURE_REFUSAL = ("BUDGET_TOO_LOW", "packages[0].budget")


def _name_account(ctx: dict, *, sandbox: bool) -> None:
    """Seed an account with the given sandbox flag and make it the one the update names.

    ``_seed_account_for_principal`` seeds the row and the caller's access to it; the
    reference is then written in the dict spelling the update bag uses, into the bag when
    one exists and into ``ctx["account_ref"]`` for a bag built later.
    """
    from tests.bdd.steps.generic.given_auth import _seed_account_for_principal

    _seed_account_for_principal(ctx, sandbox=sandbox)
    ref = {"account_id": ctx["account_ref"].account_id}
    ctx["account_ref"] = ref
    if "update_kwargs" in ctx:
        ctx["update_kwargs"]["account"] = ref


@given("the request's account reference resolves to a sandbox account")
def given_request_account_is_sandbox(ctx: dict) -> None:
    """The update will name a sandbox account."""
    _name_account(ctx, sandbox=True)


@given(parsers.parse("an update_media_buy request whose resolved account is {account_context}"))
def given_update_request_with_account(ctx: dict, account_context: str) -> None:
    """An accepted update of the Background's buy, naming the account the row describes."""
    _name_account(ctx, sandbox=_ACCOUNT_CONTEXTS[account_context])
    _update_request_for(ctx, None)


#: The package update that makes a terminal-failure row fail, by the trigger the row names.
#: Both are refused inside the update, AFTER the boundary resolved the account, so a failure
#: builder that read the resolved account would show on the refusal. ``None`` (the sentence
#: without "because ...") is a package budget of 0, which the schema admits
#: (package-update.json /properties/budget minimum 0) and the seller's own budget rule
#: refuses with :data:`_TERMINAL_FAILURE_REFUSAL`; a negative budget would be refused by the
#: request schema before the account is resolved. A package the buy does not hold is
#: refused with PACKAGE_NOT_FOUND (update_media_buy.mdx error table).
_TERMINAL_FAILURE_PACKAGE: dict[str | None, dict[str, Any]] = {
    None: {"package_id": "pkg_001", "budget": 0.0},
    "the package update names a package the media buy does not hold": {
        "package_id": "pkg_not_in_buy",
        "budget": _UPDATED_BUDGET,
    },
}


@given(parsers.parse("the update resolves to the {shape} response shape"))
@given(parsers.parse("the update resolves to the {shape} response shape because {trigger}"))
def given_update_response_shape(ctx: dict, shape: str, trigger: str | None = None) -> None:
    """Arrange the request and the approval path so the update answers with *shape*.

    synchronous success: auto-approval and a valid package budget change. terminal
    failure: auto-approval and the package update :data:`_TERMINAL_FAILURE_PACKAGE` names
    for *trigger*. Each is refused inside the update, after the boundary resolved the
    account, so a sandbox account is in hand when the error is built; a negative budget
    would be refused by the request schema before the account was resolved at all.
    submitted task: manual approval of a valid change.
    """
    kwargs = _ensure_update_defaults(ctx)
    if shape == "terminal failure":
        assert trigger in _TERMINAL_FAILURE_PACKAGE, (
            f"unknown terminal-failure trigger {trigger!r}; known: {sorted(map(str, _TERMINAL_FAILURE_PACKAGE))}"
        )
        kwargs["packages"] = [dict(_TERMINAL_FAILURE_PACKAGE[trigger])]
        given_tenant_human_review_flag(ctx, "false")
        return
    kwargs.setdefault("packages", [{"package_id": "pkg_001", "budget": _UPDATED_BUDGET}])
    if shape == "synchronous success":
        given_tenant_human_review_flag(ctx, "false")
    elif shape == "submitted task":
        given_tenant_human_review_flag(ctx, "true")
    else:
        raise AssertionError(f"unknown update response shape: {shape!r}")


@then('the response payload should NOT contain a "sandbox" field')
def then_payload_has_no_sandbox(ctx: dict) -> None:
    """The response the buyer received carries no sandbox member, on either branch."""
    then_response_not_contain_field(ctx, "sandbox")


def _assert_sandbox_flag_true(ctx: dict) -> None:
    assert wire_field(ctx, "sandbox") is True, "a sandbox-account update must report sandbox: true"


def _assert_sandbox_flag_not_true(ctx: dict) -> None:
    flag = wire_lookup(ctx, "sandbox")
    assert flag is WIRE_MISSING or flag is False, (
        f"a production-account update must not report sandbox: true, got {flag!r}"
    )


def _assert_terminal_failure_refusal(ctx: dict) -> None:
    """The conformant refusal the terminal-failure Given provokes: its code, on its field.

    Named, not "any error": an account-resolution failure for the sandbox account is also
    a conformant error, and it would satisfy both rows below without the sandbox account
    ever reaching the seller's own validation.
    """
    code, field = _TERMINAL_FAILURE_REFUSAL
    ctx["result"].assert_wire_error_is_schema_conformant()
    ctx["result"].assert_wire_error(code, field=field)


def _assert_real_rejection(ctx: dict) -> None:
    """The seller's real budget refusal came back, and no success member did (not a silent ok)."""
    _assert_terminal_failure_refusal(ctx)
    then_response_not_contain_field(ctx, "media_buy_id")


def _assert_sandbox_forbidden(ctx: dict) -> None:
    """The terminal-failure arm forbids sandbox (update-media-buy-response.json oneOf/1 `not`)."""
    _assert_terminal_failure_refusal(ctx)
    then_response_not_contain_field(ctx, "sandbox")


_SANDBOX_OUTCOMES = {
    "sandbox flag present and true": _assert_sandbox_flag_true,
    "sandbox flag not true": _assert_sandbox_flag_not_true,
    "sandbox flag omitted": lambda ctx: wire_absent(ctx, "sandbox"),
    "rejected with a real validation error (not a silent ok)": _assert_real_rejection,
    "sandbox field forbidden on this shape": _assert_sandbox_forbidden,
}


@then(parsers.parse("the sandbox response-field outcome should be {outcome}"))
def then_sandbox_outcome(ctx: dict, outcome: str) -> None:
    """Grade the row's sandbox outcome on the response the buyer received."""
    _SANDBOX_OUTCOMES[outcome](ctx)


# ═══════════════════════════════════════════════════════════════════════
# "the result should be <outcome>": the update-only outcome families
# ═══════════════════════════════════════════════════════════════════════
#
# The shared dispatcher (uc002_create_media_buy.then_result_should_be) asks these, so each
# outcome sentence has one meaning across use cases.


def _requested_budget(ctx: dict) -> float:
    packages = ctx["update_kwargs"]["packages"]
    return float(packages[0]["budget"])


def _assert_synchronous_success(ctx: dict) -> None:
    """The update succeeded synchronously: the wire names the buy that was updated.

    Read off the success wire, which an error result does not have, so a rejection fails
    here with the wire's own diagnosis.
    """
    _assert_wire_field_equals(ctx, "media_buy_id", _existing_media_buy(ctx).media_buy_id)


def _assert_persisted_success(ctx: dict) -> None:
    """Synchronous success, and the requested package budget is what the seller stored.

    When the package update also carried a ``targeting_overlay``, every dimension it sent
    must be stored with the value sent: a seller that applied the budget and dropped the
    overlay answered the same success. Campaign-level fields sent beside the packages are
    graded by :func:`_assert_campaign_fields_applied`.
    """
    _assert_synchronous_success(ctx)
    stored, config = _stored_package(ctx, "pkg_001")
    assert stored is not None and float(stored) == _requested_budget(ctx), (
        f"update succeeded but the stored budget is {stored!r}, not the requested {_requested_budget(ctx)}"
    )
    sent_overlay = ctx["update_kwargs"]["packages"][0].get("targeting_overlay")
    if sent_overlay is not None:
        stored_overlay = config.get("targeting_overlay") or {}
        unapplied = {dim: value for dim, value in sent_overlay.items() if stored_overlay.get(dim) != value}
        assert not unapplied, (
            f"update succeeded but the stored targeting_overlay {stored_overlay!r} does not hold {unapplied!r}"
        )
    _assert_campaign_fields_applied(ctx)


def _assert_campaign_fields_applied(ctx: dict) -> None:
    """Every campaign-level field the request carried is what the seller stored.

    update_media_buy.mdx § Compatibility transaction boundary: one request is one atomic
    mutation, so a pause or a flight change sent beside package changes is applied with
    them. A seller that applied the packages and dropped the pause or the dates answered
    the same success. A request carrying none of these fields leaves nothing to check.
    """
    sent = ctx["update_kwargs"]
    sent_paused = sent.get("paused")
    sent_times = {field: sent[field] for field in ("start_time", "end_time") if field in sent}
    if sent_paused is None and not sent_times:
        return
    stored = _stored_existing_buy(ctx)
    if sent_paused is not None:
        assert stored.is_paused is sent_paused, (
            f"update succeeded but the stored is_paused is {stored.is_paused!r}, not {sent_paused!r}"
        )
        if sent_paused:
            # lifecycle.mdx: a paused buy reports media_buy_status "paused".
            _assert_wire_field_equals(ctx, "media_buy_status", "paused")
    for field, value in sent_times.items():
        _assert_instant_stored_as_sent(field, value, getattr(stored, field))


def _assert_accepted_unmodified(ctx: dict) -> None:
    """Synchronous success that reports no package modified.

    For a request carrying no updatable field: the pinned request requires none, and
    update-media-buy-response.json lists in ``affected_packages`` only "each modified
    package", so the array is absent or empty.
    """
    _assert_synchronous_success(ctx)
    then_no_affected_packages(ctx)


def _assert_frequency_cap_stored(ctx: dict) -> None:
    """Synchronous success, and pkg_001's stored overlay holds the frequency cap that was sent.

    The members the buyer set must be stored with the values sent; a member the buyer left out
    may be stored as null, which says the same thing.
    """
    _assert_synchronous_success(ctx)
    sent = ctx["update_kwargs"]["packages"][0]["targeting_overlay"]["frequency_cap"]
    _budget, config = _stored_package(ctx, "pkg_001")
    stored = (config.get("targeting_overlay") or {}).get("frequency_cap")
    assert isinstance(stored, dict), f"update succeeded but pkg_001 stores no frequency_cap: {config!r}"
    stored_members = {name: value for name, value in stored.items() if value is not None}
    assert stored_members == sent, f"stored frequency_cap {stored!r} is not the one sent, {sent!r}"


def _non_null_dimensions(overlay: dict[str, Any]) -> dict[str, Any]:
    """The dimensions an overlay document sets: a null one is cleared or never set."""
    return {name: value for name, value in overlay.items() if value is not None}


def _assert_overlay_patched(ctx: dict) -> None:
    """Synchronous success, and the stored overlay is the prior one patched by the sent one.

    update_media_buy.mdx (Package Update Object, targeting_overlay): "Per-dimension patch ...
    Omit the overlay or a dimension inside it to leave that dimension unchanged; provide a
    non-null value to replace it; provide ``null`` to clear it." So every dimension the update
    sent is stored as sent, and every other dimension is the one the earlier update stored. A
    seller that drops the sent overlay, or replaces the whole stored one, fails here.
    """
    _assert_synchronous_success(ctx)
    sent = ctx["update_kwargs"]["packages"][0].get("targeting_overlay", {})
    expected = _non_null_dimensions({**ctx["prior_targeting_overlay"], **sent})
    _budget, config = _stored_package(ctx, "pkg_001")
    stored = _non_null_dimensions(config.get("targeting_overlay") or {})
    assert stored == expected, f"stored targeting_overlay is {stored!r}, expected {expected!r}"


def _assert_submitted(ctx: dict) -> None:
    """The submitted task envelope: status submitted, with a task_id to poll."""
    _assert_wire_field_equals(ctx, "status", "submitted")
    then_response_contains_task_id(ctx)


def _assert_assignment_routing_persisted(ctx: dict) -> None:
    """Synchronous success, and the package now holds exactly the requested assignments,
    each routed to the placement_ids it asked for -- none where it named none.

    creative_assignments replaces the package's set (BR-RULE-024), so the stored set is
    compared whole: a refusal, a dropped assignment, or routing the seller did not record
    all fail here, where a bare "success" read nothing back.
    """
    _assert_synchronous_success(ctx)
    requested = {
        assignment["creative_id"]: assignment.get("placement_ids")
        for assignment in ctx["update_kwargs"]["packages"][0]["creative_assignments"]
    }
    stored = _stored_assignment_routing(ctx, "pkg_001")
    assert stored == requested, f"the seller stored the assignments {stored!r}, not the requested {requested!r}"


def _stored_existing_buy(ctx: dict) -> SimpleNamespace:
    """Synchronous success, then the seller's record of the existing buy.

    Read through ``MediaBuyUoW`` (the live server's database on e2e_rest), because the
    claim is what the seller RECORDED.
    """
    from tests.bdd.steps.domain.uc002_invariants import _stored_buy

    _assert_synchronous_success(ctx)
    return _stored_buy(ctx, _existing_media_buy(ctx).media_buy_id)


def _assert_start_time_stored_as_now(ctx: dict) -> None:
    """``asap`` was resolved to the instant the update wrote the row (``updated_at``), not ignored."""
    from tests.bdd.steps.domain.uc002_invariants import _assert_start_resolved_to

    stored = _stored_existing_buy(ctx)
    _assert_start_resolved_to(stored.start_time, stored.updated_at, "the update")


def _sent_instant(sent: str) -> datetime:
    """The UTC instant of a date-time string the update sent."""
    return datetime.fromisoformat(sent.replace("Z", "+00:00")).astimezone(UTC)


def _assert_instant_stored_as_sent(field: str, sent: str, stored: datetime | None) -> None:
    """The concrete date-time the update sent for *field* is the instant the seller stored."""
    from tests.bdd.steps.domain.uc002_invariants import _as_utc

    expected = _sent_instant(sent)
    assert stored is not None and _as_utc(stored) == expected, (
        f"the update sent {field} {sent!r}, but the seller stored {stored}"
    )


def _assert_start_time_stored_as_sent(ctx: dict) -> None:
    """The concrete date-time the update sent is the start_time the seller stored."""
    sent = ctx["update_kwargs"]["start_time"]
    _assert_instant_stored_as_sent("start_time", sent, _stored_existing_buy(ctx).start_time)


def _assert_completed(ctx: dict) -> None:
    """Applied in-line: the protocol status is ``completed`` on the success arm naming the buy.

    ``update-media-buy-response.json`` (adcp v3.2.7) separates the synchronous success arm
    from the submitted envelope; the top-level ``status`` (protocol-envelope TaskStatus) is
    what tells the buyer which one it got, so an approval-gated update fails here.
    """
    _assert_wire_field_equals(ctx, "status", "completed")
    _assert_synchronous_success(ctx)


def _assert_success_at_revision(ctx: dict, revision: int) -> None:
    """Synchronous success that reports, and stores, the advanced *revision*.

    update_media_buy.mdx: "Every mutating update increments ``revision`` and returns the new
    value in the success response." The wire value is what the buyer hands back on its next
    update; the stored value is what the seller will compare it against.
    """
    stored = _stored_existing_buy(ctx)
    then_response_revision_value(ctx, revision)
    assert stored.revision == revision, (
        f"the response reports revision {revision} but the seller stored {stored.revision!r}"
    )


def _assert_end_time_stored(ctx: dict) -> None:
    """Synchronous success; the seller stored the requested end_time and kept the start."""
    start, _end = _original_flight(ctx)
    _assert_stored_flight(_stored_existing_buy(ctx), start=start, end=_sent_instant(ctx["update_kwargs"]["end_time"]))


def _assert_flight_unchanged(ctx: dict) -> None:
    """Synchronous success, and the stored flight is the one the Given seeded."""
    start, end = _original_flight(ctx)
    _assert_stored_flight(_stored_existing_buy(ctx), start=start, end=end)


_UPDATE_OUTCOMES = {
    "accepted": _assert_synchronous_success,
    "accepted with the frequency cap stored": _assert_frequency_cap_stored,
    "success with each assignment's placement_ids stored as requested": _assert_assignment_routing_persisted,
    "accepted with no package modified": _assert_accepted_unmodified,
    "success (completed)": _assert_completed,
    "success (submitted)": _assert_submitted,
    "accepted with the requested end_time stored": _assert_end_time_stored,
    "accepted with the stored flight unchanged": _assert_flight_unchanged,
    "success with persisted records": _assert_persisted_success,
    "success with pending status": _assert_submitted,
    "submitted task envelope (awaiting approval)": _assert_submitted,
    "start_time stored as now": _assert_start_time_stored_as_now,
    "start_time stored as sent": _assert_start_time_stored_as_sent,
    "success with the stored overlay patched per dimension": _assert_overlay_patched,
    "refused INVALID_REQUEST by the package update's root not": _assert_refused_by_root_not,
}

#: ``success at revision N``: the parametrized update outcome, graded by
#: :func:`_assert_success_at_revision`.
_SUCCESS_AT_REVISION = re.compile(r"success at revision (?P<revision>\d+)")


def is_update_outcome(outcome: str) -> bool:
    """Whether *outcome* is one of the update-only outcome sentences graded here."""
    return outcome in _UPDATE_OUTCOMES or _SUCCESS_AT_REVISION.fullmatch(outcome) is not None


def assert_update_outcome(ctx: dict, outcome: str) -> None:
    """Grade an update-only outcome sentence."""
    if (match := _SUCCESS_AT_REVISION.fullmatch(outcome)) is not None:
        _assert_success_at_revision(ctx, int(match["revision"]))
        return
    _UPDATE_OUTCOMES[outcome](ctx)


def assert_update_not_persisted(ctx: dict) -> None:
    """After a rejected update, the targeted package does not hold the requested budget."""
    stored, _config = _stored_package(ctx, "pkg_001")
    assert stored is None or float(stored) != _requested_budget(ctx), (
        f"the update was rejected but the stored budget is the requested {_requested_budget(ctx)}"
    )

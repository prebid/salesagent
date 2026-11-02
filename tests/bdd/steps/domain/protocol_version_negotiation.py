"""Domain step definitions for BR-PROTOCOL-001 (salesagent-lfeo0, epic salesagent-i3a8d).

Grades inbound AdCP version negotiation on a tool that is NOT get_adcp_capabilities,
across every transport. UC-010 already grades it on capabilities; that was the whole
problem, because capabilities was the only tool that negotiated.

NOTHING HERE PATCHES PRODUCTION. The seller's advertised release set is read, never set,
so every scenario is true of the deployed build and none of them is limited to
in-process transports.

Where the expected values come from: the PINNED SDK (``adcp.get_adcp_spec_version()``),
not ``src.core.version_negotiation``'s own constants. Reading the seller's constant to
predict the seller's behaviour asserts production against itself -- it would still pass if
the derivation broke. The pin is the external authority the derivation is supposed to
follow, so a drift between them reddens these scenarios. That is also where the deleted
unit test's derivation assertion now lives, one layer out and on the wire.

Reused rather than redefined:

Given: "a tenant is configured for product discovery", "an inventory profile with only
    domain ...", and "a product linked to that inventory profile with pricing", all from
    tests/bdd/steps/domain/uc_get_products_inventory.py.
When: ``_call_get_products`` -- the same single funnel the inventory scenarios use, so
    these requests reach the wire by the identical path and differ only in the pins.
Then: "the response contains error code ..." and "the response arrives" come from
    tests/bdd/steps/generic/then_error.py; "the error is compliant with the AdCP error
    spec" from then_schema.py. Only the assertions with no generic equivalent live here.
"""

from __future__ import annotations

import adcp
from pytest_bdd import parsers, then, when

from tests.bdd.steps._outcome_helpers import require_payload
from tests.bdd.steps.domain.uc_get_products_inventory import _call_get_products

_CODE = "VERSION_UNSUPPORTED"

# An account id no Given seeds, in either the in-process database or the live server's. A
# scenario that names it is answered on the account the moment the boundary resolves one
# before judging the pin, so the expected VERSION_UNSUPPORTED cannot be reached by accident.
_UNKNOWN_ACCOUNT_ID = "acct-no-such-account-protocol-001"


def _pinned_release() -> str:
    """The release-precision version the pinned SDK says this build speaks: "3.1"."""
    return ".".join(adcp.get_adcp_spec_version().split(".")[:2])


def _pinned_major() -> int:
    """The major the pinned SDK says this build speaks: 3."""
    return int(adcp.get_adcp_spec_version().split(".", 1)[0])


# ── When steps ──────────────────────────────────────────────────────


@when(parsers.parse("the buyer requests products pinning adcp_major_version {major:d}"))
def when_products_with_major(ctx: dict, major: int) -> None:
    """Dispatch get_products carrying only a major pin."""
    _call_get_products(ctx, adcp_major_version=major)


@when(parsers.parse('the buyer requests products pinning adcp_version "{version}"'))
def when_products_with_release(ctx: dict, version: str) -> None:
    """Dispatch get_products carrying only a release-precision pin."""
    _call_get_products(ctx, adcp_version=version)


@when("the buyer requests products pinning the seller's advertised major")
def when_products_with_advertised_major(ctx: dict) -> None:
    """Dispatch get_products pinning the major this build is supposed to serve."""
    _call_get_products(ctx, adcp_major_version=_pinned_major())


@when(parsers.parse('the buyer requests products pinning adcp_version "{version}" and the seller\'s advertised major'))
def when_products_with_bad_release_good_major(ctx: dict, version: str) -> None:
    """BOTH pins: an unserveable release alongside the major this build does serve.

    The case the old alternatives reading got wrong -- it returned on the first pin that
    matched, so the supported major excused the unsupported release and the buyer was
    answered with products shaped by a release they had not asked for.
    """
    _call_get_products(ctx, adcp_version=version, adcp_major_version=_pinned_major())


@when(
    parsers.parse(
        "the buyer requests products pinning the seller's advertised release and adcp_major_version {major:d}"
    )
)
def when_products_with_good_release_bad_major(ctx: dict, major: int) -> None:
    """The mirror: a serveable release alongside a major this build does not serve."""
    _call_get_products(ctx, adcp_version=_pinned_release(), adcp_major_version=major)


@when(parsers.parse("the buyer requests products naming an unknown account and pinning adcp_major_version {major:d}"))
def when_products_with_account_and_major(ctx: dict, major: int) -> None:
    """An unserveable major on a request that also names an account.

    The account is what makes this scenario different from its sibling above: naming one
    requires a credential and makes the boundary resolve it, so a negotiation that runs after
    identity resolution answers this request on the account rather than on the pin.
    """
    _call_get_products(ctx, adcp_major_version=major, account={"account_id": _UNKNOWN_ACCOUNT_ID})


@when(parsers.parse('the buyer requests products naming an unknown account and pinning adcp_version "{version}"'))
def when_products_with_account_and_release(ctx: dict, version: str) -> None:
    """The release-precision form of the same ordering claim."""
    _call_get_products(ctx, adcp_version=version, account={"account_id": _UNKNOWN_ACCOUNT_ID})


# ── Then steps ──────────────────────────────────────────────────────


@then("the buyer receives the seller's products")
def then_products_returned(ctx: dict) -> None:
    """The request was SERVED, not merely un-refused.

    Asserting only "no error" would pass for a response that never reached the tool, which
    is the shape an over-eager negotiator produces.
    """
    payload = require_payload(ctx)
    products = getattr(payload, "products", None)
    assert products, f"expected the seller's products, got {products!r} on payload {payload!r}"


@then("the error details should carry the majors this seller advertises")
def then_details_carry_supported_majors(ctx: dict) -> None:
    """``supported_majors`` is a SHOULD-emit integer array through 3.x.

    It was declared on VersionUnsupportedDetails and never populated, so a buyer refused
    for a MAJOR was answered with releases only -- not the field they sent.
    """
    details = ctx["result"].wire_error_details(_CODE) or {}
    assert details.get("supported_majors") == [_pinned_major()], (
        f"expected supported_majors {[_pinned_major()]}, got {details.get('supported_majors')!r} in {details!r}"
    )


@then(parsers.parse("the error details should echo the rejected adcp_major_version {major:d}"))
def then_details_echo_rejected_major(ctx: dict, major: int) -> None:
    """The answer names what was refused, so it is self-describing without the request."""
    details = ctx["result"].wire_error_details(_CODE) or {}
    assert details.get("adcp_major_version") == major, (
        f"expected the refusal to echo adcp_major_version {major}, got "
        f"{details.get('adcp_major_version')!r} in {details!r}"
    )

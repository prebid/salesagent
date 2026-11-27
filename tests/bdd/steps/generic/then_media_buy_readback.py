"""The get_media_buys read-back: where a buy and its packages sit on the wire, and its revision.

Shared by every feature that reads a buy back with get_media_buys. They lived in the UC-019
step module, which ``pytest_plugins`` does not register, so other features re-declared the
steps or reached into its private helpers (#2683 review).
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import parsers, then

from tests.bdd.steps._outcome_helpers import wire_dict


def _resolve_media_buy_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin label to the real database media_buy_id."""
    labels = ctx.get("media_buy_labels", {})
    if label in labels:
        return labels[label]
    return label  # fallback: label IS the real ID (legacy)


def _wire_media_buy_entry(ctx: dict, mb_id: str, document: dict | None = None) -> dict:
    """Locate one seeded buy's entry in the buyer-visible wire document.

    Resolving through the Gherkin label (not the raw id) is what keeps a scenario
    that seeded several buys from silently grading whichever entry came first.
    """
    real_id = _resolve_media_buy_id(ctx, mb_id)
    doc = wire_dict(ctx) if document is None else document
    buys = doc.get("media_buys", [])
    for buy in buys:
        if buy.get("media_buy_id") == real_id:
            return buy
    raise AssertionError(
        f"Media buy '{mb_id}' (real_id={real_id}) is not in the response; "
        f"media_buys carried {[b.get('media_buy_id') for b in buys]!r}"
    )


@then(parsers.parse('the media buy "{mb_id}" revision should be {expected:d}'))
def then_media_buy_revision_equals(ctx: dict, mb_id: str, expected: int) -> None:
    """Assert the buy's wire revision is exactly the expected integer."""
    buy = _wire_media_buy_entry(ctx, mb_id)
    assert buy.get("revision") == expected, (
        f"Expected media buy '{mb_id}' revision {expected} on the wire, got {buy.get('revision')!r}"
    )


def _is_wire_integer(value: Any) -> bool:
    """Whether a wire value is an integer in the sense the pinned schema means.

    The obligation is JSON Schema's ``"type": "integer"``, which is a statement
    about the NUMBER (Draft 6+: a number with zero fractional part), not about the
    Python type the transport happened to decode it into. That distinction is
    load-bearing here: A2A frames its DataPart as a protobuf ``Struct``, whose only
    numeric kind is ``number_value`` (a double), so an integer field arrives as
    ``1.0`` on A2A and ``1`` on MCP. Asserting ``isinstance(int)`` would fail the
    a2a branch of every revision scenario over a framing detail while letting a real
    fractional revision through on MCP; this rejects ``1.5`` and ``"1"`` on both.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer()


@then("every returned media buy should include an integer revision field")
def then_every_buy_has_integer_revision(ctx: dict) -> None:
    """Sweep EVERY returned buy for an integer revision — not just the first."""
    buys = wire_dict(ctx).get("media_buys", [])
    assert buys, "No media buys were returned, so 'every returned media buy' asserts nothing"
    for buy in buys:
        assert "revision" in buy, (
            f"media buy {buy.get('media_buy_id')!r} carries no revision key; "
            f"the pinned item schema requires it (keys: {sorted(buy)})"
        )
        revision = buy["revision"]
        assert _is_wire_integer(revision), (
            f"media buy {buy.get('media_buy_id')!r} revision {revision!r} ({type(revision).__name__}) is not an "
            'integer; the pinned item schema types it "integer"'
        )


@then("every revision should be >= 1")
def then_every_revision_at_least_one(ctx: dict) -> None:
    """Sweep EVERY returned buy against the schema minimum of 1."""
    buys = wire_dict(ctx).get("media_buys", [])
    assert buys, "No media buys were returned, so 'every revision' asserts nothing"
    for buy in buys:
        revision = buy.get("revision")
        assert revision >= 1, (
            f"media buy {buy.get('media_buy_id')!r} revision {revision!r} is below the pinned schema minimum of 1"
        )


def _wire_media_buy(ctx: dict, mb_id: str) -> dict:
    """Return the media buy the buyer received on the wire, by Gherkin label."""
    real_id = _resolve_media_buy_id(ctx, mb_id)
    document = wire_dict(ctx)
    matching = [buy for buy in document.get("media_buys", []) if buy.get("media_buy_id") == real_id]
    assert len(matching) == 1, (
        f"expected exactly one media_buys[] entry for {mb_id!r} (real_id={real_id!r}); got "
        f"{len(matching)} in {[b.get('media_buy_id') for b in document.get('media_buys', [])]!r}"
    )
    return matching[0]


def _wire_package(ctx: dict, pkg_id: str, *, mb_id: str | None = None) -> dict:
    """Return the package the buyer received on the wire, by Gherkin label.

    ``mb_id`` scopes the search to one media buy when the step text names it;
    without it the whole document is searched, and the "exactly one" assertion
    still refuses an ambiguous match.
    """
    buys = [_wire_media_buy(ctx, mb_id)] if mb_id is not None else wire_dict(ctx).get("media_buys", [])
    matching = [pkg for buy in buys for pkg in buy.get("packages", []) if pkg.get("package_id") == pkg_id]
    assert len(matching) == 1, (
        f"expected exactly one packages[] entry for {pkg_id!r} across {len(buys)} media buy(s) on the "
        f"wire; got {len(matching)}: {[p.get('package_id') for buy in buys for p in buy.get('packages', [])]!r}"
    )
    return matching[0]


def _reported_creative_ids(ctx: dict, pkg_id: str) -> list[str]:
    """The creatives a package's ``creative_approvals`` name on the get_media_buys wire, sorted.

    ``get_media_buys`` reports one ``creative_approvals`` entry per creative assigned to the
    package, so this is the seller's record of the package's creatives as the buyer reads it.
    """
    return sorted(approval["creative_id"] for approval in _wire_package(ctx, pkg_id).get("creative_approvals") or [])

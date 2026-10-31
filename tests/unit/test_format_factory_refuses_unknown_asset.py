"""The format factory builds the asset it was asked for, or refuses.

``make_asset`` used to resolve its class with ``_ASSET_CLASS_MAP.get(asset_type,
ImageFormatAsset)`` over a map covering 6 of the 14 asset types the generated
``Format.assets`` union models, so a Given asking for a video, audio or html asset
got an IMAGE asset and every assertion downstream graded the image path. A factory
that substitutes a different object for the one asked for cannot fail, and a
scenario built on it cannot falsify — which is why this is graded here rather than
left to the scenarios that consume it.

The same hole existed one level down: ``make_asset_group``'s inner-class lookup
fell back to the union's first member (also image) and ``make_asset_group`` then
dropped a ``None`` silently, so a group asked for two asset types could come back
holding one, or two of the wrong kind.

Three properties, which together are the whole contract:

* an unbuildable asset type RAISES;
* every type the factory claims it can build comes back carrying that
  ``asset_type`` — not merely "an object";
* the vocabulary is exactly what the pinned union admits, top-level and in-group.

BOTH SIDES OF THAT LAST COMPARISON ARE NOW DERIVED, and the two derivations read
DIFFERENT ARTIFACTS OF THE SAME WHEEL -- which is what keeps it a check rather
than a tautology. The factory's side comes from the SDK's GENERATED CLASSES
(``_asset_classes_by_type(Format)``); this side comes from the SDK's BUNDLED JSON
SCHEMA, via the repo's own ``tests.helpers.pinned_schema`` loader. Codegen output
against schema bundle, so a divergence between them still fails loudly -- and
that is the divergence that matters, because it is the one the factory's own
comments worry about ("a scenario needing one of the other 7 is blocked on SDK
generation").

This side used to be a hand-written literal, deliberately: "an SDK bump that
changes either union fails loudly in both directions". It did fire on a bump --
and what it reported was its own staleness, not a defect. The literal held the
14 discriminators of ``_schemas/3.0``; the pin is 3.2, which declares 21. A
constant beside a derived value is the half that drifts, and re-transcribing it
only defers that to the next bump (salesagent-8qela.168). What a pin CHANGE is
not, by itself, is a defect; what a codegen/schema DIVERGENCE is, always.

THE GROUP SET IS DERIVED FROM ITS OWN ARM rather than subtracted from the
top-level one. It was ``PINNED_ASSET_TYPES - {"brief", "catalog"}``, encoding an
SDK limitation that no longer exists: at the 3.2 pin the ``repeatable_group`` arm
declares all 21 and the factory's group map models all 21.

AND BOTH POPULATIONS ARE ASSERTED NON-EMPTY. 3.2 expresses this union as a
NESTED discriminated union -- an outer ``oneOf`` on ``item_type`` with an inner
``oneOf`` on ``asset_type`` -- so a reader that looks for ``asset_type`` at the
first level finds NOTHING and returns an empty set. A derivation that reads zero
and compares it to zero is green and grades nothing, which is how this fix could
have been worse than the literal it replaces.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.factories.format import ASSET_TYPES, GROUP_ASSET_TYPES, make_asset, make_asset_group
from tests.helpers import pinned_schema


def _asset_type_consts(node: Any) -> frozenset[str]:
    """Every ``asset_type`` const anywhere under *node*.

    Walks the whole subtree rather than a fixed depth, because the pin nests the
    union: ``items.oneOf[item_type]`` then ``.oneOf[asset_type]``. Reading one
    level finds no discriminator at all.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            const = (value.get("properties") or {}).get("asset_type") or {}
            if isinstance(const, dict) and isinstance(const.get("const"), str):
                found.add(const["const"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return frozenset(found)


def _assets_items() -> dict[str, Any]:
    """The pinned ``Format.assets`` item schema, from the SDK's bundled JSON."""
    schema = pinned_schema.load("core/format.json")
    items = ((schema.get("properties") or {}).get("assets") or {}).get("items") or {}
    assert isinstance(items, dict) and items, (
        "core/format.json declares no assets.items at the pinned version, so both "
        "expected sets below would be empty and every comparison would be vacuous"
    )
    return items


def _arm(item_type: str) -> dict[str, Any]:
    """The ``items.oneOf`` arm whose ``item_type`` const is *item_type*."""
    arms = _assets_items().get("oneOf") or []
    for candidate in arms:
        if ((candidate.get("properties") or {}).get("item_type") or {}).get("const") == item_type:
            return candidate
    raise AssertionError(
        f"no assets.items arm declares item_type={item_type!r}; the pin's union has "
        f"been reshaped and this module's derivation no longer finds it. Arms present: "
        f"{[((a.get('properties') or {}).get('item_type') or {}).get('const') for a in arms]}"
    )


#: The asset_type discriminators of the pinned core/format.json ``assets`` union,
#: derived from the individual-slot arm. The repeatable_group member and the SDK's
#: ``_unknown`` fallback branch are not asset types and are not buildable by name.
PINNED_ASSET_TYPES = _asset_type_consts(_arm("individual"))

#: The same, for the in-group union — derived from its OWN arm, not subtracted
#: from the one above.
PINNED_GROUP_ASSET_TYPES = _asset_type_consts(_arm("repeatable_group"))


def test_the_pinned_unions_are_not_empty() -> None:
    """Population check: an empty derivation would make every test below vacuous.

    This is the failure mode the literal did not have. The pin nests the union two
    levels deep, so a derivation that reads only the outer ``oneOf`` returns an
    empty set, and ``frozenset() == frozenset()`` passes while grading nothing.
    """
    assert PINNED_ASSET_TYPES, (
        "no asset_type const found under the individual arm of core/format.json — "
        "the derivation matched nothing rather than the union being empty"
    )
    assert PINNED_GROUP_ASSET_TYPES, "no asset_type const found under the repeatable_group arm of core/format.json"


def test_make_asset_refuses_an_unknown_asset_type() -> None:
    """An asset type outside the vocabulary raises instead of yielding an image."""
    with pytest.raises(ValueError, match="not_a_real_asset_type"):
        make_asset("not_a_real_asset_type")


def test_make_asset_group_refuses_an_unknown_asset_type() -> None:
    """A group member the inner union has no class for raises, never silently drops."""
    with pytest.raises(ValueError, match="not_a_real_asset_type"):
        make_asset_group("image", "not_a_real_asset_type")


# DELETED: test_make_asset_group_refuses_a_type_the_group_union_excludes.
#
# It asserted "brief is a real top-level asset type and still not groupable" and
# expected make_asset_group("brief") to raise. THE PIN CONTRADICTS THAT. In
# core/format.json at 3.2 the assets.items ``repeatable_group`` arm declares all
# 21 asset_type consts, brief and catalog among them, and the generated group
# union models all 21 — so nothing is excluded and there is no type for the
# refusal to fire on. Measured: both arms 21, and the factory's group map 21,
# symmetric difference empty against the schema.
#
# Deleted rather than skipped or weakened, per the charter: a test asserting
# incorrect behaviour goes, with the pin cited for what is correct. The property
# it was reaching for — the group vocabulary is exactly the pinned group union —
# is graded below by derivation on both sides, which also catches the reverse
# (a type the schema groups and the SDK does not generate). The unknown-type
# refusal above is unaffected and is pin-independent.


@pytest.mark.parametrize("asset_type", sorted(PINNED_ASSET_TYPES))
def test_make_asset_builds_the_asset_type_it_was_asked_for(asset_type: str) -> None:
    """The object carries the requested discriminator — the wrong-object check."""
    assert make_asset(asset_type).asset_type == asset_type


@pytest.mark.parametrize("asset_type", sorted(PINNED_GROUP_ASSET_TYPES))
def test_make_asset_group_builds_the_asset_types_it_was_asked_for(asset_type: str) -> None:
    assert [a.asset_type for a in make_asset_group(asset_type).assets] == [asset_type]


def test_factory_vocabulary_is_the_pinned_union() -> None:
    """Two derivations of one wheel: generated classes against bundled schema.

    Not a tautology — the left side comes from the SDK's generated ``Format``
    class and the right from its bundled ``core/format.json``, so a codegen gap
    (a type the schema declares and the generator did not model, or the reverse)
    fails here.
    """
    assert ASSET_TYPES == PINNED_ASSET_TYPES


def test_group_factory_vocabulary_is_the_pinned_group_union() -> None:
    """The same comparison for the in-group union, derived from its own arm."""
    assert GROUP_ASSET_TYPES == PINNED_GROUP_ASSET_TYPES

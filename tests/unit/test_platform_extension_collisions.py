"""The ``platform_extensions[]`` collision rule, graded against the pin's own words.

``formats/canonical/_base.json``'s ``platform_extensions`` description states it (3.2.3):

    **Collision precedence (normative).** When two or more ``platform_extensions[]``
    entries on the same declaration extend the same target (e.g., both extend
    ``tracking``) with overlapping field names, **array order is authoritative -- later
    entries override earlier ones on a per-field basis** (last-in-array-wins).

Three properties of that sentence are what these tests hold, and each of them is a way an
implementation can look right and be wrong:

* **per-field**, not per-extension -- two partially-overlapping extensions can each win a
  different field, so a single winner per target is the wrong model;
* **array order**, so reversing the array must move the winner. That is the probe this
  file is built around: removing an extension exercises the no-collision branch and would
  pass against an implementation that resolves precedence backwards;
* **the same target**, which lives in the extension's DEFINITION (``extends``) and not in
  the reference. A reference carries ``uri`` + ``digest`` only, so a collision is not
  decidable from the declaration alone.
"""

from src.core.format_resolver import platform_extension_collisions

AGENT = "https://creative.adcontextprotocol.org/translated/meta/extensions"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _ref(name: str, digest: str = DIGEST_A) -> dict[str, str]:
    return {"uri": f"{AGENT}/{name}", "digest": digest}


def _definition(extends: str, *fields: str) -> dict[str, object]:
    """An extension definition in the shape the pinned bundled ``extensions`` map carries.

    ``get-products-response.json``'s ``extensions`` is keyed ``<uri>@<digest>`` and each
    value requires ``extends`` (the canonical concept) and ``fields`` (a JSON Schema
    fragment declaring the additional fields). The field NAMES are the fragment's
    ``properties`` keys.
    """
    return {"extends": extends, "fields": {"properties": {f: {"type": "string"} for f in fields}}}


def _key(ref: dict[str, str]) -> str:
    return f"{ref['uri']}@{ref['digest']}"


def test_extensions_extending_different_targets_do_not_collide() -> None:
    pixel, cta = _ref("meta_pixel"), _ref("meta_cta")
    collisions = platform_extension_collisions(
        [pixel, cta],
        {_key(pixel): _definition("tracking", "pixel_id"), _key(cta): _definition("cta_vocabulary", "pixel_id")},
    )
    assert collisions == [], "the same field name under two different targets is not an overlap"


def test_the_later_entry_wins_the_overlapping_field() -> None:
    first, second = _ref("meta_pixel"), _ref("meta_pixel_v2")
    collisions = platform_extension_collisions(
        [first, second],
        {_key(first): _definition("tracking", "pixel_id"), _key(second): _definition("tracking", "pixel_id")},
    )
    assert len(collisions) == 1
    assert collisions[0].target == "tracking"
    assert collisions[0].overlapping_fields == ["pixel_id"]
    assert collisions[0].winning_extension_uri == second["uri"]


def test_reversing_the_array_moves_the_winner() -> None:
    """The probe, as a test: array order is the subject, so inverting it must invert the answer.

    An implementation resolving precedence FIRST-in-array-wins passes
    :func:`test_the_later_entry_wins_the_overlapping_field` if its author wrote the fixture
    to match, and fails here. An implementation that ignores order entirely fails both.
    """
    first, second = _ref("meta_pixel"), _ref("meta_pixel_v2")
    definitions = {
        _key(first): _definition("tracking", "pixel_id"),
        _key(second): _definition("tracking", "pixel_id"),
    }
    forward = platform_extension_collisions([first, second], definitions)
    reversed_ = platform_extension_collisions([second, first], definitions)
    assert forward[0].winning_extension_uri == second["uri"]
    assert reversed_[0].winning_extension_uri == first["uri"]
    assert forward[0].overlapping_fields == reversed_[0].overlapping_fields, (
        "the same fields overlap either way; only the winner moves"
    )


def test_per_field_winners_differ_within_one_target() -> None:
    """Partial overlap: the rule is per-field, so one target can have two winners."""
    a, b, c = _ref("ext_a"), _ref("ext_b"), _ref("ext_c")
    collisions = platform_extension_collisions(
        [a, b, c],
        {
            _key(a): _definition("tracking", "x", "y"),
            _key(b): _definition("tracking", "y", "z"),
            _key(c): _definition("tracking", "x"),
        },
    )
    won = {collision.winning_extension_uri: collision.overlapping_fields for collision in collisions}
    assert won == {b["uri"]: ["y"], c["uri"]: ["x"]}, (
        "y is declared by a and b so b wins it; x is declared by a and c so c wins it; "
        "z is declared once and does not overlap"
    )


def test_an_identical_uri_and_digest_is_a_redundancy_rather_than_a_divergence() -> None:
    """A repeated ``uri@digest`` cannot diverge, and the digest is what proves it.

    Equal digests mean the same document, so every overlapping field holds the same value
    and last-in-array-wins selects a value identical to the one it displaced. There is
    nothing for a buyer to observe, so emitting a divergence advisory here would report a
    conflict that does not exist.
    """
    ref = _ref("meta_pixel", DIGEST_A)
    collisions = platform_extension_collisions([ref, ref], {_key(ref): _definition("tracking", "pixel_id")})
    assert collisions == []


def test_the_same_uri_at_two_digests_does_collide() -> None:
    """Same extension, two versions: the documents differ, so the overlap is real."""
    old, new = _ref("meta_pixel", DIGEST_A), _ref("meta_pixel", DIGEST_B)
    collisions = platform_extension_collisions(
        [old, new],
        {_key(old): _definition("tracking", "pixel_id"), _key(new): _definition("tracking", "pixel_id")},
    )
    assert len(collisions) == 1
    assert collisions[0].winning_extension_uri == new["uri"]


def test_a_reference_with_no_definition_is_not_asserted_to_collide() -> None:
    """Undecidable is not the same as absent, and neither is a collision.

    ``target`` and the field names live in the definition. With no definition for a
    reference this seller cannot say which target it extends, so it cannot say the
    reference overlaps anything -- and must not guess, because the advisory's whole content
    would be invented. This is the state the seller is in TODAY for every reference: the
    bundled ``extensions`` map is populated by nothing (salesagent-hff54's deferred half).
    """
    known, unknown = _ref("meta_pixel"), _ref("mystery")
    collisions = platform_extension_collisions([known, unknown], {_key(known): _definition("tracking", "pixel_id")})
    assert collisions == []


def test_no_extensions_and_no_definitions_are_both_quiet() -> None:
    assert platform_extension_collisions(None, None) == []
    assert platform_extension_collisions([], {}) == []
    assert platform_extension_collisions([_ref("meta_pixel")], None) == []

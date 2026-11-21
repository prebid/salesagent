"""A creative's format kind is never absent; what a PACKAGE names may be.

The user's ruling: *"it is always possible to derive the kind. If it is not possible,
something is wrong with the premise, not with the rule."* So
``CreativeFormatIdentity.kind`` is ``str``, and the kindless state that this epic was about
is unrepresentable rather than guarded. ``FormatSelector`` is the other population --
``media-buy/package-request.json`` gives a package three ways to name a format and two of
them legitimately carry no kind, which is the state ``satisfies`` must be able to hold long
enough to report ``LEGACY_UNPROJECTABLE``.

THESE TESTS EXIST BECAUSE THE SUITE WAS BLIND TO THE CHANGE. Narrowing the field broke
nothing: 6840 passed / 5 failed, and the 5 are an unrelated pre-existing pair. Fire-probed
by widening it back to ``| None`` -- also 6840 / 5, byte-identical. So "no breakage" was not
evidence the narrowing was right, it was evidence nothing graded it, and the narrowing would
otherwise have shipped on a green that could not have gone red. Each test below names the
obligation it carries rather than the implementation it touches.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.format_resolver import creative_identity_of
from src.core.schemas import CreativeFormatIdentity, FormatId, FormatSelector
from src.core.schemas.creative import CreativeAssetRequest

AGENT = "https://creative.adcontextprotocol.org"


def _creative(**arm: object) -> CreativeAssetRequest:
    return CreativeAssetRequest(creative_id="c-1", name="c", assets={}, **arm)  # type: ignore[arg-type]


class TestTheIllegalStateIsUnrepresentable:
    def test_a_creative_identity_cannot_be_built_without_a_kind(self) -> None:
        """The whole point: not "guarded against", but unconstructable.

        A guard can be routed around by a new caller; a mandatory field cannot. This is the
        assertion the suite did not have, and without it the narrowing was invisible.
        """
        with pytest.raises(ValidationError):
            CreativeFormatIdentity()  # type: ignore[call-arg]

    def test_a_creative_identity_cannot_be_built_with_kind_none(self) -> None:
        """Explicitly passing ``None`` is refused too, not coerced to a default."""
        with pytest.raises(ValidationError):
            CreativeFormatIdentity(kind=None)  # type: ignore[arg-type]

    def test_an_out_of_vocabulary_kind_is_refused_on_construction(self) -> None:
        """The kind is a CLOSED 16-member pinned enum, and the type is where that is enforced.

        Worth its own row because the vocabulary check MOVED in this change: it used to
        arrive free from this construction inside ``_validation``, which built an identity it
        had no projection for. The check at its new site is graded below; this grades that
        the type still refuses the value on its own.
        """
        with pytest.raises(ValidationError):
            CreativeFormatIdentity(kind="not_a_kind")


class TestTheSelectorIsTheOtherPopulation:
    def test_a_selector_may_name_no_kind_at_all(self) -> None:
        """An ``format_option_refs`` route has an option and no kind until it resolves."""
        assert FormatSelector().kind is None

    def test_a_selector_holds_an_unprojectable_legacy_reference(self) -> None:
        """The ``LEGACY_UNPROJECTABLE`` state, which ``satisfies`` reports to the buyer.

        This is why the split exists rather than the field simply being narrowed everywhere:
        a package naming a legacy format that projects to nothing is a GRADED product-side
        outcome, and reporting it requires being able to hold the unprojected state.
        """
        selector = FormatSelector.of_legacy(FormatId(agent_url=AGENT, id="product_card_standard"))

        assert selector.kind is None
        assert selector.assertion is not None

    def test_a_creative_identity_converts_to_a_selector_carrying_every_member(self) -> None:
        """One direction only, and it must not lose anything on the way.

        Asserted member by member rather than by equality of some projection: a conversion
        that dropped ``legacy_parameters`` would still compare equal on the members a
        careless test looked at, and the parameters are part of WHICH format a reference
        names -- a template pinned to 300x250 is not the one pinned to 728x90.
        """
        identity = CreativeFormatIdentity.of_legacy(
            FormatId(agent_url=AGENT, id="display_300x250", width=300, height=250), kind="image"
        )

        selector = identity.as_selector()

        assert (selector.kind, selector.legacy, selector.legacy_parameters, selector.assertion) == (
            identity.kind,
            identity.legacy,
            identity.legacy_parameters,
            identity.assertion,
        )


class TestTheArmExclusivityIsTheDTOsJob:
    """The second of the three mechanisms, graded because it was measured and not asserted.

    A kindless creative identity is kept out by three things: this arm exclusivity, the
    creation gate (below), and the ``NOT NULL`` column -- which nothing asserts, because a
    migration is not a thing to test: apply it and observe it works, revert it and observe
    the previous state. When
    ``_validation`` stopped borrowing ``CreativeFormatIdentity``'s construction validator,
    the arm check was the half that stayed behind on the DTO -- I measured that it still
    held and then shipped without an assertion, which is how a mechanism nobody grades
    survives until it does not.
    """

    def test_both_arms_at_once_is_refused(self) -> None:
        """``core/creative-asset.json`` is a oneOf on the identifier, not an anyOf.

        It matters to the kind specifically: a creative carrying both arms has two possible
        kinds -- the declared one and whatever the reference projects to -- and nothing
        downstream would know which is the identity.
        """
        with pytest.raises(ValidationError):
            _creative(format_kind="image", format_id={"agent_url": AGENT, "id": "display_300x250"})

    def test_neither_arm_is_refused(self) -> None:
        """No arm means no kind is derivable, which the creation edge could not raise about
        in a way the buyer could act on -- so the DTO refuses it by field instead."""
        with pytest.raises(ValidationError):
            _creative()


class TestTheCreationEdgeDerivesOrRaises:
    def test_the_canonical_arm_needs_no_projection(self) -> None:
        """A creative that carries its kind is read, not derived -- and no spec is consulted."""
        identity = creative_identity_of(_creative(format_kind="image"), spec=None)

        assert identity.kind == "image"

    def test_a_projectable_legacy_reference_derives_its_kind(self) -> None:
        """The registry glob, which is the route available with no fetched declaration."""
        identity = creative_identity_of(_creative(format_id={"agent_url": AGENT, "id": "display_300x250"}), spec=None)

        assert identity.kind == "image"

    def test_an_unprojectable_legacy_reference_raises_unsupported_feature(self) -> None:
        """ "If it cannot be derived ... it just raises" -- and the code is what the pin says.

        ``manifest.json`` / ``enums/error-code.json``: "An unresolved option reference or an
        unprojectable legacy ID is rejected with UNSUPPORTED_FEATURE." The code and its
        recovery are asserted, not merely the raise: a bare ``ValueError`` would surface to
        the buyer as INTERNAL_ERROR -- a server fault where they need a correctable refusal
        naming what to resend.
        """
        with pytest.raises(AdCPCapabilityNotSupportedError) as caught:
            creative_identity_of(_creative(format_id={"agent_url": AGENT, "id": "product_card_standard"}), spec=None)

        assert caught.value.error_code == "UNSUPPORTED_FEATURE"
        assert caught.value.recovery == "correctable"

    def test_the_raise_carries_the_id_the_buyer_sent(self) -> None:
        """A refusal the buyer cannot act on is the silence this epic removed, in error form."""
        with pytest.raises(AdCPCapabilityNotSupportedError) as caught:
            creative_identity_of(_creative(format_id={"agent_url": AGENT, "id": "product_card_standard"}), spec=None)

        assert caught.value.details is not None
        assert caught.value.details.format_id == "product_card_standard"

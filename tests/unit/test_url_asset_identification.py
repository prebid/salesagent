"""The pin's url-asset ladder: what a manifest's URL assets are FOR, and when to refuse.

``core/assets/url-asset.json`` states the receiver's rule normatively, and the whole
sentence matters because the last clause is the remedy:

    "Senders SHOULD include ``url_type`` on every URL asset. When ``url_type`` is absent,
     receivers SHOULD fall back to the format's ``url-asset-requirements.role`` ... When
     neither ``url_type`` nor a format-side ``role`` is available, receivers MUST NOT
     silently pick a mechanism; they SHOULD reject the manifest."

Before this, ``extract_click_url`` matched a hardcoded six-member list of guessed asset-id
names. Measured against ``core/asset-group-vocabulary.json``, the pin's normative registry
of ``asset_group_id`` values, that list overlapped the registry in two of six members and
did not contain ``landing_page_url`` -- the id fourteen of the sixteen canonical kinds
declare for their click destination. So every canonical creative's clickthrough was
dropped, and silently: no response field carries it, so the buy succeeded and the line item
shipped unclickable.

The distinction these tests exist to hold is IDENTIFIED versus INVOKABLE. The registry
defines three url-typed entries, not one -- ``landing_page_url``, ``privacy_policy_url`` and
``subtitle_file`` -- and a format-side ``third_party_tracker`` role is declared but has "no
safe fallback". Each of those is an asset the pin SAYS something about while giving the
seller no mechanism to invoke, so refusing them would reject correct manifests. Only an
asset no rung speaks for at all is refused.

Pure functions over the pinned wheel, so: unit. The buyer-visible outcome -- the click
destination reaching the ad server, and the refusal when none can be identified -- is
graded by BR-UC-002's canonical-clickthrough scenarios on every transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.helpers.creative_helpers import (
    _GROUP_ID_ROLE_OVERRIDES,
    _SLOT_ROLE_TO_MECHANISM,
    _asset_group_vocabulary,
    extract_click_url,
    extract_impression_tracker_url,
    unidentified_url_asset_reason,
    url_asset_identification,
)
from tests.factories.format import _ASSET_CLASS_MAP, FormatFactory, make_asset

CLICK_URL = "https://advertiser.example.com/canonical-landing"
TRACKER_URL = "https://measure.example.com/imp.gif"

#: An ``asset_group_id`` the registry does not define. The registry explicitly permits one
#: ("Non-canonical IDs remain valid for platform-specific extensions"), so a manifest using
#: it is legitimate rather than malformed — which is what makes it a fair test of what the
#: seller does when it has been told a URL exists and NOT told what it is for.
UNNAMED = "mystery"


def _creative(asset_id: str, **fields: Any) -> dict[str, Any]:
    """One manifest carrying a single url asset, plus the fields under test."""
    return {"assets": {asset_id: {"asset_type": "url", "url": CLICK_URL, **fields}}}


def _spec_declaring(asset_id: str, role: str) -> Any:
    return FormatFactory(
        assets=[_ASSET_CLASS_MAP["url"](asset_id=asset_id, required=True, requirements={"role": role})]
    )


class TestTheLadderResolvesAClickDestination:
    def test_the_assets_own_url_type_is_the_first_rung(self) -> None:
        """ "Senders SHOULD include ``url_type`` on every URL asset" — so it wins outright.

        Keyed :data:`UNNAMED`, which no registry entry defines, so ONLY this rung can
        answer. A fixture offering two signals at once would grade neither.
        """
        creative = _creative(UNNAMED, url_type="clickthrough")

        assert extract_click_url(creative, None, apply_macro_substitution=False) == CLICK_URL

    @pytest.mark.parametrize("asset_id", ["landing_page_url", "click_url", "landing_url", "final_url", "link"])
    def test_the_canonical_asset_group_id_and_its_aliases_resolve(self, asset_id: str) -> None:
        """The registry's ``landing_page_url`` entry and the six aliases it declares.

        Read off the pin rather than retyped: the entry's own note says "Six different field
        names exist across platform format definitions today", and the list this replaced
        carried two of them. ``landing_page_url`` itself -- what every canonical kind
        declares -- was in no list at all, which is the bug.

        No ``url_type`` and no format spec here, so the ``asset_group_id`` is the only
        signal: this is the pin's second rung for a canonical manifest, where the slot
        carries no ``role`` property of its own.
        """
        creative = _creative(asset_id)

        assert extract_click_url(creative, None, apply_macro_substitution=False) == CLICK_URL

    def test_a_url_the_manifest_never_identifies_is_not_guessed_at(self) -> None:
        """The MUST NOT, as a value: a perfectly good URL is not made the destination.

        THE CONTROL for everything above. Each positive case is also satisfied by a reader
        that simply returns the first url asset it finds; this one is not, and it is the
        only thing separating a registry lookup from a re-invented guess list.
        """
        creative = _creative(UNNAMED)

        assert extract_click_url(creative, None, apply_macro_substitution=False) is None

    def test_a_registry_role_this_seller_does_not_invoke_is_not_the_destination(self) -> None:
        """``privacy_policy_url`` is url-typed and real, and it is not a click destination.

        A reader that knew only the click-destination entry would treat every other
        registry name as unidentified; one that resolved any url asset would ship the
        privacy policy as the landing page.
        """
        creative = _creative("privacy_policy_url")

        assert extract_click_url(creative, None, apply_macro_substitution=False) is None


class TestWhatIsIdentifiedIsWiderThanWhatIsInvokable:
    def test_every_name_the_registry_defines_is_identified(self) -> None:
        """THE POPULATION, not a sample: all 55 canonical names and declared aliases.

        A per-name sample cannot say that the lookup consults the whole registry rather than
        the handful of entries whoever wrote it had in mind — and that was the first version
        of this change, which read only the click-destination entry and would therefore have
        REFUSED every manifest carrying a ``privacy_policy_url``.

        Driven off the registry itself, so a pin that adds an entry extends this assertion
        instead of leaving a hole in it.
        """
        refused = {
            asset_id for asset_id in _asset_group_vocabulary() if url_asset_identification(_creative(asset_id), None)[1]
        }

        assert refused == set()

    def test_the_registry_to_role_mapping_covers_the_registry_as_it_is_pinned(self) -> None:
        """Every registry name is a role this seller knows, or deliberately invokes nothing.

        The mapping rests on a coincidence of vocabulary that is real but not guaranteed:
        the registry's tracker entries are named with the SAME words as
        ``url-asset-requirements.role``'s enum, so an ``asset_group_id`` lands straight in
        the role table, and ``landing_page_url`` is the single entry needing a translation.

        If a pin renames an entry — ``impression_tracker`` to ``impression_url``, say — the
        coincidence breaks and the tracker silently stops resolving, with every other test
        here still green because they all go through the same broken lookup. This asserts
        the overlap itself: exactly these registry names map to a mechanism, and the
        override table contains exactly the entries that need one.
        """
        vocabulary = _asset_group_vocabulary()
        invokable = {
            canonical
            for canonical in set(vocabulary.values())
            if _SLOT_ROLE_TO_MECHANISM.get(_GROUP_ID_ROLE_OVERRIDES.get(canonical, canonical))
        }

        assert invokable == {"landing_page_url", "impression_tracker", "click_tracker", "viewability_tracker"}
        assert set(_GROUP_ID_ROLE_OVERRIDES) <= set(vocabulary), "an override naming no registry entry is dead"

    def test_a_third_party_tracker_role_is_identified_and_still_not_invoked(self) -> None:
        """ "no safe fallback (mechanism is integration-specific ... MAY reject or warn)".

        The role IS declared, so the seller knows what the URL is for and the manifest is
        not refused; no mechanism follows, so nothing fires it. Collapsing those two facts
        is how a correct manifest gets rejected.
        """
        creative = _creative(UNNAMED)
        spec = _spec_declaring(UNNAMED, "third_party_tracker")

        mechanisms, unidentified = url_asset_identification(creative, spec)

        assert (mechanisms, unidentified) == ({}, [])

    def test_a_media_asset_is_not_a_url_asset(self) -> None:
        """An image carries a ``url`` too, and is not subject to the url-asset rule."""
        creative = {"assets": {"image_main": {"asset_type": "image", "url": CLICK_URL, "width": 1, "height": 1}}}

        assert url_asset_identification(creative, FormatFactory(assets=[make_asset("image", "image_main")])) == (
            {},
            [],
        )

    def test_an_asset_declaring_no_type_at_all_is_left_alone(self) -> None:
        """This seller's own asset validator does not require ``asset_type``.

        So a stored legacy creative can carry a url under a name nothing defines and no
        declared type. Refusing it would be the mirror of the bug: a creative that works
        today stops, for a field the seller never required. The historically-honoured
        url-asset names are the deliberate exception, graded below.
        """
        creative = {"assets": {"banner_image": {"url": CLICK_URL}}}

        assert url_asset_identification(creative, None) == ({}, [])


class TestTrackersResolveThroughTheSameRegistry:
    @pytest.mark.parametrize("asset_id", ["impression_tracker", "impression_pixel", "imp_tracker", "imp_url"])
    def test_the_registrys_impression_tracker_entry_and_its_aliases_fire_as_a_pixel(self, asset_id: str) -> None:
        """The registry entry the old four-name guess list was approximating.

        ``impression_tracker``: "Renderer-fired impression tracker URL", with the aliases
        ``imp_tracker``, ``impression_pixel`` and ``imp_url``. Two of the guess list's four
        members were this entry or an alias; the other two (``pixel``, ``tracker_pixel``)
        name nothing in the registry and now refuse.
        """
        creative = {"assets": {asset_id: {"asset_type": "url", "url": TRACKER_URL}}}

        assert extract_impression_tracker_url(creative, None) == TRACKER_URL

    def test_a_viewability_tracker_is_not_fired_as_a_pixel(self) -> None:
        """``viewability_tracker -> tracker_script``, which the impression reader must skip.

        The pin's parenthetical is the reason: "OMID and equivalent verification SDKs
        require a <script> tag — firing them as a pixel produces no measurement". The old
        guess list had no way to express this: every name in it meant one mechanism.
        """
        creative = {"assets": {"viewability_tracker": {"asset_type": "url", "url": TRACKER_URL}}}

        mechanisms, unidentified = url_asset_identification(creative, None)

        assert (mechanisms, unidentified) == ({"viewability_tracker": "tracker_script"}, [])
        assert extract_impression_tracker_url(creative, None) is None


class TestTheSentenceSaysWhatIsMissing:
    """The seller NAMES what it could not identify; it does not refuse the buy.

    ``core/assets/url-asset.json`` at the governing version ends "...receivers MUST NOT
    silently pick a mechanism." At 3.1.1 it continued "; they SHOULD reject the manifest",
    and that clause was REMOVED by 3.2.3 — a version declining to require the remedy. So
    the prohibition on guessing is graded above (nothing resolves from an unidentified
    asset) and the remedy here is a sentence, not a rejection.

    What the seller still owes is that nobody loses a clickthrough SILENTLY. These grade the
    sentence. That it does not yet reach the BUYER is a filed gap: the advisory lane needs a
    declared ``errors[]``, ``CreateMediaBuySuccess`` has none, and the ``warnings[]`` array
    that is the right carrier draws its ``code`` from a closed three-member enum with no
    member meaning this.
    """

    def test_an_unidentified_url_asset_produces_a_sentence_naming_it(self) -> None:
        """Four things in it, because a reader told only "no clickthrough" cannot act.

        The asset id, the missing signal, the canonical name to use instead, and which
        creative it was. A bare "something was dropped" would be the silence again, wearing
        a log level.
        """
        reason = unidentified_url_asset_reason(_creative(UNNAMED), None, "creative_0001")

        assert reason is not None
        assert UNNAMED in reason
        assert "url_type" in reason
        assert "landing_page_url" in reason
        assert "creative_0001" in reason

    def test_an_identified_manifest_produces_no_sentence(self) -> None:
        assert unidentified_url_asset_reason(_creative("landing_page_url"), None, "creative_0001") is None

    @pytest.mark.parametrize(
        "asset_id", ["click", "clickthrough", "destination_url", "landing_page", "pixel", "tracker_pixel"]
    )
    def test_a_name_this_seller_once_honoured_is_named_rather_than_dropped(self, asset_id: str) -> None:
        """The six members of the two old guess lists that appear in no registry entry.

        Acting on them is the mechanism guess the pin forbids. Simply ceasing to act on
        them would mean a creative that resolves a clickthrough today stops doing so with
        nobody told — the same silence, arrived at from the other side. So the sentence says
        the name was honoured before and no longer is.

        Note these have NO ``asset_type`` here: that is the shape a pre-canonical stored
        creative has, and it is why they are named explicitly rather than covered by the
        url-asset test above.
        """
        creative = {"assets": {asset_id: {"url": CLICK_URL}}}

        reason = unidentified_url_asset_reason(creative, None, "creative_0001")

        assert reason is not None
        assert asset_id in reason
        assert "no longer acted on" in reason

    def test_such_a_name_still_resolves_when_the_manifest_declares_its_mechanism(self) -> None:
        """Deprecated name plus ``url_type``: the first rung answers and nothing is said.

        The control that keeps the six from being a blanket ban. The pin's objection is to
        picking a mechanism nobody declared, not to the name.
        """
        creative = {"assets": {"landing_page": {"url": CLICK_URL, "url_type": "clickthrough"}}}

        assert extract_click_url(creative, None, apply_macro_substitution=False) == CLICK_URL
        assert unidentified_url_asset_reason(creative, None, "creative_0001") is None

    def test_a_format_declared_role_also_rescues_a_deprecated_name(self) -> None:
        """Same, through the second rung, for a legacy creative whose format declares it."""
        creative = {"assets": {"landing_page": {"asset_type": "url", "url": CLICK_URL}}}
        spec = _spec_declaring("landing_page", "clickthrough")

        assert extract_click_url(creative, spec, apply_macro_substitution=False) == CLICK_URL
        assert unidentified_url_asset_reason(creative, spec, "creative_0001") is None

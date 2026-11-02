"""Cross-tool media-buy status mapping consistency.

get_media_buy_delivery and get_media_buys both derive their wire status from
the persisted ``MediaBuy.status`` column. The spec requires the two required
tools to describe the same buy with the same lifecycle status
(enums/media-buy-status.json). Both now delegate to the single shared resolver
``resolve_canonical_status`` (CLAUDE.md DRY invariant), so this module pins the
resolver's behavior AND the two tools' thin adaptations of it — including the
divergence points (delivery-only "failed", the legacy/unknown fallback) that a
dict-only comparison would miss.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from adcp.types import MediaBuyStatus

from src.core.tools._media_buy_status import (
    CANONICAL_STATUSES,
    NO_MORE_DATA_STATUSES,
    PERSISTED_STATUS_TO_CANONICAL,
    TERMINAL_STATUSES,
    resolve_canonical_status,
)
from src.core.tools.media_buy_list import _compute_status

# A reference date inside the default flight window below, so a generic serving
# status resolves to "active" (not date-refined away).
_REF = date(2025, 6, 1)


def _buy(
    status: str,
    *,
    start: date = date(2025, 1, 1),
    end: date = date(2025, 12, 31),
    is_paused: bool = False,
) -> SimpleNamespace:
    """A minimal buy-like row (matches the MediaBuy / _MediaBuyData surface the resolver reads)."""
    return SimpleNamespace(
        status=status,
        start_date=start,
        end_date=end,
        start_time=None,
        end_time=None,
        is_paused=is_paused,
    )


class TestCrossToolStatusMappingConsistency:
    """get_media_buy_delivery and get_media_buys report the same status for the same buy.

    Regression: the two tools kept mirrored persisted-status maps that drifted
    (they disagreed on "draft"; delivery dropped unmapped legacy rows while list
    showed them). They now share one resolver. Delivery emits the canonical
    string; list adapts only the delivery-only "failed" (no lifecycle
    equivalent) to "rejected".

    Spec: enums/media-buy-status.json (lifecycle vocabulary);
    get-media-buy-delivery-response.json (delivery status enum).
    """

    def test_both_tools_agree_for_every_persisted_status(self):
        """For every mapped persisted status the two tools' wire values agree.

        The only sanctioned divergence: delivery may report "failed"; the
        lifecycle enum has none, so get_media_buys reports "rejected".
        """
        for persisted in PERSISTED_STATUS_TO_CANONICAL:
            buy = _buy(persisted)
            delivery_status = resolve_canonical_status(buy, _REF)
            list_status = _compute_status(buy, _REF).value

            assert delivery_status in CANONICAL_STATUSES, persisted
            if delivery_status == "failed":
                assert list_status == "rejected", persisted
            else:
                assert list_status == delivery_status, (
                    f"persisted {persisted!r}: delivery -> {delivery_status!r}, list -> {list_status!r}"
                )

    def test_failed_is_reportable_on_delivery_only(self):
        """ "failed" is a real delivery status but collapses to "rejected" on the lifecycle surface."""
        buy = _buy("failed")
        assert resolve_canonical_status(buy, _REF) == "failed"
        assert _compute_status(buy, _REF) is MediaBuyStatus.rejected

    def test_draft_is_pending_creatives(self):
        """A lingering draft buy has no creatives assigned — exactly pending_creatives."""
        buy = _buy("draft")
        assert resolve_canonical_status(buy, _REF) == "pending_creatives"
        assert _compute_status(buy, _REF) is MediaBuyStatus.pending_creatives


class TestCanonicalVocabularyPinnedToSdk:
    """CANONICAL_STATUSES stays derived and equivalent to the SDK lifecycle enum.

    The delivery tool uses CANONICAL_STATUSES as its valid internal-filter set,
    so if it ever diverges from what the resolver can return — or from the pinned
    SDK MediaBuyStatus enum — a real status becomes unfilterable and fetch-by-ID
    silently drops buys. Both relationships are pinned here so an SDK bump that
    widens the lifecycle enum fails loudly instead of drifting.
    """

    def test_canonical_statuses_is_derived_from_the_map(self):
        assert CANONICAL_STATUSES == frozenset(PERSISTED_STATUS_TO_CANONICAL.values())

    def test_canonical_statuses_matches_sdk_lifecycle_enum_plus_failed(self):
        # The lifecycle enum has no "failed"; delivery adds it as a delivery-only
        # terminal. Every other canonical value must be an SDK lifecycle value.
        assert CANONICAL_STATUSES == {s.value for s in MediaBuyStatus} | {"failed"}

    def test_terminal_statuses_membership_is_pinned(self):
        """Pin the exact TERMINAL_STATUSES set, not just its derived behavior.

        The reporting_delayed override and the terminal date-refinement skip both
        key off this set; a silent widen/narrow (e.g. an SDK bump moving a status
        in or out of terminal) would change buyer-visible status without a failing
        test unless the membership itself is pinned.
        """
        assert TERMINAL_STATUSES == {"paused", "completed", "rejected", "canceled", "failed"}

    def test_no_more_data_statuses_is_terminal_minus_paused(self):
        """Pin NO_MORE_DATA_STATUSES (drives notification_type=final / next_expected_at).

        Derived as TERMINAL_STATUSES - {paused}: a paused buy may resume and report
        again, so it is NOT a no-more-data state. Pin both the derivation and the
        resulting membership so neither can drift silently.
        """
        assert NO_MORE_DATA_STATUSES == TERMINAL_STATUSES - {"paused"}
        assert NO_MORE_DATA_STATUSES == {"completed", "rejected", "canceled", "failed"}


class TestEveryVocabularyMemberHasAProjectionRow:
    """No persisted value may exist without a wire projection.

    This is the surviving half of ``TestAdcpProjectionAgreesWithCanonicalMap``,
    which pinned ``_PERSISTED_STATUS_TO_ADCP`` — a SECOND persisted->wire
    projection in media_buy_list.py, derived from this one and consumed by nobody.
    It was deleted with the map it pinned (L5/L1 of the #1941 remediation); what
    could not be deleted with it is the obligation below, which was only ever
    incidental to that oracle.
    """

    def test_vocabulary_and_canonical_map_describe_the_same_set(self):
        """The vocabulary type and the projection map are one set.

        The vocabulary is a TYPE (``PersistedMediaBuyStatus``) and a type gives no
        exhaustiveness guarantee over a dict — mypy checks that only for ``match`` +
        ``assert_never``. So a member added to the enum with no projection row is
        invisible to the type checker and must fail loudly here instead. Without
        this, the read path's ``PERSISTED_STATUS_TO_CANONICAL[...]`` index would be
        the thing that discovers it, at runtime, on a buyer's request.

        Imported inside the test on purpose: this is the only assertion in the
        module that depends on the vocabulary type, and a module-level import would
        red every unrelated test here while the type is absent.
        """
        from src.core.database.models import PersistedMediaBuyStatus

        assert set(PersistedMediaBuyStatus) == set(PERSISTED_STATUS_TO_CANONICAL), (
            "every persisted status must have a wire projection row: "
            f"missing {set(PersistedMediaBuyStatus) - set(PERSISTED_STATUS_TO_CANONICAL)}, "
            f"extra {set(PERSISTED_STATUS_TO_CANONICAL) - set(PersistedMediaBuyStatus)}"
        )


class TestLegacyAndUnknownStatusesNotDropped:
    """Legacy persisted values and unknown statuses resolve to a valid status, never dropped.

    Regression (delivery): the old delivery resolver passed an unmapped
    persisted value through verbatim, so a legacy "ready" row (written by
    PR #375) or an admin "scheduled" row failed the internal-status filter and
    made even fetch-by-ID report MEDIA_BUY_NOT_FOUND for a buy that exists —
    while get_media_buys mapped the same row to a valid status.

    Those aliases are vocabulary members, so both tools map and date-refine them
    and they always land in CANONICAL_STATUSES (== the delivery fetch-by-ID filter
    set). That is the regression this class exists for and it is unchanged.

    What changed: a value OUTSIDE the vocabulary is no longer absorbed. It used to
    be treated as a generic serving state, which is how an undefined status could
    reach the buyer as "active" with no commitment instant — a document the pinned
    schema forbids. It is now refused where it would enter (see
    ``MediaBuyRepository.update_status``), so the resolver indexes the map instead
    of guessing.
    """

    def test_legacy_aliases_resolve_to_valid_status(self):
        """The legacy aliases the regression was about are mapped, not dropped."""
        for persisted in ("ready", "scheduled"):
            buy = _buy(persisted)  # inside the flight window
            delivery_status = resolve_canonical_status(buy, _REF)

            # Never a raw passthrough — must be in the delivery filter set so
            # fetch-by-ID cannot drop it.
            assert delivery_status in CANONICAL_STATUSES, persisted
            assert delivery_status == "active", persisted
            # And the two tools still agree.
            assert _compute_status(buy, _REF).value == delivery_status, persisted

    def test_every_status_the_column_can_hold_resolves_into_the_delivery_filter_set(self):
        """Each vocabulary member resolves to a value the delivery filter accepts.

        This is the surviving half of an assertion that used to feed the resolver
        an invented status and demand it resolve to "active". That absorption was
        the mechanism by which a state nobody defined reached the buyer as a
        serving buy carrying no ``confirmed_at`` — invalid against the pinned
        response schema — so the invented value is now refused where it would
        enter, and only its ABSENCE from the vocabulary is checked here.

        The refusal itself is a persistence behaviour and is graded against a real
        write, not from this module: ``tests/integration/
        test_media_buy_revision_confirmation.py::
        test_an_unrecognised_status_is_refused_at_the_write_boundary``.

        What remains here is the original obligation — "an unmapped value is never
        dropped by the delivery filter" — narrowed to the values that can actually
        occur. That narrowing only holds while the set of occurring values IS the
        vocabulary, which is why the sentinel's absence is asserted alongside the
        sweep.
        """
        from src.core.database.models import PersistedMediaBuyStatus

        assert "totally_unknown_legacy" not in set(PersistedMediaBuyStatus)
        for member in PersistedMediaBuyStatus:
            assert resolve_canonical_status(_buy(member.value), _REF) in CANONICAL_STATUSES, member

    def test_legacy_ready_alias_date_refines_like_active(self):
        """A legacy "ready"/"scheduled" buy follows the flight window like any serving buy."""
        before = _buy("ready", start=date(2025, 9, 1), end=date(2025, 12, 31))
        within = _buy("scheduled", start=date(2025, 1, 1), end=date(2025, 12, 31))
        after = _buy("ready", start=date(2025, 1, 1), end=date(2025, 3, 31))

        assert resolve_canonical_status(before, _REF) == "pending_start"
        assert resolve_canonical_status(within, _REF) == "active"
        assert resolve_canonical_status(after, _REF) == "completed"

    def test_legacy_pending_activation_maps_to_pending_start_not_serving(self):
        """pending_activation is scheduler-held until creatives approve, like pending_start.

        The scheduler (media_buy_status_scheduler.py) promotes pending_activation
        to active only after creative approval — identical to pending_start. Date-
        refining it to the serving state made a past-start buy with unapproved
        creatives read as "active". It maps to pending_start regardless of the
        flight window; both tools agree.
        """
        for window in (
            {"start": date(2025, 9, 1), "end": date(2025, 12, 31)},  # before flight
            {"start": date(2025, 1, 1), "end": date(2025, 12, 31)},  # mid-flight
            {"start": date(2025, 1, 1), "end": date(2025, 3, 31)},  # past flight
        ):
            buy = _buy("pending_activation", start=window["start"], end=window["end"])
            assert resolve_canonical_status(buy, _REF) == "pending_start", window
            assert _compute_status(buy, _REF).value == "pending_start", window


class TestTheFlightWindowNeverOverridesAnExplicitDecision:
    """Only the generic serving state is date-refined; everything else is verbatim.

    This class used to grade a ``simulate=True`` mode, in which any NON-terminal
    persisted state also followed the flight window so a time-simulation client
    (``mock_time`` / ``jump_to_event``) could watch a buy reach "completed" and
    receive the "final" delivery notification. That parameter is gone with the
    testing-hook channel that fed it (commit a1b79d22d): no request can ask for
    simulation, so there is no second refinement rule left to grade. What survives
    is the rule that made the simulated one an exception — the persisted lifecycle
    is authoritative, and a date can only refine the serving state.
    """

    def test_a_non_serving_status_is_not_refined_by_a_past_flight_window(self):
        """A pending buy whose flight has ended is still pending, not completed."""
        buy = _buy("pending_creatives", start=date(2025, 1, 1), end=date(2025, 3, 31))

        assert resolve_canonical_status(buy, date(2025, 6, 1)) == "pending_creatives"

    def test_a_terminal_decision_survives_its_flight_window(self):
        """A date must not resurrect a buy the seller deliberately stopped."""
        for terminal in ("canceled", "rejected", "paused"):
            buy = _buy(terminal, start=date(2025, 1, 1), end=date(2025, 3, 31))

            # Both inside the window and long past it: neither edge re-derives it.
            assert resolve_canonical_status(buy, date(2025, 2, 1)) == terminal, terminal
            assert resolve_canonical_status(buy, date(2025, 6, 1)) == terminal, terminal

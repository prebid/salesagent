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
    PENDING_PERSISTED_STATUSES,
    PERSISTED_STATUS_TO_CANONICAL,
    SERVING_PERSISTED_STATUSES,
    TERMINAL_STATUSES,
    WEBHOOK_ONLY_FIELDS,
    resolve_canonical_status,
)
from src.core.tools.media_buy_list import _PERSISTED_STATUS_TO_ADCP, _compute_status

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

    def test_serving_persisted_statuses_membership_is_pinned(self):
        """Pin the exact SERVING_PERSISTED_STATUSES set (drives the schedulers' queries).

        Regression #1556: the schedulers hardcoded partial copies of this set and
        stranded legacy "ready" rows — reported active by get_media_buy_delivery
        but never sent delivery webhooks and never migrated. A silent widen/narrow
        of the map would change which buys the schedulers process without a
        failing test unless the membership itself is pinned.
        """
        assert SERVING_PERSISTED_STATUSES == {"active", "approved", "ready", "scheduled"}

    def test_pending_persisted_statuses_membership_is_pinned(self):
        """Pin the exact PENDING_PERSISTED_STATUSES set (the status scheduler's promote gate).

        Same #1556 class as SERVING: the status scheduler promotes exactly these
        pre-serving persisted statuses to "active" once the flight starts. A silent
        widen would auto-promote a buy the seller has not accepted.
        """
        assert PENDING_PERSISTED_STATUSES == {"pending_start", "pending_activation"}

    def test_pending_persisted_statuses_excludes_the_human_approval_gates(self):
        """Derived from the map's pending_start keys MINUS the human-approval gates.

        ``pending`` and ``pending_approval`` also map to pending_start, but must NEVER
        be date-promoted by the scheduler (awaiting seller acceptance). Pin the exact
        subtraction so neither a map change nor a blind re-derivation can re-introduce
        the #1556 hand-coded-partial-copy defect.
        """
        pending_start_keys = frozenset(k for k, v in PERSISTED_STATUS_TO_CANONICAL.items() if v == "pending_start")
        assert PENDING_PERSISTED_STATUSES == pending_start_keys - {"pending", "pending_approval"}
        # and the two gates really are in the map (so the subtraction is load-bearing)
        assert {"pending", "pending_approval"} <= pending_start_keys

    def test_webhook_only_fields_membership_is_pinned(self):
        """Pin the exact WEBHOOK_ONLY_FIELDS set — the shared omission oracle's source of truth.

        The centralized real-wire oracles (BDD then_poll_omits_webhook_only_fields +
        the integration cross-transport test, both via
        assert_omits_webhook_only_fields) check exactly this set. Without this pin,
        removing a field from WEBHOOK_ONLY_FIELDS would silently stop those oracles
        from checking it. The three are the response fields the pinned schema marks
        "only present in webhook deliveries" (get-media-buy-delivery-response.json;
        unchanged in AdCP 3.1.1).
        """
        assert WEBHOOK_ONLY_FIELDS == {"notification_type", "sequence_number", "next_expected_at"}


class TestAdcpProjectionAgreesWithCanonicalMap:
    """The update-response lifecycle projection stays in lockstep with the canonical map.

    ``_PERSISTED_STATUS_TO_ADCP`` (media_buy_list.py) is the pure-column coercion
    consumed by the update-response dual-emit; ``PERSISTED_STATUS_TO_CANONICAL``
    (_media_buy_status.py) is the authoritative persisted->status map. They must
    agree row-for-row modulo exactly TWO sanctioned adaptations (#1417 round-8
    review):

    1. ``failed`` -> ``rejected`` — the same lifecycle collapse ``_compute_status``
       applies: the AdCP lifecycle enum has no "failed".
    2. ``ready``/``scheduled`` -> ``pending_start`` — the coercion runs with NO row
       or flight dates (buy-is-None fallback), so the date-gated serving aliases
       cannot be date-refined; pre-flight is the truthful unrefined reading.

    The per-row expectations below are LITERALS on purpose: recomputing them from
    the production override dict would make this oracle tautological. A third
    divergence — or an omitted persisted value — must fail loudly here.
    """

    # Every persisted MediaBuy.status value and the exact ADCP lifecycle value the
    # projection must produce. Literal, not derived (see class docstring).
    EXPECTED_ADCP_ROWS: dict[str, str] = {
        "active": "active",
        "approved": "active",
        "ready": "pending_start",  # sanctioned adaptation #2
        "scheduled": "pending_start",  # sanctioned adaptation #2
        "pending_activation": "pending_start",
        "paused": "paused",
        "completed": "completed",
        "rejected": "rejected",
        "canceled": "canceled",
        "failed": "rejected",  # sanctioned adaptation #1
        "draft": "pending_creatives",
        "pending": "pending_start",
        "pending_approval": "pending_start",
        "pending_creatives": "pending_creatives",
        "pending_start": "pending_start",
    }

    def test_expected_rows_cover_the_canonical_key_universe(self):
        """The literal expectations themselves cover exactly the canonical keys.

        Guards the oracle: adding a persisted value to the canonical map without
        deciding its lifecycle projection fails here first.
        """
        assert set(self.EXPECTED_ADCP_ROWS) == set(PERSISTED_STATUS_TO_CANONICAL)

    def test_adcp_map_covers_every_persisted_status(self):
        """No persisted value may ever be omitted from the lifecycle projection.

        An omitted key makes ``normalize_persisted_media_buy_status`` return None
        and silently drop the ``media_buy_status`` field for a real persisted row.
        """
        assert set(_PERSISTED_STATUS_TO_ADCP) == set(PERSISTED_STATUS_TO_CANONICAL), (
            f"missing from ADCP projection: {set(PERSISTED_STATUS_TO_CANONICAL) - set(_PERSISTED_STATUS_TO_ADCP)}; "
            f"extra in ADCP projection: {set(_PERSISTED_STATUS_TO_ADCP) - set(PERSISTED_STATUS_TO_CANONICAL)}"
        )

    def test_every_row_matches_canonical_modulo_sanctioned_adaptations(self):
        """Each row equals its canonical value except the two sanctioned adaptations.

        Asserted per row against the LITERAL expectations so a future third
        divergence (like the past draft drift) fails loudly with the exact row.
        """
        for persisted, expected in self.EXPECTED_ADCP_ROWS.items():
            actual = _PERSISTED_STATUS_TO_ADCP.get(persisted)
            assert actual is not None, f"persisted {persisted!r} missing from _PERSISTED_STATUS_TO_ADCP"
            assert actual is MediaBuyStatus(expected), (
                f"persisted {persisted!r}: ADCP projection -> {actual.value!r}, expected {expected!r} "
                f"(canonical: {PERSISTED_STATUS_TO_CANONICAL[persisted]!r})"
            )


class TestLegacyAndUnknownStatusesNotDropped:
    """Legacy persisted values and unknown statuses resolve to a valid status, never dropped.

    Regression (delivery): the old delivery resolver passed an unmapped
    persisted value through verbatim, so a legacy "ready" row (written by
    PR #375) or an admin "scheduled" row failed the internal-status filter and
    made even fetch-by-ID report MEDIA_BUY_NOT_FOUND for a buy that exists —
    while get_media_buys mapped the same row to a valid status. Both tools now
    treat any unmapped/legacy value as a generic serving state and date-refine
    it, so it always lands in CANONICAL_STATUSES (== the delivery fetch-by-ID
    filter set) and both tools agree.
    """

    def test_legacy_and_unknown_statuses_resolve_to_valid_status(self):
        for persisted in ("ready", "scheduled", "totally_unknown_legacy"):
            buy = _buy(persisted)  # inside the flight window
            delivery_status = resolve_canonical_status(buy, _REF)

            # Never a raw passthrough — must be in the delivery filter set so
            # fetch-by-ID cannot drop it.
            assert delivery_status in CANONICAL_STATUSES, persisted
            assert delivery_status == "active", persisted
            # And the two tools still agree.
            assert _compute_status(buy, _REF).value == delivery_status, persisted

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


class TestSimulationReachesTerminalStatus:
    """Under time simulation, a non-terminal buy follows the simulated clock; terminals are preserved.

    Regression (finding #3): honoring the persisted lifecycle short-circuited
    date refinement, so a time-simulation client (jump_to_event) on a buy
    created as pending_creatives never reached "completed" and the "final"
    delivery notification was unreachable.
    """

    def test_simulated_pending_buy_reaches_completed_past_flight(self):
        buy = _buy("pending_creatives", start=date(2025, 1, 1), end=date(2025, 3, 31))
        past_flight = date(2025, 6, 1)
        assert resolve_canonical_status(buy, past_flight, simulate=True) == "completed"
        # Without simulation the persisted lifecycle stays authoritative.
        assert resolve_canonical_status(buy, past_flight, simulate=False) == "pending_creatives"

    def test_simulation_preserves_terminal_decisions(self):
        """Simulation must not resurrect a buy the seller deliberately stopped."""
        for terminal in ("canceled", "rejected", "paused"):
            buy = _buy(terminal, start=date(2025, 1, 1), end=date(2025, 3, 31))
            assert resolve_canonical_status(buy, date(2025, 6, 1), simulate=True) == terminal

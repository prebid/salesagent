"""Guard: the get_media_buys carrier cannot re-derive the facts it was handed.

``_MediaBuyData`` exists so the build loop projects rather than decides. That claim is
only true while the carrier withholds the INPUTS a status is derived from — it is not
made true by deleting the second ``_compute_status`` call, because a deleted call can be
written again, and it is not made true by renaming the field, because a rename leaves
``resolve_canonical_status(carrier, today)`` working exactly as before.

So the criterion is graded as *the inputs are absent*, not as *the call is gone*. That
distinction is the whole finding this guard records: a structural claim a rename can
satisfy is a claim about the name, not the shape.

There is no buyer-visible surface for any of this — the carrier is module-private — so no
BDD row can reach it. Left ungraded, "the fields were deleted" would be a declaration
nothing measures, which is indistinguishable from a deletion that never happened.
"""

import pytest

from src.core.tools._media_buy_status import resolve_canonical_status
from src.core.tools.media_buy_list import _MediaBuyData

#: Everything ``resolve_canonical_status`` reads to decide a status. While ANY of these
#: is present on the carrier, re-derivation downstream of the fetch seam stays
#: expressible and only convention prevents it.
_REDERIVATION_INPUTS = frozenset({"start_date", "end_date", "start_time", "end_time", "is_paused", "status"})


@pytest.mark.arch_guard
def test_carrier_withholds_every_status_input() -> None:
    """None of the derivation inputs is declared on the carrier."""
    declared = set(_MediaBuyData.__dataclass_fields__)

    assert not (declared & _REDERIVATION_INPUTS), (
        f"_MediaBuyData declares {sorted(declared & _REDERIVATION_INPUTS)}, which "
        f"resolve_canonical_status reads. While they are present the build loop can "
        f"recompute a status the fetch seam already resolved, which is the duplicated "
        f"policy this carrier exists to make unwritable. Project the resolved value "
        f"through wire_status instead of carrying the inputs."
    )


@pytest.mark.arch_guard
def test_carrier_carries_the_resolved_answer() -> None:
    """The carrier holds the resolved status, so the build loop has something to project."""
    assert "wire_status" in _MediaBuyData.__dataclass_fields__


@pytest.mark.arch_guard
def test_rederivation_is_inexpressible_not_merely_unused() -> None:
    """``resolve_canonical_status`` cannot run on a carrier at all.

    The hole this closes that the field check above cannot: an input re-exposed through a
    DESCRIPTOR rather than a dataclass field. A ``@property`` named ``status`` does not
    appear in ``__dataclass_fields__``, so the roster check passes while
    ``resolve_canonical_status`` works again — measured, not supposed.

    It does NOT catch a rename. ``resolve_canonical_status`` reads ``buy.status`` first
    (_media_buy_status.py:179-183), so this assertion is really "the carrier has no
    attribute named ``status``" — a name check wearing a behaviour check's clothes.
    Restoring the same data under new field names (``flight_start``,
    ``persisted_lifecycle``) passes all three tests here. That blind spot is SHARED by
    the whole guard, and it is stated rather than papered over: an earlier version of
    this docstring claimed the opposite, and the claim measured false.
    """
    from datetime import date

    carrier = _MediaBuyData(
        media_buy_id="mb_1",
        account_id="acct_test",
        currency="USD",
        budget=None,
        raw_request={},
        created_at=None,
        updated_at=None,
        wire_status="active",
        confirmed_at=None,
        revision=1,
    )

    with pytest.raises(AttributeError):
        resolve_canonical_status(carrier, date.today())


@pytest.mark.arch_guard
def test_blob_rule_fails_closed_on_a_name_the_model_does_not_declare() -> None:
    """``_resolve_blob_field`` raises rather than passing an unvalidated value through.

    Graded here because no BDD row can reach it: an undeclared ``model_field`` is a
    programming error, not a wire condition, so there is no request that produces it.

    The first version validated with ``validate_assignment``, and ``GetMediaBuysPackage``
    inherits ``extra="allow"`` from the SDK — so assigning an undeclared name SET AN EXTRA
    and raised nothing, and the function returned the raw blob value with no advisory and
    no noise. A rule that fails open on an unknown name is a roster with extra steps. The
    mismatch is live, not hypothetical: this module already reads the blob key
    ``targeting`` into the field ``targeting_overlay``.
    """
    from src.core.schemas import GetMediaBuysPackage
    from src.core.tools.media_buy_list import _resolve_blob_field

    advisories: list = []
    with pytest.raises(KeyError):
        _resolve_blob_field(
            {"not_a_declared_field": {"arbitrary": "blob"}},
            "not_a_declared_field",
            model=GetMediaBuysPackage,
            model_field="not_a_declared_field",
            subject="package 'p' on media buy 'm'",
            field_path="media_buys[].packages[p].not_a_declared_field",
            advisories=advisories,
        )
    assert advisories == []

"""Tests for the cancellation default override on UpdateMediaBuyRequest.

Covers tescoboy issue #155: the upstream `adcp.types.UpdateMediaBuyRequest`
declares `canceled: Literal[True] = True`, so every Pydantic-validated
payload silently carries `canceled: true` even when the buyer never
asked to cancel. This is dormant today (no production code reads the
field) but a one-commit-away data-loss vector — AdCP cancellation is
irreversible.

The seller-side schema overrides `canceled` to `Literal[True] | None = None`
so omission means "not a cancellation request" rather than defaulting
to True. The same fix is applied at the package level (`AdCPPackageUpdate`).
"""

from src.core.schemas import AdCPPackageUpdate, UpdateMediaBuyRequest


class TestUpdateMediaBuyRequestCanceledDefault:
    def test_omitted_canceled_is_none(self):
        # Pre-fix this asserted `True` because the library declared
        # canceled: Literal[True] = True.
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z")
        assert req.canceled is None

    def test_omitted_canceled_not_in_exclude_unset_dump(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z")
        dump = req.model_dump(exclude_unset=True)
        assert "canceled" not in dump, "Buyer omitted canceled — must not appear in exclude_unset dump."

    def test_full_dump_omits_canceled_when_unset(self):
        # The schema's `_serialize_model` drops None-valued fields, so a
        # full dump for a non-cancellation request omits `canceled`
        # entirely. This is what gets persisted to
        # `workflow_steps.request_data`, so the time bomb is defused on
        # the persistence path too — not just at replay (#155).
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z")
        dump = req.model_dump()
        assert "canceled" not in dump

    def test_explicit_canceled_true_preserved(self):
        # Buyer explicitly cancels — the field is preserved verbatim and
        # `cancellation_reason` can ride along per spec.
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_1",
            canceled=True,
            cancellation_reason="campaign goal achieved",
        )
        assert req.canceled is True
        assert req.cancellation_reason == "campaign goal achieved"

    def test_persistence_round_trip_omits_canceled(self):
        # The replay-path filter in workflows.py limits keys to
        # UpdateMediaBuyRequest.model_fields. After model_validate of a
        # buyer payload that didn't include canceled, the round-tripped
        # request has canceled=None.
        buyer_payload = {
            "media_buy_id": "mb_1",
            "end_time": "2026-06-01T00:00:00Z",
        }
        req = UpdateMediaBuyRequest.model_validate(buyer_payload)
        round_tripped = UpdateMediaBuyRequest.model_validate(req.model_dump(exclude_unset=True))
        assert round_tripped.canceled is None


class TestPackageUpdateCanceledDefault:
    def test_package_omitted_canceled_is_none(self):
        pkg = AdCPPackageUpdate(package_id="pkg_1")
        assert pkg.canceled is None

    def test_package_explicit_canceled_preserved(self):
        pkg = AdCPPackageUpdate(package_id="pkg_1", canceled=True, cancellation_reason="paused too long")
        assert pkg.canceled is True

"""The Mock ad server's receipt for a creative push records what it was handed.

An audit record of a creative upload that carries only a COUNT cannot answer the question
such a record exists to answer. Several fields of an uploaded asset appear in no response
the buyer ever sees -- the click destination is the plain case, which is why dropping it is
silent: the buy succeeds and the line item ships unclickable with no error anywhere.

The receipt is also the only observation of the push that survives a process boundary. The
adapter's in-memory ``_media_buys[...]["creatives"]`` dies with the process, so over
e2e_rest -- where the adapter runs inside the server container -- the row is what
``MediaBuyCreateEnv.uploaded_adapter_assets`` reads. These tests are the in-process
instrument for that: they grade the production payload directly, so a change that empties it
reddens here rather than only on the one transport that needs a Docker stack.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import Principal

#: A click destination is the field under test precisely because no response carries it.
CLICK_URL = "https://advertiser.example.com/landing"


def _adapter() -> MockAdServer:
    adapter = MockAdServer(
        config={},
        principal=Principal(principal_id="principal_test", name="Test Principal", platform_mappings={}),
        tenant_id="tenant_test",
    )
    # Shadow the CLASS-level registry with an instance dict: _media_buys is declared on
    # MockAdServer itself, so mutating the inherited object would leak into every other test
    # in the session.
    adapter._media_buys = {"mb_1": {"creatives": []}}
    adapter.audit_logger = MagicMock()
    return adapter


def _asset(creative_id: str = "cr_1") -> dict[str, Any]:
    """One asset in the shape ``_build_adapter_asset_from_creative`` produces."""
    return {
        "creative_id": creative_id,
        "package_assignments": [{"package_id": "pkg_1", "weight": 100}],
        "width": 300,
        "height": 250,
        "url": "https://cdn.example.com/banner.png",
        "click_url": CLICK_URL,
        "asset_type": "image",
        "name": "Banner",
    }


def _receipt(adapter: MockAdServer) -> dict[str, Any]:
    """The ``details`` payload of the push the adapter recorded.

    The count is asserted off ``call_args_list`` rather than with ``assert_called_once()``:
    that spelling followed by a ``call_args`` read is the split assertion the weak-mock
    guard refuses, because it checks the arity and the payload against two different
    things. ``test_the_record_is_one_call_naming_this_adapters_operation`` below pins the
    whole call in one assertion, which is the guard's remedy; this helper is the payload
    reader the per-field tests share.
    """
    calls = adapter.audit_logger.log_operation.call_args_list
    assert len(calls) == 1, f"expected exactly one push record, got {len(calls)}: {calls!r}"
    return calls[0].kwargs["details"]


class TestCreativePushReceipt:
    def test_the_record_is_one_call_naming_this_adapters_operation(self):
        """The whole call, in one assertion: arity, operation, principal, payload.

        The readback finds this row by its operation string, which the audit logger writes
        as ``"<adapter_name>.<operation>"`` — so a renamed operation would leave the row
        unfindable with nothing failing. Spelled out rather than split into a call-count
        check plus a payload read, which would grade the two against different things.
        """
        adapter = _adapter()
        assets = [_asset()]

        adapter._add_creative_assets_immediate("mb_1", assets, datetime.now(UTC))

        adapter.audit_logger.log_operation.assert_called_once_with(
            operation="add_creative_assets",
            principal_name="Test Principal",
            principal_id="principal_test",
            adapter_id="unknown",
            success=True,
            details={"media_buy_id": "mb_1", "creative_count": 1, "assets": assets},
        )

    def test_the_receipt_carries_the_assets_as_handed(self):
        """The whole asset list, by value -- not a count, not a projection of it.

        Asserted as equality against the list that went IN rather than by fishing out
        ``click_url``: a projection authored at the write site can drop exactly the field
        nobody reads, and that is the defect this record exists to make impossible.
        """
        adapter = _adapter()
        assets = [_asset("cr_1"), _asset("cr_2")]

        adapter._add_creative_assets_immediate("mb_1", assets, datetime.now(UTC))

        assert _receipt(adapter)["assets"] == assets

    def test_the_click_destination_is_in_the_receipt(self):
        """Named separately because it is the obligation, and it has no other observer.

        The sibling above would still pass if ``_build_adapter_asset_from_creative`` stopped
        producing a ``click_url`` at all -- equality against the input holds whatever the
        input is. This one says the recorded asset carries a destination.
        """
        adapter = _adapter()

        adapter._add_creative_assets_immediate("mb_1", [_asset()], datetime.now(UTC))

        assert [a["click_url"] for a in _receipt(adapter)["assets"]] == [CLICK_URL]

    def test_the_count_the_admin_activity_view_reads_is_retained(self):
        """``business_activity_service`` reads ``details["creative_count"]``.

        The receipt GREW; it did not change shape. Without this, adding the assets and
        dropping the count would blank a number in the admin UI and no test would say so.
        """
        adapter = _adapter()

        adapter._add_creative_assets_immediate("mb_1", [_asset("cr_1"), _asset("cr_2")], datetime.now(UTC))

        assert _receipt(adapter)["creative_count"] == 2

    def test_the_receipt_survives_json_serialization_unchanged(self):
        """It is read back out of a JSON column, so a leaf that cannot serialize is a bug.

        The database path serializes with ``fallback=str``, which does not fail on an
        unserializable leaf -- it stringifies it, and the readback then compares a string
        against the object the in-process branch returns. A round-trip here is what refuses
        that silently.
        """
        adapter = _adapter()
        assets = [_asset()]

        adapter._add_creative_assets_immediate("mb_1", assets, datetime.now(UTC))

        receipt = _receipt(adapter)
        assert json.loads(json.dumps(receipt)) == receipt

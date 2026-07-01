"""The prebid ext namespace SSOT: write with prebid_ext, read with prebid_vendor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.ext_namespace import (
    PREBID_EXT_NAMESPACE,
    PROPERTY_LIST_ADVISORIES_KEY,
    prebid_ext,
    prebid_vendor,
)

pytestmark = pytest.mark.unit


class TestPrebidExt:
    def test_wraps_fields_under_the_namespace(self):
        assert prebid_ext(property_list_targeting=True) == {PREBID_EXT_NAMESPACE: {"property_list_targeting": True}}

    def test_multiple_fields(self):
        ext = prebid_ext(a=1, b=[2, 3])
        assert ext == {"prebid": {"a": 1, "b": [2, 3]}}


class TestPrebidVendor:
    def test_reads_from_dict_ext(self):
        assert prebid_vendor({"prebid": {"x": 1}}) == {"x": 1}

    def test_reads_from_model_like_ext(self):
        # ExtensionObject is a model on the construction path; getattr resolves it.
        assert prebid_vendor(SimpleNamespace(prebid={"x": 1})) == {"x": 1}

    def test_none_ext_returns_none(self):
        assert prebid_vendor(None) is None

    def test_missing_or_non_dict_vendor_returns_none(self):
        assert prebid_vendor({"other": {}}) is None
        assert prebid_vendor({"prebid": "not-a-dict"}) is None

    def test_roundtrip_write_then_read(self):
        assert prebid_vendor(prebid_ext(property_list_targeting=False)) == {"property_list_targeting": False}


class TestPropertyListAdvisoriesKey:
    """The advisory sub-key is single-sourced, so the create-success producer and the
    response reader land on the same ``ext.prebid.property_list_advisories`` slot."""

    def test_lands_under_the_prebid_namespace(self):
        ext = prebid_ext(**{PROPERTY_LIST_ADVISORIES_KEY: [{"code": "PRODUCT_UNAVAILABLE"}]})
        assert ext == {PREBID_EXT_NAMESPACE: {"property_list_advisories": [{"code": "PRODUCT_UNAVAILABLE"}]}}

    def test_producer_consumer_share_the_key(self):
        # Writing under the constant and reading by the same constant must roundtrip —
        # the structural guarantee that the producer/consumer literals cannot drift.
        advisories = [{"code": "PRODUCT_UNAVAILABLE", "message": "no overlap"}]
        vendor = prebid_vendor(prebid_ext(**{PROPERTY_LIST_ADVISORIES_KEY: advisories}))
        assert vendor.get(PROPERTY_LIST_ADVISORIES_KEY) == advisories

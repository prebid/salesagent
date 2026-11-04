"""A valid value for each request member this seller accepts by inheritance and does not apply.

Valid against the pinned request models, so a row reaches the seller's own refusal rather
than the schema's. Shared by the create and update scenarios that send them.
"""

from __future__ import annotations

from typing import Any

FREQUENCY_CAP: dict[str, Any] = {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}

#: The aggregate members ``create_media_buy`` and ``update_media_buy`` both declare.
SHARED_REQUEST_MEMBERS: dict[str, Any] = {
    "bidding": {"automatic": True},
    "budget_cap_timezone": "America/New_York",
    "daily_budget_cap": 100,
    "frequency_cap": FREQUENCY_CAP,
    "invoice_recipient": {
        "legal_name": "Agency Billing Inc",
        "address": {"street": "1 Main St", "city": "New York", "postal_code": "10001", "country": "US"},
    },
    "pacing": "even",
}

CREATE_REQUEST_MEMBERS: dict[str, Any] = {
    **SHARED_REQUEST_MEMBERS,
    "advertiser_industry": "technology.software",
    "artifact_webhook": {
        "url": "https://buyer.example/artifacts",
        "authentication": {"schemes": ["Bearer"], "credentials": "0123456789012345678901234567890123456789"},
        "delivery_mode": "batched",
    },
    "io_acceptance": {"io_id": "io_1", "accepted_at": "2030-01-01T00:00:00Z", "signatory": "A Buyer"},
    "opportunity": {"opportunity_id": "op_1"},
    "paused": True,
}

UPDATE_REQUEST_MEMBERS: dict[str, Any] = {
    **SHARED_REQUEST_MEMBERS,
    "total_budget": {"amount": 5000, "currency": "USD"},
}

#: A valid value for each overlay dimension the scenarios send.
_PLACE = [{"country": "US", "system": "geonames", "place_type": "city", "values": ["5128581"]}]
_LIST = {"agent_url": "https://lists.example", "list_id": "blocked"}
OVERLAY_DIMENSIONS: dict[str, Any] = {
    "age_restriction": {"min": 18, "verification_required": False},
    "audience_exclude": ["aud_1"],
    "audience_include": ["aud_1"],
    "audiences_none_of": ["aud_1"],
    "browser_exclude": ["chrome"],
    "browser_none_of": ["chrome"],
    "collection_list": _LIST,
    "collection_selection": {"mode": "default"},
    "connection_type_any_of": [1],
    "content_cat_none_of": ["IAB1"],
    "daypart_targets": [{"days": ["monday"], "start_hour": 9, "end_hour": 17}],
    "device_platform_exclude": ["ios"],
    "device_type_exclude": ["mobile"],
    "device_type_none_of": ["mobile"],
    "geo_countries": ["US"],
    "geo_places": _PLACE,
    "geo_places_exclude": _PLACE,
    "geo_regions": ["US-NY"],
    "keywords_none_of": ["shoes"],
    "language": ["en"],
    "media_type_any_of": ["display"],
    "media_type_none_of": ["video"],
    "os_none_of": ["ios"],
    "placement_selection": {"mode": "default"},
    "property_list_exclude": _LIST,
}

CREATE_PACKAGE_MEMBERS: dict[str, Any] = {
    "audience_evidence_pins": [
        {"evidence_id": "e_1", "snapshot_id": "s_1", "version": "1", "content_digest": "sha256:" + "0" * 64}
    ],
    "audience_evidence_requirements": {"requirement_mode": "required", "evidence_presence": "required"},
    "bidding": {"automatic": True},
    "daily_budget_cap": 100,
    "end_time": "2099-02-01T00:00:00Z",
    "impressions": 1000,
    "pacing": "even",
    "start_time": "2099-01-15T00:00:00Z",
}

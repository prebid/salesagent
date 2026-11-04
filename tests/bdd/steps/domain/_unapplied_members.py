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

"""Slim MCP input schemas for tools whose auto-generated schemas are too large.

The adcp library generates input schemas from Pydantic models via
``_generate_pydantic_schemas()`` and then inlines all ``$ref`` nodes via
``_inline_refs()``.  For ``create_media_buy`` this produces ~93 000 lines of
JSON that fills an LLM context window before any useful work can happen.

This module provides hand-crafted replacements that:
* Cover every **required** field (enforced by ``test_slim_schema_guard.py``).
* Include the most commonly used optional fields (ranked by test-corpus usage).
* Stay under ~100 lines so the schema is a negligible context cost.

Runtime behaviour is unchanged — the slim schema only affects what MCP
clients see during tool discovery; the underlying function still validates
against the full ``CreateMediaBuyRequest`` Pydantic model.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# create_media_buy
# ---------------------------------------------------------------------------
# Required fields (5):  idempotency_key, account, brand, start_time, end_time
# Top optional fields by test-corpus frequency:
#   packages[].creatives (77 files), packages[].targeting_overlay (22 files),
#   po_number (19 files), push_notification_config (12 files),
#   packages[].pacing (8 files), reporting_webhook (7 files)
# ---------------------------------------------------------------------------

CREATE_MEDIA_BUY_SLIM_SCHEMA: dict = {
    "type": "object",
    "required": ["idempotency_key", "account", "brand", "start_time", "end_time"],
    "properties": {
        # ── required ──────────────────────────────────────────────────────
        "idempotency_key": {
            "type": "string",
            "description": (
                "Client-generated unique key (16-255 chars, alphanumeric + _.:-). "
                "Re-send the same key to safely retry without creating a duplicate."
            ),
        },
        "account": {
            "type": "object",
            "description": (
                "Account to bill. Either {account_id: str} or {brand: {domain: str}, operator: str, sandbox?: bool}."
            ),
        },
        "brand": {
            "type": "object",
            "description": "Brand reference. Provide {domain: 'example.com'}.",
        },
        "start_time": {
            "type": "string",
            "description": "Campaign start: ISO 8601 datetime or the literal string 'asap'.",
        },
        "end_time": {
            "type": "string",
            "format": "date-time",
            "description": "Campaign end: ISO 8601 datetime.",
        },
        # ── common optional (top-level) ────────────────────────────────────
        "name": {
            "type": "string",
            "description": "Human-readable campaign name.",
        },
        "po_number": {
            "type": "string",
            "description": "Purchase order number for tracking.",
        },
        "push_notification_config": {
            "type": "object",
            "description": (
                "Webhook for async task-completion notifications. "
                "Provide {url, authentication: {schemes, credentials}}."
            ),
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "authentication": {"type": "object"},
            },
            "required": ["url"],
        },
        "reporting_webhook": {
            "type": "object",
            "description": (
                "Webhook for periodic delivery-metrics reports. Provide {url, authentication, reporting_frequency}."
            ),
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "authentication": {"type": "object"},
                "reporting_frequency": {
                    "type": "string",
                    "enum": ["hourly", "daily", "monthly"],
                },
            },
            "required": ["url", "authentication", "reporting_frequency"],
        },
        # ── packages ───────────────────────────────────────────────────────
        "packages": {
            "type": "array",
            "description": "One entry per product/placement combination.",
            "items": {
                "type": "object",
                "required": ["product_id", "budget", "pricing_option_id"],
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "product_id from get_products.",
                    },
                    "budget": {
                        "type": "number",
                        "description": "Package budget in the account currency.",
                    },
                    "pricing_option_id": {
                        "type": "string",
                        "description": "pricing_option_id from the product's pricing_options.",
                    },
                    "start_time": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Package flight start (inherits media buy start if omitted).",
                    },
                    "end_time": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Package flight end (inherits media buy end if omitted).",
                    },
                    "pacing": {
                        "type": "string",
                        "enum": ["even", "asap", "front_loaded"],
                        "description": "Delivery pacing strategy.",
                    },
                    "targeting_overlay": {
                        "type": "object",
                        "description": "Targeting constraints for this package.",
                        "properties": {
                            "geo_countries": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "ISO 3166-1 alpha-2 country codes, e.g. ['NL', 'DE'].",
                            },
                            "geo_regions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "ISO 3166-2 codes, e.g. ['US-CA', 'GB-SCT'].",
                            },
                            "device_type": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["desktop", "mobile", "tablet", "ctv", "dooh"],
                                },
                            },
                            "frequency_cap": {
                                "type": "object",
                                "description": "Max impressions per entity per time window.",
                                "properties": {
                                    "max_impressions": {"type": "integer"},
                                    "window": {
                                        "type": "object",
                                        "properties": {
                                            "interval": {"type": "integer"},
                                            "unit": {
                                                "type": "string",
                                                "enum": ["hours", "days", "campaign"],
                                            },
                                        },
                                        "required": ["interval", "unit"],
                                    },
                                },
                            },
                        },
                    },
                    "creatives": {
                        "type": "array",
                        "description": (
                            "Inline creative assets to upload and assign to this package. "
                            "Each item needs: creative_id (str), name (str), "
                            "format_kind (e.g. 'image'), assets (object keyed by slot). "
                            "Example asset slot: image: {asset_type:'image', url, width, height}."
                        ),
                        "items": {
                            "type": "object",
                            "required": ["creative_id", "name", "assets"],
                            "properties": {
                                "creative_id": {"type": "string"},
                                "name": {"type": "string"},
                                "format_kind": {
                                    "type": "string",
                                    "enum": [
                                        "image",
                                        "html5",
                                        "display_tag",
                                        "video_hosted",
                                        "video_vast",
                                        "audio_hosted",
                                        "native_in_feed",
                                    ],
                                },
                                "assets": {"type": "object"},
                            },
                        },
                    },
                },
            },
        },
        # ── proposal shortcut ──────────────────────────────────────────────
        "proposal_id": {
            "type": "string",
            "description": (
                "Execute a committed proposal instead of specifying packages manually. "
                "Pair with total_budget to derive package budgets from allocation percentages."
            ),
        },
        "total_budget": {
            "type": "object",
            "description": "Total budget when executing a proposal: {amount: number, currency: str}.",
            "properties": {
                "amount": {"type": "number"},
                "currency": {"type": "string"},
            },
            "required": ["amount", "currency"],
        },
    },
}

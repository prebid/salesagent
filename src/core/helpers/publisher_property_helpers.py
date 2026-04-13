"""Helpers for normalizing publisher_properties to AdCP discriminated union format.

AdCP 2.13.0+ requires PublisherPropertySelector dicts to have a selection_type
discriminator ("all", "by_id", or "by_tag"). Legacy data and inventory profiles
created via the admin UI "full JSON" mode may lack this field.

This module provides ensure_selection_type() to normalize on read.
"""

from __future__ import annotations

import re

_PROPERTY_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
_PROPERTY_TAG_PATTERN = re.compile(r"^[a-z0-9_]+$")


def ensure_selection_type(properties: list[dict]) -> list[dict] | None:
    """Ensure each publisher_properties dict has a selection_type discriminator.

    Non-destructive: adds selection_type when missing, keeps all other fields intact.
    Only filters property_ids/property_tags to valid values (^[a-z0-9_]+$).

    For each dict in the list:
    - Already has selection_type → passthrough unchanged
    - Has valid property_ids → adds selection_type "by_id", replaces property_ids with valid subset
    - Has valid property_tags → adds selection_type "by_tag", replaces property_tags with valid subset
    - Neither → adds selection_type "all"

    Non-dict entries are skipped. Returns None if result is empty.
    """
    converted = []
    for prop in properties:
        if not isinstance(prop, dict):
            continue

        if "selection_type" in prop:
            converted.append(prop)
            continue

        # Work on a copy — don't mutate the original
        result = dict(prop)
        result.setdefault("publisher_domain", "unknown")

        prop_ids = prop.get("property_ids", [])
        prop_tags = prop.get("property_tags", [])

        valid_ids = [pid for pid in prop_ids if _PROPERTY_ID_PATTERN.match(str(pid))]
        valid_tags = [tag for tag in prop_tags if _PROPERTY_TAG_PATTERN.match(str(tag))]

        if valid_ids:
            result["property_ids"] = valid_ids
            result["selection_type"] = "by_id"
        elif valid_tags:
            result["property_tags"] = valid_tags
            result["selection_type"] = "by_tag"
        else:
            result["selection_type"] = "all"

        converted.append(result)

    return converted if converted else None

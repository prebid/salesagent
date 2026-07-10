"""Shared Gherkin brand-cell parsing for BDD steps (#1324)."""

from __future__ import annotations

import json
from typing import Any


def parse_brand_gherkin_param(brand: str) -> Any:
    """Parse a brand Examples cell as JSON dict/string or bare token."""
    try:
        return json.loads(brand)
    except json.JSONDecodeError:
        return brand

"""Reference format fixture: the single source of truth for testing-mode formats.

This module reads a checked-in fixture (``reference_formats.json``) for its
``formats`` — full ``Format`` definitions captured from the pinned reference
creative agent. ``ADCP_TESTING=true`` serves these (via
``creative_agent_registry._get_reference_formats``) so the in-process harness and
the e2e server return identical formats by construction.

The fixture is refreshed only when formats change, via an explicit script/make
target (``make creative-formats-refresh`` → ``scripts/refresh-reference-formats.py``),
never per-session. See salesagent issue #1418.

Design principles:
1. Tests never depend on external infrastructure (fixture is checked in)
2. Refresh is explicit; the fixture diff is reviewed in the PR (the drift gate)
"""

import json
from functools import lru_cache
from pathlib import Path

from src.core.schemas import Format

# Default agent URL for AdCP reference implementation
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Schema version of the reference_formats.json fixture.
FIXTURE_SCHEMA_VERSION = 2

# Cache file location
CACHE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "creative_formats"
CACHE_FILE = CACHE_DIR / "reference_formats.json"


@lru_cache(maxsize=1)
def load_reference_formats() -> tuple[Format, ...]:
    """Load full reference Format definitions from the checked-in fixture.

    This is the testing-mode source of truth: the formats here are what
    ``ADCP_TESTING=true`` serves, captured from the pinned reference agent.

    Memoized (the fixture is immutable at runtime). Returns a tuple so the
    memoized value cannot be mutated by callers.

    Raises:
        FileNotFoundError: fixture missing — checked-in fixture is required.
        ValueError: fixture empty, malformed, or any entry fails Format
            validation. We never silently return [] (No Quiet Failures).
    """
    if not CACHE_FILE.exists():
        raise FileNotFoundError(
            f"Reference formats fixture missing at {CACHE_FILE}. It is checked in; "
            "regenerate with `make creative-formats-refresh`."
        )

    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Reference formats fixture at {CACHE_FILE} is unreadable: {exc}") from exc

    raw_formats = data.get("formats")
    if not raw_formats:
        raise ValueError(
            f"Reference formats fixture at {CACHE_FILE} has no 'formats' entries. "
            "Regenerate with `make creative-formats-refresh`."
        )

    try:
        return tuple(Format.model_validate(entry) for entry in raw_formats)
    except Exception as exc:  # pydantic ValidationError or malformed entry
        raise ValueError(f"Reference formats fixture at {CACHE_FILE} contains an invalid Format entry: {exc}") from exc

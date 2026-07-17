"""Reference format fixture: the single source of truth for testing-mode formats.

This module reads a checked-in fixture (``reference_formats.json``) that holds
two things captured from the pinned reference creative agent:

1. ``legacy_id_map`` — a shallow ``{legacy_string_id: agent_url}`` map used to
   upgrade deprecated string format_ids to namespaced ``FormatId`` objects.
2. ``formats`` — full ``Format`` definitions. ``ADCP_TESTING=true`` serves these
   (via ``creative_agent_registry._get_reference_formats``) so the in-process
   harness and the e2e server return identical formats by construction.

The fixture is refreshed only when formats change, via an explicit script/make
target (``make creative-formats-refresh`` → ``scripts/refresh-reference-formats.py``),
never per-session. See salesagent issue #1418.

Design principles:
1. Tests never depend on external infrastructure (fixture is checked in)
2. Legacy string format_ids automatically upgrade to namespaced format
3. Refresh is explicit; the fixture diff is reviewed in the PR (the drift gate)
4. Default agent_url is the AdCP reference implementation
"""

import json
from functools import lru_cache
from pathlib import Path

from adcp.types import FormatId as LibraryFormatId

from src.core.schemas import Format, FormatId, url

# Default agent URL for AdCP reference implementation
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Schema version of the reference_formats.json fixture.
FIXTURE_SCHEMA_VERSION = 2

# Cache file location
CACHE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "creative_formats"
CACHE_FILE = CACHE_DIR / "reference_formats.json"


def load_format_cache() -> dict[str, str]:
    """Load the legacy format_id → agent_url map from the reference fixture.

    Returns:
        Dict mapping legacy string format_id to agent_url. Empty if the fixture
        is missing or unreadable (legacy upgrade then fails loud per-id).
    """
    if not CACHE_FILE.exists():
        # Return empty cache - will use default agent URL
        return {}

    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
            return data.get("legacy_id_map", {})
    except (OSError, json.JSONDecodeError):
        return {}


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


def upgrade_legacy_format_id(format_id_value: str | dict | FormatId) -> FormatId:
    """Upgrade legacy string format_id to namespaced FormatId object.

    If format_id is already an object, returns it as-is.
    If format_id is a string, looks up agent_url from cache or uses default.

    Args:
        format_id_value: Legacy string or new FormatId object

    Returns:
        FormatId object with agent_url namespace

    Examples:
        >>> upgrade_legacy_format_id("display_300x250")
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")

        >>> upgrade_legacy_format_id({"agent_url": "...", "id": "..."})
        FormatId(agent_url="...", id="...")
    """
    # Already a FormatId object (check both our FormatId and library's FormatId)
    if isinstance(format_id_value, FormatId):
        return format_id_value

    # Library FormatId (not our subclass) - convert to our FormatId
    if isinstance(format_id_value, LibraryFormatId):
        # Extract parameters for parameterized formats (AdCP 2.5)
        kwargs = {
            "agent_url": format_id_value.agent_url,
            "id": format_id_value.id,
        }
        if format_id_value.width is not None:
            kwargs["width"] = format_id_value.width
        if format_id_value.height is not None:
            kwargs["height"] = format_id_value.height
        if format_id_value.duration_ms is not None:
            kwargs["duration_ms"] = format_id_value.duration_ms
        return FormatId(**kwargs)

    # Already a dict with agent_url
    if isinstance(format_id_value, dict):
        if "agent_url" in format_id_value and "id" in format_id_value:
            return FormatId(**format_id_value)
        # Dict without agent_url - use default
        if "id" in format_id_value:
            return FormatId(agent_url=url(DEFAULT_AGENT_URL), id=format_id_value["id"])

    # Legacy string format - upgrade to namespaced format (DEPRECATED)
    if isinstance(format_id_value, str):
        import logging

        logger = logging.getLogger(__name__)

        # Check cache for agent_url
        cache = load_format_cache()

        if format_id_value not in cache:
            # Unknown format - fail loudly per AdCP spec guidance
            raise ValueError(
                f"Unknown format_id '{format_id_value}'. String format_ids are deprecated. "
                f"Must provide structured format with agent_url. "
                f"Known formats: {list(cache.keys())[:10]}..."
            )

        agent_url = cache[format_id_value]

        # Log deprecation warning
        logger.warning(
            f"⚠️  DEPRECATED: String format_id '{format_id_value}' received. "
            f"Use structured format: {{'agent_url': '{agent_url}', 'id': '{format_id_value}'}}. "
            f"String format_ids will be removed in a future version."
        )

        return FormatId(agent_url=url(agent_url), id=format_id_value)

    raise ValueError(f"Invalid format_id type: {type(format_id_value)}")


def get_agent_url_for_format(format_id: str) -> str:
    """Get agent_url for a given format ID string.

    Args:
        format_id: Format ID string (e.g., "display_300x250")

    Returns:
        Agent URL (from cache or default)
    """
    cache = load_format_cache()
    return cache.get(format_id, DEFAULT_AGENT_URL)

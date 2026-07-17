#!/usr/bin/env python3
"""Capture the pinned creative agent's full format catalog into the checked-in fixture.

The fixture (tests/fixtures/creative_formats/reference_formats.json) is the single
source of truth for testing-mode formats: ADCP_TESTING=true serves it via
creative_agent_registry._get_reference_formats(), so the in-process harness and the
e2e server return identical formats by construction. This script refreshes it from
the running pinned reference agent — run it only when the pin or the agent's catalog
changes; the reviewed fixture diff in the PR is the drift gate. See issue #1418.

The capture uses the PRODUCTION fetch path (CreativeAgentRegistry.get_formats_for_agent
with ADCP_TESTING off), so it exercises the same tolerant-validation pipeline the
server uses at runtime — no duplicated fetch logic.

Usage::

    scripts/creative-agent-stack.sh up                       # bring up the pinned agent
    uv run python scripts/refresh-reference-formats.py \
        --url $(scripts/creative-agent-stack.sh url)

Guard: refuses the live public host (creative.adcontextprotocol.org) unless
ALLOW_LIVE_CREATIVE_AGENT=1, mirroring tests/integration/test_creative_agent_live.py.
The fixture must only ever capture from the deterministic pinned agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Canonical agent_url stamped into every fixture format_id. The pinned agent may
# report its own container URL (e.g. http://localhost:9999/...); we normalize to the
# canonical public URL so all existing references (legacy_id_map, ProductFactory,
# upgrade_legacy_format_id) keep resolving. The raw source is recorded in metadata.
CANONICAL_AGENT_URL = "https://creative.adcontextprotocol.org"
PUBLIC_CREATIVE_AGENT_HOST = "creative.adcontextprotocol.org"


def _read_adcp_pin() -> str:
    """Read ADCP_PIN from scripts/creative-agent-stack.sh — the single source of the pin.

    Parsing (rather than duplicating the value) means a pin bump in the stack
    script can never leave a stale pin stamped into the fixture metadata.
    """
    script = REPO_ROOT / "scripts" / "creative-agent-stack.sh"
    for line in script.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADCP_PIN="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit(f"ADCP_PIN not found in {script}")


ADCP_PIN = _read_adcp_pin()


def _check_url_guard(url: str) -> None:
    """Refuse the live public host unless explicitly overridden (issue #1418)."""
    if os.environ.get("ALLOW_LIVE_CREATIVE_AGENT") == "1":
        return
    if not url or PUBLIC_CREATIVE_AGENT_HOST in url:
        sys.exit(
            f"Refusing to capture from {url!r}: the fixture must be captured from the pinned "
            "reference agent, never the live public host (its catalog drifts). Bring it up with "
            "`scripts/creative-agent-stack.sh up` then pass `--url $(scripts/creative-agent-stack.sh url)`. "
            "Deliberate prod capture: ALLOW_LIVE_CREATIVE_AGENT=1."
        )


async def _capture_formats(url: str):
    """Fetch the full catalog via the production registry path (ADCP_TESTING off)."""
    # Disable the testing short-circuit so we hit the real fetch+validation pipeline.
    os.environ["ADCP_TESTING"] = "false"

    from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry

    registry = CreativeAgentRegistry()
    # Construct our own CreativeAgent rather than rely on DEFAULT_AGENT (which reads
    # CREATIVE_AGENT_URL at import time and may not match --url).
    agent = CreativeAgent(agent_url=url, name="Pinned Reference Creative Agent", enabled=True, priority=1)
    return await registry.get_formats_for_agent(agent, force_refresh=True)


def _build_fixture(formats, source_url: str, legacy_id_map: dict[str, str]) -> dict:
    """Normalize, validate, sort, and assemble the v2 fixture payload.

    Every entry is dumped via the production model and re-validated against Format —
    that round-trip is the structural drift gate. is_standard is exclude=True (dropped
    by model_dump), so we inject it explicitly to preserve standard/custom counts.
    """
    from src.core.schemas import Format

    entries: list[dict] = []
    for fmt in formats:
        data = fmt.model_dump(mode="json")
        # Normalize the agent_url to canonical so existing references keep resolving.
        if isinstance(data.get("format_id"), dict):
            data["format_id"]["agent_url"] = CANONICAL_AGENT_URL
        # is_standard is exclude=True on Format; reinject so the fixture preserves it.
        data["is_standard"] = True
        # Re-validate the normalized entry — fail loud on any structural defect.
        Format.model_validate(data)
        entries.append(data)

    if not entries:
        sys.exit("Capture returned zero formats — refusing to write an empty fixture.")

    # Deterministic order for stable diffs.
    entries.sort(key=lambda d: d["format_id"]["id"])

    ids = [e["format_id"]["id"] for e in entries]
    if len(ids) != len(set(ids)):
        sys.exit(f"Capture produced duplicate format_ids: {sorted({i for i in ids if ids.count(i) > 1})}")

    return {
        "schema_version": 2,
        "captured": {
            "image": "adcp-creative-agent",
            "pin": ADCP_PIN,
            "captured_at": datetime.now(UTC).isoformat(),
            "source_url": source_url,
        },
        "agent_url": CANONICAL_AGENT_URL,
        "legacy_id_map": legacy_id_map,
        "formats": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("CREATIVE_AGENT_URL", ""),
        help="URL of the pinned reference creative agent (e.g. scripts/creative-agent-stack.sh url).",
    )
    args = parser.parse_args()

    url = args.url.strip()
    if not url:
        sys.exit("No --url given and CREATIVE_AGENT_URL unset. See scripts/creative-agent-stack.sh url.")
    _check_url_guard(url)

    from src.core.format_cache import CACHE_FILE, load_format_cache

    # Preserve the existing legacy_id_map verbatim — its keys (display_300x250, audio_30s,
    # native_1x1, ...) are deprecated string ids, NOT agent catalog ids, and must not be
    # regenerated from the capture (upgrade_legacy_format_id raises on unknown ids).
    legacy_id_map = load_format_cache()
    if not legacy_id_map:
        sys.exit(
            f"Existing legacy_id_map is empty at {CACHE_FILE}. It must be preserved across refreshes "
            "(it backs upgrade_legacy_format_id). Aborting rather than dropping it."
        )

    print(f"[refresh] capturing formats from {url} (pin {ADCP_PIN})")
    formats = asyncio.run(_capture_formats(url))
    print(f"[refresh] captured {len(formats)} format(s)")

    fixture = _build_fixture(formats, source_url=url, legacy_id_map=legacy_id_map)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(fixture, f, indent=2)
        f.write("\n")

    print(f"[refresh] wrote {len(fixture['formats'])} format(s) to {CACHE_FILE}")
    print("[refresh] review the fixture diff before committing — that diff is the drift gate.")


if __name__ == "__main__":
    main()

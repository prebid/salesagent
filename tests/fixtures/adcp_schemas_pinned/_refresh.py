#!/usr/bin/env python3
"""Refresh the pinned AdCP JSON-schema fixtures used by test_pydantic_schema_alignment
AND by scripts/verify_feature_error_codes.py (the enums/error-code.json vendor).

Source of truth: adcontextprotocol/adcp @ commit
    467fd93d77112baf9e094e18980119edcd3a4d07  (tag v3.1.1, 2026-06-30)

This commit is an INTENTIONAL, frozen reference point matching the project's main
spec/SDK pin (EXPECTED_SPEC_VERSION in tests/unit/test_adcp_spec_version.py,
adcp==X.Y.Z in pyproject.toml — see docs/adcp-spec-version.md). The upstream adcp
repo ships constantly and `/schemas/latest` drifts; we deliberately do NOT track it.
The commit is immutable on GitHub, so the schemas are vendored here (committed) —
spec-derived gates read them offline and never fetch `/schemas/latest`.

salesagent-ulft: this PINNED_SHA previously targeted "AdCP 3.1" (04f59d2d5) while the
rest of the project had already moved to 3.1.1 — a silent drift that made
verify_feature_error_codes.py's --casing-only gate blind to every code added between
3.1 and 3.1.1. tests/unit/test_adcp_spec_version.py now asserts this PINNED_SHA
resolves to the EXPECTED_SPEC_VERSION tag, so it cannot drift silently again.

Layout: schema `$id`/`$ref` namespace is `/schemas/<rest>`; each is written to
`<this dir>/<rest>` (so `/schemas/core/account-ref.json` -> `core/account-ref.json`).

Only the transitive `$ref` closure of the request schemas the test maps is vendored.

To refresh (e.g. to advance the pinned commit — a deliberate, reviewed change,
done in lockstep with an EXPECTED_SPEC_VERSION bump):
    uv run python tests/fixtures/adcp_schemas_pinned/_refresh.py

It reads from a local clone at ~/projects/adcp if present (faster), else GitHub raw.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path

PINNED_SHA = "467fd93d77112baf9e094e18980119edcd3a4d07"
REPO = "adcontextprotocol/adcp"
SRC_PREFIX = "static/schemas/source"  # repo path that backs the `/schemas/...` namespace
LOCAL_CLONE = Path.home() / "projects" / "adcp"
FIXTURE_DIR = Path(__file__).parent

# Request schemas the alignment test maps to Pydantic models, plus response schemas
# whose contract individual tests assert against (the BFS roots).
ROOTS = [
    "/schemas/media-buy/get-products-request.json",
    "/schemas/media-buy/update-media-buy-request.json",
    "/schemas/media-buy/get-media-buy-delivery-request.json",
    "/schemas/creative/sync-creatives-request.json",
    "/schemas/creative/list-creatives-request.json",
    # Response schemas grounding specific contract tests:
    "/schemas/media-buy/create-media-buy-response.json",  # test_adcp_contract F4 (valid_actions/context)
    "/schemas/account/sync-accounts-response.json",  # test_sync_response_account_contract F5 (required fields)
    "/schemas/creative/sync-creatives-response.json",  # PR1399 R3-F2 (creatives required)
    # PR1399 Plan-B: machine-complete RESPONSE_ALIGNMENTS over every implemented response model.
    "/schemas/media-buy/get-products-response.json",
    "/schemas/media-buy/update-media-buy-response.json",
    "/schemas/media-buy/get-media-buy-delivery-response.json",
    "/schemas/creative/get-creative-delivery-response.json",
    "/schemas/creative/list-creatives-response.json",
    "/schemas/creative/list-creative-formats-response.json",
    "/schemas/account/list-accounts-response.json",
    "/schemas/signals/get-signals-response.json",
    "/schemas/signals/activate-signal-response.json",
    # Standalone enum vendored for the BDD error-code guard (verify_feature_error_codes.py).
    # Not in any request/response $ref closure, so it must be listed explicitly to stay pinned.
    "/schemas/enums/error-code.json",
]


def _read_local(rel: str) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(LOCAL_CLONE), "show", f"{PINNED_SHA}:{SRC_PREFIX}{rel}"],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else None


def _read_github(rel: str) -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{PINNED_SHA}/{SRC_PREFIX}{rel}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (pinned host)
        return resp.read().decode()


def fetch(ref: str) -> str:
    rel = ref[len("/schemas") :]  # "/schemas/core/x.json" -> "/core/x.json"
    return _read_local(rel) or _read_github(rel)


def main() -> None:
    seen: set[str] = set()
    stack = list(ROOTS)
    written = 0
    while stack:
        ref = stack.pop().split("#")[0]
        if not ref.startswith("/schemas/") or ref in seen:
            continue
        seen.add(ref)
        body = fetch(ref)
        out = FIXTURE_DIR / ref[len("/schemas/") :]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(json.loads(body), indent=2) + "\n")
        written += 1
        stack.extend(re.findall(r'"\$ref"\s*:\s*"([^"]+)"', body))
    print(f"vendored {written} schema files from {REPO}@{PINNED_SHA[:9]} into {FIXTURE_DIR}")


if __name__ == "__main__":
    main()

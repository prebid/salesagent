#!/usr/bin/env python3
"""Refresh the pinned AdCP error-code enum vendored here.

Source of truth: adcontextprotocol/adcp @ commit
    04f59d2d56d3d77033162c310e99a1188e4eb419  (tag v3.1-04f59d2d5, 2026-05-13)

This commit is an INTENTIONAL, frozen reference point, DELIBERATELY independent
of the installed adcp SDK's own pin (see docs/adcp-spec-version.md "Pinned
schema sources"). It exists ONLY for enums/error-code.json's ``enumMetadata``
``suggestion`` text, read by
tests/unit/test_architecture_error_suggestion_enum_conformance.py. The
installed SDK's error-code enum has grown independently (92+ codes vs. this
fixture's 64) and its ``suggestion`` wording diverges from this fixture's on
4 codes (CREDENTIAL_IN_ARGS, MEDIA_BUY_NOT_FOUND, PACKAGE_NOT_FOUND,
REQUOTE_REQUIRED, verified at migration time) — moving that reader onto the
SDK tree requires first reconciling that divergence (tracked as
github.com/prebid/salesagent/issues/1883; see docs/adcp-spec-version.md),
not a mechanical resolver swap.

Every OTHER pinned-schema consumer — structural request/response shape,
``$ref`` resolution, AND the ``recovery`` half of this same enumMetadata
block (verified byte-identical across all 64 shared codes, so
tests/harness/transport.py and
tests/unit/test_architecture_error_recovery_enum_conformance.py both migrated)
— reads through tests/helpers/pinned_schema.py, which resolves from the
installed SDK's own tree. scripts/verify_feature_error_codes.py also
migrated (it only reads the ``enum`` code list, not enumMetadata content).
This fixture directory no longer vendors any schema-shape files, only this
one enum, kept only for its suggestion-text divergence.

``$id`` convention (GH #1881)
----------------------------
Vendored files keep upstream's ``$id`` **verbatim**: the site-rooted,
VERSION-FREE form ``/schemas/<category>/<name>.json`` (so
``/schemas/enums/error-code.json``, never ``/schemas/3.1.1/enums/...``).
``main()`` refuses to write a file whose fetched ``$id`` is anything else.

Two reasons this is the decision rather than a versioned ``$id``:

- The point of this directory is to preserve ONE frozen upstream artifact for
  byte-comparison. Any field _refresh.py rewrote would no longer be evidence of
  what upstream said.
- The pin here is a SHA, not a spec version. Stamping a version into ``$id``
  would assert a spec identity the commit does not carry — 04f59d2d5 predates
  3.1.1, and the number would silently go stale the moment the SHA advances.

Nothing resolves ``$ref``s against this tree (it holds exactly one leaf enum, and
every ``$ref``-resolving consumer reads the SDK tree via
tests/helpers/pinned_schema.py), so the ``$id`` here is provenance, not routing.
Enforced offline by tests/unit/test_pinned_fixture_id_convention.py.

To refresh (e.g. to advance the pinned commit — a deliberate, reviewed change
that must also re-check the recovery/suggestion divergence against the SDK):
    uv run python tests/fixtures/adcp_schemas_pinned/_refresh.py

It reads from a local clone at ~/projects/adcp if present (faster), else GitHub raw.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path

PINNED_SHA = "04f59d2d56d3d77033162c310e99a1188e4eb419"
REPO = "adcontextprotocol/adcp"
SRC_PREFIX = "static/schemas/source"  # repo path that backs the `/schemas/...` namespace
LOCAL_CLONE = Path.home() / "projects" / "adcp"
FIXTURE_DIR = Path(__file__).parent

# The sole surviving root: error-code enumMetadata (see module docstring for why
# this is a deliberately independent pin, not part of the general schema-shape closure).
ROOTS = [
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


class IdConventionError(RuntimeError):
    """A fetched schema's ``$id`` does not follow the vendoring convention."""


def check_id_convention(ref: str, schema: dict) -> None:
    """Raise unless *schema*'s ``$id`` is the version-free ``ref`` it was fetched as.

    See the module docstring's "$id convention" section. Called before writing,
    so a refresh that would change the vendored ``$id`` aborts loudly instead of
    silently regressing the file and being caught (if at all) by a downstream
    reader much later.
    """
    actual = schema.get("$id")
    if actual != ref:
        raise IdConventionError(
            f"{ref}: upstream $id is {actual!r}, expected {ref!r}. Vendored fixtures keep "
            f"upstream's version-free /schemas/<category>/<name>.json form verbatim (GH #1881). "
            f"If upstream deliberately changed its $id convention, update this script's "
            f"docstring and tests/unit/test_pinned_fixture_id_convention.py in the same "
            f"reviewed change — do not vendor the new form silently."
        )


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
        schema = json.loads(body)
        check_id_convention(ref, schema)
        out = FIXTURE_DIR / ref[len("/schemas/") :]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(schema, indent=2) + "\n")
        written += 1
        stack.extend(re.findall(r'"\$ref"\s*:\s*"([^"]+)"', body))
    print(f"vendored {written} schema files from {REPO}@{PINNED_SHA[:9]} into {FIXTURE_DIR}")


if __name__ == "__main__":
    main()

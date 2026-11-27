#!/usr/bin/env python3
"""Refresh the pinned AdCP legacy webhook-HMAC test vectors used by test_webhook_hmac_vectors.

Source of truth: adcontextprotocol/adcp @ tag v3.1.1
    (commit 467fd93d77112baf9e094e18980119edcd3a4d07)

This is the repo's PINNED AdCP spec version (docs/adcp-spec-version.md). The
upstream adcp repo ships constantly; we deliberately do NOT track its default
branch. The vectors are NOT bundled in the installed `adcp` PyPI package (verified:
absent from adcp==6.6.0's site-packages tree) -- they only exist in the spec
SOURCE repo, so they are vendored here (committed) and the test reads them
offline, mirroring tests/fixtures/adcp_schemas_pinned/_refresh.py's pattern for
the same reason (CI and other machines have no spec checkout at all).

Source path: static/test-vectors/webhook-hmac-sha256.json

To refresh (e.g. to advance the pinned tag -- a deliberate, reviewed change,
done in lockstep with docs/adcp-spec-version.md):
    uv run python tests/fixtures/adcp_webhook_vectors_pinned/_refresh.py

Where the spec tree lives is not decided here: storyboard_spec.adcp_home() owns
that ($ADCP_HOME, then the in-repo release bundle, then a personal clone), the
same seam every other pinned-fixture refresher and storyboard consumer calls.
If that tree can serve the pinned commit, it is read locally (faster); otherwise
this falls back to GitHub raw at the same SHA, so a machine with no tree at all
still refreshes correctly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import storyboard_spec  # noqa: E402

PINNED_SHA = "467fd93d77112baf9e094e18980119edcd3a4d07"  # tag v3.1.1
REPO = "adcontextprotocol/adcp"
SRC_PATH = "static/test-vectors/webhook-hmac-sha256.json"
FIXTURE_DIR = Path(__file__).parent
OUT_FILE = FIXTURE_DIR / "webhook-hmac-sha256.json"


def _read_local() -> str | None:
    """The vectors out of the pinned tree, when that tree can serve this commit.

    Only a git checkout can: the file lives under ``static/``, which the release
    bundle does not ship (it carries ``compliance/`` and ``schemas/`` only) and
    which has no history to ask for PINNED_SHA. So a resolved tree that is a
    bundle -- or no tree at all -- simply misses here, exactly as an absent clone
    did before, and main() takes the network path at the same pinned SHA.

    adcp_home() resolves a version to find the bundle candidate, and that
    resolution raises when the SDK pin and docs/adcp-spec-version.md disagree.
    That disagreement is real and worth its own fix, but it says nothing about
    the vectors at PINNED_SHA, so it degrades to the network path rather than
    taking the refresher down with it.
    """
    try:
        tree = storyboard_spec.adcp_home(REPO_ROOT)
    except storyboard_spec.StoryboardAuditError:
        return None
    r = subprocess.run(
        ["git", "-C", str(tree), "show", f"{PINNED_SHA}:{SRC_PATH}"],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else None


def _read_github() -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{PINNED_SHA}/{SRC_PATH}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (pinned host)
        return resp.read().decode()


def main() -> None:
    body = _read_local() or _read_github()
    OUT_FILE.write_text(json.dumps(json.loads(body), indent=2) + "\n", encoding="utf-8")
    print(f"vendored {SRC_PATH} from {REPO}@{PINNED_SHA[:9]} into {OUT_FILE}")


if __name__ == "__main__":
    main()

"""Guard: the TS conformance runner must target the SAME AdCP version this repo pins.

The storyboard job grades us with `@adcp/sdk`'s runner. That SDK declares which
AdCP release its CLIENT is built for in its own ``package.json`` (``adcp_version``),
independently of the ``--compliance-version`` / ``--compliance-dir`` we hand it.

Those two can disagree silently, and have more than once: a client one release
behind the storyboards it grades fails nothing, because the storyboards and schemas
are the right version and only the code driving them is not.

Pointing a mismatched client at the right storyboards is exactly the kind of
"looks measured, isn't" result this whole module exists to prevent, so it gets a
guard rather than a comment. The guard asserts equality and offers no tolerance:
a divergence that is allowed is a divergence nobody re-examines.

Lives in tests/storyboard/ (not tests/unit/) because it reads the INSTALLED
package: the assertion is about what will actually run, not about what a manifest
claims. That directory is only meaningfully collected where the runner's npm deps
exist — the same precondition the conformance module itself has.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SDK_PACKAGE_JSON = Path(__file__).parent / "runner" / "node_modules" / "@adcp" / "sdk" / "package.json"

sys.path.insert(0, str(_REPO_ROOT))

from scripts.audit import storyboard_spec  # noqa: E402

#: WHEN BUMPING THE RUNNER, it must satisfy BOTH or the storyboard leg stops measuring what
#: it claims to:
#:
#:   * declare this repo's pin in its own ``adcp_version`` (``npm view @adcp/sdk@<v>
#:     adcp_version``), which is what this guard asserts;
#:   * be on a line carrying the A2A request-signing dispatch (adcp-client#2967). Without it
#:     every ``signed_requests`` vector on the A2A card is ungradable, so the newest release
#:     is not automatically the right one. Check it by comparing the candidate's
#:     ``dist/lib/testing/storyboard/request-signing/index.d.ts`` exports against the current
#:     runner's: none may disappear, and ``a2a-dispatch.*`` and ``a2a-operation-resolution.*``
#:     must both be present. Also check that every CLI flag ``test_storyboard_conformance``
#:     passes still exists in the candidate's CLI.
#:
#: There is deliberately no escape hatch for a runner that satisfies only the first: a
#: tolerated mismatch here is indistinguishable from a measured result, which is the failure
#: this module exists to prevent.


def test_runner_sdk_targets_the_pinned_adcp_version() -> None:
    """The runner's adcp_version matches the repo's pin, or is the one accepted divergence.

    Not an xfail. An xfail would pass whatever the two versions said, including a second,
    unexamined divergence appearing beside this one. Pinning the triple keeps the assertion
    real: the known state passes, and any movement -- a runner upgrade, a backport landing, a
    deliberate pin bump -- fails and asks for a decision.
    """
    if not _SDK_PACKAGE_JSON.is_file():
        pytest.fail(
            f"conformance runner SDK not installed at {_SDK_PACKAGE_JSON}. "
            "Run `npm ci` in tests/storyboard/runner/ — this guard asserts on the "
            "INSTALLED package deliberately, so a missing install is a real failure "
            "and not something to skip past."
        )

    sdk = json.loads(_SDK_PACKAGE_JSON.read_text(encoding="utf-8"))
    sdk_targets = sdk.get("adcp_version")
    repo_pins = storyboard_spec.pinned_version(_REPO_ROOT)

    assert sdk_targets == repo_pins, (
        f"conformance runner targets AdCP {sdk_targets!r} but this repo pins {repo_pins!r}.\n"
        f"  installed: @adcp/sdk@{sdk.get('version')}\n"
        "Grading with a client built for a different release measures the wrong contract, "
        "however right the --compliance-version happens to be. Pin an @adcp/sdk whose "
        "adcp_version matches (`npm view @adcp/sdk@<v> adcp_version`) and which is on a line "
        "carrying the A2A request-signing dispatch, or move the repo's pin deliberately per "
        "docs/adcp-spec-version.md."
    )

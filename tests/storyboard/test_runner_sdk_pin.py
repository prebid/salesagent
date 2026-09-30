"""Guard: the TS conformance runner must target the SAME AdCP version this repo pins.

The storyboard job grades us with `@adcp/sdk`'s runner. That SDK declares which
AdCP release its CLIENT is built for in its own ``package.json`` (``adcp_version``),
independently of the ``--compliance-version`` / ``--compliance-dir`` we hand it.

Those two can disagree silently, and did: `@adcp/sdk@9.3.0` declares **3.1.0**
while this repo pins **3.1.1**, so for its whole life the baseline was measured by
a client one release behind the storyboards it was grading against. Nothing failed
— the storyboards and schemas were the right version, only the code driving them
was not. `@adcp/sdk@11.0.0` is the newest release still on 3.1.1 (11.1.0 moves to
3.1.2).

Pointing a mismatched client at the right storyboards is exactly the kind of
"looks measured, isn't" result this whole module exists to prevent, so it gets a
guard rather than a comment.

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

#: The one divergence this repo accepts, and why. Keyed ``(sdk version, sdk adcp_version,
#: repo pin)`` so a move on ANY of the three fails and asks for a decision, rather than the
#: divergence widening quietly.
#:
#: THE REPO'S PIN IS NOT WHAT IS WRONG. 3.1.1 is the contract this seller implements. The
#: runner's ``adcp_version`` is fixed by its npm release, so the client's contract and the
#: vectors it grades against are one choice upstream rather than two. This repo already passes
#: ``--compliance-version`` with the pinned version, and it does not move the client -- the
#: assertion message below says as much itself.
#:
#: 14.0.0-rc.42 is not a free choice. Grading both protocol surfaces requires the 14 line:
#: it is the only line carrying the A2A request-signing dispatch (adcp-client#2967, merged
#: 2026-09-20), without which every ``signed_requests`` vector on the A2A card is ungradable.
#: The 14 line declares 3.2.0-rc.4.
#:
#: THE DIVERGENCE WIDENED AT THIS BUMP, from 3.2.0-rc.1 to 3.2.0-rc.4, and that is recorded
#: rather than smoothed over: the runner line we need moved further from our pin, we did not
#: choose to move away from it. The measured conformance score did not change across the bump
#: (see below), which is what makes the wider divergence acceptable rather than merely tolerated.
#:
#: adcontextprotocol/adcp-client#2950 -- "``--compliance-version`` does not move the client
#: contract, and no maintained line declares 3.1.1" -- is now CLOSED, and it is worth being
#: precise about what that did and did not give us. It shipped ``@adcp/sdk@13.1.0`` on the
#: ``adcp-3.1`` tag, which declares ``adcp_version: 3.1.20``. Closer than 3.2.0-rc.4, still not
#: 3.1.1, and it predates the A2A dispatch above -- so it is not a retirement path for this
#: divergence today. Retire the pin when a line both declares this repo's pin and carries the
#: A2A signing dispatch, or when the runner separates the client contract from the vectors it
#: grades against.
_ACCEPTED_DIVERGENCE = ("14.0.0-rc.42", "3.2.0-rc.4", "3.1.1")


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

    observed = (sdk.get("version"), sdk_targets, repo_pins)
    if observed == _ACCEPTED_DIVERGENCE:
        return

    assert sdk_targets == repo_pins, (
        f"conformance runner targets AdCP {sdk_targets!r} but this repo pins {repo_pins!r}.\n"
        f"  installed: @adcp/sdk@{sdk.get('version')}\n"
        f"  accepted divergence: {_ACCEPTED_DIVERGENCE}\n"
        "Grading with a client built for a different release measures the wrong contract, "
        "however right the --compliance-version happens to be. This triple is not the accepted "
        "one, so something moved: a runner upgrade, a pin bump, or a backport landing. Either "
        "pin an @adcp/sdk whose adcp_version matches (check `npm view @adcp/sdk@<v> "
        "adcp_version`), move the repo's pin deliberately per docs/adcp-spec-version.md, or "
        "update _ACCEPTED_DIVERGENCE above with the reason the new pair is acceptable."
    )

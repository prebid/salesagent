"""The tree COMPLIES with every extra ruff config, not merely that each ban would fire.

``tests/unit/test_ruff_boundary_bans.py`` proves the bans work: it shells out to the real
ruff with the real config against a SYNTHETIC PROBE FILE, positive case and negative case.
That is a different assertion from this one, and the gap between them was live. Measured:
that module passed, 94 tests green, on a tip where ``ruff --config ruff-environment.toml
src/ scripts/`` reported ``Found 1 error`` -- ``scripts/audit/run_identity.py`` read
``dict(os.environ)`` and nothing noticed (salesagent-ruhyl).

Nothing automated graded the tree against these configs. ``run_all_tests.sh`` does not
invoke ``make quality`` or ``make quality-ci``; ``tox -e quality`` runs the mypy and pylint
ratchet counters and the ledger check, not these; the cassini box run execs the runner and
inherits that; and ``.github/workflows/ci.yml`` fires on push/pull_request to main or
develop while this workflow merges locally and never pushes. So a violation of any of the
five sat until somebody happened to run ``make quality`` by hand (salesagent-oogtk).

This is the same hole, with the same remedy, that the ratchet counters already had:
``session-completion.md`` records that ``make quality`` being *documented* was their entire
enforcement until they were moved into suites that actually execute. These configs were not
moved with them.

THE CONFIGS AND THEIR FLAGS ARE PARSED FROM THE MAKEFILE, never restated here. The Makefile
is where the gate is defined, so a sixth config added there is graded by this test the day
it is added, and a changed flag is picked up rather than silently diverging. ``--ignore-noqa``
on the egress line is exactly the kind of difference a hand-copied list loses.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_MAKEFILE = _REPO / "Makefile"

#: The lowest number of configs that means the parse FOUND something. A regex that matches
#: nothing passes every test it feeds, so the count is asserted before the configs are used
#: -- otherwise renaming the Makefile recipe would turn this module green and blind.
_MINIMUM_EXPECTED_CONFIGS = 5


def _ruff_config_invocations() -> list[list[str]]:
    """Every ``ruff check --config <x>.toml ...`` recipe line in the Makefile, as argv.

    Read from the Makefile rather than declared, for the reason in the module docstring.
    Leading tabs and the ``uv run`` prefix are dropped: the test runs ruff through the
    interpreter already running it, so the environment is the one pytest resolved.
    """
    invocations = []
    for raw in _MAKEFILE.read_text().splitlines():
        line = raw.strip()
        if "ruff check --config" not in line or line.startswith("#"):
            continue
        tokens = shlex.split(line)
        if tokens[:2] == ["uv", "run"]:
            tokens = tokens[2:]
        assert tokens[0] == "ruff", f"unexpected ruff recipe shape in the Makefile: {line}"
        invocations.append(tokens[1:])
    return invocations


_INVOCATIONS = _ruff_config_invocations()


def _config_name(argv: list[str]) -> str:
    return argv[argv.index("--config") + 1]


def test_the_makefile_parse_found_the_config_lines() -> None:
    """Guards the parse itself: a derivation that matches nothing grades nothing."""
    assert len(_INVOCATIONS) >= _MINIMUM_EXPECTED_CONFIGS, (
        f"parsed only {len(_INVOCATIONS)} `ruff check --config` lines from {_MAKEFILE}, "
        f"expected at least {_MINIMUM_EXPECTED_CONFIGS}. Either the recipe moved and this "
        "parse needs updating, or the gate lost configs -- both are failures, and a silent "
        "pass here would hide either one."
    )


@pytest.mark.parametrize("argv", _INVOCATIONS, ids=_config_name)
def test_src_and_scripts_comply_with_the_config(argv: list[str]) -> None:
    """``src/`` and ``scripts/`` carry zero violations of this config.

    A hard zero, not a ratchet: every one of these was measured at zero when this test was
    written, so there is no backlog to shrink and no reason to admit one. A new violation is
    a build failure at the commit that introduces it, which is the whole point.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ruff", *argv],
        capture_output=True,
        text=True,
        cwd=_REPO,
        check=False,
    )
    assert result.returncode == 0, (
        f"{_config_name(argv)} is violated by the tree.\n"
        f"Reproduce with the Makefile's own line:\n"
        f"  uv run ruff {shlex.join(argv)}\n\n"
        f"{result.stdout}{result.stderr}"
    )

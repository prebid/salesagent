"""Executable meta-test for the test-flag ast-grep ban.

The ENFORCEMENT is the rule, not this module. It lives at
``.ast-grep/rules/test-flag-never-reaches-production.yml``, is discovered through
``sgconfig.yml``'s ``ruleDirs``, and runs on the ``make quality`` /
``make quality-ci`` ``ast-grep scan`` line. This module stands to it exactly as
``tests/unit/test_ast_grep_credential_header_ban.py`` stands to the credential-header
ban: it proves the ban FIRES, that it is BUILD-FAILING, and that its one exemption is
live rather than prose. It deliberately does not re-implement the detection — a second
AST walk written here would be a divergent copy of the rule, and the rule is the gate.

WHAT THE RULE REFUSES. Any read of ``adcp_testing`` under ``src/``. The flag says a
suite is running and nothing about the seller being served, so a production path that
branches on it serves a different seller under test than in deployment — which makes the
suite's verdict conditional on the suite being what ran it. CLAUDE.md pattern 11 carries
the reasoning and the two fixes, neither of which is the fork.

THE EXEMPTION. ``src/core/config.py``, because it declares the field. Case (d) below is
what keeps that from becoming a hiding place: the file must actually contain reads, and
the properties doing the reading are pinned shrink-only by
``tests/unit/test_test_flag_properties_only_shrink.py``. GH #2255 owns emptying them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import repo_root, stage_ast_grep_root

RULE_ID = "test-flag-never-reaches-production"
RULE_FILE = f".ast-grep/rules/{RULE_ID}.yml"

#: A production read of the flag, in the spelling every real site uses: an attribute chain
#: off a settings object. Written under ``src/`` in a staged project root so the rule's own
#: ``files:`` scoping applies to it exactly as it would to a real module.
_VIOLATING_SOURCE = """\
from src.core.config import get_settings


def choose_behavior() -> str:
    if get_settings().testing.adcp_testing:
        return "the test answer"
    return "the real answer"
"""

#: The same decision taken on a real input. This is what the rule steers toward, so it
#: must NOT match — a negative case that fails would make the rule unusable.
_CONFORMING_SOURCE = """\
from src.core.config import get_settings


def choose_behavior() -> str:
    if get_settings().adapters.mock_adapter_selected:
        return "the mock adapter's answer"
    return "the real answer"
"""


def _ast_grep_bin() -> Path:
    """The venv-local ast-grep — what ``uv run ast-grep`` resolves to, and nothing else.

    Resolved next to ``sys.executable`` rather than through PATH for the reason the
    credential-header module records: a PATH lookup finds a homebrew binary that exists on
    a developer machine and not in CI, so this module would pass locally while the gate
    line it grades failed there. Absence FAILS rather than skips, for the same reason.
    """
    candidate = Path(sys.executable).parent / "ast-grep"
    assert candidate.exists(), (
        f"{candidate} does not exist: ast-grep is not installed in this environment's venv. "
        "Declare `ast-grep-cli` in the pyproject dev group so `uv run ast-grep` resolves."
    )
    return candidate


def _scan(*paths: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real ast-grep with the real rule file over *paths*, from the repo root or a staged *cwd*."""
    return subprocess.run(
        [str(_ast_grep_bin()), "scan", "--rule", RULE_FILE, *paths, "--json=compact"],
        capture_output=True,
        text=True,
        cwd=cwd or repo_root(),
        check=False,
    )


def _matches(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    """Parse ast-grep's compact JSON, refusing any exit code that means it did not run.

    The anti-vacuity gate for every case here. ast-grep exits 6 when ``--rule`` names a
    file it cannot read, printing nothing on stdout — so an absent or unparseable rule
    parses as "no matches" and every negative-shaped assertion below would pass while
    grading nothing. Only 0 (clean) and 1 (violations found) mean the rule executed.
    """
    assert proc.returncode in (0, 1), (
        f"ast-grep exited {proc.returncode}, which means it did not run the rule "
        f"(6 = rule file unreadable, 3 = filter matched no rule).\n"
        f"stdout={proc.stdout[:400]!r}\nstderr={proc.stderr[:400]!r}"
    )
    # ``--json=compact`` emits ONE JSON array, not one object per line: a clean scan prints
    # "[]". Parsing line-by-line made a clean scan read as a single empty-list "match".
    return json.loads(proc.stdout or "[]")


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """A staged project root this ONE case writes its probe modules into, under ``src/``.

    Under ``src/`` deliberately: the rule is scoped ``files: ["src/**/*.py"]``, so a probe
    outside that glob would prove nothing about a production read — the scan would report
    clean whether the rule worked or not. But under a STAGED root (``stage_ast_grep_root``
    copies the real config and rules next to it), never the live ``src/``: there the probe
    is a file every other guard's scan can list and then fail to read once this case
    deletes it, which under xdist is a race between workers.
    """
    root = stage_ast_grep_root(tmp_path)
    (root / "src" / "_ast_grep_probe").mkdir(parents=True)
    return root


def test_a_production_read_of_the_flag_matches(planted: Path) -> None:
    """(a) POSITIVE — the rule sees the spelling every real site uses."""
    probe = planted / "src" / "_ast_grep_probe" / "reads_the_flag.py"
    probe.write_text(_VIOLATING_SOURCE, encoding="utf-8")

    found = _matches(_scan(str(probe.relative_to(planted)), cwd=planted))

    assert len(found) == 1, f"expected exactly one match for one read, got {len(found)}: {found}"
    assert found[0]["ruleId"] == RULE_ID
    assert "adcp_testing" in found[0]["text"]


def test_the_match_is_build_failing(planted: Path) -> None:
    """(b) BUILD-FAILING — a violation exits non-zero.

    Without ``severity: error`` ast-grep prints its matches and exits 0, and a rule that
    reports while passing is the dormancy every guard here exists to remove.
    """
    probe = planted / "src" / "_ast_grep_probe" / "reads_the_flag.py"
    probe.write_text(_VIOLATING_SOURCE, encoding="utf-8")

    assert _scan(str(probe.relative_to(planted)), cwd=planted).returncode == 1


def test_the_same_decision_on_a_real_input_is_clean(planted: Path) -> None:
    """(c) NEGATIVE — reading a settings field a deployment can set is the fix, not a finding."""
    probe = planted / "src" / "_ast_grep_probe" / "reads_a_real_input.py"
    probe.write_text(_CONFORMING_SOURCE, encoding="utf-8")

    proc = _scan(str(probe.relative_to(planted)), cwd=planted)

    assert _matches(proc) == []
    assert proc.returncode == 0


def test_the_config_exemption_is_live_not_prose() -> None:
    """(d) EXEMPTION LIVE — config.py is exempt AND actually contains reads.

    An exemption for a file that no longer violates is a stale entry pretending to be a
    decision. This asserts the file still holds reads (so the exemption has a subject) and
    that the rule nonetheless reports nothing for it (so the exemption is in force).
    """
    config = repo_root() / "src" / "core" / "config.py"
    assert "adcp_testing" in config.read_text(encoding="utf-8"), (
        "src/core/config.py no longer reads adcp_testing, so its `ignores:` entry in "
        f"{RULE_FILE} has no subject and must be deleted — with it gone the ban needs no "
        "exemption at all, which is the outcome GH #2255 is for."
    )

    proc = _scan("src/core/config.py")

    assert _matches(proc) == [], "the config.py exemption is not in force"
    assert proc.returncode == 0


# There is no case scanning the live tree here. `make quality-ci` line 29 IS that scan
# (`uv run ast-grep scan --config sgconfig.yml`), it covers every rule at once, and it
# fails the build — so a pytest case re-running it over `src/` graded nothing the gate
# did not already grade, and it collided with this module's own probe back when cases
# (a)-(c) planted it under the live `src/`. They plant it in a staged root now, but a
# tree scan here would still grade nothing the gate does not.

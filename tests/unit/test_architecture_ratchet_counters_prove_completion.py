"""Guard: a ratchet counter that shells out must prove the tool FINISHED.

``run_count_ratchet`` writes the baseline on an ORDINARY run whenever the count
dropped and nothing regressed. That auto-lower is the ratchet working, and it is
why a short count is worse than a wrong one: a pylint or mypy process that dies
partway through ``src/`` returns fewer hits, the smaller number reads as
progress, and a ceiling nobody chose is committed permanently. Every honest run
afterwards fails with a message whose only documented remedy — raise the
baseline — is forbidden by policy.

The upstream-ceiling probe (``test_architecture_ratchet_hooks_use_driver.py``)
does not reach this. It is one-sided by construction: it refuses values ABOVE
the ceiling, and a short count is spuriously BELOW it, so ``min(baseline,
current)`` passes the probe on the way to the write. The probe stops a raise;
nothing stopped a fabricated drop (salesagent-b341x.20).

Detecting a short count after the fact is not possible — it is a smaller
integer and looks like exactly what success looks like. So the property here is
structural: every shell-out goes through the ONE helper that demands a
completion proof, and that helper has no way to be called without one.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import load_hook_module
from tests.unit.test_architecture_ratchet_hooks_use_driver import RATCHET_HOOKS

_HOOKS = Path(__file__).resolve().parents[2] / ".pre-commit-hooks"

#: The seam itself: ``count_ratchet`` is where subprocess use is supposed to
#: live, both for counting tools and for the git calls behind the ceiling probe.
_DRIVER = "count_ratchet"

#: Ways to start a process. A counter reaching for any of these directly is
#: choosing its own completion contract, which is the state this guard exists to
#: prevent — ``check_code_duplication`` did exactly that and got it wrong
#: (``returncode & 33 and count == 0``: a crash that had already emitted
#: findings satisfied neither half).
_PROCESS_STARTERS = frozenset({"run", "call", "check_call", "check_output", "Popen", "popen", "system"})

#: ``run_counting_tool`` parameters that carry the proof. Neither may acquire a
#: default: a default is a way to shell out without saying how completion is
#: recognised, which is the hole, one level up.
_REQUIRED_PROOF_PARAMS = ("accepts_returncode", "completion_marker")


def _module(name: str) -> ast.Module:
    return ast.parse((_HOOKS / f"{name}.py").read_text(encoding="utf-8"))


def _process_starts(tree: ast.Module) -> list[str]:
    """``subprocess.run``-style calls, by dotted name, in source order."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _PROCESS_STARTERS:
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in ("subprocess", "os"):
            found.append(f"{owner.id}.{node.func.attr}")
    return found


def test_no_ratchet_hook_starts_a_process_of_its_own() -> None:
    """Every count that shells out routes through the proving helper.

    This is the rule, not a description of today's hooks: the next counter that
    reaches for ``subprocess.run`` fails here until it goes through
    ``run_counting_tool`` and names its completion marker.
    """
    offenders = sorted(
        (name, _process_starts(_module(name))) for name in RATCHET_HOOKS if _process_starts(_module(name))
    )

    assert offenders == [], (
        f"These ratchet hooks start a process directly: {offenders}. "
        "Call count_ratchet.run_counting_tool instead — a hand-rolled subprocess "
        "call decides for itself what 'the tool finished' means, and the one that "
        "did (check_code_duplication) accepted a crashed pylint's partial tally "
        "and wrote it to the baseline."
    )


def test_a_hook_that_shells_out_must_supply_both_proofs() -> None:
    """Every ``run_counting_tool`` call passes both, explicitly, by keyword."""
    missing: list[tuple[str, str]] = []
    for name in RATCHET_HOOKS:
        for node in ast.walk(_module(name)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "run_counting_tool":
                continue
            supplied = {kw.arg for kw in node.keywords}
            absent = [param for param in _REQUIRED_PROOF_PARAMS if param not in supplied]
            if absent:
                missing.append((name, ", ".join(absent)))

    assert missing == [], f"These run_counting_tool calls omit a completion proof: {missing}"


def test_the_proof_parameters_have_no_default() -> None:
    """A default would be a way to shell out without proving completion.

    The old signature took ``has_findings``, whose contract was "exit 1 is fine
    as long as SOMETHING was found" — under which a crash that had already
    emitted findings was indistinguishable from a clean run. Making the proof
    unavoidable is the difference between a bug fixed and a bug made
    unrepresentable.
    """
    signature = inspect.signature(load_hook_module(_DRIVER).run_counting_tool)

    for param in _REQUIRED_PROOF_PARAMS:
        assert param in signature.parameters, f"run_counting_tool no longer takes {param}"
        detail = signature.parameters[param]
        assert detail.kind is inspect.Parameter.KEYWORD_ONLY, f"{param} must be keyword-only"
        assert detail.default is inspect.Parameter.empty, (
            f"run_counting_tool.{param} acquired the default {detail.default!r}. "
            "A counter must not be able to shell out without saying how the "
            "tool's completion is recognised."
        )


# ── Provenance: which tool measured it ──────────────────────────────────────
#
# salesagent-v03pe.1, the residual .20 deliberately left out of scope. After .20
# each shell-out counter echoes its completion evidence, so the DENOMINATOR is
# stated: "Found 194 errors in 31 files (checked 319 source files)". The
# INSTRUMENT was not. ADR-009's caveat lives in check_mypy_untyped_defs_count's
# own header -- "counts drift with mypy / plugin versions" -- and nowhere a run
# can see, so a count that moved because a dependency bumped is indistinguishable
# in the output from one that moved because the source did.
#
# The version is NOT written into the baseline file. That would make a bump a
# visible diff, which is the appeal, but it would equally make every contributor
# on a different patch release produce one -- and the driver's upstream-ceiling
# probe reads baselines across git refs, so a version key in them is a new way
# for the ratchet to disagree with itself about what a baseline says.


def test_the_version_probe_names_the_same_tool_that_produced_the_count() -> None:
    """Derived from the counting command, so it cannot name a different tool.

    A ``version_cmd=`` parameter beside ``cmd`` would be a second place to name the
    tool, and a provenance line naming a DIFFERENT tool than the one that produced
    the number is worse than no line at all.
    """
    driver = load_hook_module(_DRIVER)

    module_form = ["/usr/bin/python3", "-m", "mypy", "src", "--check-untyped-defs"]
    assert driver.version_probe(module_form) == ["/usr/bin/python3", "-m", "mypy", "--version"]

    # ruff's counting command carries a subcommand; the probe must not inherit it.
    ruff_form = ["/usr/bin/python3", "-m", "ruff", "check", "src", "--output-format=json"]
    assert driver.version_probe(ruff_form) == ["/usr/bin/python3", "-m", "ruff", "--version"]

    assert driver.version_probe(["/usr/local/bin/pylint", "src"]) == ["/usr/local/bin/pylint", "--version"]


def test_a_failed_version_probe_is_named_rather_than_dropped(tmp_path: Path) -> None:
    """Provenance never refuses the run, and never goes silently missing.

    No number depends on the version, so refusing would block a commit over a
    cosmetic failure -- but a line that quietly dropped it would be the same defect
    one size down: an output that stops saying what it could not establish.
    """
    driver = load_hook_module(_DRIVER)

    # The executable does not exist at all -> OSError from the probe.
    assert driver._tool_version(["definitely-not-a-real-tool-xyz"], cwd=tmp_path).startswith("version UNKNOWN")

    # The tool exists but has no --version (probe exits non-zero). Written in the
    # `-m <module>` form every counter here uses, so it goes through the real
    # derivation rather than the bare-executable fallback.
    assert driver._tool_version(
        [sys.executable, "-m", "definitely_not_a_real_module_xyz", "src"], cwd=tmp_path
    ).startswith("version UNKNOWN (exit ")

    # Exits 0 and prints nothing. A bare executable, so `--version` reaches IT and
    # not the interpreter running it.
    stub = tmp_path / "quiet_tool"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    assert driver._tool_version([str(stub), "src"], cwd=tmp_path) == "version UNKNOWN (no output)"


def test_the_evidence_line_carries_the_instrument(capsys, monkeypatch, tmp_path: Path) -> None:
    """A run states what it measured AND what measured it."""
    driver = load_hook_module(_DRIVER)
    monkeypatch.setattr(driver, "_tool_version", lambda _cmd, *, cwd: "faketool 9.9.9")

    driver.run_counting_tool(
        [sys.executable, "-c", "print('DONE: 7 things')"],
        cwd=tmp_path,
        label="faketool",
        accepts_returncode=(0).__eq__,
        completion_marker=lambda out: out.strip() or None,
    )

    printed = capsys.readouterr().out
    assert "faketool [faketool 9.9.9]: completed — DONE: 7 things" in printed, (
        f"the evidence line must name the instrument beside the denominator, got: {printed!r}"
    )


def test_a_refusal_names_the_instrument_too(capsys, monkeypatch, tmp_path: Path) -> None:
    """A refusal is the message most likely to be read after a dependency bump."""
    driver = load_hook_module(_DRIVER)
    monkeypatch.setattr(driver, "_tool_version", lambda _cmd, *, cwd: "faketool 9.9.9")

    with pytest.raises(SystemExit):
        driver.run_counting_tool(
            [sys.executable, "-c", "raise SystemExit(2)"],
            cwd=tmp_path,
            label="faketool",
            accepts_returncode=(0).__eq__,
            completion_marker=lambda out: out.strip() or None,
        )

    assert "faketool [faketool 9.9.9] did not run to completion" in capsys.readouterr().err

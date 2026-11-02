"""Structural guard: the binding sweep's buckets are earned, and all of them exist.

Two defects in ``scripts/audit/storyboard_binding_sweep.py``, both instances of the
shape salesagent-v03pe collects — an instrument answering a question it cannot answer.

**A default that means "fine".** ``Binding.bucket`` defaulted to ``"A"`` with the
comment ``# A ok``, and ``audit()`` only ever moved a scenario off A when a check
FIRED. Every check the sweep could not RUN therefore rendered as a pass: a scenario
whose ``@source`` names no ``phase=`` gets ``phase_is_graded() -> None``, no grading
check at all, and was published as verified. Measured at the 3.1.1 pin, one of 21
scenarios was in that state — ``T-UC-005-storyboard-baseline-format-id-object-shape``,
whose only ``@source`` is a JSON schema path, so no storyboard check ever ran for it.

**Two buckets nothing assigned.** The declared vocabulary was "A ok · B wrong path ·
C wrong tag · D under-asserts · E prod-blocked". ``audit()`` assigned B and C.
D and E lived in a comment and in the rendered legend, so the published histogram
reported zero under-asserting and zero production-blocked scenarios — two claims this
sweep has no data for. They are deleted, and this module derives the assignable set
from the code so a declared-but-unassigned bucket cannot come back.

**Order dependence.** One branch used ``max(binding.bucket, "B")`` and every other
plain assignment, so a scenario that earned C and then hit the phase-mismatch branch
was downgraded to B. Which bucket a scenario landed in depended on the order the
checks happened to run in.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import storyboard_binding_sweep as sweep  # noqa: E402

SWEEP_SOURCE = Path(sweep.__file__)


def _binding(**kwargs) -> sweep.Binding:
    return sweep.Binding(feature="f.feature", line=1, identifier="T-X", tags=[], title="t", **kwargs)


def _buckets_audit_can_assign() -> set[str]:
    """Every bucket ``audit()`` can actually produce, read from the module's AST.

    Derived, not listed: a hand-written list of "buckets the code assigns" is the same
    artifact as the comment that claimed D and E, and would rot the same way. The two
    assigners are ``Binding.flag(BUCKET_*)`` and ``Binding.not_measured``, which is
    ``BUCKET_UNVERIFIED`` by construction.
    """
    tree = ast.parse(SWEEP_SOURCE.read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "not_measured":
            assigned.add(sweep.BUCKET_UNVERIFIED)
        if node.func.attr == "flag":
            assigned |= {
                getattr(sweep, arg.id)
                for arg in node.args
                if isinstance(arg, ast.Name) and arg.id.startswith("BUCKET_")
            }
    return assigned


def test_every_declared_bucket_is_assigned_by_some_code_path() -> None:
    """A bucket in the legend that no branch assigns is a claim the sweep cannot make.

    ``D under-asserts`` and ``E prod-blocked`` were exactly that, and the histogram
    published their zeros as findings.
    """
    declared = set(sweep.BUCKET_LEGEND)
    unassignable = sorted(declared - _buckets_audit_can_assign())
    assert unassignable == [], (
        f"{len(unassignable)} bucket(s) are declared in BUCKET_LEGEND and assigned by no code path: "
        f"{unassignable}. Every scenario's count for them is a structural zero, which reads as a "
        "finding. Implement the bucket, or delete it from the vocabulary."
    )


def test_the_verified_bucket_requires_a_check_to_have_run() -> None:
    """``A`` is a positive verdict, not the absence of a negative one."""
    ran_nothing = _binding().finalize()
    assert ran_nothing.bucket == sweep.BUCKET_UNVERIFIED, (
        "a scenario for which no grading check ran must not be reported as verified — "
        "that is the `bucket = 'A'  # A ok` default this guard exists to keep dead"
    )
    assert ran_nothing.unverified, "the unrunnable check must be NAMED, not merely counted"

    ran_one = _binding(checks_run=1).finalize()
    assert ran_one.bucket == sweep.BUCKET_VERIFIED


def test_an_unrunnable_check_is_never_folded_into_a_passing_side() -> None:
    """NOT MEASURED is its own verdict, outranking verified."""
    binding = _binding(checks_run=3)
    binding.not_measured("@source declares no phase=")
    assert binding.finalize().bucket == sweep.BUCKET_UNVERIFIED, (
        "a scenario that passed three checks and could not run a fourth is not verified — "
        "the fourth is a hole in the verdict, not an absence of a finding"
    )


def test_the_bucket_does_not_depend_on_the_order_the_checks_ran() -> None:
    """Worst-wins, both ways round. ``max(bucket, "B")`` in one branch and plain
    assignment in the others made C-then-B report B."""
    forwards = _binding(checks_run=1)
    forwards.flag(sweep.BUCKET_TAG_UNJUSTIFIED)
    forwards.flag(sweep.BUCKET_WRONG_SOURCE)

    backwards = _binding(checks_run=1)
    backwards.flag(sweep.BUCKET_WRONG_SOURCE)
    backwards.flag(sweep.BUCKET_TAG_UNJUSTIFIED)

    assert forwards.finalize().bucket == backwards.finalize().bucket == sweep.BUCKET_TAG_UNJUSTIFIED, (
        f"C-then-B gave {forwards.bucket!r} and B-then-C gave {backwards.bucket!r}. The bucket a "
        "scenario lands in must not depend on the order the checks happen to run in."
    )


@pytest.mark.parametrize("worse,better", [("U", "A"), ("B", "U"), ("C", "B")])
def test_the_bucket_ranking_is_strictly_worsening(worse: str, better: str) -> None:
    """The legend's declaration order IS the severity order — one owner, not two."""
    assert sweep._BUCKET_RANK[worse] > sweep._BUCKET_RANK[better]


def test_the_published_histogram_names_every_bucket_including_the_zeros() -> None:
    """A histogram of only the buckets that occurred cannot be told apart from a
    vocabulary that has shrunk — which is how D and E disappearing would have looked."""
    from tests.unit._storyboard_guard_env import ADCP_HOME, BUNDLE_RESOLVED

    if not BUNDLE_RESOLVED:
        pytest.skip("pinned AdCP compliance tree not provisioned")

    result = sweep.audit(REPO_ROOT, ADCP_HOME)
    assert set(result["buckets"]) == set(sweep.BUCKET_LEGEND), (
        "the histogram must carry one entry per declared bucket, zeros included"
    )
    assert sum(result["buckets"].values()) == result["scenario_count"], (
        f"the buckets sum to {sum(result['buckets'].values())} over {result['scenario_count']} "
        "scenarios — a scenario counted in no bucket is a scenario nobody reports"
    )
    assert result["scenarios_with_unrunnable_checks"] >= sum(
        1 for b in result["bindings"] if b["bucket"] == sweep.BUCKET_UNVERIFIED
    ), "every U scenario has at least one unrunnable check, so the count cannot be lower"

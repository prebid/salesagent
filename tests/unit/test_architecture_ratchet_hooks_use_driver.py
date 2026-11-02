"""Structural guard: every count-ratchet hook gets its ceiling from the driver.

A ratcheting baseline may only shrink. Enforcing that needs two halves — the
count-vs-baseline compare (which every hook had) and the baseline-vs-upstream
probe (which four hooks re-implemented and two never had at all). The missing
half is not hypothetical: `.mypy-untyped-defs-baseline` was committed at 237
against a merge-base value of 227 and rode through green, because
``check_mypy_untyped_defs_count.py`` had no probe to trip.

``count_ratchet.run_count_ratchet`` now owns the probe, so the property this
module protects is narrow and structural: a hook that ratchets a baseline must
route through the driver, and must not hand-roll a second probe beside it. That
makes "a ratchet with no ceiling" unrepresentable rather than merely reviewable.
"""

from __future__ import annotations

import ast
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[2] / ".pre-commit-hooks"

#: Hooks that own a ratcheting baseline file. Keyed by module name so a new
#: ratchet hook is added here deliberately, in the same change that writes it.
RATCHET_HOOKS: dict[str, str] = {
    "check_type_ignore_count": ".type-ignore-baseline",
    "check_ruff_complexity_count": ".ruff-complexity-baseline",
    "check_mypy_untyped_defs_count": ".mypy-untyped-defs-baseline",
    "check_code_duplication": ".duplication-baseline",
    "check_fixme_citation_count": ".fixme-citation-baseline",
    "check_admin_raw_session_count": ".admin-raw-session-baseline",
}

#: Names a hook must NOT define: the per-hook probe copies the driver replaced.
_FORBIDDEN_LOCAL_PROBES = frozenset({"read_main_baseline", "check_baseline_not_raised"})


def _module(name: str) -> ast.Module:
    return ast.parse((_HOOKS / f"{name}.py").read_text(encoding="utf-8"))


def _called_names(tree: ast.Module) -> set[str]:
    return {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _defined_names(tree: ast.Module) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def test_every_ratchet_hook_exists() -> None:
    """The registry names real hooks — a rename must update it, not orphan it."""
    missing = sorted(name for name in RATCHET_HOOKS if not (_HOOKS / f"{name}.py").exists())

    assert missing == [], f"RATCHET_HOOKS names hooks that do not exist: {missing}"


def test_every_ratchet_hook_routes_through_the_driver() -> None:
    """No hook may compare against a baseline without the driver's ceiling probe."""
    offenders = sorted(name for name in RATCHET_HOOKS if "run_count_ratchet" not in _called_names(_module(name)))

    assert offenders == [], (
        "These ratchet hooks do not call count_ratchet.run_count_ratchet: "
        f"{offenders}. A hand-rolled compare skips the upstream-ceiling probe, "
        "which is how .mypy-untyped-defs-baseline was raised 227 -> 237 unseen."
    )


def test_no_hook_hand_rolls_its_own_upstream_probe() -> None:
    """The probe is the driver's, in one copy (CLAUDE.md DRY invariant).

    Four hooks each carried a ~40-line near-copy of read_main_baseline +
    check_baseline_not_raised; two carried none. Duplicated enforcement is how
    the two-that-carried-none went unnoticed.
    """
    offenders = sorted(
        (name, sorted(_defined_names(_module(name)) & _FORBIDDEN_LOCAL_PROBES))
        for name in RATCHET_HOOKS
        if _defined_names(_module(name)) & _FORBIDDEN_LOCAL_PROBES
    )

    assert offenders == [], (
        f"These hooks re-implement the driver's upstream probe: {offenders}. "
        "Pass count_upstream= to run_count_ratchet instead."
    )


def test_every_ratchet_baseline_file_is_committed() -> None:
    """A ratchet whose baseline file is absent would be created at today's count."""
    repo_root = _HOOKS.parent
    missing = sorted(baseline for baseline in RATCHET_HOOKS.values() if not (repo_root / baseline).exists())

    assert missing == [], f"Ratchet baseline files missing from the tree: {missing}"

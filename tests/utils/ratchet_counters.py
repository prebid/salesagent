"""The ratcheting baselines, their counters, and the assertions over them.

One registry, imported by both suites that grade it: the fast counters run in
``tests/unit`` (every executor slice, every gate, CI), the slow two run in
``tests/quality`` (the full gate). Splitting the REGISTRY as well would let the
two halves drift, which is the failure this whole area keeps producing.

Why any of this is a test rather than only a pre-commit hook: see
``tests/unit/test_architecture_ratchet_enforcement.py``. Short version — the
hooks are staged ``pre-push``, this repo never pushes, and ci.yml triggers only
on push/PR to main|develop, so the hooks ran nowhere (salesagent-aemue.13).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.unit._architecture_helpers import load_hook_module, repo_root

#: Key used for single-integer baseline files, which have no per-key structure.
SINGLE_KEY = "count"


@dataclass(frozen=True)
class RatchetCounter:
    """A ratcheting baseline, the counter that measures it, and its hook id."""

    hook_id: str
    module: str
    baseline: str
    #: ``(hook_module, repo_root) -> {key: count}``. The hooks' own counters
    #: differ in arity and return type (one int vs a per-key dict), so each
    #: entry names its own adapter and this registry re-implements no counting
    #: of its own — a second implementation is a second thing that can drift
    #: from the hook it is supposed to mirror.
    measure: Callable[[Any, Path], Mapping[str, int]]

    def current(self) -> dict[str, int]:
        """Count against the working tree, using the hook's own counter."""
        return dict(self.measure(load_hook_module(self.module), repo_root()))

    def committed(self) -> dict[str, int]:
        """The baseline as committed, normalized to the same per-key shape."""
        raw = (repo_root() / self.baseline).read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            return {str(key): int(value) for key, value in json.loads(raw).items()}
        return {SINGLE_KEY: int(raw)}


#: File scans and one ruff invocation — measured at well under a second in
#: total, so the unit suite absorbs them without a detectable change.
FAST_RATCHETS = (
    RatchetCounter(
        "type-ignore-no-regression",
        "check_type_ignore_count",
        ".type-ignore-baseline",
        lambda hook, root: {SINGLE_KEY: hook.count_type_ignores(root / hook.SRC_DIR)},
    ),
    RatchetCounter(
        "fixme-citation-no-regression",
        "check_fixme_citation_count",
        ".fixme-citation-baseline",
        lambda hook, root: hook.count_beads_citations(root),
    ),
    RatchetCounter(
        "admin-raw-session-no-regression",
        "check_admin_raw_session_count",
        ".admin-raw-session-baseline",
        lambda hook, root: hook.count_raw_session_usage(root),
    ),
    RatchetCounter(
        "ruff-complexity-no-regression",
        "check_ruff_complexity_count",
        ".ruff-complexity-baseline",
        lambda hook, root: hook.count_rule_violations(root, root / hook.SRC_DIR),
    ),
)

#: A full mypy pass and a full pylint R0801 pass. Measured together at 12m18s,
#: against a unit suite that runs in ~5m30s — putting them there would roughly
#: triple the most-frequently-run suite, so they are graded by the ``quality``
#: tox env, which the full gate runs in parallel with the others (no added
#: wall-clock: the critical path is bdd_inprocess at ~12m40s).
SLOW_RATCHETS = (
    RatchetCounter(
        "mypy-untyped-defs-no-regression",
        "check_mypy_untyped_defs_count",
        ".mypy-untyped-defs-baseline",
        lambda hook, root: {SINGLE_KEY: hook.count_untyped_defs_errors(root)},
    ),
    RatchetCounter(
        "check-code-duplication",
        "check_code_duplication",
        ".duplication-baseline",
        lambda hook, root: {scope: hook.count_duplications(f"{scope}/") for scope in hook.SCOPES},
    ),
)

ALL_RATCHETS = FAST_RATCHETS + SLOW_RATCHETS


def assert_not_over_baseline(ratchet: RatchetCounter) -> None:
    """The ratchet itself: a count above its committed baseline is a failure."""
    current, committed = ratchet.current(), ratchet.committed()
    over = {key: (value, committed.get(key, 0)) for key, value in current.items() if value > committed.get(key, 0)}

    assert not over, (
        f"{ratchet.baseline} exceeded: "
        + ", ".join(f"{key}={now} > baseline {base}" for key, (now, base) in sorted(over.items()))
        + f"\nFix the new violations. Do NOT raise {ratchet.baseline} — it may only shrink."
    )


def assert_not_slack(ratchet: RatchetCounter) -> None:
    """A baseline above its true count is room a future regression lands in free.

    Not a correctness failure on its own, which is exactly why it goes unnoticed:
    the end state is identical to having raised the baseline by the same amount.
    """
    current, committed = ratchet.current(), ratchet.committed()
    slack = {key: (current.get(key, 0), base) for key, base in committed.items() if current.get(key, 0) < base}

    assert not slack, f"{ratchet.baseline} is slack (committed above actual) — lower it to match:\n" + "\n".join(
        f"  {key}: actual={now} committed={base}" for key, (now, base) in sorted(slack.items())
    )

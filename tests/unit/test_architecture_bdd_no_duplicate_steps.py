"""Guard: BDD step functions must not have identical implementations.

When multiple step functions share the exact same body (after stripping
docstrings), it signals a DRY violation — they should be collapsed into a
single regex/parametrized step or share a common helper.

Scanning approach: AST — collect all @given/@when/@then decorated functions in
``tests/bdd/steps/``, normalize their bodies, and flag groups of 3+ identical
implementations. (2 is tolerable for partition/boundary pairs.)

"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Threshold: flag when N or more functions share the same body.
#
# TWO, not three. A PAIR of step functions with byte-identical bodies is the disease in its
# smallest form, and holding the threshold at 3 made the smallest form invisible: "the Buyer
# has no authentication credentials" and "no tenant can be resolved from the request context"
# were two functions whose bodies were both ``ctx["identity"] = None``, so five scenarios
# asserting that NO tenant could be resolved were served one, and graded the opposite of what
# they said. Two sentences that must express different states cannot share a body.
_DUPLICATE_THRESHOLD = 2

# The pairs that already exist, as a COUNT that may only fall -- the same ratchet shape as
# .duplication-baseline, and for the same reason: lowering the threshold to 2 does not create
# these, it reveals the ones that were always there. An enumerated allowlist would be lines
# nobody reads; a number fails the moment one more appears, which is the property that matters.
#
# Lower it when you collapse a pair. Never raise it: a new pair is a new defect, and the
# scenario above is what one costs.
#
# 54 -> 46: ten uc006 groups collapsed to one canonical sentence each.
#
# THIS SCAN FINDS CANDIDATES, NOT DEFECTS. It normalises string literals away, and in Gherkin
# the literal is usually the claim -- so two steps that differ ONLY in the ctx key they read,
# the action they assert, or the fixture value they set look byte-identical here. Four such
# false positives were caught by reading the pair before collapsing it, two of them only after
# a collapse went red: given_assignments_referencing_same_package reads "idempotent_package_id"
# while its twin reads "cross_tenant_package_id", and the two output_format_ids steps differ in
# the creative NAME, which is the subject of the name-fallback scenario that uses one of them.
# Read both bodies verbatim before lowering this number again.
#
# 46 -> 41: five uc019 groups collapsed. All five were SAFE by the
# stricter rule the consolidation tool now applies -- bodies identical modulo docstrings
# and assertion MESSAGES only, every literal that reaches ctx or production equal -- so
# no claim was merged away; a sixth group was dead on both spellings and deleted whole.
#
# 41 -> 35: uc011 (3 spellings + 1 dead def + the notification-subscriber pair, where the
# PAUSED sentence was kept as canonical because both seeds set active=False), uc004 (2),
# uc006 (2). The groups NOT collapsed are as informative as the ones that were: uc004's
# supports / does-NOT-support pairs share a body on purpose -- production has no per-seller
# capability gate for dimensions, metrics or attribution, so both sentences establish the
# same fact and the Then does the grading (their docstrings say so). Body identity is
# necessary, not sufficient; the SENTENCE is the final gate.
#
# 35 -> 26: uc010 (2), uc026 (1 + a dead def), then_error (2), then_payload (1),
# given_media_buy (2), admin_accounts (1), given_entities (a dead second decorator), uc003
# (a dead def). Left on purpose, each read verbatim: then_error's "no database records
# should be created" vs "no new media buy should have been created" (the body grades only
# media buys -- the broader sentence over-claims), then_media_buy's pricing- vs
# date-validation-passes, given_config's "format named X" vs "format X with no render
# dimensions" (the body sets no dimensions state), given_entities' 224-use tenant-resolvable
# vs 7-use setup-checklist-complete, uc002's natural-key vs account_id not-found, uc003's
# revision int vs "string" (identical source, different runtime TYPE -- legitimately
# distinct), uc026's paused-false vs should-deliver, uc010's adapter-unavailable vs
# advisory-warning. Every one of those is a sentence claim the shared body does not
# distinguish; merging would erase the claim, not the duplication.
#
# 26 -> 25: uc006's "(non-draft)" Given now refuses a draft status, so it no longer shares
# a body with its "approved_at set" twin -- the sentence's claim got its own assertion
# instead of a merge. The cluster's other groups were resolved on the
# Gherkin side: two dead sentences swept to their canonical twin, one row corrected to the
# pin, so nothing here to lower for them.
#
# 24 -> 22: both remaining uc010 pairs separated on their own, without a collapse, when the
# capability steps grew the state their sentences always claimed. "full capabilities
# configured" now reports the mock adapter's pricing-model set
# (``env.set_supported_pricing_models()``), which its "full degradation baseline" twin does
# not; "the adapter is unavailable" now seeds a real publisher partner so the degradation rows
# grade the adapter channel alone, which its "advisory warning" twin does not. Both were listed
# above as claims a shared body failed to distinguish -- the bodies now distinguish them, so
# the groups are gone rather than merged.
_DUPLICATE_GROUP_BASELINE = 22

# Steps exempt from the 3+ identical-body scan (load-bearing: each suppresses a
# cluster that would otherwise fail test_no_excessive_duplicate_step_bodies).
# Allowlist can only shrink — remove entries when the duplicate cluster is gone.
# Non-load-bearing entries removed per #1560 review; audit tracked in #1561.
# The uc019/uc026 buyer_ref pass-body/duplicate stubs that #1561 tracked no longer
# exist: the media-buy validation refactor stripped top-level buyer_ref from the
# request contract (pinned 04f59d2d5), and the e2e-harness wiring implemented the
# remaining steps. No pass-body stubs remain, so the allowlist is empty.
_ALLOWED_DUPLICATES: set[str] = set()

# The SECOND half of the ratchet, and the merge is what proved it necessary. The incoming
# branch graded this obligation as ``assert not duplicates`` at threshold 3 — no body may be
# shared by 3+ steps, full stop. This branch replaced that with the GROUP-count ratchet above,
# which is strictly better at catching a new pair and strictly blind to an existing pair
# GROWING into a trio: a third clone joins group N, the group count stays put, and the guard
# stays green on exactly the shape the incoming branch refused.
#
# Demonstrated, not theorised: a third step whose body is ``ctx.setdefault("tenant_id",
# "test_tenant")`` — joining the live given_tenant_resolvable / given_tenant_setup_complete
# pair — left the group count at 22 and the guard passing.
#
# So the MEMBER count is ratcheted too, two-sided, in the same shape. Together the two numbers
# carry both sides' obligation: no new group, and no group growing.
_DUPLICATE_MEMBER_BASELINE = 45


def _is_step_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @given, @when, or @then."""
    step_names = {"given", "when", "then"}
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id in step_names:
                return True
        if isinstance(dec, ast.Name) and dec.id in step_names:
            return True
    return False


def _normalize_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Produce a canonical string representation of the function body.

    Strips the docstring (first Expr with str Constant), then dumps
    remaining statements as AST. This means two functions with
    identical logic but different docstrings will match.
    """
    stmts = list(func.body)
    # Strip docstring
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]

    if not stmts:
        return "<empty>"

    return ast.dump(ast.Module(body=stmts, type_ignores=[]))


def _iter_step_functions() -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, int]]:
    """Yield (step_func_node, repo_relative_path, lineno) for every @given/@when/@then under bdd/steps/."""
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR.parent.parent))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_step_decorated(node):
                yield node, relative, node.lineno


def _scan_bdd_steps() -> list[tuple[str, list[str]]]:
    """Find groups of step functions with identical bodies.

    Returns list of (normalized_body_preview, [func locations]) for groups
    exceeding the threshold.
    """
    body_to_funcs: dict[str, list[str]] = {}

    for node, relative, lineno in _iter_step_functions():
        if node.name in _ALLOWED_DUPLICATES:
            continue
        body_key = _normalize_body(node)
        loc = f"{relative}:{lineno} {node.name}"
        body_to_funcs.setdefault(body_key, []).append(loc)

    return [(key[:80], funcs) for key, funcs in body_to_funcs.items() if len(funcs) >= _DUPLICATE_THRESHOLD]


class TestBddNoDuplicateSteps:
    """Structural guard: step functions must not have identical bodies."""

    @pytest.mark.arch_guard
    def test_no_excessive_duplicate_step_bodies(self):
        """No more than 2 step functions should share the same implementation.

        Groups of 3+ identical bodies indicate a DRY violation that should
        be collapsed into a regex step or shared helper.
        """
        duplicates = _scan_bdd_steps()

        lines = []
        for preview, funcs in duplicates:
            lines.append(f"\n  {len(funcs)} identical bodies (body: {preview}):")
            for f in funcs:
                lines.append(f"    {f}")

        assert len(duplicates) <= _DUPLICATE_GROUP_BASELINE, (
            f"Found {len(duplicates)} group(s) of step functions with identical bodies "
            f"(threshold: {_DUPLICATE_THRESHOLD}+), above the recorded baseline of "
            f"{_DUPLICATE_GROUP_BASELINE}. Two sentences sharing one body cannot express two "
            f"states -- give the new one its own body, or collapse the pair into a single "
            f"parametrized step:" + "".join(lines)
        )
        assert len(duplicates) == _DUPLICATE_GROUP_BASELINE, (
            f"Only {len(duplicates)} duplicate group(s) remain but the baseline still says "
            f"{_DUPLICATE_GROUP_BASELINE}. Lower _DUPLICATE_GROUP_BASELINE to "
            f"{len(duplicates)} in the same change that removed them, so the ratchet cannot "
            f"drift back up unnoticed."
        )

        members = sum(len(funcs) for _, funcs in duplicates)
        assert members <= _DUPLICATE_MEMBER_BASELINE, (
            f"{members} step function(s) share a body with another, above the recorded baseline "
            f"of {_DUPLICATE_MEMBER_BASELINE}. The GROUP count above cannot see this: a third "
            f"clone joining an existing pair leaves it unchanged. A body shared by three "
            f"sentences cannot express three states:" + "".join(lines)
        )
        assert members == _DUPLICATE_MEMBER_BASELINE, (
            f"Only {members} duplicated step function(s) remain but the baseline still says "
            f"{_DUPLICATE_MEMBER_BASELINE}. Lower _DUPLICATE_MEMBER_BASELINE to {members} in "
            f"the same change that removed them, so the ratchet cannot drift back up unnoticed."
        )

    @pytest.mark.arch_guard
    def test_allowed_duplicate_entries_still_exist(self) -> None:
        """Every _ALLOWED_DUPLICATES entry must still name a live BDD step function.

        Scope: rename/delete detection only — does not assert an entry is
        load-bearing for the 3+ identical-body scan. Non-load-bearing audit:
        #1561.
        """
        step_names = {node.name for node, _, _ in _iter_step_functions()}
        missing = sorted(name for name in _ALLOWED_DUPLICATES if name not in step_names)
        assert not missing, (
            f"Stale _ALLOWED_DUPLICATES entries ({len(missing)}) — step removed/renamed, "
            f"remove from allowlist:\n" + "\n".join(f"  {name}" for name in missing)
        )

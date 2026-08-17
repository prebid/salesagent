"""Guard: a BDD oracle must not read a ``ctx`` key that no step ever writes.

``ctx`` is the mutable dict shared across steps in a scenario. A Then-step reads
the entities and outcomes a prior step was expected to put there. When it reads a
key **nothing writes**, one of two things is true:

* the read supplies a literal default -- the oracle silently degrades into a
  constant, which is indistinguishable from having no assertion at all; or
* it does not -- the read yields ``None`` or raises ``KeyError``, which is at
  least loud, but the branch behind it is dead.

The first form is the dangerous one, and it is invisible to every other guard:
``test_architecture_bdd_no_trivial_assertions`` and ``_no_pass_steps`` catch
truthiness and no-op bodies, not a defaulted read of state that was never set.

It has already cost real coverage. ``ctx.get("last_max_results", 50)`` re-paged a
cursor continuation at a hardcoded 50 regardless of the page size the scenario set
up; the only scenario happened to use 50, so the row was green while proving
nothing. ``ctx.get("pre_request_account_ids", set())`` sat behind a guard on two
other never-set keys, so a POST-F1 state-isolation obligation asserted *nothing* --
injecting ``assert False`` into its body passed on all three transports.

Background and the remaining work: GH #1749.

Detection notes, each of which cost a real finding when it was missing:

* ``_require(ctx, "k")`` / ``_require_response`` / ``_require_error``
  (``tests/bdd/steps/_outcome_helpers.py``) are the blessed loud-read helpers and
  MUST count as reads. A census that ignores them flags correctly-written call
  sites and passes badly-written ones -- observed directly: after a fix replaced a
  bare read with ``_require``, the naive read-count went *down*.
* "Literal default" must include ``set()``, ``dict()``, ``list()`` and empty
  ``{} [] ()``, not just ``ast.Constant``. A Constant-only matcher misses
  ``ctx.get(k, set())`` and ``ctx.get(k, {})``.
* ``when_*`` counts as an oracle context. ``last_max_results`` above lives in a
  When and silently changed what got *dispatched*, upstream of any assertion.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_BDD_ROOT = Path(__file__).resolve().parents[1] / "bdd"

# Functions whose bodies are oracle-shaped: Then-steps, assertion helpers, and the
# When-steps that decide what gets dispatched.
_ORACLE_PREFIXES = ("then_", "assert_", "_assert", "when_")

# Loud-read helpers from tests/bdd/steps/_outcome_helpers.py. These raise a
# diagnostic AssertionError when the key is absent, which is the behaviour this
# guard wants -- so they are READS, not violations.
_REQUIRE_HELPERS = {"_require", "_require_response", "_require_error"}

# TIER 1 -- orphan key read WITH a literal default (the silent-constant form).
# MUST STAY EMPTY. This is the shape that turns an oracle into a constant, and it
# is at zero today. An entry here is not a deferral, it is a test that has stopped
# testing -- fix the read (write the key, or use `_require`) instead.
_ALLOWED_DEFAULTED_ORPHANS: set[str] = set()

# TIER 2 -- orphan key read with NO default. These yield None or raise rather than
# silently constant-ing, so they are dead branches rather than vacuous oracles.
# Shrink-only: never add. Tracked by GH #1749.
# Retired: bad_package_id (both Givens now record nonexistent_package_id),
# media_buy_id + target_media_buy_id (then_error_no_reveal now reads the dispatched
# media_buy_ids instead of two keys no step ever wrote).
_ALLOWED_ORPHANS: frozenset[str] = frozenset(
    {
        "captured_logs",
        "dispatched_pipeline",
        "existing_product",
        "expected_existing_package_id",
        "explicit_buying_mode",
        "last_order_name",
        "request_push_config",
        "seeded_task_count",
    }
)

# Writes whose key is not a string literal (`ctx[some_var] = ...`). An AST census
# cannot resolve these, so each is a potential false-orphan source. Pinning the
# count means adding one forces a conscious re-validation of the orphan list
# instead of letting this analysis silently degrade.
_MAX_DYNAMIC_KEY_WRITES = 4


def _is_literal_default(node: ast.expr) -> bool:
    """True for a default that stands in for absent scenario state."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        return not getattr(node, "elts", None) and not getattr(node, "keys", None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"set", "dict", "list"} and not node.args
    return False


def _ctx_key(node: ast.expr) -> str | None:
    """Return the string-literal subscript key of a `ctx[...]` node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ctx_fixture_alias_writes(tree: ast.AST) -> set[str]:
    """Keys the ``ctx`` FIXTURE seeds through the local dict it yields.

    The fixture builds its dict under a local name and yields that -- currently
    ``d["e2e_config"] = ...; ... yield d``. Those writes are as real as any step's,
    but a scanner keyed on the literal name ``ctx`` cannot see them, so every key
    seeded this way looks like an orphan.

    That is not hypothetical: ``e2e_config`` was allowlisted as a dead read on
    exactly this mistake, and the FIXME it carried told the next reader to delete a
    load-bearing line -- doing so would break every e2e_rest scenario (no server-DB
    repoint, no ``_reset_e2e_db``, ``RestE2EDispatcher`` errors out).
    """
    seeded: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "ctx"):
            continue
        yielded = {y.value.id for y in ast.walk(node) if isinstance(y, ast.Yield) and isinstance(y.value, ast.Name)}
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in yielded
                        and (key := _ctx_key(target.slice)) is not None
                    ):
                        seeded.add(key)
    return seeded


def _scan() -> tuple[set[str], dict[str, list[str]], list[tuple[str, str]], list[str]]:
    """Return (written keys, read key -> sites, defaulted orphan reads, dynamic writes)."""
    written: set[str] = set()
    read: dict[str, list[str]] = defaultdict(list)
    defaulted: list[tuple[str, str]] = []  # (key, "file:line")
    dynamic: list[str] = []

    for path in sorted(_BDD_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(_BDD_ROOT)

        # Keys the ctx fixture seeds through the local dict it yields count as writes.
        written |= _ctx_fixture_alias_writes(tree)

        for node in ast.walk(tree):
            # ctx["k"] = ...   (and the unresolvable ctx[<expr>] = ... form)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                        if target.value.id != "ctx":
                            continue
                        key = _ctx_key(target.slice)
                        if key is None:
                            dynamic.append(f"{rel}:{node.lineno}")
                        else:
                            written.add(key)

            if isinstance(node, ast.Call):
                func = node.func
                # ctx.setdefault("k", ...) writes; ctx.get("k") reads
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "ctx"
                    and node.args
                    and (key := _ctx_key(node.args[0])) is not None
                ):
                    if func.attr == "setdefault":
                        written.add(key)
                    elif func.attr == "get":
                        read[key].append(f"{rel}:{node.lineno}")
                # _require(ctx, "k") is a READ, and the blessed one
                if isinstance(func, ast.Name) and func.id in _REQUIRE_HELPERS and len(node.args) >= 2:
                    if (key := _ctx_key(node.args[1])) is not None:
                        read[key].append(f"{rel}:{node.lineno}")

            # ctx["k"] in a load position
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "ctx"
                and isinstance(node.ctx, ast.Load)
                and (key := _ctx_key(node.slice)) is not None
            ):
                read[key].append(f"{rel}:{node.lineno}")

        # Defaulted reads, but only inside oracle-shaped functions.
        for func_def in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if not func_def.name.startswith(_ORACLE_PREFIXES):
                continue
            for node in ast.walk(func_def):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "ctx"
                    and len(node.args) == 2
                    and (key := _ctx_key(node.args[0])) is not None
                    and _is_literal_default(node.args[1])
                ):
                    defaulted.append((key, f"{rel}:{node.lineno}"))

    return written, read, defaulted, dynamic


class TestBddNoOrphanCtxReads:
    """A BDD oracle must not read scenario state no step ever wrote."""

    def test_no_defaulted_read_of_an_orphan_key(self) -> None:
        """TIER 1: a literal default over a never-written key makes the oracle a constant."""
        written, _read, defaulted, _dynamic = _scan()
        violations = sorted(
            f"{site}: ctx.get({key!r}, <literal>) -- nothing writes {key!r}"
            for key, site in defaulted
            if key not in written and key not in _ALLOWED_DEFAULTED_ORPHANS
        )
        assert not violations, (
            "BDD oracle reads a ctx key nothing writes AND supplies a literal default, so the "
            "assertion silently degrades into a constant:\n  "
            + "\n  ".join(violations)
            + "\n\nFix the read, do not allowlist it: write the key in the Given/When that "
            "establishes the precondition, and read it with `_require(ctx, key, hint=...)` so "
            "absence fails loudly. See GH #1749."
        )

    def test_orphan_ctx_reads_match_allowlist(self) -> None:
        """TIER 2: reading a never-written key at all is dead code.

        One assertion covers both directions -- a NEW orphan and a STALE allowlist entry
        (a violation that was fixed but left listed) -- via the shared helper, so the
        ratchet only turns one way and the stale check cannot drift from the new-violation
        check. Hand-rolling the set diff here is itself a guarded anti-pattern
        (``test_architecture_no_handrolled_allowlist_diff``).
        """
        written, read, _defaulted, _dynamic = _scan()
        found = {(key,) for key in read if key not in written}
        assert_violations_match_allowlist(
            found=found,
            allowlist={(key,) for key in _ALLOWED_ORPHANS},
            fix_hint=(
                "A BDD step reads a ctx key no step writes. Either write the key where the "
                "precondition is established (and read it with `_require(ctx, key, hint=...)` so "
                "absence fails loudly), or delete the dead read. Do not grow the allowlist. "
                "See GH #1749."
            ),
        )

    def test_dynamic_key_writes_do_not_grow(self) -> None:
        """`ctx[<expr>] = ...` cannot be resolved, so it can mask a real orphan."""
        _written, _read, _defaulted, dynamic = _scan()
        assert len(dynamic) <= _MAX_DYNAMIC_KEY_WRITES, (
            f"Dynamic-key ctx writes grew to {len(dynamic)} (pinned at {_MAX_DYNAMIC_KEY_WRITES}):\n  "
            + "\n  ".join(sorted(dynamic))
            + "\n\nAn AST census cannot resolve `ctx[<expr>] = ...`, so every one of these is a "
            "key this guard may wrongly believe is never written. Prefer a string-literal key. If "
            "a dynamic write is genuinely needed, re-validate the orphan allowlist and raise the "
            "pin deliberately."
        )

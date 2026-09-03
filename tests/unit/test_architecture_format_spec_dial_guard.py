"""Guard: ``_get_format_spec_sync`` calls in ``media_buy_create.py`` must be
guarded by ``is_dialled_agent_url``.

``_get_format_spec_sync`` (and the ``fetch_format_spec``/registry chain behind
it) dials a creative's stored ``agent_url`` over the egress seam. An adapter-
provided pseudo-URL like ``broadstreet://<tenant_id>`` (served in-process by
the adapter — ``src/core/tools/creative_formats.py``,
``src/adapters/broadstreet/adapter.py``) can never be resolved that way, so an
unconditional call rejects a format the seller itself advertises
(GH #1802). The fix wraps each call in
``if creative.agent_url and is_dialled_agent_url(creative.agent_url): ...`` —
this guard pins that every call stays inside such a conditional.

Scope: this checks ``src/core/tools/media_buy_create.py`` specifically — the
two call sites codebase-scan (GH #1802) named. It does not attempt
to check ``src/core/tools/creatives/_validation.py`` (the reference
implementation the guard above copies, already correct) or
``src/core/format_resolver.py`` (the DEFERRED single-choke-point candidate,
GH #1802) — neither wraps the call in a local ``if`` the same way, so a
generic scan would need a different shape per file. If a THIRD unguarded call
site is added to media_buy_create.py, this guard fails it too — the check is
"is `_get_format_spec_sync` ever called without an enclosing
`is_dialled_agent_url` conditional ANYWHERE in this file", not merely "are
exactly two guarded".
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_guard_subject_resolves,
    parse_module,
    repo_root,
)

TARGET_FILE = "src/core/tools/media_buy_create.py"
DIAL_FUNCTION = "_get_format_spec_sync"
GUARD_FUNCTION = "is_dialled_agent_url"


def test_the_dial_and_guard_functions_this_module_names_still_exist() -> None:
    """Both subjects resolved by import, so a rename fails here rather than going quiet.

    ``media_buy_create`` is heavy -- importing it at module scope would abort the
    whole unit run at collection and mask every other result -- so it is imported
    inside the test. A rename still reddens ``make quality``, as a failure rather
    than a collection error.
    """
    assert_guard_subject_resolves(
        "src.core.tools.media_buy_create",
        DIAL_FUNCTION,
        why=f"This guard scans {TARGET_FILE} for it by name, so its absence makes every assertion here vacuous.",
    )
    # The guard function is DEFINED elsewhere and CALLED in the target -- the scan
    # looks for the call. Binding it to its real home is the point: asserting it
    # against the target file is the mistake this test exists to prevent, and is
    # the mistake it caught when first written.
    assert_guard_subject_resolves(
        "src.core.format_resolver",
        GUARD_FUNCTION,
        why="The guard looks for calls to it inside the target, so a rename leaves every dial site reported as unguarded.",
    )

    # The guard function is DEFINED elsewhere and CALLED in the target -- the
    # scan looks for the call. Binding it to its real home is the point: writing
    # the assertion against the target file is the mistake this test exists to
    # make impossible, and it is the mistake this test caught when first written.
    assert_guard_subject_resolves(
        "src.core.format_resolver",
        GUARD_FUNCTION,
        why=(
            "The guard looks for CALLS to it inside the target, so a rename leaves every dial "
            "site reported as unguarded."
        ),
    )


def _call_matches(call: ast.Call, name: str) -> bool:
    func = call.func
    if isinstance(func, ast.Name) and func.id == name:
        return True
    return isinstance(func, ast.Attribute) and func.attr == name


def _expr_mentions(expr: ast.expr, name: str) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Call) and _call_matches(n, name):
            return True
        if isinstance(n, ast.Name) and n.id == name:
            return True
    return False


def _walk_guarded(node: ast.AST, guarded: bool, violations: list[int]) -> None:
    if isinstance(node, ast.Call) and _call_matches(node, DIAL_FUNCTION) and not guarded:
        violations.append(node.lineno)

    if isinstance(node, ast.If):
        body_guarded = guarded or _expr_mentions(node.test, GUARD_FUNCTION)
        for child in node.body:
            _walk_guarded(child, body_guarded, violations)
        for child in node.orelse:
            _walk_guarded(child, guarded, violations)
        return

    for child in ast.iter_child_nodes(node):
        _walk_guarded(child, guarded, violations)


def find_unguarded_dial_violations(tree: ast.Module) -> list[int]:
    """Line numbers of ``_get_format_spec_sync(...)`` calls with no enclosing
    ``is_dialled_agent_url`` conditional.

    Shaped as a ``(tree) -> list[int]`` detector so the meta-tests can feed it
    synthetic sources directly.
    """
    violations: list[int] = []
    _walk_guarded(tree, guarded=False, violations=violations)
    return sorted(violations)


class TestFormatSpecDialIsGuarded:
    """Every ``_get_format_spec_sync`` call in media_buy_create.py must sit
    inside an ``is_dialled_agent_url`` conditional. No allowlist."""

    @pytest.mark.arch_guard
    def test_no_unguarded_format_spec_dial(self):
        tree = parse_module(repo_root() / TARGET_FILE)
        violations = find_unguarded_dial_violations(tree)

        if violations:
            lines = [
                f"Unguarded _get_format_spec_sync call(s) in {TARGET_FILE}:",
                "",
                *(f"  line {n}" for n in violations),
                "",
                "A stored creative's agent_url may be an adapter-provided pseudo-URL",
                "(e.g. broadstreet://<tenant_id>) that no dialled agent can ever resolve.",
                "Wrap the call in `if creative.agent_url and is_dialled_agent_url(creative.agent_url):`",
                "— see the existing two call sites in this file for the reference shape.",
                "There is no allowlist.",
            ]
            raise AssertionError("\n".join(lines))


class TestFormatSpecDialGuardDetector:
    """The detector's own correctness, on synthetic sources."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            find_unguarded_dial_violations,
            snippets={
                "unconditional call": (
                    "def f(creative):\n    return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
                "guarded by an unrelated condition": (
                    "def f(creative):\n"
                    "    if creative.format:\n"
                    "        return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
                "call in the else branch of a guard": (
                    "def f(creative):\n"
                    "    if is_dialled_agent_url(creative.agent_url):\n"
                    "        pass\n"
                    "    else:\n"
                    "        return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "guarded by is_dialled_agent_url directly",
                (
                    "def f(creative):\n"
                    "    if is_dialled_agent_url(creative.agent_url):\n"
                    "        return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
            ),
            (
                "guarded as part of a boolean `and` condition",
                (
                    "def f(creative):\n"
                    "    if creative.agent_url and is_dialled_agent_url(creative.agent_url):\n"
                    "        return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
            ),
            (
                "guarded by a nested if inside an outer if",
                (
                    "def f(creative):\n"
                    "    if creative.format:\n"
                    "        if is_dialled_agent_url(creative.agent_url):\n"
                    "            return _get_format_spec_sync(creative.agent_url, creative.format)\n"
                ),
            ),
            (
                "no call at all",
                "def f(creative):\n    return creative.format\n",
            ),
        ],
    )
    def test_detector_ignores_non_violations(self, label, source):
        assert find_unguarded_dial_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_media_buy_create_is_scanned_and_clean(self):
        """The fixed module IS subject to this scan, and is clean post-fix."""
        tree = parse_module(repo_root() / TARGET_FILE)
        assert find_unguarded_dial_violations(tree) == []

"""Guard: WebhookURLValidator._maybe_allow_localhost's needle list stays narrow.

The testing-mode SSRF override used to decide its allowance by
substring-matching a HUMAN-READABLE rejection message against six needles. Two
("private/internal network", "private/internal ip address") matched EVERY member
of BLOCKED_NETWORKS, not just the intended Docker compose-network range, admitting
AWS ECS credentials (link-local), CGNAT, and IPv6 ULA under ADCP_TESTING. A third
("docker.internal") matched attacker-CONTROLLED text interpolated into an
unresolvable-hostname error message. Only two needles are safe: "localhost" (an
exact BLOCKED_HOSTNAMES entry) and "127.0.0" (only ever appears via the
127.0.0.0/8 network's own str() repr, never from caller input).

This guard pins the needle set to exactly those two so a future edit cannot
silently reopen the hole — any string literal compared inside
_maybe_allow_localhost's body that isn't one of the two allowed needles fails
the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_detector_catches_ast_snippets, rel

_TARGET_FILE = Path(__file__).resolve().parents[2] / "src" / "core" / "webhook_validator.py"
_ALLOWED_NEEDLES = {"localhost", "127.0.0"}


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _find_disallowed_needles(tree: ast.Module) -> list[str]:
    """Return string literals compared inside _maybe_allow_localhost that aren't allowed."""
    func = _find_function(tree, "_maybe_allow_localhost")
    if func is None:
        return []
    found: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    if side.value not in _ALLOWED_NEEDLES:
                        found.append(side.value)
    return found


def test_maybe_allow_localhost_needles_stay_narrow() -> None:
    """The live function must only compare against the two allowed needles."""
    tree = ast.parse(_TARGET_FILE.read_text(encoding="utf-8"), filename=str(_TARGET_FILE))
    disallowed = _find_disallowed_needles(tree)
    assert not disallowed, (
        f"{rel(_TARGET_FILE)}::_maybe_allow_localhost compares against needle(s) "
        f"{disallowed} outside the pinned allowlist {sorted(_ALLOWED_NEEDLES)} — this reopens "
        "the SSRF hole (a needle matching every BLOCKED_NETWORKS member, or "
        "attacker-controlled interpolated text). If a new needle is genuinely safe, add it to "
        "_ALLOWED_NEEDLES here with the same reasoning bar applied above."
    )


def test_detector_catches_reintroduced_broad_needle() -> None:
    """Meta-test (positive): a reintroduced over-broad needle must be flagged."""
    assert_detector_catches_ast_snippets(
        _find_disallowed_needles,
        snippets={
            "private_internal_network": (
                "def _maybe_allow_localhost(is_valid, error, *, allow_localhost):\n"
                "    if not is_valid and allow_localhost:\n"
                "        lowered = error.lower()\n"
                "        if 'localhost' in lowered or 'private/internal network' in lowered:\n"
                "            return True, ''\n"
                "    return is_valid, error\n"
            ),
            "docker_internal": (
                "def _maybe_allow_localhost(is_valid, error, *, allow_localhost):\n"
                "    if not is_valid and allow_localhost:\n"
                "        if 'docker.internal' in error.lower():\n"
                "            return True, ''\n"
                "    return is_valid, error\n"
            ),
        },
    )


def test_detector_allows_the_pinned_needles() -> None:
    """Meta-test (negative): the two allowed needles must NOT be flagged."""
    source = (
        "def _maybe_allow_localhost(is_valid, error, *, allow_localhost):\n"
        "    if not is_valid and allow_localhost:\n"
        "        lowered = error.lower()\n"
        "        if 'localhost' in lowered or '127.0.0' in error:\n"
        "            return True, ''\n"
        "    return is_valid, error\n"
    )
    tree = ast.parse(source, filename="<known-good>")
    assert not _find_disallowed_needles(tree)


def test_detector_ignores_other_functions() -> None:
    """A needle string elsewhere in the file (outside the target function) is not scanned."""
    source = (
        "def unrelated(x):\n"
        "    return 'private/internal network' in x\n"
        "\n"
        "def _maybe_allow_localhost(is_valid, error, *, allow_localhost):\n"
        "    if not is_valid and allow_localhost:\n"
        "        if 'localhost' in error.lower():\n"
        "            return True, ''\n"
        "    return is_valid, error\n"
    )
    tree = ast.parse(source, filename="<other-function>")
    assert not _find_disallowed_needles(tree)

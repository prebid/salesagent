"""Guard: NO_IDENTITY_OVERRIDE is the ONLY identity-omission sentinel.

Prevents the object()-sentinel fork from regrowing: a
local ``_NO_OVERRIDE = object()`` (or similarly identity/override-named
sentinel), reintroduced anywhere under ``tests/harness/`` or
``tests/helpers/`` instead of importing the shared
``tests.harness.transport.NO_IDENTITY_OVERRIDE``. Census at the time this
guard was added: 10 such local sentinels across client.py, dispatchers.py,
_base.py (x4), _mixins.py, and tests/helpers/mcp_envelope_capture.py — see
the beads notes for the full disposition table.

AST-based (not regex/text) — walks assignment targets, so there is no
regex-slip case to cover (per the dev-practices sweep-verify formula: an
AST-walk guard needs positive + negative meta-tests, not a regex-slip test).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import rel, repo_root, safe_parse

SCAN_DIRS = ("tests/harness", "tests/helpers")
CANONICAL_FILE = "tests/harness/transport.py"
CANONICAL_NAME = "NO_IDENTITY_OVERRIDE"


def _looks_like_identity_sentinel(name: str) -> bool:
    """True when *name* is shaped like an identity-omission sentinel.

    Matches every historical instance (``_NO_OVERRIDE``, ``_no_identity``,
    ``_NO_IDENTITY_OVERRIDE``, ``_AUTO_IDENTITY``) while leaving unrelated
    ``object()`` sentinels for other fields (e.g.
    ``OMIT_IDEMPOTENCY_KEY``, ``_UNSET``, ``_SENTINEL``) alone.
    """
    lowered = name.lower()
    return "identity" in lowered or "override" in lowered


def _find_identity_sentinel_assignments(tree: ast.Module) -> list[str]:
    """Names of top-level or nested identity-shaped ``= object()`` assignments."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        is_bare_object_call = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "object"
            and not value.args
            and not value.keywords
        )
        if not is_bare_object_call:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _looks_like_identity_sentinel(target.id):
                found.append(target.id)
    return found


def _scan(repo: Path) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for scan_dir in SCAN_DIRS:
        directory = repo / scan_dir
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            rel_path = rel(path, repo)
            if rel_path == CANONICAL_FILE:
                continue
            tree = safe_parse(path)
            if tree is None:
                continue
            names = _find_identity_sentinel_assignments(tree)
            if names:
                offenders[rel_path] = names
    return offenders


@pytest.mark.arch_guard
def test_no_local_identity_sentinels_outside_transport_module() -> None:
    offenders = _scan(repo_root())
    assert not offenders, (
        "Local identity-omission sentinel(s) found outside the canonical export "
        f"{CANONICAL_NAME} ({CANONICAL_FILE}): {offenders}. Import {CANONICAL_NAME} "
        "from tests.harness.transport instead of reintroducing a local object() "
        "sentinel."
    )


@pytest.mark.arch_guard
def test_positive_control_canonical_sentinel_is_detected() -> None:
    """The detector actually recognizes the pattern it's meant to ban — proves
    the main guard isn't vacuously passing because the AST walk is broken."""
    tree = safe_parse(repo_root() / CANONICAL_FILE)
    assert tree is not None
    assert CANONICAL_NAME in _find_identity_sentinel_assignments(tree)


@pytest.mark.arch_guard
def test_positive_control_detector_catches_reintroduced_local_sentinel() -> None:
    """A locally-reintroduced identity sentinel (the exact historical shape) IS
    caught — proves the guard would fail loudly if the disease regrows."""
    source = (
        "def dispatch(self, env, **kwargs):\n"
        "    _NO_OVERRIDE = object()\n"
        "    identity = kwargs.pop('identity', _NO_OVERRIDE)\n"
        "    return identity\n"
    )
    tree = ast.parse(source, filename="<known-bad>")
    assert _find_identity_sentinel_assignments(tree) == ["_NO_OVERRIDE"]


@pytest.mark.arch_guard
def test_negative_control_unrelated_object_sentinels_not_flagged() -> None:
    """object() sentinels for unrelated fields (idempotency key, factory field
    defaults, generic dispatch sentinels) must not be flagged — the guard is
    scoped to the identity-argument omission disease specifically."""
    source = "OMIT_IDEMPOTENCY_KEY = object()\n_UNSET = object()\n_SENTINEL = object()\n_DEFAULT_PRODUCT = object()\n"
    tree = ast.parse(source, filename="<known-good>")
    assert _find_identity_sentinel_assignments(tree) == []

"""Guard: the reserved-TLD policy has exactly one matcher, and it lives in the seam.

``src/core/security/url_validator.py`` owns the RFC 2606/6761 reserved-TLD
decision. It exports the decision as ``reserved_tld_for_host`` (which tld, or
None) and ``is_reserved_tld_host`` (the boolean, a thin delegation).
``RESERVED_TLDS`` is the owner's data, not a shared constant for callers to
re-match.

Why a guard rather than a comment: this is the exact shape that produced the
sync_accounts provisioning bug (GH #1291). ``_check_domain_validity`` imported
``RESERVED_TLDS`` and matched it with a bare ``brand_domain.endswith(tld)``,
which — unlike the owner — does not lowercase, does not strip a trailing root
dot, and does not match a bare reserved LABEL. The two sites disagreed, and the
disagreement failed OPEN: an account was provisioned for a domain the
notification prover then refused as unprovable. A caller that re-matches the set
cannot be spotted by reading either file alone, which is why it survived review.

The allowlist is EMPTY and stays that way. A caller that needs the policy calls
the function; a test that genuinely needs to read the set (e.g. asserting its
contents against the pinned spec) marks the line ``# noqa: reserved-tld``, which
is a read, not a second matcher.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import format_failure, parse_module, repo_root

#: The module that owns the decision. Everything else asks it.
OWNER = "src/core/security/url_validator.py"

#: Files permitted to import the raw set. Empty by design — see module docstring.
#: This guard's own file is excluded structurally (it never imports the symbol).
ALLOWED_FILES: frozenset[str] = frozenset()

_ESCAPE_HATCH = "# noqa: reserved-tld"

_KNOWN_BAD = {
    "from-import of the raw set": (
        "from src.core.security.url_validator import RESERVED_TLDS\n"
        "for tld in RESERVED_TLDS:\n"
        "    if domain.endswith(tld):\n"
        "        pass\n"
    ),
    "attribute access on the module": (
        "from src.core.security import url_validator\n"
        "if any(d.endswith(t) for t in url_validator.RESERVED_TLDS):\n"
        "    pass\n"
    ),
}

#: Compliant forms the guard must NOT flag. Without these a guard that simply
#: returned every line would pass its positive meta-tests and ban the fix itself.
_KNOWN_GOOD = {
    "calls the which-tld function": (
        "from src.core.security.url_validator import reserved_tld_for_host\n"
        "tld = reserved_tld_for_host(domain)\n"
        "if tld is not None:\n"
        "    raise ValueError(tld)\n"
    ),
    "calls the boolean": (
        "from src.core.security.url_validator import is_reserved_tld_host\n"
        "if is_reserved_tld_host(hostname):\n"
        "    return False\n"
    ),
    "unrelated symbol from the same module": (
        "from src.core.security.url_validator import check_url_ssrf\n"
        "ok, err = check_url_ssrf(url, require_https=True)\n"
    ),
}


def find_reserved_tld_import_violations(tree: ast.Module) -> list[int]:
    """Line numbers where ``RESERVED_TLDS`` is pulled in for a caller to match."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "RESERVED_TLDS" for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "RESERVED_TLDS":
            lines.append(node.lineno)
    return lines


def _scan(repo) -> list[str]:
    violations: list[str] = []
    for root_name in ("src", "tests"):
        root = repo / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(repo))
            if rel == OWNER or rel in ALLOWED_FILES:
                continue
            tree = parse_module(path)
            if tree is None:
                continue
            source_lines = path.read_text(encoding="utf-8").splitlines()
            for lineno in find_reserved_tld_import_violations(tree):
                line = source_lines[lineno - 1] if lineno <= len(source_lines) else ""
                if _ESCAPE_HATCH in line:
                    continue
                violations.append(f"{rel}:{lineno}: RESERVED_TLDS matched outside {OWNER}")
    return violations


@pytest.mark.arch_guard
def test_reserved_tld_set_is_not_matched_outside_the_owner() -> None:
    violations = _scan(repo_root())
    assert not violations, format_failure(
        summary="The reserved-TLD set must only be matched by its owning module",
        violations=violations,
        fix_hint=(
            "Call reserved_tld_for_host(host) (returns WHICH tld, for the refusal message) or "
            "is_reserved_tld_host(host) (the boolean) from src.core.security.url_validator. "
            "A call-site match over RESERVED_TLDS skips the owner's normalization — case, a "
            "trailing root dot, and a bare reserved label — and fails OPEN on all three."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("label", sorted(_KNOWN_BAD))
def test_detector_catches_known_bad_snippet(label: str) -> None:
    tree = ast.parse(_KNOWN_BAD[label], filename=f"<known-bad:{label}>")
    assert find_reserved_tld_import_violations(tree), f"Detector missed known-bad form: {label}"


@pytest.mark.arch_guard
@pytest.mark.parametrize("label", sorted(_KNOWN_GOOD))
def test_detector_passes_known_good_snippet(label: str) -> None:
    tree = ast.parse(_KNOWN_GOOD[label], filename=f"<known-good:{label}>")
    assert not find_reserved_tld_import_violations(tree), (
        f"Detector flagged a COMPLIANT form: {label} — it would ban the correct fix"
    )


@pytest.mark.arch_guard
def test_owner_exposes_one_matcher_that_the_boolean_delegates_to() -> None:
    """``is_reserved_tld_host`` must delegate, not carry a second comparison.

    Without this, the guard above is satisfiable by a boolean that keeps its own
    matching comprehension beside ``reserved_tld_for_host`` — the same disease,
    relocated inside the owner.
    """
    from src.core.security.url_validator import is_reserved_tld_host, reserved_tld_for_host

    tree = parse_module(repo_root() / OWNER)
    assert tree is not None, f"Could not parse {OWNER}"
    boolean_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "is_reserved_tld_host"),
        None,
    )
    assert boolean_fn is not None, "is_reserved_tld_host not found in the owner"
    called = {n.func.id for n in ast.walk(boolean_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "reserved_tld_for_host" in called, (
        "is_reserved_tld_host must delegate to reserved_tld_for_host, not re-match RESERVED_TLDS"
    )
    # Not "contains no comparison" — the delegation itself is `... is not None`.
    # The thing that must not appear is the SET: touching RESERVED_TLDS here means
    # the boolean does its own matching beside the function it claims to delegate to.
    assert not [n for n in ast.walk(boolean_fn) if isinstance(n, ast.Name) and n.id == "RESERVED_TLDS"], (
        "is_reserved_tld_host reads RESERVED_TLDS directly — that is a second matcher"
    )

    # And the two agree on the spellings the plain endswith a caller would write misses.
    for host in ("Acme.TEST", "acme.test.", "test", "acme.internal", "acme.local"):
        assert reserved_tld_for_host(host) is not None, host
        assert is_reserved_tld_host(host) is True, host
    for host in ("acme.com", "backend.internal.com", "not-deployed.internal.example.com"):
        assert reserved_tld_for_host(host) is None, host
        assert is_reserved_tld_host(host) is False, host

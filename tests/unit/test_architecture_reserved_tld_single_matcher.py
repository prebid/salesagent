"""Guard: the reserved-TLD policy has exactly one matcher, and it lives in the seam.

``src/core/security/egress/policy.py`` owns the RFC 2606/6761 reserved-TLD
decision. It exports it as ``is_reserved_tld_host``; ``RESERVED_TLDS`` is the
owner's data, not a shared constant for callers to re-match and not a shape for
a second module to re-declare.

Why a guard rather than a comment: this is the exact shape that produced the
sync_accounts provisioning bug (GH #1291). ``_check_domain_validity`` imported
``RESERVED_TLDS`` and matched it with a bare ``brand_domain.endswith(tld)``,
which -- unlike the owner -- does not lowercase, does not strip a trailing root
dot, and does not match a bare reserved LABEL. The two sites disagreed, and the
disagreement failed OPEN: an account was provisioned for a domain the
notification prover then refused as unprovable. A caller that re-matches the set
cannot be spotted by reading either file alone, which is why it survived review.

WHERE THE OWNER IS, AND WHY IT MOVED
------------------------------------
This guard was written against ``src/core/security/url_validator.py``, which
owned the decision on the branch that introduced it. GH #1802 consolidated every
copy of outbound address policy into the egress seam and deleted that module;
the reserved-TLD frozenset and its matcher moved into ``egress/policy.py``
verbatim (see that module's "moved verbatim from the deleted url_validator.py"
and the comment block above its ``RESERVED_TLDS``). The live consumer --
``src/services/notification_proof_service.py`` -- imports the seam's predicate.
So the owner named here is the seam, and the capability is unchanged: ONE
matcher, and it is the seam's.

WHAT THE DETECTOR HAD TO GROW
-----------------------------
The original detector looked only for an IMPORT of ``RESERVED_TLDS`` (or an
attribute read off the owning module), because on its home branch there was only
ever one definition, so a second matcher could arise only by borrowing the first
one's data. That is no longer the only way to get two. A module that DECLARES
its own ``RESERVED_TLDS`` is the same defect in its most dangerous form -- it
shares no data with the owner at all, so the two sets can differ in MEMBERSHIP
as well as in matching, and an import-only detector reports nothing at all. An
empty allowlist over a detector that cannot see the live violation is worse than
no guard, so redefinition is a violation here too.

The allowlist is EMPTY and stays that way. A caller that needs the policy calls
the predicate; a test that genuinely needs to read the set (e.g. asserting its
contents against the pinned spec) marks the line ``# noqa: reserved-tld``, which
is a read, not a second matcher.
"""

from __future__ import annotations

import ast
import importlib

import pytest

from tests.unit._architecture_helpers import format_failure, parse_module, repo_root, safe_parse

#: The module that owns the decision. Everything else asks it.
OWNER = "src/core/security/egress/policy.py"

#: Dotted form of :data:`OWNER`, for the behavioural rows below. Kept beside the
#: path so a move updates both together.
OWNER_MODULE = "src.core.security.egress.policy"

#: Files permitted to import or re-declare the raw set. Empty by design -- see
#: module docstring. This guard's own file is excluded structurally: every
#: mention of the symbol here is inside a string literal or a docstring, so the
#: AST carries no import, no attribute read and no assignment for it.
ALLOWED_FILES: frozenset[str] = frozenset()

_ESCAPE_HATCH = "# noqa: reserved-tld"

#: The pinned spec enumerates the RFC 6761 special-use names EXHAUSTIVELY --
#: AdCP 3.1.1, ``docs/creative/canonical-formats.mdx:222``: "RFC 6761
#: special-use names (`.local`, `.localhost`, `.internal`, `.test`, `.example`,
#: `.invalid`)". Carrying a subset is how the owner and a caller come to
#: disagree about a host neither is wrong about in isolation, so membership is
#: graded here against the spec rather than against the owner's own comment.
SPEC_RESERVED_TLDS: frozenset[str] = frozenset({".local", ".localhost", ".internal", ".test", ".example", ".invalid"})

_KNOWN_BAD = {
    "from-import of the raw set": (
        "from src.core.security.egress.policy import RESERVED_TLDS\n"
        "for tld in RESERVED_TLDS:\n"
        "    if domain.endswith(tld):\n"
        "        pass\n"
    ),
    "attribute access on the module": (
        "from src.core.security.egress import policy\nif any(d.endswith(t) for t in policy.RESERVED_TLDS):\n    pass\n"
    ),
    "a second module declaring its own copy": (
        'RESERVED_TLDS: frozenset[str] = frozenset({".test", ".invalid"})\n'
        "def is_reserved_tld_host(hostname):\n"
        "    return any(hostname.endswith(t) for t in RESERVED_TLDS)\n"
    ),
    "a second copy declared without an annotation": 'RESERVED_TLDS = {".test", ".invalid"}\n',
}

#: Compliant forms the guard must NOT flag. Without these a guard that simply
#: returned every line would pass its positive meta-tests and ban the fix itself.
_KNOWN_GOOD = {
    "calls the boolean": (
        "from src.core.security.egress.policy import is_reserved_tld_host\n"
        "if is_reserved_tld_host(hostname):\n"
        "    return False\n"
    ),
    "names the policy in a refusal without re-deriving it": (
        "from src.core.security.egress.policy import is_reserved_tld_host\n"
        "if is_reserved_tld_host(host):\n"
        '    raise ValueError(f"{host} sits under a reserved TLD")\n'
    ),
    "unrelated symbol from the same module": (
        "from src.core.security.egress.policy import EgressPolicy\nverdict = EgressPolicy().check_registration(url)\n"
    ),
    "a similarly named local that is not the set": (
        "reserved_tlds_documented = 6\nassert reserved_tlds_documented == 6\n"
    ),
}


def find_reserved_tld_policy_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Lines where a second reserved-TLD matcher is created, and what each did.

    Three forms, all ending with two sites deciding the same question: borrowing
    the owner's set by import, reading it off the owner's module, or declaring a
    rival copy.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "RESERVED_TLDS" for alias in node.names):
                found.append((node.lineno, "imports the owner's set to match it elsewhere"))
        elif isinstance(node, ast.Attribute) and node.attr == "RESERVED_TLDS":
            found.append((node.lineno, "reads the owner's set off the module to match it elsewhere"))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "RESERVED_TLDS":
                found.append((node.lineno, "declares a SECOND reserved-TLD set"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "RESERVED_TLDS":
                    found.append((node.lineno, "declares a SECOND reserved-TLD set"))
    return sorted(found)


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
            tree = safe_parse(path)
            if tree is None:
                continue
            source_lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, what in find_reserved_tld_policy_violations(tree):
                line = source_lines[lineno - 1] if lineno <= len(source_lines) else ""
                if _ESCAPE_HATCH in line:
                    continue
                violations.append(f"{rel}:{lineno}: {what} — the owner is {OWNER}")
    return violations


@pytest.mark.arch_guard
def test_reserved_tld_set_is_not_matched_outside_the_owner() -> None:
    violations = _scan(repo_root())
    assert not violations, format_failure(
        summary="The reserved-TLD decision must have exactly one matcher, and it is the egress seam's",
        violations=violations,
        fix_hint=(
            f"Call is_reserved_tld_host(host) from {OWNER_MODULE}. A call-site match over "
            "RESERVED_TLDS skips the owner's normalization — case, a trailing root dot, and a "
            "bare reserved label — and fails OPEN on all three (GH #1291). A module that "
            "declares its OWN RESERVED_TLDS is the same defect with the membership free to "
            "drift as well: delete the rival copy and import the owner's predicate."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_owner_still_exports_the_symbols_this_guard_scans_for() -> None:
    """A guard that scans BY STRING keeps passing once the string is renamed."""
    module = importlib.import_module(OWNER_MODULE)
    missing = sorted(n for n in ("RESERVED_TLDS", "is_reserved_tld_host") if not hasattr(module, n))
    assert not missing, (
        f"{missing} no longer exist(s) in {OWNER_MODULE}. This guard scans for the literal name "
        "'RESERVED_TLDS', so a rename makes every scan come back empty — which is "
        "indistinguishable from finding nothing wrong."
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("label", sorted(_KNOWN_BAD))
def test_detector_catches_known_bad_snippet(label: str) -> None:
    tree = ast.parse(_KNOWN_BAD[label], filename=f"<known-bad:{label}>")
    assert find_reserved_tld_policy_violations(tree), f"Detector missed known-bad form: {label}"


@pytest.mark.arch_guard
@pytest.mark.parametrize("label", sorted(_KNOWN_GOOD))
def test_detector_passes_known_good_snippet(label: str) -> None:
    tree = ast.parse(_KNOWN_GOOD[label], filename=f"<known-good:{label}>")
    assert not find_reserved_tld_policy_violations(tree), (
        f"Detector flagged a COMPLIANT form: {label} — it would ban the correct fix"
    )


def _functions_reading_the_set(tree: ast.Module) -> set[str]:
    """Names of functions in *tree* that read ``RESERVED_TLDS`` directly."""
    reading: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(isinstance(n, ast.Name) and n.id == "RESERVED_TLDS" for n in ast.walk(node)):
            reading.add(node.name)
    return reading


@pytest.mark.arch_guard
def test_owner_contains_exactly_one_matcher() -> None:
    """Only one function inside the owner may read the set.

    Without this, the scan above is satisfiable by an owner that keeps a second
    matching comprehension beside the first — the same disease, relocated inside
    the owner, where no cross-file scan will ever see it.

    Deliberately shape-agnostic. The branch this guard came from split the
    decision into ``reserved_tld_for_host`` (which tld, for a refusal message)
    with ``is_reserved_tld_host`` delegating to it; the seam carries the single
    predicate alone, because the call site that wanted the tld NAMED
    (``_check_domain_validity``) no longer exists. Both shapes have exactly one
    reader, which is the invariant that actually matters — so this asserts that
    rather than mandating a which-tld function with no caller.
    """
    tree = parse_module(repo_root() / OWNER)
    readers = _functions_reading_the_set(tree)
    assert len(readers) == 1, (
        f"{OWNER} has {len(readers)} function(s) reading RESERVED_TLDS ({sorted(readers)}); "
        "exactly one may. A second reader is a second matcher, free to disagree with the first "
        "on case, a trailing root dot, or a bare reserved label."
    )
    (matcher,) = readers
    if matcher != "is_reserved_tld_host":
        boolean_fn = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "is_reserved_tld_host"),
            None,
        )
        assert boolean_fn is not None, f"is_reserved_tld_host not found in {OWNER}"
        called = {n.func.id for n in ast.walk(boolean_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert matcher in called, (
            f"is_reserved_tld_host neither matches nor delegates to {matcher} — the exported "
            "predicate must BE the single matcher or be a thin call to it"
        )


@pytest.mark.arch_guard
def test_owner_carries_every_reserved_name_the_pinned_spec_enumerates() -> None:
    """The set's MEMBERSHIP, graded against the pinned spec rather than itself.

    One matcher over an incomplete set still fails OPEN — it just fails open for
    every caller at once instead of for one. AdCP 3.1.1 enumerates the six RFC
    6761 special-use names exhaustively
    (``docs/creative/canonical-formats.mdx:222``); carrying four of them lets
    ``hooks.acme.internal`` and ``printer.acme.local`` past the notification
    prover's "can an endpoint under this host ever be PROVEN?" refusal.
    """
    module = importlib.import_module(OWNER_MODULE)
    missing = sorted(SPEC_RESERVED_TLDS - set(module.RESERVED_TLDS))  # noqa: reserved-tld
    assert not missing, (
        f"{OWNER} omits {missing}, which AdCP 3.1.1 enumerates at "
        "docs/creative/canonical-formats.mdx:222 as RFC 6761 special-use names."
    )


@pytest.mark.arch_guard
def test_the_matcher_normalizes_the_spellings_a_call_site_endswith_misses() -> None:
    """The behavioural half: what the single matcher is FOR.

    Every refused row is a spelling a caller's plain ``endswith`` gets wrong —
    mixed case, a trailing root dot, a bare reserved label — which is why the
    decision may not be re-implemented at a call site. The allowed rows are the
    non-vacuity half: ``.example`` is reserved as a TLD only, so a matcher that
    refused everything merely containing a reserved label would grade nothing.
    """
    is_reserved_tld_host = importlib.import_module(OWNER_MODULE).is_reserved_tld_host

    for host in ("Acme.TEST", "acme.test.", "test", "acme.internal", "acme.local"):
        assert is_reserved_tld_host(host) is True, host
    for host in ("acme.com", "backend.internal.com", "not-deployed.internal.example.com"):
        assert is_reserved_tld_host(host) is False, host

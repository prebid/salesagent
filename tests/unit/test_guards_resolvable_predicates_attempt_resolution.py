"""Guard: a "*_resolvable" signing predicate must actually attempt resolution.

src/core/signing/posture.py's _private_half_is_resolvable used to return True
whenever SOME passphrase was configured, never attempting the real decrypt --
presence was mistaken for correctness. This guard bans that shape going
forward: any function in src/core/signing/*.py whose name promises
resolvability (matches "*resolvable*") must call one of the real resolution
functions (resolve_signing_material / _resolve_signing_provider) somewhere in
its body, or be explicitly allowlisted with a stated reason (e.g. a predicate
that is honestly scoped to configuration-presence only, which should be named
"*_configured", not "*_resolvable" -- see the sibling *_configured predicates
in src/core/tenant_status.py and src/adapters/gam/auth.py for the honest
naming this guard does not need to police).
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT

_SIGNING_DIR = REPO_ROOT / "src" / "core" / "signing"
_REAL_RESOLVERS = {"resolve_signing_material", "_resolve_signing_provider"}

# Format: relative_path_from_repo_root::function_name
# Pre-existing violations that predate this guard. The list can only shrink.
ALLOWLIST: set[str] = set()


def _calls_a_real_resolver(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in _REAL_RESOLVERS:
                return True
    return False


def _scan() -> set[str]:
    violations: set[str] = set()
    for path in sorted(_SIGNING_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and "resolvable" in node.name.lower()
                and not _calls_a_real_resolver(node)
            ):
                key = f"{rel}::{node.name}"
                if key not in ALLOWLIST:
                    violations.add(key)
    return violations


class TestResolvablePredicatesAttemptResolution:
    """Every '*resolvable*'-named function in src/core/signing/ must actually
    attempt resolution (resolve_signing_material / _resolve_signing_provider),
    not merely check configuration presence."""

    def test_no_presence_only_resolvable_predicates(self):
        violations = _scan()
        assert not violations, (
            "functions named '*resolvable*' in src/core/signing/ that never call "
            "resolve_signing_material/_resolve_signing_provider -- a name promising "
            "resolvability must attempt resolution, not just check configuration "
            "presence (rename to '*_configured' if presence-only is the honest "
            "contract):\n" + "\n".join(f"  - {v}" for v in sorted(violations))
        )

    def test_positive_meta_flags_presence_only_predicate(self):
        tree = ast.parse(
            "def _is_resolvable(row):\n"
            "    if get_config().signing.key_passphrase is not None:\n"
            "        return True\n"
            "    return False\n"
        )
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert not _calls_a_real_resolver(fn), "detector failed to flag a presence-only predicate"

    def test_negative_meta_ignores_predicate_that_calls_resolve_signing_material(self):
        tree = ast.parse(
            "def _is_resolvable(repo, row, now):\n"
            "    try:\n"
            "        resolve_signing_material(repo, tenant_id=row.tenant_id, now=now)\n"
            "    except AdCPConfigurationError:\n"
            "        return False\n"
            "    return True\n"
        )
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert _calls_a_real_resolver(fn), "detector false-positived on a predicate that DOES attempt resolution"

    def test_negative_meta_ignores_unrelated_function_names(self):
        tree = ast.parse("def is_configured(self):\n    return self.refresh_token is not None\n")
        violating = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and "resolvable" in n.name.lower() and not _calls_a_real_resolver(n)
        ]
        assert not violating, "detector false-positived on a function whose name doesn't claim resolvability"

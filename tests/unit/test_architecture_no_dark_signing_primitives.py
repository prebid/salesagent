"""Guard: no dark per-tenant signing primitive.

**The disease** (verbatim from the salesagent-7x8t codebase scan): *a per-tenant
capability or credential primitive that exists in ``src/`` but is reached ONLY from
tests — no production caller (admin route, CLI script, bootstrap, migration, service)
ever invokes it, so the feature is dark in production.*

That is what salesagent-7x8t was: ``provision_signing_key``, ``SigningKeyUoW``,
``create_from_keypair``, ``revoke``, ``active_at`` and
``clear_signing_provider_cache`` all existed, were all graded end-to-end by the test
suite, and none of them had a single production caller. Every tenant's
``/.well-known/jwks.json`` answered ``{"keys": []}`` while the suite was green.

The detector is two-sided, because the disease is defined by ABSENCE and a single
grep can only show presence:

1. the symbol is referenced from ``tests/``  — someone believes it works; and
2. the symbol is referenced from no LIVE module under ``src/`` or ``scripts/``.

Re-exports through ``__init__.py`` are not callers — that is precisely how
``SigningKeyUoW`` looked reachable while having zero consumers.

Two detector-correctness rules, each of which a naive version gets wrong:

* **Load context only.** ``obj.attr = x`` binds ``attr``; it does not CALL it, so a
  Store-context attribute must never count as a production caller. (Same for
  ``obj.attr: T``.) Graded by ``test_store_context_is_not_a_production_caller``.
* **Liveness is transitive.** A reference from a module that is itself dark rescues
  nothing — pre-fix, ``create_from_keypair``'s only referrer was ``keys.py``, which
  was dark itself. Scope modules are therefore resolved to a fixpoint: a scope module
  counts as a caller only once something already live reaches it. Graded by
  ``test_a_dark_module_does_not_rescue_the_symbols_it_calls``.

## Scope — and why it is not broader

Covered: the per-tenant SIGNING-KEY CREDENTIAL surface — mint, store, resolve,
select, publish, and the unit-of-work layer that owns them.

    src/core/signing/keys.py                        mint + store
    src/core/signing/provider.py                    resolve + provider cache
    src/core/signing/posture.py                     key-presence derivation
    src/core/signing/algorithms.py                  value sets + kid minting
    src/core/signing/trust_root.py                  publication documents
    src/core/database/repositories/signing_key.py   row read/write/selectors
    src/core/database/repositories/uow.py           the owning UoW layer

Deliberately NOT whole-tree, and not even whole-``src/core/signing/``. A whole-tree
version needs a large allowlist for framework entry points, adapter interface
methods and abstract bases, and a guard with a 200-entry allowlist protects nothing.
Specifically excluded, with reasons:

* ``canonical.py``, ``operations.py``, ``replay_store.py``, ``revocation.py``,
  ``request_verifier_middleware.py`` — the inbound-VERIFIER surface. These handle the
  COUNTERPARTY's keys, not a per-tenant credential of ours, and their entry points
  are the ASGI middleware and the ``adcp`` SDK's ``ReplayStore`` / resolver Protocols,
  which dispatch from OUTSIDE this tree. ``PostgresReplayStore.remember`` and
  ``.at_capacity`` are called by ``adcp.signing`` and by nothing in ``src/``, so the
  two-sided test structurally cannot decide them.
* ``src/core/agent_identity.py`` — ``brand_json_url`` / ``jwks_origin``
  (``:138``/``:157``) really are still dark, but they are DEFERRED to D1
  (``salesagent-z6nr.20``), the consumer that needs ``identity.brand_json_url``.
  Including the module would also import a name-collision false negative:
  ``revocation.py:144`` reads ``resolution.brand_json_url`` off an unrelated
  dataclass, and a name-based detector cannot tell the two apart.

Known limitation, stated rather than hidden: matching is by NAME, so a same-named
attribute on an unrelated object reads as a caller. Inside this scope no such
collision exists today; that is part of why the scope is drawn here.

## Allowlist policy

Shrink-only, and it must stay near zero — a growing allowlist here means dark
primitives are accumulating again, which is the whole failure this guard exists to
stop. Every entry carries a GitHub issue number at the source location.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    format_failure,
    parse_module,
    repo_root,
)

# Repo-relative paths making up the per-tenant signing-credential surface.
SCOPE: tuple[str, ...] = (
    "src/core/signing/keys.py",
    "src/core/signing/provider.py",
    "src/core/signing/posture.py",
    # The primitives moved to the layer's dependency-free leaf (salesagent-n78j0.3).
    # REPOINTED, not dropped: ``src/core/signing/algorithms.py`` still exists but is now a
    # frozen forwarding shim for one committed migration, holding four re-exports and no
    # logic. Scanning the shim instead of the leaf would have kept this guard GREEN while
    # it graded nothing — the same shape as a guard grading its own copy of a rule.
    "src/core/signing_contract/algorithms.py",
    "src/core/signing/trust_root.py",
    "src/core/database/repositories/signing_key.py",
    "src/core/database/repositories/uow.py",
)

# (repo-relative module, public symbol) pairs known to be reached only from tests.
#
# SHRINK-ONLY. Each entry needs a `# FIXME(#<gh-issue>)` at the source location.
ALLOWLIST: set[tuple[str, str]] = set()
# EMPTY, and it ships that way (#1757 B2). It briefly carried
# ("src/core/signing/provider.py", "resolve_signing_provider") with a FIXME — but BOTH
# the guard and the symbol are introduced by this PR, so that was an allowlist larger
# than it started, and this guard's own fix hint forbids exactly that resolution: "Do NOT
# add an allowlist entry to make this pass."
#
# The entry's argument was half right. resolve_signing_provider is NOT dead code: it and
# resolve_signing_material are one-line projections of the same _resolve_cached call, and
# ~10 integration tests grade that shared path THROUGH the provider form — including
# provider.sign() and provider.key_id(), i.e. production's binding of resolved material
# into an InMemorySigningProvider. Deleting it would make those tests construct that
# themselves and grade a test-side copy.
#
# But the conclusion was wrong, and so was "delete it". The symbol is NOT on the layer's
# facade — src/core/signing/__init__.py neither imports nor exports it — and that module's
# docstring says everything below the package is private to the layer. So it was already
# private BY THE LAYER'S OWN RULE and merely unmarked. It is now _resolve_signing_provider,
# which this guard does not scan (see test_private_symbols_are_not_scanned), the
# declaration matches the facade's contract, and no graded behaviour was removed.

_FIX_HINT = (
    "A public symbol on the signing-credential surface is referenced from tests/ but from no live\n"
    "module under src/ or scripts/ — it is dark in production. Wire it to a real caller (admin\n"
    "route, ops script, service, bootstrap) in the same change that introduces it. Do NOT add an\n"
    "allowlist entry to make this pass; the allowlist is shrink-only and every entry needs a\n"
    "# FIXME(#<gh-issue>) at the source location naming the ticket that will light the symbol up."
)


def scope_paths() -> list[Path]:
    """Absolute paths of the modules this guard scans."""
    repo = repo_root()
    return [repo / rel for rel in SCOPE]


def public_symbols(tree: ast.Module) -> list[tuple[str, int]]:
    """Public top-level functions/classes plus public methods, as ``(name, lineno)``.

    Methods are included because half of salesagent-7x8t's dark primitives were
    repository methods (``revoke``, ``active_at``, ``get_by_kid``): a top-level-only
    detector would have reported the fix as complete while they stayed dark.

    Module-level constants are excluded — data has no caller, and tests legitimately
    import value sets as expectations.
    """
    found: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
            found.append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            found.append((node.name, node.lineno))
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and not member.name.startswith("_"):
                    found.append((member.name, member.lineno))
    return found


def module_references(tree: ast.Module) -> set[str]:
    """Every name this module READS.

    Load context only: ``obj.attr = x`` and ``obj.attr: T`` bind a name, they do not
    invoke it, so neither makes the module a caller. ``import`` aliases are excluded
    too — an import on its own reaches nothing, which is exactly how an ``__init__.py``
    re-export used to disguise a dark symbol.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            names.add(node.attr)
    return names


def find_dark_primitive_violations(
    tree: ast.Module,
    *,
    production_refs: frozenset[str],
    test_refs: frozenset[str],
) -> list[int]:
    """Line numbers of public symbols in *tree* that only tests reach.

    Both sides are required. A symbol referenced by nobody at all is dead code, a
    different (and less dangerous) defect: nobody believes it works.
    """
    return [lineno for name, lineno in public_symbols(tree) if name in test_refs and name not in production_refs]


def live_scope_modules(
    *,
    scope_symbols: dict[str, set[str]],
    scope_refs: dict[str, set[str]],
    outside_refs: dict[str, set[str]],
) -> set[str]:
    """Scope modules reachable from outside the scope, resolved to a fixpoint.

    Seeded with every non-scope module; a scope module joins once something already
    live references one of its public symbols, and then its own references start to
    count. Without the fixpoint a closed ring of dark modules vouches for itself.
    """
    live_refs: dict[str, set[str]] = dict(outside_refs)
    pending = dict(scope_symbols)
    promoted = True
    while promoted:
        promoted = False
        reached = set().union(*live_refs.values()) if live_refs else set()
        for module, symbols in list(pending.items()):
            if symbols & reached:
                live_refs[module] = scope_refs.get(module, set())
                del pending[module]
                promoted = True
    return set(live_refs) - set(outside_refs)


def _production_modules() -> dict[str, set[str]]:
    """Repo-relative module -> names it reads, for every non-``__init__`` src/scripts file."""
    repo = repo_root()
    modules: dict[str, set[str]] = {}
    for root in ("src", "scripts"):
        for path in sorted((repo / root).rglob("*.py")):
            if path.name == "__init__.py" or "__pycache__" in path.parts:
                continue
            modules[str(path.relative_to(repo))] = module_references(parse_module(path))
    return modules


def _test_references() -> frozenset[str]:
    repo = repo_root()
    names: set[str] = set()
    for path in sorted((repo / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names |= module_references(parse_module(path))
    return frozenset(names)


def _scan() -> set[tuple[str, str]]:
    """Every ``(module, symbol)`` on the signing-credential surface that only tests reach."""
    production = _production_modules()
    scope_symbols = {
        rel: {name for name, _ in public_symbols(parse_module(path))}
        for rel, path in zip(SCOPE, scope_paths(), strict=True)
    }
    scope_refs = {rel: production[rel] for rel in SCOPE}
    outside_refs = {rel: refs for rel, refs in production.items() if rel not in scope_symbols}

    live = live_scope_modules(scope_symbols=scope_symbols, scope_refs=scope_refs, outside_refs=outside_refs)
    production_refs = frozenset(
        set().union(*outside_refs.values()) | set().union(*(scope_refs[m] for m in live), set())
    )
    test_refs = _test_references()

    violations: set[tuple[str, str]] = set()
    for rel, path in zip(SCOPE, scope_paths(), strict=True):
        tree = parse_module(path)
        flagged = set(find_dark_primitive_violations(tree, production_refs=production_refs, test_refs=test_refs))
        violations |= {(rel, name) for name, lineno in public_symbols(tree) if lineno in flagged}
    return violations


class TestNoDarkSigningPrimitives:
    """The class-level pin: the signing-credential surface has no test-only symbol."""

    def test_every_scoped_module_exists(self):
        """A renamed/moved module must not silently empty the guard's scan set."""
        missing = [rel for rel, path in zip(SCOPE, scope_paths(), strict=True) if not path.exists()]
        assert not missing, format_failure(
            summary="Scoped modules are gone — update SCOPE, do not let the guard scan nothing:",
            violations=missing,
        )

    def test_no_public_signing_symbol_is_reached_only_from_tests(self):
        assert_violations_match_allowlist(_scan(), ALLOWLIST, fix_hint=_FIX_HINT)


class TestDetectorCatchesTheDisease:
    """Meta-tests. A guard whose detector finds nothing is worthless."""

    def test_detector_flags_a_symbol_only_tests_reach(self):
        assert_detector_catches_ast_snippets(
            lambda tree: find_dark_primitive_violations(
                tree,
                production_refs=frozenset(),
                test_refs=frozenset({"provision_signing_key", "SigningKeyUoW", "revoke", "active_at"}),
            ),
            snippets={
                "dark top-level function (row 1: provision_signing_key)": (
                    "def provision_signing_key(repo, *, tenant_id):\n    return repo\n"
                ),
                "dark class (row 2: SigningKeyUoW)": ("class SigningKeyUoW:\n    pass\n"),
                "dark public METHOD (rows 4/6: revoke, active_at)": (
                    "class SigningKeyRepository:\n"
                    "    def revoke(self, kid, *, at):\n        return None\n"
                    "    def active_at(self, *, now):\n        return None\n"
                ),
            },
        )

    def test_store_context_is_not_a_production_caller(self):
        """``obj.attr = x`` binds a name; it does not invoke it.

        Counting a Store-context attribute would let a monkeypatch-shaped assignment
        anywhere in ``src/`` vouch for a symbol nobody calls.
        """
        stored = module_references(ast.parse("import mod\nmod.provision_signing_key = fake\n"))
        assert "provision_signing_key" not in stored

        annotated = module_references(ast.parse("import mod\nmod.provision_signing_key: object\n"))
        assert "provision_signing_key" not in annotated

        loaded = module_references(ast.parse("import mod\nmod.provision_signing_key(repo)\n"))
        assert "provision_signing_key" in loaded

    def test_import_alone_is_not_a_production_caller(self):
        """A re-export through ``__init__.py`` is how ``SigningKeyUoW`` looked reachable."""
        assert module_references(ast.parse("from src.core.signing.keys import provision_signing_key\n")) == set()

    def test_a_dark_module_does_not_rescue_the_symbols_it_calls(self):
        """Transitivity: pre-fix, ``create_from_keypair``'s only referrer was dark ``keys.py``."""
        live = live_scope_modules(
            scope_symbols={"keys.py": {"provision_signing_key"}, "repo.py": {"create_from_keypair"}},
            scope_refs={"keys.py": {"create_from_keypair"}, "repo.py": set()},
            outside_refs={"unrelated.py": {"something_else"}},
        )
        assert live == set()

        wired = live_scope_modules(
            scope_symbols={"keys.py": {"provision_signing_key"}, "repo.py": {"create_from_keypair"}},
            scope_refs={"keys.py": {"create_from_keypair"}, "repo.py": set()},
            outside_refs={"blueprint.py": {"provision_signing_key"}},
        )
        assert wired == {"keys.py", "repo.py"}


class TestDetectorDoesNotOverfire:
    """Negative meta-tests — the detector must stay silent on what is NOT the disease."""

    def test_a_symbol_with_a_production_caller_is_clean(self):
        tree = ast.parse("def provision_signing_key(repo):\n    return repo\n")
        assert (
            find_dark_primitive_violations(
                tree,
                production_refs=frozenset({"provision_signing_key"}),
                test_refs=frozenset({"provision_signing_key"}),
            )
            == []
        )

    def test_a_symbol_no_one_references_is_not_this_disease(self):
        """Unreferenced code is dead code — nobody believes it works. Different defect."""
        tree = ast.parse("def provision_signing_key(repo):\n    return repo\n")
        assert find_dark_primitive_violations(tree, production_refs=frozenset(), test_refs=frozenset()) == []

    def test_private_symbols_are_not_scanned(self):
        tree = ast.parse("def _write_private_key_pem(path, pem):\n    return None\n")
        assert (
            find_dark_primitive_violations(
                tree,
                production_refs=frozenset(),
                test_refs=frozenset({"_write_private_key_pem"}),
            )
            == []
        )

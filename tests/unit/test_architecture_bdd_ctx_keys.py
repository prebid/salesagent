"""Guard: a BDD step may not write a ctx key that nothing reads.

The scenario ``ctx`` is a shared dict with no schema, so a key that no step reads
is a fact the harness records and never checks. It reads like state — the next
author copies the line — but nothing can disagree with it, which is how
``push_notification_config`` ended up with two writers that set different keys and
a Then step that passed off the one its own sentence never wrote.

The bar is ZERO, and there is no allowlist. A shrink-only ceiling would fence the
slop and read as permission to add more; this is a harness the next scenario copies
from, so what ships becomes the pattern. If your Given has nothing to establish,
say so in its body — do not record the claim into ctx.

**How a key counts as read.** By AST, not by grep. Grep undercounted the original
survey by 25 keys because ``ctx.setdefault`` is a write it does not recognise and
``_require(ctx, "k")`` is a read it cannot see. This module therefore resolves:

  * ``ctx[k]`` / ``ctx.get`` / ``ctx.pop`` / ``k in ctx`` / ``ctx.setdefault``;
  * helpers whose first parameter is ``ctx`` (any local dict handed to one is a ctx
    alias and is re-scanned to a fixpoint — that is how the sub-context dicts in
    ``uc002_nfr`` and conftest's own fixture are covered);
  * helpers that subscript ctx with one of their OWN parameters (``_require``),
    resolved at each call site;
  * ``ctx.setdefault(k, d)`` whose return value is used for anything but an
    immediate mutation — that IS a read of the existing value, and it is how
    ``_config``/``_packages`` memoize across step invocations;
  * comprehension key variables bound to a literal tuple;
  * DYNAMIC keys, by literal prefix (``f"db_principal_{owner}"`` reads every key
    starting ``db_principal_``).

The last one is the hazard this guard has to refuse rather than approximate: a
dynamic read the resolver does not understand would make a live key look dead, and
deleting it turns a passing test into a crash or a silent default. So
``test_every_dynamic_ctx_key_is_resolvable`` fails on any dynamic form outside the
two it can reason about, instead of quietly ignoring it.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parents[1]
_BDD_DIR = _TESTS_DIR / "bdd"

#: Methods whose receiver is mutated, never read. ``ctx.setdefault(k, []).append(x)``
#: writes; ``x = ctx.setdefault(k, {})`` then ``x[a]`` reads.
_MUTATORS = frozenset({"append", "extend", "add", "update", "discard", "remove", "insert", "sort", "clear"})


@dataclass
class _Op:
    file: str
    func: str
    line: int
    key: str
    dynamic: bool = False


@dataclass
class _Scan:
    writes: list[_Op] = field(default_factory=list)
    reads: list[_Op] = field(default_factory=list)
    #: key expressions the resolver could not turn into a literal or a prefix,
    #: as (kind, file, line, source)
    unresolved: list[tuple[str, str, int, str]] = field(default_factory=list)
    #: dynamic read prefixes, e.g. "db_principal_"
    read_prefixes: set[str] = field(default_factory=set)


def _is_step_driver(source: str) -> bool:
    """A module outside tests/bdd that drives BDD step functions directly.

    Unit and integration tests build a ``ctx`` by hand and call a step to exercise
    its assertion logic, so they are READERS of the ctx protocol even though they
    are not steps. Missing them is not hypothetical: ``self_dispatched_response``
    is written by five such drivers and by nothing under tests/bdd, and a scan
    that stopped at tests/bdd called it writerless.
    """
    return "tests.bdd.steps" in source


def _trees() -> dict[str, ast.Module]:
    """Parse every module that participates in the ctx protocol.

    WRITES are counted only from tests/bdd — the rule is about what a STEP leaves
    behind. READS are counted from the step drivers too, because a unit test that
    reads a key is as much a named consumer as a Then step is.
    """
    out = {}
    for p in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        try:
            source = p.read_text()
        except OSError:
            continue
        rel = str(p.relative_to(_TESTS_DIR.parent))
        if not (_BDD_DIR in p.parents or _is_step_driver(source)):
            continue
        out[rel] = ast.parse(source, filename=str(p))
    return out


def _is_step_file(rel: str) -> bool:
    return rel.startswith("tests/bdd/")


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments, for keys spelled as a constant."""
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = stmt.value.value
    return consts


def _imported_constants(tree: ast.Module, by_module: dict[str, dict[str, str]]) -> dict[str, str]:
    """Constants this module IMPORTS from a sibling, resolved through the exporter.

    ``_module_constants`` alone is per-file, so a key spelled as a constant that is
    DEFINED in one step module and USED in another resolved on the writing side and not
    on the reading side -- which is the one asymmetry that makes a live key look dead.
    That is exactly how it failed: ``_dispatch.py`` defines
    ``CREDENTIAL_REGISTRATIONS = "credential_registrations"`` and writes with it, while
    ``signing_enforcement.py`` imports the name and reads with it, so the write was seen,
    the read was not, and the key was reported as written-by-a-step-and-read-by-nothing.

    Resolution follows the IMPORT, not a global union of every constant name in the
    corpus: two modules are free to bind the same name to different strings, and a union
    would silently pick one of them.
    """
    resolved: dict[str, str] = {}
    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.ImportFrom) or not stmt.module or stmt.level:
            continue
        exporter = by_module.get(stmt.module.replace(".", "/") + ".py")
        if not exporter:
            continue
        for alias in stmt.names:
            if alias.name in exporter:
                resolved[alias.asname or alias.name] = exporter[alias.name]
    return resolved


def _joined_prefix(node: ast.JoinedStr) -> str | None:
    """The literal head of an f-string, e.g. ``f"db_principal_{x}"`` -> ``db_principal_``."""
    if node.values and isinstance(node.values[0], ast.Constant) and isinstance(node.values[0].value, str):
        return node.values[0].value
    return None


def _ctx_sinks(trees: dict[str, ast.Module]) -> set[str]:
    """Every function whose FIRST positional parameter is named ``ctx``."""
    sinks = {"dispatch_request", "dispatch"}
    for tree in trees.values():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = [a.arg for a in n.args.posonlyargs + n.args.args]
                if params and params[0] == "ctx":
                    sinks.add(n.name)
    return sinks


def _placeholder_helpers(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    """ctx-first helpers that key ctx by one of their own parameters (the ``_require`` shape).

    Returns ``{function name: {parameter names used as keys}}``. Their reads are
    invisible at the definition; they resolve at each call site.
    """
    found: dict[str, set[str]] = {}
    for tree in trees.values():
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]
            if not params or params[0] != "ctx":
                continue
            others = set(params[1:])
            hits = set()
            for sub in ast.walk(n):
                key_expr = None
                if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id == "ctx":
                    key_expr = sub.slice
                elif (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "ctx"
                    and sub.func.attr in ("get", "setdefault", "pop")
                    and sub.args
                ):
                    key_expr = sub.args[0]
                if isinstance(key_expr, ast.Name) and key_expr.id in others:
                    hits.add(key_expr.id)
            if hits:
                found[n.name] = hits
    return found


class _Visitor(ast.NodeVisitor):
    """Record every ctx operation inside one function, following ctx aliases."""

    def __init__(
        self,
        scan: _Scan,
        file: str,
        fn: str,
        names: set[str],
        ctx: _ScanContext,
        deferred_keys: frozenset[str] = frozenset(),
    ) -> None:
        self.scan, self.file, self.fn, self.names, self.shared = scan, file, fn, names, ctx
        self.grew = False
        self.comp_keys: dict[str, list[str]] = {}
        self.local_prefixes: dict[str, str] = {}
        #: parameters THIS function keys ctx by (the ``_require`` shape). Its own body
        #: cannot say which key that is -- the call sites can, and they record it there.
        self.deferred_keys = deferred_keys

    # -- helpers -------------------------------------------------------
    def _add_alias(self, name: str) -> None:
        if name not in self.names:
            self.names.add(name)
            self.grew = True

    def _keys(self, node: ast.expr) -> tuple[list[str], list[str], bool]:
        """(literal keys, dynamic prefixes, resolved?) for a key expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value], [], True
        if isinstance(node, ast.JoinedStr):
            prefix = _joined_prefix(node)
            return ([], [prefix], True) if prefix else ([], [], False)
        if isinstance(node, ast.Name):
            if node.id in self.deferred_keys:
                return [], [], True  # resolved at the call sites, not here
            if node.id in self.comp_keys:
                return self.comp_keys[node.id], [], True
            if node.id in self.local_prefixes:
                return [], [self.local_prefixes[node.id]], True
            if node.id in self.shared.constants:
                return [self.shared.constants[node.id]], [], True
        return [], [], False

    def _record(self, kind: str, node: ast.expr, at: ast.AST) -> None:
        literals, prefixes, ok = self._keys(node)
        bucket = self.scan.writes if kind == "write" else self.scan.reads
        for lit in literals:
            bucket.append(_Op(self.file, self.fn, at.lineno, lit))
        for pre in prefixes:
            bucket.append(_Op(self.file, self.fn, at.lineno, pre, dynamic=True))
            if kind == "read":
                self.scan.read_prefixes.add(pre)
        if not ok:
            self.scan.unresolved.append((kind, self.file, at.lineno, ast.unparse(node)))

    def _is_ctx(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in self.names

    # -- statements ----------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        # ``ctx = {...}`` builds a scenario context by hand. That is exactly how the
        # step DRIVERS in tests/unit and tests/integration do it -- and missing it is
        # why self_dispatched_response looked writerless: its five writers are local
        # dict literals named ctx, not parameters.
        if isinstance(node.value, (ast.Dict, ast.DictComp)) or (
            isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "dict"
        ):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "ctx":
                    self._add_alias(t.id)
        for t in node.targets:
            self._store_target(t, node)
            # local f-string key variables: principal_key = f"db_principal_{owner}"
            if isinstance(t, ast.Name) and isinstance(node.value, ast.JoinedStr):
                prefix = _joined_prefix(node.value)
                if prefix:
                    self.local_prefixes[t.id] = prefix
        if self._is_ctx(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self._add_alias(t.id)
        self.generic_visit(node)

    def _store_target(self, target: ast.expr, at: ast.AST) -> None:
        if isinstance(target, ast.Subscript) and self._is_ctx(target.value):
            self._record("write", target.slice, at)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._store_target(elt, at)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._store_target(node.target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._store_target(node.target, node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_ctx(node.value) and isinstance(node.ctx, ast.Load):
            self._record("read", node.slice, node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            if isinstance(op, (ast.In, ast.NotIn)) and self._is_ctx(comparator):
                self._record("read", node.left, node)
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._bind_comprehension(node)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._bind_comprehension(node)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._bind_comprehension(node)
        self.generic_visit(node)

    def _bind_comprehension(self, node: ast.DictComp | ast.ListComp | ast.SetComp) -> None:
        for gen in node.generators:
            if isinstance(gen.target, ast.Name) and isinstance(gen.iter, (ast.Tuple, ast.List, ast.Set)):
                literals = [e.value for e in gen.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if literals:
                    self.comp_keys[gen.target.id] = literals

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)

        if isinstance(func, ast.Attribute) and self._is_ctx(func.value) and node.args:
            method = func.attr
            if method in ("get", "pop"):
                self._record("read", node.args[0], node)
            elif method == "setdefault":
                self._record("write", node.args[0], node)
                if self._setdefault_value_is_consumed(node):
                    self._record("read", node.args[0], node)
            elif method == "update":
                # bulk write whose keys cannot be enumerated -- refuse rather than mis-read
                self.scan.unresolved.append(("write", self.file, node.lineno, ast.unparse(node)))

        if name in self.shared.placeholder_helpers and len(node.args) > 1:
            self._record("read", node.args[1], node)

        if name in self.shared.sinks and node.args and isinstance(node.args[0], ast.Name):
            self._add_alias(node.args[0].id)

        self.generic_visit(node)

    def _setdefault_value_is_consumed(self, call: ast.Call) -> bool:
        parent = self.shared.parents.get(id(call))
        if parent is None or isinstance(parent, ast.Expr):
            return False
        if isinstance(parent, ast.Attribute):
            return parent.attr not in _MUTATORS
        if isinstance(parent, ast.Subscript):
            return isinstance(parent.ctx, ast.Load)
        return True


@dataclass
class _ScanContext:
    sinks: set[str]
    placeholder_helpers: dict[str, set[str]]
    constants: dict[str, str]
    parents: dict[int, ast.AST]


def _scan() -> _Scan:
    trees = _trees()
    sinks = _ctx_sinks(trees)
    helpers = _placeholder_helpers(trees)
    parents: dict[int, ast.AST] = {}
    for tree in trees.values():
        for n in ast.walk(tree):
            for child in ast.iter_child_nodes(n):
                parents[id(child)] = n

    by_module = {rel: _module_constants(tree) for rel, tree in trees.items()}
    scan = _Scan()
    for rel, tree in trees.items():
        # Own constants win over imported ones, which is what Python does: a module that
        # rebinds an imported name uses its own value from that point on.
        constants = {**_imported_constants(tree, by_module), **by_module[rel]}
        shared = _ScanContext(sinks, helpers, constants, parents)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]
            names = {p for p in params if p == "ctx"}
            if rel == "tests/bdd/conftest.py" and n.name == "ctx":
                names.add("d")  # the fixture builds the dict under a local name
            deferred = frozenset(helpers.get(n.name, ()))
            # converge the alias set, then record once
            for _ in range(6):
                probe = _Visitor(_Scan(), rel, n.name, set(names), shared, deferred)
                for stmt in n.body:
                    probe.visit(stmt)
                if not probe.grew:
                    break
                names = probe.names
            visitor = _Visitor(scan, rel, n.name, names, shared, deferred)
            for stmt in n.body:
                visitor.visit(stmt)
    return scan


def _write_never_read(scan: _Scan) -> dict[str, list[_Op]]:
    read_keys = {op.key for op in scan.reads if not op.dynamic}
    prefixes = tuple(sorted(scan.read_prefixes))
    orphans: dict[str, list[_Op]] = defaultdict(list)
    for op in scan.writes:
        if not _is_step_file(op.file):
            continue  # only a STEP's writes are the rule's subject
        if op.dynamic or op.key in read_keys or op.key.startswith(prefixes):
            continue
        orphans[op.key].append(op)
    return orphans


@pytest.fixture(scope="module")
def ctx_scan() -> _Scan:
    return _scan()


@pytest.mark.arch_guard
def test_no_ctx_key_is_written_without_a_reader(ctx_scan: _Scan) -> None:
    """Every ctx key a BDD step writes must be read by a named consumer.

    Zero, with no allowlist. See the module docstring for why a shrink-only
    ceiling is the wrong shape for this one.
    """
    orphans = _write_never_read(ctx_scan)
    lines = [
        f"  ctx[{key!r}] — written at " + ", ".join(f"{op.file}:{op.line} {op.func}()" for op in ops[:4])
        for key, ops in sorted(orphans.items())
    ]
    assert not orphans, (
        f"{len(orphans)} ctx key(s) are written by a BDD step and read by nothing:\n"
        + "\n".join(lines)
        + "\n\nFix: delete the write, or give the key a reader that grades something. "
        "If the step has nothing to establish, say that in its body — a ctx key no "
        "one reads is a claim that cannot be wrong."
    )


@pytest.mark.arch_guard
def test_every_dynamic_ctx_key_is_resolvable(ctx_scan: _Scan) -> None:
    """No ctx key expression may be one this guard cannot reason about.

    A dynamic read the resolver cannot turn into a literal or a prefix would make a
    live key look dead, and the guard above would then invite someone to delete it.
    So an unrecognised form fails HERE, where the fix is to teach the resolver,
    rather than silently widening what counts as unread.
    """
    # Every unresolvable READ matters wherever it is: it could be the reader that
    # keeps a key alive. An unresolvable WRITE only matters in a step file, because
    # only a step's writes are the rule's subject -- a driver doing
    # ``ctx.update(seeded_ctx)`` to set up a fixture writes nothing this rule judges.
    blocking = [u for u in ctx_scan.unresolved if u[0] == "read" or _is_step_file(u[1])]
    assert not blocking, (
        "ctx is keyed by expression(s) this guard cannot resolve:\n"
        + "\n".join(f"  [{kind}] {f}:{line}  {src}" for kind, f, line, src in blocking)
        + "\n\nFix: use a literal key, an f-string with a literal prefix, or a module "
        "constant — or teach _Visitor._keys the new form. Do not leave it unresolved: "
        "an unreadable key expression makes live keys look dead."
    )


@pytest.mark.arch_guard
def test_scan_sees_the_corpus(ctx_scan: _Scan) -> None:
    """The scanner must actually be looking at the steps.

    A resolver bug that silences the scan would make both guards above pass on an
    empty record set. These floors are far below the real counts (~575 writes over
    ~165 keys); they only catch a scan that collapsed.
    """
    assert len(ctx_scan.writes) > 300, (
        f"only {len(ctx_scan.writes)} ctx writes found — the scan is not reaching tests/bdd"
    )
    assert len(ctx_scan.reads) > 900, f"only {len(ctx_scan.reads)} ctx reads found — the scan is not reaching tests/bdd"
    assert ctx_scan.read_prefixes, "no dynamic ctx key prefixes resolved — prefix handling is broken"

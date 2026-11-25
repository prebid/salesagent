"""Guard: wire shaping happens in the one serializer seat, not per class.

Two rules, one root. ``WireSerializerMixin`` (src/core/schemas/_base.py) owns the
single ``@model_serializer(mode="wrap")`` through which a response model shapes what
reaches the buyer. Both violations below are ways of stepping outside that seat, and
both fail SILENTLY — which is what makes them worth a guard rather than a review note.

1. **Hand-rolled required-nullable re-insert.** ``model_dump()`` calls ``super()``
   and then puts a key back that ``exclude_none=True`` dropped. It is invisible to
   ``model_dump_json()``, it cannot see the caller's ``exclude=``, and under
   ``mode="json"`` it can plant a raw Python value in a JSON document. Name the
   schema in ``_PINNED_SCHEMA_REF`` instead — the retained set is derived from the
   pin's required-and-nullable intersection, so it cannot drift from the spec.

2. **A second model serializer on one model.** Pydantic runs only the FIRST model
   serializer it finds in the MRO and drops the rest with no error and no warning, so
   a class that declares its own beside an inherited one silently disables the
   inherited behaviour. Verified on the pinned pydantic: two wrap serializers on one
   model produce exactly one call. Extend the shared seat instead.

Introduced with the L5 reshape (GH #1900 remediation): the scan behind that change
found the pattern at two sites, one of which was the abstraction itself.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import format_failure, parse_module, rel, repo_root

# The seat itself, and this guard's own prose/snippets.
ALLOWED_FILES = {
    "src/core/schemas/_base.py",
    "tests/unit/test_architecture_one_wire_serializer_seat.py",
}

_KNOWN_BAD_REINSERT = """
class Thing(SomeBase):
    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if "confirmed_at" not in result:
            result["confirmed_at"] = self.confirmed_at
        return result
"""

# The regex-slip case: a different local name and `data`/`.get` spelling must still be
# caught, because the guard reads the AST, not the word "result".
_KNOWN_BAD_REINSERT_RENAMED = """
class Thing(SomeBase):
    def model_dump(self, **kwargs):
        payload = super().model_dump(**kwargs)
        if payload.get("next_expected_at") is None:
            payload["next_expected_at"] = None
        return payload
"""

# The generic spelling: a loop over a declared field set, writing a VARIABLE key.
# This is what the pre-fix mixin looked like, and a literal-only detector misses it.
_KNOWN_BAD_REINSERT_VARIABLE_KEY = """
class Thing(SomeBase):
    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        for field in self._ALWAYS_INCLUDE_NULL_FIELDS:
            if field not in result:
                result[field] = getattr(self, field, None)
        return result
"""

_KNOWN_GOOD = '''
class Thing(AlwaysIncludeFieldsMixin, SomeBase):
    _ALWAYS_INCLUDE_NULL_FIELDS = frozenset({"confirmed_at"})

    def _should_always_include(self, field: str) -> bool:
        return self.notification_type is not None

    def model_dump(self, **kwargs):
        """Pattern #4 nested re-serialization is NOT this disease."""
        result = super().model_dump(**kwargs)
        if "packages" in result and self.packages:
            result["packages"] = [p.model_dump(**kwargs) for p in self.packages]
        return result
'''


def _dumps_to_names(fn: ast.FunctionDef) -> set[str]:
    """Names bound to the result of a ``super().model_dump(...)`` call in *fn*."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "model_dump"):
            continue
        inner = func.value
        if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "super"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _subscript_writes(fn: ast.FunctionDef, names: set[str]) -> list[ast.Assign]:
    """``<name>[<key>] = ...`` writes onto one of *names*.

    The key may be a string literal OR a variable. The variable spelling is the one
    that matters most: the pre-fix mixin looped over a declared field set and wrote
    ``result[field] = ...``, which a literal-only detector walks straight past — the
    generic form of the disease would have slipped the guard written for its
    instances. Verified against the real pre-fix source, not only a snippet.
    """
    writes: list[ast.Assign] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)):
                continue
            if target.value.id not in names:
                continue
            key = target.slice
            if isinstance(key, ast.Name) or (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                writes.append(node)
    return writes


def _is_exempt_write(node: ast.Assign, names: set[str]) -> bool:
    """True when the write transforms what the dump ALREADY carries, rather than
    putting back a key the dump dropped. Only the latter is the disease.

    Two legitimate shapes are exempt:

    * the value reads from the dumped dict itself — a rename (``data["format_ids"] =
      data.pop("formats")``) or a rewrite (``data["assets"] =
      strip_none_deep(data["assets"])``);
    * the value is a nested ``model_dump()`` — Pattern #4 re-serialization, which
      replaces a key the dump already produced.
    """
    for sub in ast.walk(node.value):
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "model_dump":
            return True
    return False


def find_reinsert_violations(tree: ast.Module, relpath: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "model_dump":
            continue
        names = _dumps_to_names(node)
        if not names:
            continue
        for write in _subscript_writes(node, names):
            if _is_exempt_write(write, names):
                continue
            violations.append(f"{relpath}:{write.lineno}")
    return violations


def _scan(repo, finder) -> list[str]:
    violations: list[str] = []
    for path in sorted((repo / "src").rglob("*.py")):
        relpath = rel(path, repo)
        if relpath in ALLOWED_FILES:
            continue
        tree = parse_module(path)
        if tree is not None:
            violations.extend(finder(tree, relpath))
    return violations


@pytest.mark.arch_guard
def test_no_hand_rolled_required_nullable_reinsert() -> None:
    violations = _scan(repo_root(), find_reinsert_violations)
    assert not violations, format_failure(
        summary="Required-nullable retention belongs to AlwaysIncludeFieldsMixin, not a model_dump() override",
        violations=violations,
        fix_hint=(
            "Name the schema in _PINNED_SCHEMA_REF on the class — the retained set is "
            "derived from the pin's required-and-nullable intersection, so it cannot "
            "drift from the spec. A model_dump() override cannot see the caller's "
            "exclude= and never runs for model_dump_json()."
        ),
        docs_link="docs/development/structural-guards.md",
    )


def _violations_for(tmp_path, source: str, finder) -> list[str]:
    probe = tmp_path / "src" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")
    return _scan(tmp_path, finder)


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "source",
    [_KNOWN_BAD_REINSERT, _KNOWN_BAD_REINSERT_RENAMED, _KNOWN_BAD_REINSERT_VARIABLE_KEY],
    ids=["result-name", "renamed-local", "variable-key"],
)
def test_reinsert_detector_catches_known_bad(tmp_path, source: str) -> None:
    assert _violations_for(tmp_path, source, find_reinsert_violations), (
        "Detector must flag a hand-rolled re-insert regardless of the local variable name"
    )


@pytest.mark.arch_guard
def test_reinsert_detector_passes_known_good(tmp_path) -> None:
    assert not _violations_for(tmp_path, _KNOWN_GOOD, find_reinsert_violations), (
        "Pattern #4 nested re-serialization is legitimate and must not be flagged"
    )

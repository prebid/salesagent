"""Guard: JSONType columns must never receive json.dumps pre-serialized strings.

Disease: a writer passes ``json.dumps(...)`` into a JSONType (JSONB) column —
constructor kwarg or attribute assignment. The column type takes dicts/lists
directly; the str used to be silently coerced to ``{}`` (audit details,
principal platform_mappings, and self-serve signup tenant fields were all
emptied this way), and now raises TypeError at runtime.
This guard catches the mistake at test time instead.

The JSONType column inventory is mapper-derived (``orm_classes()``, the live
registry), not name-matched, so Text columns that legitimately store JSON strings
(e.g. ``SyncJob.summary``) are never dragged in. Form A (constructor kwarg)
is import-resolving like test_guards_orm_constructor_kwargs: the callee's class is
the one ``orm_references`` gives as the call's own scope binds it; form B
(attribute assignment) matches any ``obj.<attr> = json.dumps(...)`` where
``<attr>`` is a JSONType column name on any mapper — a name-based
approximation whose residual risk is documented in the meta-tests.
"""

from __future__ import annotations

import ast

from sqlalchemy.orm import class_mapper

from src.core.database.json_type import JSONType
from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    orm_classes,
    orm_references,
    repo_root,
    scan_rows,
)

# (file, "ClassName.attr" or ".attr") pairs permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _jsontype_cols_by_class() -> dict[str, set[str]]:
    """ORM class name -> attribute names of its JSONType columns."""
    out: dict[str, set[str]] = {}
    for name, cls in orm_classes().items():
        cols = {
            attr.key
            for attr in class_mapper(cls).column_attrs
            if any(isinstance(c.type, JSONType) for c in attr.columns)
        }
        if cols:
            out[name] = cols
    return out


def _is_json_dumps(node: ast.expr) -> bool:
    """True for ``json.dumps(...)``, unwrapping ``x if c else y`` conditionals."""
    if isinstance(node, ast.IfExp):
        return _is_json_dumps(node.body) or _is_json_dumps(node.orelse)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
    )


def _scan_file(tree: ast.Module, relpath: str, by_class: dict[str, set[str]]) -> list[tuple[str, str, int]]:
    """(rel_path, target-description, lineno) per violation in one file."""
    all_json_cols = set().union(*by_class.values()) if by_class else set()
    references = orm_references(relpath, tree)
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        # Form A: Model(col=json.dumps(...)) with Model a mapped class as the call's scope binds it
        if isinstance(node, ast.Call) and (orm := references.get(id(node.func))) in by_class:
            for kw in node.keywords:
                if kw.arg in by_class[orm] and _is_json_dumps(kw.value):
                    hits.append((relpath, f"{orm}.{kw.arg}", node.lineno))
        # Form B: obj.col = json.dumps(...) where col is a JSONType column name
        if isinstance(node, ast.Assign) and _is_json_dumps(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr in all_json_cols:
                    hits.append((relpath, f".{tgt.attr}", node.lineno))
    return hits


def _scan_src() -> list[tuple[str, str, int]]:
    by_class = _jsontype_cols_by_class()
    return scan_rows([repo_root() / "src"], lambda tree, relpath: _scan_file(tree, relpath, by_class))


class TestJsonDumpsJsontypeGuard:
    def test_no_json_dumps_into_jsontype_columns(self):
        """No json.dumps reaches a JSONType column in src/, and every allowlist entry still violates."""
        assert_violations_match_allowlist(
            {(path, target) for path, target, _line in _scan_src()},
            ALLOWLIST,
            fix_hint=(
                "json.dumps bound to a JSONType column — the column takes dicts/lists "
                "directly; the str raises TypeError at bind time (was: silent {} data loss). "
                "Pass the value itself -- do not allowlist."
            ),
        )


class TestGuardMetaTests:
    def _scan_source(self, source: str) -> list[tuple[str, str, int]]:
        return _scan_file(ast.parse(source), "src/x.py", _jsontype_cols_by_class())

    def test_positive_constructor_kwarg(self):
        src = "import json\nfrom src.core.database.models import AuditLog\na = AuditLog(details=json.dumps({'x': 1}))\n"
        hits = self._scan_source(src)
        assert [h[1] for h in hits] == ["AuditLog.details"]

    def test_positive_aliased_import(self):
        src = (
            "import json\n"
            "from src.core.database.models import AuditLog as DBLog\n"
            "a = DBLog(details=json.dumps({'x': 1}))\n"
        )
        assert [h[1] for h in self._scan_source(src)] == ["AuditLog.details"]

    def test_positive_attribute_assignment(self):
        src = "import json\ntenant.ai_policy = json.dumps({'enabled': True})\n"
        assert [h[1] for h in self._scan_source(src)] == [".ai_policy"]

    def test_positive_conditional_expression(self):
        """The public.py shape: json.dumps([x]) if x else None."""
        src = (
            "import json\n"
            "from src.core.database.models import Tenant\n"
            "t = Tenant(authorized_domains=json.dumps(['d']) if d else None)\n"
        )
        assert [h[1] for h in self._scan_source(src)] == ["Tenant.authorized_domains"]

    def test_negative_text_column_json_dumps_allowed(self):
        """SyncJob.summary is a Text column — json.dumps there is correct."""
        src = "import json\nsync_job.summary = json.dumps({'ok': 1})\n"
        assert self._scan_source(src) == []

    def test_negative_plain_dict_passes(self):
        src = "from src.core.database.models import AuditLog\na = AuditLog(details={'x': 1})\n"
        assert self._scan_source(src) == []

    def test_negative_a_parameter_shadows_the_import(self):
        """Inside ``f`` the name is the parameter, not the model: the call's own scope decides."""
        src = (
            "import json\n"
            "from src.core.database.models import AuditLog\n"
            "def f(AuditLog):\n"
            "    return AuditLog(details=json.dumps({'x': 1}))\n"
        )
        assert self._scan_source(src) == []

    def test_would_be_missed_indirect_dumps_documented(self):
        """Known limitation: json.dumps assigned to a local first
        (``s = json.dumps(x); AuditLog(details=s)``) escapes this guard —
        the scan matches the call expression at the binding site only.
        The runtime TypeError in JSONType.process_bind_param is the
        backstop for that shape; reviewers own the residual."""
        src = (
            "import json\n"
            "from src.core.database.models import AuditLog\n"
            "s = json.dumps({'x': 1})\n"
            "a = AuditLog(details=s)\n"
        )
        assert self._scan_source(src) == []

    def test_would_be_missed_untyped_receiver_documented(self):
        """Known limitation (form B is name-based): assigning to a JSONType
        column NAME on a non-ORM object is flagged even though no DB write
        occurs — kept deliberately, since JSONType column names are
        distinctive and a false positive is a rename away."""
        src = "import json\nview_model.ai_policy = json.dumps({})\n"
        assert [h[1] for h in self._scan_source(src)] == [".ai_policy"]

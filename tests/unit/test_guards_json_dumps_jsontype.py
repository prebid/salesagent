"""Guard: JSONType columns must never receive json.dumps pre-serialized strings.

Disease: a writer passes ``json.dumps(...)`` into a JSONType (JSONB) column —
constructor kwarg or attribute assignment. The column type takes dicts/lists
directly; the str used to be silently coerced to ``{}`` (audit details,
principal platform_mappings, and self-serve signup tenant fields were all
emptied this way), and now raises TypeError at runtime.
This guard catches the mistake at test time instead.

The JSONType column inventory is mapper-derived (``Base.registry.mappers``),
not name-matched, so Text columns that legitimately store JSON strings
(e.g. ``SyncJob.summary``) are never dragged in. Form A (constructor kwarg)
is import-resolving like test_guards_orm_constructor_kwargs; form B
(attribute assignment) matches any ``obj.<attr> = json.dumps(...)`` where
``<attr>`` is a JSONType column name on any mapper — a name-based
approximation whose residual risk is documented in the meta-tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy.orm import class_mapper

from src.core.database.json_type import JSONType
from src.core.database.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_MODULE = "src.core.database.models"

# (file, "ClassName.attr" or ".attr") pairs permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _jsontype_cols_by_class() -> dict[str, set[str]]:
    """ORM class name -> attribute names of its JSONType columns."""
    out: dict[str, set[str]] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        cols = {
            attr.key
            for attr in class_mapper(cls).column_attrs
            if any(isinstance(c.type, JSONType) for c in attr.columns)
        }
        if cols:
            out[cls.__name__] = cols
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


def _scan_file(path: Path, by_class: dict[str, set[str]]) -> list[tuple[str, str, int]]:
    """(rel_path, target-description, lineno) per violation in one file."""
    all_json_cols = set().union(*by_class.values()) if by_class else set()
    tree = ast.parse(path.read_text())
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == MODELS_MODULE:
            for alias in node.names:
                bindings[alias.asname or alias.name] = alias.name
    rel = str(path.relative_to(REPO_ROOT))
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        # Form A: Model(col=json.dumps(...)) with Model imported from models.py
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            orm = bindings.get(node.func.id)
            if orm in by_class:
                for kw in node.keywords:
                    if kw.arg in by_class[orm] and _is_json_dumps(kw.value):
                        hits.append((rel, f"{orm}.{kw.arg}", node.lineno))
        # Form B: obj.col = json.dumps(...) where col is a JSONType column name
        if isinstance(node, ast.Assign) and _is_json_dumps(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr in all_json_cols:
                    hits.append((rel, f".{tgt.attr}", node.lineno))
    return hits


def _scan_src() -> list[tuple[str, str, int]]:
    by_class = _jsontype_cols_by_class()
    hits: list[tuple[str, str, int]] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        hits.extend(_scan_file(path, by_class))
    return hits


class TestJsonDumpsJsontypeGuard:
    def test_no_json_dumps_into_jsontype_columns(self):
        violations = [v for v in _scan_src() if (v[0], v[1]) not in ALLOWLIST]
        assert not violations, (
            "json.dumps bound to a JSONType column — the column takes dicts/lists "
            "directly; the str raises TypeError at bind time (was: silent {} data loss):\n"
            + "\n".join(f"  {f}:{line} {target} = json.dumps(...)" for f, target, line in violations)
        )

    def test_allowlist_entries_still_violate(self):
        """Stale-entry check: every allowlisted pair still has a live violation."""
        actual = {(v[0], v[1]) for v in _scan_src()}
        stale = ALLOWLIST - actual
        assert not stale, f"Allowlist entries no longer violating — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    def _scan_source(self, source: str) -> list[tuple[str, str, int]]:
        import tempfile

        by_class = _jsontype_cols_by_class()
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=REPO_ROOT, delete=False) as fh:
            fh.write(source)
            tmp = Path(fh.name)
        try:
            return _scan_file(tmp, by_class)
        finally:
            tmp.unlink()

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
        src = "import json\ntenant.policy_settings = json.dumps({'enabled': True})\n"
        assert [h[1] for h in self._scan_source(src)] == [".policy_settings"]

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
        src = "import json\nview_model.policy_settings = json.dumps({})\n"
        assert [h[1] for h in self._scan_source(src)] == [".policy_settings"]

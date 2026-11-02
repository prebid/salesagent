"""Guard: ORM constructions must only use kwargs the mapper actually has.

Disease: code constructs a models.py class with kwargs from an OLD schema
(``PushNotificationConfig(config_id=..., auth_type=...)``,
``GAMLineItem(currency_code=...)``). SQLAlchemy's declarative
constructor raises ``TypeError`` at runtime, a blanket ``except`` turns it into
an error flash, and the route ships broken — the admin webhook family was dead
in production this way with zero test coverage.

The scan is import-resolving, not name-matching: a call site is checked only
when its callee binds to an import from ``src.core.database.models`` (aliased
imports included), so Pydantic schemas that share an ORM class name (Product,
MediaBuy, Creative, ...) are not false positives, and ``... as DBFoo`` aliases
are not misses.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy.orm import class_mapper

from src.core.database.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_MODULE = "src.core.database.models"

# (file, class, kwarg) triples permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _mapped_attrs() -> dict[str, set[str]]:
    """ORM class name -> settable attribute names (columns, relationships, descriptors)."""
    out: dict[str, set[str]] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        attrs = {a.key for a in class_mapper(cls).attrs}
        attrs |= {k for k in vars(cls) if not k.startswith("_")}
        out[cls.__name__] = attrs
    return out


def _orm_bindings(tree: ast.AST) -> dict[str, str]:
    """Local name -> ORM class name, for names imported from models.py."""
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == MODELS_MODULE:
            for alias in node.names:
                bindings[alias.asname or alias.name] = alias.name
    return bindings


def _scan_file(path: Path, mapped: dict[str, set[str]]) -> list[tuple[str, str, list[str], int]]:
    """Return (rel_path, class_name, bad_kwargs, lineno) for each violating call."""
    tree = ast.parse(path.read_text())
    bindings = _orm_bindings(tree)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        orm_name = bindings.get(node.func.id)
        if orm_name is None or orm_name not in mapped:
            continue
        bad = [kw.arg for kw in node.keywords if kw.arg and kw.arg not in mapped[orm_name]]
        if bad:
            violations.append((str(path.relative_to(REPO_ROOT)), orm_name, bad, node.lineno))
    return violations


def _scan_src() -> list[tuple[str, str, list[str], int]]:
    mapped = _mapped_attrs()
    hits = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        hits.extend(_scan_file(path, mapped))
    return hits


class TestOrmConstructorKwargsGuard:
    def test_no_orm_construction_with_unknown_kwargs(self):
        """Every ORM construction in src/ uses only mapper attributes."""
        violations = [v for v in _scan_src() if (v[0], v[1]) not in ALLOWLIST]
        assert not violations, (
            "ORM constructions with kwargs the mapper does not have (runtime TypeError):\n"
            + "\n".join(f"  {f}:{line} {cls}(...) unknown kwargs: {bad}" for f, cls, bad, line in violations)
        )

    def test_allowlist_entries_still_violate(self):
        """Stale-entry check: every allowlisted (file, class) still has a violation."""
        actual = {(v[0], v[1]) for v in _scan_src()}
        stale = ALLOWLIST - actual
        assert not stale, f"Allowlist entries no longer violating — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    """The guard's scanner itself must catch/pass the right shapes."""

    def _scan_source(self, source: str) -> list[tuple[str, str, list[str], int]]:
        import tempfile

        mapped = _mapped_attrs()
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=REPO_ROOT, delete=False) as fh:
            fh.write(source)
            tmp = Path(fh.name)
        try:
            return _scan_file(tmp, mapped)
        finally:
            tmp.unlink()

    def test_positive_detects_unknown_kwarg(self):
        src = "from src.core.database.models import PushNotificationConfig\nc = PushNotificationConfig(config_id='x')\n"
        hits = self._scan_source(src)
        assert len(hits) == 1
        assert hits[0][1] == "PushNotificationConfig"
        assert hits[0][2] == ["config_id"]

    def test_positive_detects_aliased_import(self):
        """The tayg-era scanner's would-be-missed case: `... as DBFoo` aliases."""
        src = (
            "from src.core.database.models import PushNotificationConfig as DBConfig\nc = DBConfig(auth_type='none')\n"
        )
        hits = self._scan_source(src)
        assert [h[2] for h in hits] == [["auth_type"]]

    def test_negative_valid_construction_passes(self):
        src = (
            "from src.core.database.models import PushNotificationConfig\n"
            "c = PushNotificationConfig(id='x', tenant_id='t', principal_id='p', url='u')\n"
        )
        assert self._scan_source(src) == []

    def test_negative_same_named_pydantic_class_ignored(self):
        """A Product that is NOT imported from models.py is not checked."""
        src = "from src.core.schemas import Product\np = Product(publisher_properties=[])\n"
        assert self._scan_source(src) == []

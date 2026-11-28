"""Guard: ORM constructions must only use kwargs the mapper actually has.

Disease: code constructs a models.py class with kwargs from an OLD schema
(``PushNotificationConfig(config_id=..., auth_type=...)``,
``GAMLineItem(currency_code=...)``). SQLAlchemy's declarative
constructor raises ``TypeError`` at runtime, a blanket ``except`` turns it into
an error flash, and the route ships broken — the admin webhook family was dead
in production this way with zero test coverage.

The scan is import-resolving, not name-matching: a call site is checked only
when its callee denotes a mapped class as the call's own scope binds it
(``orm_references``: aliased imports and ``models.Foo`` included, a parameter or a
local import of the same name shadowing it), so Pydantic schemas that share an ORM
class name (Product, MediaBuy, Creative, ...) are not false positives, and
``... as DBFoo`` aliases are not misses.
"""

from __future__ import annotations

import ast

from sqlalchemy.orm import class_mapper

from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    iter_call_expressions,
    orm_classes,
    orm_references,
    repo_root,
    scan_rows,
)

# (file, "Class.kwarg") pairs permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _mapped_attrs() -> dict[str, set[str]]:
    """ORM class name -> settable attribute names (columns, relationships, descriptors)."""
    return {
        name: {a.key for a in class_mapper(cls).attrs} | {k for k in vars(cls) if not k.startswith("_")}
        for name, cls in orm_classes().items()
    }


def _scan_file(tree: ast.Module, relpath: str, mapped: dict[str, set[str]]) -> list[tuple[str, str, list[str], int]]:
    """Return (rel_path, class_name, bad_kwargs, lineno) for each violating call."""
    references = orm_references(relpath, tree)
    violations = []
    for call in iter_call_expressions(tree):
        orm_name = references.get(id(call.func))
        if orm_name is None or orm_name not in mapped:
            continue
        bad = [kw.arg for kw in call.keywords if kw.arg and kw.arg not in mapped[orm_name]]
        if bad:
            violations.append((relpath, orm_name, bad, call.lineno))
    return violations


def _scan_src() -> list[tuple[str, str, list[str], int]]:
    mapped = _mapped_attrs()
    return scan_rows([repo_root() / "src"], lambda tree, relpath: _scan_file(tree, relpath, mapped))


class TestOrmConstructorKwargsGuard:
    def test_no_orm_construction_with_unknown_kwargs(self):
        """Every ORM construction in src/ uses only mapper attributes, and every allowlist entry still violates."""
        assert_violations_match_allowlist(
            {(path, f"{cls}.{kwarg}") for path, cls, bad, _line in _scan_src() for kwarg in bad},
            ALLOWLIST,
            fix_hint=(
                "An ORM construction passes a kwarg the mapper does not have, which raises TypeError "
                "at runtime. Fix the kwarg or the model -- do not allowlist."
            ),
        )


class TestGuardMetaTests:
    """The guard's scanner itself must catch/pass the right shapes."""

    def _scan_source(self, source: str) -> list[tuple[str, str, list[str], int]]:
        return _scan_file(ast.parse(source), "src/x.py", _mapped_attrs())

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

    def test_positive_detects_a_construction_through_the_models_module(self):
        src = "from src.core.database import models\nc = models.PushNotificationConfig(config_id='x')\n"
        assert [(h[1], h[2]) for h in self._scan_source(src)] == [("PushNotificationConfig", ["config_id"])]

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

    def test_negative_a_parameter_shadows_the_import(self):
        """Inside ``f`` the name is the parameter, not the model: the call's own scope decides."""
        src = (
            "from src.core.database.models import PushNotificationConfig\n"
            "def f(PushNotificationConfig):\n"
            "    return PushNotificationConfig(config_id='x')\n"
        )
        assert self._scan_source(src) == []

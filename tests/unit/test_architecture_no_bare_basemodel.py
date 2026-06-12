"""Guard: No bare BaseModel inheritance in client-facing schema classes.

Schema classes in the scanned packages must inherit from SalesAgentBaseModel,
AdCPBaseModel, or a Library* type that carries model_config with
ConfigDict(extra=get_pydantic_extra_mode()). Bare pydantic.BaseModel /
pydantic.RootModel lack the environment-based extra-field policy (Pattern #7),
which means:
  - Production: extra fields would raise ValidationError (Pydantic default)
    instead of being silently ignored for forward compatibility
  - Development: extra fields would NOT be rejected, hiding spec drift

Scanning approach: AST parse the files in the scanned packages, find ClassDef
nodes whose bases include ``BaseModel``/``RootModel`` -- either as a bare name
(``BaseModel``) or as an attribute access (``pydantic.BaseModel``). These are
violations unless they explicitly set ``model_config = ConfigDict(...)``.

Scope rationale: this guard targets *client-facing* AdCP schemas and the REST
request bodies that parse buyer input. The scanned set is therefore the
``src/core/schemas/`` package PLUS ``src/routes/api_v1.py`` (REST request
DTOs). It is deliberately NOT a whole-``src/`` scan: a repo-wide scan also
catches internal config schemas, AI-agent result models, async helper DTOs,
and context objects that are not on the AdCP wire boundary and have no
Pattern-#7 obligation. Widening to those is a separate concern.

Allowlist: Violations that predate this guard are tracked here. The list
can only shrink. Every entry must have a matching ``# FIXME(#<gh-issue>)``
comment at the source location. Currently empty — the api_v1 REST request
bodies were migrated to SalesAgentBaseModel (#1442).
"""

import ast
from pathlib import Path

# ── Allowlist ────────────────────────────────────────────────────────────
# Format: (filename, class_name)
# Every entry must have a matching # FIXME(#<gh-issue>) comment at the source
# location. The list can only shrink.
#
# Empty: the api_v1.py REST request bodies that used to live here were migrated
# to SalesAgentBaseModel (#1442), so they now carry the Pattern #7 extra-field
# policy and are no longer bare-BaseModel violations.
ALLOWLIST: set[tuple[str, str]] = set()

# Base class names that carry no Pattern-#7 extra-field policy on their own.
_BARE_BASE_NAMES = frozenset({"BaseModel", "RootModel"})


def _scanned_files() -> list[Path]:
    """Files in scope: the schemas package plus the REST request-body module.

    See the module docstring for why this is a curated list, not a whole-src
    scan.
    """
    files: list[Path] = []
    schemas_dir = Path("src/core/schemas")
    if not schemas_dir.is_dir():
        raise FileNotFoundError("src/core/schemas/ package not found")
    files.extend(sorted(schemas_dir.glob("*.py")))

    api_v1 = Path("src/routes/api_v1.py")
    if not api_v1.is_file():
        raise FileNotFoundError("src/routes/api_v1.py not found")
    files.append(api_v1)

    return files


def _has_model_config_in_body(class_node: ast.ClassDef) -> bool:
    """Check if a ClassDef sets model_config in its body.

    Looks for: model_config = ConfigDict(...) or model_config: ... = ...
    """
    for stmt in class_node.body:
        # model_config = ConfigDict(...)
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "model_config":
                    return True
        # model_config: ClassVar = ConfigDict(...)
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == "model_config":
                return True
    return False


def _base_label(base: ast.expr) -> str | None:
    """Return the bare base-class name a base node refers to, if any.

    Handles three AST shapes:
      - ``ast.Name`` for ``class X(BaseModel)`` (``from pydantic import BaseModel``)
      - ``ast.Attribute`` for ``class X(pydantic.BaseModel)`` -- returns the
        trailing attribute (``BaseModel``)
      - ``ast.Subscript`` for ``class X(RootModel[int])`` /
        ``class X(pydantic.RootModel[int])`` -- recurses into the subscripted
        value so the generic parameter is ignored

    Returns the attribute/name (e.g. ``"BaseModel"``/``"RootModel"``) so the
    caller can match it against the set of bases lacking a Pattern-#7 policy.
    Returns None for anything else (SalesAgentBaseModel, AdCPBaseModel,
    Library* types, mixins, etc.).
    """
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Subscript):
        return _base_label(base.value)
    return None


def _inherits_bare_basemodel(class_node: ast.ClassDef) -> bool:
    """Check if any base is bare ``BaseModel``/``RootModel``.

    Matches both ``BaseModel`` (ast.Name) and ``pydantic.BaseModel``
    (ast.Attribute), plus the RootModel equivalents. Does NOT flag Library*
    types, SalesAgentBaseModel, AdCPBaseModel, NestedModelSerializerMixin, or
    any other base whose name is not in ``_BARE_BASE_NAMES``.
    """
    return any(_base_label(base) in _BARE_BASE_NAMES for base in class_node.bases)


def _find_violations(source: str, filename: str) -> list[tuple[str, str]]:
    """Return (filename, class_name) for every bare-base class lacking config.

    Pure function over source text so it can be exercised by the self-test
    with inline snippets, not just real files.
    """
    tree = ast.parse(source)
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _inherits_bare_basemodel(node):
            continue
        if _has_model_config_in_body(node):
            continue
        found.append((filename, node.name))
    return found


class TestNoBarePydanticBaseModelSelfTest:
    """The matcher itself must detect positives and ignore negatives.

    Without this, the guard's green is meaningless: it currently matches zero
    sites in the schemas package, so a regression in the matcher would never
    be noticed.
    """

    def test_detects_bare_name_basemodel(self):
        """``class X(BaseModel)`` (imported name) is flagged."""
        src = "from pydantic import BaseModel\n\nclass Bare(BaseModel):\n    x: int\n"
        assert _find_violations(src, "snippet.py") == [("snippet.py", "Bare")]

    def test_detects_attribute_basemodel(self):
        """``class X(pydantic.BaseModel)`` (attribute access) is flagged."""
        src = "import pydantic\n\nclass Bare(pydantic.BaseModel):\n    x: int\n"
        assert _find_violations(src, "snippet.py") == [("snippet.py", "Bare")]

    def test_detects_bare_rootmodel(self):
        """``class X(RootModel[...])`` is flagged (no Pattern-#7 policy)."""
        src = "from pydantic import RootModel\n\nclass BareRoot(RootModel[int]):\n    pass\n"
        assert _find_violations(src, "snippet.py") == [("snippet.py", "BareRoot")]

    def test_detects_attribute_rootmodel(self):
        """``class X(pydantic.RootModel[...])`` is flagged."""
        src = "import pydantic\n\nclass BareRoot(pydantic.RootModel[str]):\n    pass\n"
        assert _find_violations(src, "snippet.py") == [("snippet.py", "BareRoot")]

    def test_ignores_proper_base(self):
        """A SalesAgentBaseModel/Library* subclass is NOT flagged."""
        src = (
            "class Good(SalesAgentBaseModel):\n    x: int\n\n"
            "class AlsoGood(LibraryProduct):\n    y: int\n\n"
            "class Mixin(AdCPBaseModel):\n    z: int\n"
        )
        assert _find_violations(src, "snippet.py") == []

    def test_ignores_bare_with_explicit_model_config(self):
        """A bare BaseModel that sets model_config is NOT flagged."""
        src = (
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class HasConfig(BaseModel):\n"
            "    model_config = ConfigDict(extra='ignore')\n"
            "    x: int\n"
        )
        assert _find_violations(src, "snippet.py") == []

    def test_positive_and_negative_in_same_module(self):
        """Only the bare class is reported when both shapes coexist."""
        src = (
            "from pydantic import BaseModel\n\n"
            "class Good(SalesAgentBaseModel):\n    x: int\n\n"
            "class Bad(BaseModel):\n    y: int\n"
        )
        assert _find_violations(src, "snippet.py") == [("snippet.py", "Bad")]


class TestNoBarePydanticBaseModel:
    """Schema classes must not inherit bare BaseModel/RootModel without model_config."""

    def test_no_bare_basemodel_in_schemas(self):
        """Every class inheriting BaseModel/RootModel must set model_config or be allowlisted."""
        violations = []
        for schema_file in _scanned_files():
            source = schema_file.read_text()
            filename = schema_file.name

            for found_file, class_name in _find_violations(source, filename):
                if (found_file, class_name) in ALLOWLIST:
                    continue
                # Re-derive lineno for the message.
                tree = ast.parse(source)
                lineno = next(
                    (n.lineno for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name),
                    0,
                )
                violations.append(
                    f"{found_file}:{lineno} class {class_name} inherits bare "
                    f"BaseModel/RootModel without model_config = ConfigDict(...). "
                    f"Use SalesAgentBaseModel or add model_config."
                )

        assert not violations, (
            "Schema classes inherit bare pydantic.BaseModel/RootModel without "
            "model_config (missing Pattern #7 extra-field policy):\n" + "\n".join(f"  - {v}" for v in violations)
        )

    def test_allowlist_entries_are_still_violations(self):
        """Every allowlist entry must still be a real violation.

        If you fix a violation, remove it from ALLOWLIST. This test
        catches stale entries.
        """
        still_violations: set[tuple[str, str]] = set()
        for schema_file in _scanned_files():
            source = schema_file.read_text()
            still_violations.update(_find_violations(source, schema_file.name))

        stale = ALLOWLIST - still_violations
        assert not stale, "Allowlist entries that are no longer violations (remove them):\n" + "\n".join(
            f"  - {f}:{c}" for f, c in sorted(stale)
        )

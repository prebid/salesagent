"""Guard: a response model may not default a field the repository owns.

``confirmed_at`` and ``revision`` are COLUMNS. The repository stamps the first
(write-once, when a buy reaches a seller-committed status) and bumps the second on
every mutating write. A response model that also supplies a value for either is a
second producer of the same state, and the two disagree the moment the row moves.

That is not hypothetical: ``CreateMediaBuySuccess`` shipped
``confirmed_at = Field(default_factory=lambda: datetime.now(UTC))`` and
``revision: int = 1``. No construction site passed either, the row returned by
``create_from_request`` was discarded one function away, and the response reported a
seller-commitment instant for buys whose column was NULL. It survived three review
rounds of review on PR #1941 because a default is invisible in the places
you look: no assignment for the write-seam guard to see, no missing-argument error at
any call site, and a test that asserted ``revision == 1`` read as passing when it was
in fact pinning the fabrication.

The rule is therefore about the DEFAULT, not about any call site: with no default,
omission is a construction error and every producer must say where its value came
from.

There is no longer a second constructor for the not-quite-envelope case.
``CreateMediaBuySuccess.carrier()`` was it, and it is deleted (commit ecfdd7771): an
adapter returns ``src.adapters.base.AdapterCreateResult`` / ``AdapterUpdateResult``, so
every construction of a wire model IS the buyer's envelope and passes the row's values
through ``sync_success``. A test that wants one states literal values and says why.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import format_failure, parse_module, repo_root

# Fields whose values a repository owns.
PERSISTED_FIELDS = {"confirmed_at", "revision"}

# (file, class, field) -> why this default is allowed to stand.
# Shrink-only: a new entry needs the same kind of argument, in writing.
ALLOWED_DEFAULTS: dict[tuple[str, str, str], str] = {}

_KNOWN_BAD = """
class SomeResponse(Base):
    confirmed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
"""

_KNOWN_BAD_LITERAL = """
class SomeResponse(Base):
    revision: int = 1
"""

_KNOWN_GOOD = """
class SomeResponse(Base):
    confirmed_at: AwareDatetime
    revision: int

    @classmethod
    def carrier(cls, **kwargs):
        kwargs.setdefault("revision", 1)
        return cls(**kwargs)
"""


def find_persisted_defaults(tree: ast.Module, relpath: str) -> list[str]:
    violations: list[str] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            # A defaulted field is an annotated assignment WITH a value:
            # `revision: int = 1` or `confirmed_at: X = Field(...)`. A bare
            # `revision: int` is the required declaration this guard wants.
            if not isinstance(stmt, ast.AnnAssign) or stmt.value is None:
                continue
            if not isinstance(stmt.target, ast.Name) or stmt.target.id not in PERSISTED_FIELDS:
                continue
            if (relpath, cls.name, stmt.target.id) in ALLOWED_DEFAULTS:
                continue
            violations.append(f"{relpath}:{stmt.lineno} ({cls.name}.{stmt.target.id})")
    return violations


def _scan(repo) -> list[str]:
    violations: list[str] = []
    schemas = repo / "src" / "core" / "schemas"
    if not schemas.exists():
        return violations
    for path in sorted(schemas.rglob("*.py")):
        tree = parse_module(path)
        if tree is not None:
            violations.extend(find_persisted_defaults(tree, str(path.relative_to(repo))))
    return violations


@pytest.mark.arch_guard
def test_no_response_side_defaults_for_repository_owned_fields() -> None:
    violations = _scan(repo_root())
    assert not violations, format_failure(
        summary="confirmed_at/revision are columns the repository owns; a response model may not default them",
        violations=violations,
        fix_hint=(
            "Delete the default and pass the persisted row's value at every "
            "buyer-facing construction site (sync_success). A construction that is NOT "
            "the buyer's envelope is an adapter's return, and that is a different type: "
            "src.adapters.base.AdapterCreateResult / AdapterUpdateResult."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_every_allowlisted_default_still_exists() -> None:
    """Shrink-only: an entry whose site is gone must be deleted, not left to rot."""
    repo = repo_root()
    stale = []
    for relpath, clsname, field in ALLOWED_DEFAULTS:
        tree = parse_module(repo / relpath)
        found = tree is not None and any(
            isinstance(c, ast.ClassDef)
            and c.name == clsname
            and any(
                isinstance(s, ast.AnnAssign)
                and s.value is not None
                and isinstance(s.target, ast.Name)
                and s.target.id == field
                for s in c.body
            )
            for c in ast.walk(tree)
        )
        if not found:
            stale.append(f"{relpath}::{clsname}.{field}")
    assert not stale, format_failure(
        summary="Allowlist entries whose site no longer exists",
        violations=stale,
        fix_hint="The default is gone — delete the ALLOWED_DEFAULTS entry too.",
        docs_link="docs/development/structural-guards.md",
    )


def _probe(tmp_path, source: str) -> list[str]:
    probe = tmp_path / "src" / "core" / "schemas" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")
    return _scan(tmp_path)


@pytest.mark.arch_guard
@pytest.mark.parametrize("source", [_KNOWN_BAD, _KNOWN_BAD_LITERAL], ids=["default-factory", "literal"])
def test_detector_catches_known_bad(tmp_path, source: str) -> None:
    assert _probe(tmp_path, source), "Detector must flag a defaulted repository-owned field"


@pytest.mark.arch_guard
def test_detector_passes_known_good(tmp_path) -> None:
    """A bare declaration is the fix — and a setdefault INSIDE a named factory is not
    a field default, which is the distinction the whole lane turns on."""
    assert not _probe(tmp_path, _KNOWN_GOOD)

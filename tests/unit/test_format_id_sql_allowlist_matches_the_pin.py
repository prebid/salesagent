"""The SQL constraint's FormatId property list equals the pinned schema's.

``validate_format_ids`` is PL/pgSQL and cannot read ``core/format-id.json``: a migration has to
be a fixed structural step, and one that consulted the installed ``adcp`` wheel at runtime would
apply differently depending on which version happened to be installed when it ran. So the
property list is retyped in the DDL, and this is what makes retyping it safe.

THE DETECTOR THIS REPLACES WAS AN e2e ROW. The list was five properties while the pin declared
six, so the database refused a product carrying ``pixel_ratio`` -- a property the spec declares
and marks optional. Nothing caught it until a scenario crossed a real ``INSERT INTO products``
on the ``e2e_rest`` transport; ``tests/conftest_db.py`` builds the in-process schema with
``Base.metadata.create_all``, so this function does not exist there at all
(salesagent-3cs7o.42). salesagent-knd96.

TWO EARLIER ATTEMPTS WERE REMOVED AND SHOULD NOT BE REBUILT. One parsed the migration's source
text; one downgraded and re-upgraded a module-scoped shared database to ask Postgres directly,
which is cross-test state mutation and a flake waiting for a neighbour (salesagent-y7zv3). This
one touches no database and parses no DDL text: it calls the migration's OWN ``_function`` to
get the string ``upgrade()`` installs, and reads its one constant through the AST.

WHAT THIS DOES NOT GRADE: that a seller can store and serve a ``pixel_ratio`` product. Two
scenarios already do -- BR-UC-FORMAT-SATISFACTION's density pair, whose ``e2e_rest`` rows were
the two that went red on this constraint. This grades only that the retyped list has not
drifted from the pin, which is the one thing those rows cannot see until a seller authors a
seventh property and is refused.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import types

import adcp

#: The revision that owns the constraint today. Named rather than globbed: a later revision
#: replacing the function must update this test deliberately, which is the review moment where
#: someone checks the new list against the pin.
_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "a7c31e0f45b2_allow_pixel_ratio_in_format_ids.py"
)


def _pinned_properties() -> set[str]:
    """Every property ``core/format-id.json`` declares, from the installed pin's own bundle."""
    schema = pathlib.Path(adcp.__file__).parent / "_schemas" / "3.2" / "core" / "format-id.json"
    return set(json.loads(schema.read_text())["properties"])


def _sql_names(sql_array: str) -> set[str]:
    """The quoted names inside a PL/pgSQL ``ARRAY[...]`` literal."""
    return set(re.findall(r"'([^']+)'", sql_array))


def _migration_module() -> types.ModuleType:
    """The revision, imported by path so its own ``_function`` can be called.

    Imported rather than read as text because the DDL is BUILT, not stored: there is one
    template and the upgrade passes its allowlist in. Parsing the file for a DDL constant --
    which an earlier version of this test did -- grades a string that no longer exists in the
    shape the migration actually uses.
    """
    spec = importlib.util.spec_from_file_location(_MIGRATION.stem, _MIGRATION)
    assert spec is not None and spec.loader is not None, f"{_MIGRATION} is not importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _allowed_constant() -> str:
    """The revision's ``_ALLOWED`` SQL array, read through the AST.

    Through the AST and not off the imported module, deliberately: this assertion is about what
    the FILE declares, and a loud failure when the name is absent is what makes this test fail
    honestly against a revision that does not have it rather than passing on a default.
    """
    for node in ast.parse(_MIGRATION.read_text()).body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "_ALLOWED" for t in targets):
                assert node.value is not None
                value = ast.literal_eval(node.value)
                assert isinstance(value, str), f"_ALLOWED is {type(value).__name__}, expected the SQL array string"
                return value
    raise AssertionError(f"{_MIGRATION.name} declares no _ALLOWED; this guard has nothing to compare")


def test_the_migrations_constant_matches_the_pinned_schema() -> None:
    pinned = _pinned_properties()
    # The population, asserted: a schema read that returned nothing would make every
    # comparison below pass against an empty set.
    assert len(pinned) > 2, f"core/format-id.json declared {len(pinned)} properties; the read is wrong"
    assert _sql_names(_allowed_constant()) == pinned, (
        "the migration's _ALLOWED and core/format-id.json's properties disagree. The pin is the "
        "authority: add the property to _ALLOWED, in a NEW revision if a7c31e0f45b2 has shipped. "
        "Do not widen the SQL check to accept anything."
    )


def test_the_installed_ddl_enforces_exactly_the_pinned_set() -> None:
    """What ``upgrade()`` installs, not what the file declares beside it.

    Built by calling the migration's own ``_function`` with the same arguments ``upgrade()``
    passes, so this grades the string the database receives. The constant and the DDL are read
    separately because a guard on the declaration alone passes while the installed SQL says
    something else.
    """
    module = _migration_module()
    ddl = module._function(module._ALLOWED, with_pixel_ratio=True)
    arrays = re.findall(r"allowed_keys text\[\] := (ARRAY\[[^\]]+\])", ddl)
    assert len(arrays) == 1, f"expected exactly one allowed_keys array in the installed DDL, found {len(arrays)}"
    assert _sql_names(arrays[0]) == _pinned_properties(), (
        "the DDL upgrade() installs enforces a set that disagrees with the pin. This is the copy "
        "the database applies, so this is the one that refuses writes."
    )


def test_the_refusal_message_names_the_set_the_function_enforces() -> None:
    """The RAISE text and the array it describes cannot disagree.

    THIS IS THE ASSERTION THAT EARNED ITS KEEP. ``f972939dd331`` hardcoded the prose list beside
    the array, so widening one left the message naming a set the function no longer enforced --
    and the revision that replaced it took the list TWICE, hand-typed at both call sites, which
    is the same defect in a DRY wrapper. It derives the text from the array now, and this is
    what holds that: the two are compared, rather than the literal's absence being asserted.
    A guard on the array alone cannot see a message that drifted from it.
    """
    module = _migration_module()
    ddl = module._function(module._ALLOWED, with_pixel_ratio=True)

    enforced = _sql_names(re.findall(r"allowed_keys text\[\] := (ARRAY\[[^\]]+\])", ddl)[0])
    announced = re.findall(r"FormatId has invalid properties\. Allowed: ([^.]+)\.", ddl)
    assert len(announced) == 1, f"expected exactly one 'Allowed:' message in the DDL, found {len(announced)}"
    assert {name.strip() for name in announced[0].split(",")} == enforced, (
        f"the refusal message announces {announced[0]!r} while the function enforces "
        f"{sorted(enforced)}. Derive the message from allowed_keys; do not retype it."
    )


def test_the_downgrade_drops_exactly_pixel_ratio() -> None:
    """The reverse direction is the same function with one name removed, and nothing else.

    Asserted because ``downgrade()`` passes its allowlist as a SECOND hand-typed literal -- the
    one place the DRY wrapper still takes the list twice. A downgrade that dropped two
    properties, or kept the pixel_ratio clause, would revert more than this revision added.
    """
    module = _migration_module()
    up = _sql_names(
        re.findall(
            r"allowed_keys text\[\] := (ARRAY\[[^\]]+\])", module._function(module._ALLOWED, with_pixel_ratio=True)
        )[0]
    )
    down_ddl = module._function("ARRAY['agent_url', 'id', 'width', 'height', 'duration_ms']", with_pixel_ratio=False)
    down = _sql_names(re.findall(r"allowed_keys text\[\] := (ARRAY\[[^\]]+\])", down_ddl)[0])

    assert up - down == {"pixel_ratio"}, f"the downgrade removes {sorted(up - down)}, expected exactly pixel_ratio"
    assert "pixel_ratio" not in down_ddl, "the downgrade still carries the pixel_ratio validation clause"

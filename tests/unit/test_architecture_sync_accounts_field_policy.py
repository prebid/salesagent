"""Guard: ONE field-policy table decides every ``sync_accounts`` entry field.

The Core Invariant of salesagent-gcze: every field a ``sync_accounts`` entry can
carry -- declared on the request arms OR arriving via ``extra="allow"`` -- has
exactly ONE declared disposition per entry mode, recorded in a single field-policy
table, and ALL THREE field-application sites (create, provisioning re-sync,
settings-update) are driven by that table. A field cannot then be honored on one
site and silently discarded on another.

**This guard REPLACES tests/unit/test_architecture_sync_accounts_handler_symmetry.py.**
That guard detects an applied field by AST-matching ``changes["<literal>"] = ...``.
Once application is a table walk (``changes[field] = value`` -- a non-Constant
slice) its reader returns the EMPTY SET for both arms, so
``test_settable_fields_are_applied_by_both_entry_handlers`` would pass while
proving nothing. The same change must therefore DELETE that file together with
both of its allowlists (``_KNOWN_ASYMMETRIC``, ``_KNOWN_DROPPED_BY_BOTH``): the
table IS the record, so there is no allowlist left to grow and no laundering
question about which debt a field currently sits in.

Representation-tolerant BY DESIGN. This is the TDD-red artifact written before
the table exists (salesagent-j1c0.14), so it pins the INVARIANT, not a data
structure: any module-level mapping under one of ``_ACCEPTED_TABLE_NAMES``, whose
values expose a disposition per mode either as a mapping or as attributes, and
whose dispositions are either bare strings from the closed set or objects
carrying ``kind`` + ``citation``. Pick whichever shape reads best in
``accounts.py``; only the invariant is fixed.

What it does NOT do: assert any particular field's disposition. Which disposition
each field gets is graded behaviorally in BR-UC-011 (the five
entry-field-disposition scenarios), because a table row is a claim about the wire
and only a wire scenario can hold it to that.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.unit._architecture_helpers import REPO_ROOT, assert_violations_match_allowlist

_ACCOUNTS_MODULE = "src.core.tools.accounts"
_ACCOUNTS_SRC = REPO_ROOT / "src/core/tools/accounts.py"

#: Either name is fine -- the table is module-internal, so a leading underscore
#: is idiomatic, but a public name is equally valid.
_ACCEPTED_TABLE_NAMES = ("FIELD_POLICY", "_FIELD_POLICY", "ENTRY_FIELD_POLICY", "_ENTRY_FIELD_POLICY")

#: The two entry modes the request item ``oneOf`` defines. Accepted under either
#: spelling so the table can mirror whichever the implementation uses.
_MODES: dict[str, tuple[str, ...]] = {
    "provisioning": ("provisioning", "PROVISIONING"),
    "settings_update": ("settings_update", "settings-update", "SETTINGS_UPDATE"),
}

#: Closed set of dispositions (salesagent-gcze plan step 3). ``applied`` is the
#: only one that needs no citation: it means the buyer's value reaches
#: persistence, which the BR-UC-011 scenarios grade directly.
_DISPOSITIONS = {
    "applied",
    "spec_forbidden",
    "local_extension",
    "ignored_by_design",
    "rejected",
}

#: Routing/identity keys, not settings: ``account`` selects the arm and
#: brand/operator are the natural key.
_ROUTING_KEYS = {"account", "brand", "operator"}


# ---------------------------------------------------------------------------
# In-scope field set: declared U extra-read
# ---------------------------------------------------------------------------


def _declared_entry_fields() -> set[str]:
    """Settable fields BOTH request arms declare, minus the routing keys."""
    from adcp.types.generated_poc.account.sync_accounts_request import Accounts, Accounts1

    return (set(Accounts.model_fields) & set(Accounts1.model_fields)) - _ROUTING_KEYS


def _extra_read_entry_fields() -> set[str]:
    """Field names ``accounts.py`` reads off an entry via ``getattr``.

    Both request arms are ``extra="allow"``, so a buyer can send a field the
    generated models never declare and production can still read it --
    ``governance_agents`` is exactly that, and it is why a table keyed only on
    ``model_fields`` would leave a live field with no disposition. The declared
    set alone is the aperture bug that let the omission-wipe hide.
    """
    tree = ast.parse(_ACCOUNTS_SRC.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        target, attr = node.args[0], node.args[1]
        if not (isinstance(target, ast.Name) and target.id == "entry"):
            continue
        if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
            names.add(attr.value)
    return names - _ROUTING_KEYS


def _in_scope_entry_fields() -> set[str]:
    return _declared_entry_fields() | _extra_read_entry_fields()


# ---------------------------------------------------------------------------
# Table access + normalization
# ---------------------------------------------------------------------------


def _load_policy_table() -> Mapping[str, Any]:
    """The field-policy table, or a loud failure naming the contract."""
    module = importlib.import_module(_ACCOUNTS_MODULE)
    for name in _ACCEPTED_TABLE_NAMES:
        table = getattr(module, name, None)
        if table is not None:
            assert isinstance(table, Mapping), f"{_ACCOUNTS_MODULE}.{name} must be a mapping, got {type(table)!r}"
            return table
    raise AssertionError(
        f"{_ACCOUNTS_MODULE} exposes no field-policy table (looked for {' / '.join(_ACCEPTED_TABLE_NAMES)}).\n"
        "salesagent-gcze Core Invariant: ONE table maps every in-scope sync_accounts entry field to a\n"
        "disposition per entry mode, and all three application sites (create, provisioning re-sync,\n"
        "settings-update) are driven by it. Shape: {field: {'provisioning': <disposition>,\n"
        "'settings_update': <disposition>}} where a disposition is a name from\n"
        f"{sorted(_DISPOSITIONS)} -- as a bare string for 'applied', and carrying a v3.1.1 citation\n"
        "(object with .kind/.citation, or 'kind(<citation>)') for every other value."
    )


def _disposition_for(entry: Any, mode: str) -> Any:
    """The disposition a table row declares for ``mode`` (mapping or attribute)."""
    for spelling in _MODES[mode]:
        if isinstance(entry, Mapping) and spelling in entry:
            return entry[spelling]
        value = getattr(entry, spelling.replace("-", "_"), None)
        if value is not None:
            return value
    raise AssertionError(
        f"table row {entry!r} declares no disposition for mode {mode!r} "
        f"(accepted keys/attributes: {_MODES[mode]}). Every field needs a disposition in BOTH modes -- "
        "'we never thought about this arm' is the defect the table exists to make unrepresentable."
    )


def _kind_and_citation(disposition: Any) -> tuple[str, str]:
    """Normalize a disposition to ``(kind, citation)``; citation may be ''."""
    kind = getattr(disposition, "kind", None)
    if kind is not None:
        return str(getattr(kind, "value", kind)), str(getattr(disposition, "citation", "") or "")
    text = str(getattr(disposition, "value", disposition)).strip()
    if "(" in text and text.endswith(")"):
        head, _, tail = text.partition("(")
        return head.strip(), tail[:-1].strip()
    return text, ""


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_field_policy_table_covers_every_in_scope_entry_field():
    """Table keys == in-scope fields: no field exists without a disposition.

    Both directions matter. A field with no row is the salesagent-ck9v disease
    (accepted on the wire, applied by some sites and not others). A row with no
    field is a stale decision that will silently stop being enforced.
    """
    table = _load_policy_table()
    assert_violations_match_allowlist(
        {(field,) for field in _in_scope_entry_fields()},
        {(field,) for field in table},
        fix_hint=(
            "The table IS the record — read 'new violations' as fields with NO disposition and 'stale "
            "entries' as rows for fields that left the request surface.\n"
            "In-scope = fields both request arms declare, PLUS any name accounts.py reads off an entry via "
            "getattr (extra='allow' means a buyer can send an undeclared field and production still reads it)."
        ),
    )


def test_every_field_declares_a_disposition_in_both_modes():
    """Each row carries a disposition from the closed set for BOTH entry modes."""
    table = _load_policy_table()
    bad: list[str] = []
    for field, row in table.items():
        for mode in _MODES:
            kind, _ = _kind_and_citation(_disposition_for(row, mode))
            if kind not in _DISPOSITIONS:
                bad.append(f"{field}/{mode} = {kind!r}")
    assert not bad, (
        f"dispositions outside the closed set {sorted(_DISPOSITIONS)}: {bad}.\n"
        "The set is closed on purpose: 'undecided' is what the two deleted allowlists encoded, and it is "
        "exactly what let a field sit in debt indefinitely."
    )


def test_every_non_applied_disposition_carries_a_citation():
    """Anything but ``applied`` is a refusal to honor the buyer -- it must cite why.

    ``applied`` needs no citation (the buyer's value reaches persistence, graded
    by the BR-UC-011 scenarios). Every other disposition tells the buyer their
    field will not take effect, which under the no-quiet-failure rule is only
    acceptable when it is a DECLARED decision traceable to the pinned spec.
    """
    table = _load_policy_table()
    uncited: list[str] = []
    for field, row in table.items():
        for mode in _MODES:
            kind, citation = _kind_and_citation(_disposition_for(row, mode))
            if kind != "applied" and not citation:
                uncited.append(f"{field}/{mode} = {kind}")
    assert not uncited, (
        f"non-applied dispositions without a citation: {uncited}.\n"
        "Cite the v3.1.1 artifact that decides it (schema pointer, prose file, or the local-extension "
        "rationale + tracking issue). An uncited refusal is indistinguishable from an oversight."
    )


def _string_literal_changes_keys(function_name: str) -> set[str]:
    """Field names ``function_name`` writes with a hand-written ``changes["x"]``."""
    tree = ast.parse(_ACCOUNTS_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == function_name):
            continue
        return _literal_subscript_targets(node)
    raise AssertionError(f"{function_name} not found in {_ACCOUNTS_SRC}")


def _literal_subscript_targets(node: ast.AST) -> set[str]:
    """``changes["<literal>"] = ...`` keys assigned anywhere under ``node``."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        for target in sub.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "changes"
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                found.add(target.slice.value)
    return found


def test_no_handler_applies_a_field_by_hand_written_literal():
    """Both update sites walk the table -- neither names a field in source.

    This is the half the old symmetry guard could not express. As long as a
    handler writes ``changes["billing"] = ...`` by hand, the table is
    documentation sitting next to the real decision, and the next field added to
    one arm only is still possible. When application is
    ``changes[field] = value`` over the table, divergence between the arms is not
    expressible in the first place.
    """
    offenders = {
        handler: sorted(_string_literal_changes_keys(handler))
        for handler in ("_account_fields_changed", "_process_settings_update_entry")
    }
    offenders = {handler: fields for handler, fields in offenders.items() if fields}
    assert not offenders, (
        f"field application is still hand-written per handler: {offenders}.\n"
        "Drive `changes` from the field-policy table (one walk, both arms) so a per-arm omission cannot "
        "be written. The create site generalizes as the same walk with existing=None -- "
        "_resolve_notification_configs(entry, None) is already called that way."
    )


class TestMatcherModelsTheForm:
    """Self-tests: the readers find real names and can actually fail."""

    def test_literal_reader_finds_a_planted_assignment(self):
        """Proves the AST reader is not vacuously empty after the refactor.

        Asserted against a fixture source rather than against ``accounts.py``,
        so it keeps its teeth once the real handlers stop using literals --
        a self-test that flips red on a correct implementation is worse than none.
        """
        planted = ast.parse(
            "def f(entry, existing):\n"
            "    changes = {}\n"
            "    changes['billing'] = entry.billing\n"
            "    changes[field] = value\n"
            "    other['payment_terms'] = 1\n"
        )
        assert _literal_subscript_targets(planted) == {"billing"}

    def test_getattr_reader_finds_the_extra_field(self):
        """``governance_agents`` is in scope despite not being a declared field.

        The precise finding the old guard got backwards: its ``model_fields``
        aperture concluded "dead code", while both arms are ``extra='allow'`` and
        the provisioning handler really does read the buyer's list.
        """
        assert "governance_agents" in _extra_read_entry_fields()
        assert "governance_agents" not in _declared_entry_fields()

    def test_in_scope_set_is_the_seven_fields_the_audit_enumerated(self):
        """Pins the aperture itself, so a shrinking table cannot pass by shrinking scope."""
        assert _in_scope_entry_fields() >= {
            "billing",
            "billing_entity",
            "governance_agents",
            "notification_configs",
            "payment_terms",
            "preferred_reporting_protocol",
            "sandbox",
        }

    def test_disposition_normalizer_accepts_both_shapes(self):
        """String and object dispositions normalize identically."""

        class _Obj:
            kind = "spec_forbidden"
            citation = "sync-accounts-request.json#/properties/accounts/items/oneOf/1/allOf/2"

        assert _kind_and_citation("applied") == ("applied", "")
        assert _kind_and_citation("rejected(UNSUPPORTED_FEATURE, core/account.json#/properties/sandbox)") == (
            "rejected",
            "UNSUPPORTED_FEATURE, core/account.json#/properties/sandbox",
        )
        assert _kind_and_citation(_Obj())[0] == "spec_forbidden"
        assert _kind_and_citation(_Obj())[1].endswith("allOf/2")

    def test_accounts_source_is_the_file_under_guard(self):
        assert _ACCOUNTS_SRC.is_file(), f"{_ACCOUNTS_SRC} missing -- the AST readers would silently no-op"
        assert Path(importlib.import_module(_ACCOUNTS_MODULE).__file__).resolve() == _ACCOUNTS_SRC.resolve()

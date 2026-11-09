"""The coverage rule of the persistence mapping layer, probed in both directions.

A check that cannot produce both verdicts is not evidence, so every refusal in
``src/core/database/mapping/_base.py`` is exercised here BOTH ways: a mapper that omits the
thing raises naming it, and the same mapper with the thing accounted for is defined without
complaint.

The probes run against the REAL ``Creative`` table rather than a synthetic one. The rule's
whole claim is that the omission set is derived from ``__table__.columns``, so probing the
live table is what grades the claim -- a fixture table would only prove the derivation works
on a table written to make it work.
"""

from typing import Any

import pytest

from src.core.database.mapping import RowMapper
from src.core.database.models import Creative as CreativeRow
from src.core.schemas.creative import CreativeAssetRequest

#: Every ``Creative`` column that is neither a ``CreativeAsset`` field nor the format
#: identity: what a mapper of this table has to name for itself. Spelled out here (and
#: cross-checked against the live table below) so each probe can remove ONE member and show
#: the check naming exactly that one.
_OWNED = frozenset(
    {
        "tenant_id",
        "principal_id",
        "group_id",
        "strategy_id",
        "created_at",
        "updated_at",
        "approved_at",
        "approved_by",
    }
)

_OVERRIDES = {
    "format_kind": "the format identity, spread over four columns",
    "format_option_ref": "the format identity, spread over four columns",
    "legacy_format_id": "the format identity, spread over four columns",
    "legacy_format_key": "the format identity, spread over four columns",
    "status": "seller workflow state on write, the pinned field on read",
    "data": "the JSON blob holding the pinned fields with no column of their own",
}


def _define(**overrides: Any) -> type[RowMapper[Any, Any]]:
    """Define a mapper of ``Creative`` with the stated members, so a probe can perturb one."""
    namespace: dict[str, Any] = {
        "domain": CreativeAssetRequest,
        "row": CreativeRow,
        "repository_owned": _OWNED,
        "overrides": _OVERRIDES,
    }
    namespace.update(overrides)
    return type("ProbeMapper", (RowMapper,), namespace)


def test_the_accounting_sets_cover_the_live_table() -> None:
    """The probes below perturb a mapper that is total; this is what says it was total.

    Without this, every "does not raise" assertion could be passing because the check never
    looked at the table at all. The converse -- that the sets name no name that is not a
    column -- needs no assertion here: the check refuses it, and
    :func:`test_a_declared_name_that_is_no_column_refuses` is where that is graded.
    """
    columns = {column.name for column in CreativeRow.__table__.columns}
    accounted = set(CreativeAssetRequest.model_fields) | _OWNED | set(_OVERRIDES)
    assert columns - accounted == set()


def test_a_declared_name_that_is_no_column_refuses() -> None:
    """RED, backwards. A migration that drops a column leaves exactly this behind."""
    with pytest.raises(TypeError) as excinfo:
        _define(repository_owned=_OWNED | {"retired_column"})
    assert "retired_column" in str(excinfo.value)
    assert "no column" in str(excinfo.value)


def test_a_declaration_naming_only_real_columns_is_defined() -> None:
    """GREEN, backwards: the same mapper without the stale entry."""
    assert set(_define().repository_owned) <= {column.name for column in CreativeRow.__table__.columns}


def test_an_omitted_column_refuses_at_class_definition_naming_it() -> None:
    """RED. ``strategy_id`` is a plain nullable column: nothing else accounts for it."""
    with pytest.raises(TypeError) as excinfo:
        _define(repository_owned=_OWNED - {"strategy_id"})
    assert "strategy_id" in str(excinfo.value)
    assert "Creative" in str(excinfo.value)


def test_the_same_mapper_with_that_column_accounted_for_is_defined() -> None:
    """GREEN. The one difference from the probe above is the member that was removed."""
    mapper = _define()
    assert mapper.row is CreativeRow


def test_a_blob_column_with_a_sqlalchemy_side_default_is_not_excused() -> None:
    """``data`` is ``default=dict``, which SQLAlchemy applies -- the omission, not an excusal.

    This is the probe that distinguishes the implemented rule from the one that reads
    ``column.default``: under that rule a mapper could forget the blob holding nine pinned
    fields and still import, and the row would be written ``{}``.
    """
    with pytest.raises(TypeError, match="data"):
        _define(overrides={name: why for name, why in _OVERRIDES.items() if name != "data"})


def test_a_column_the_database_defaults_is_excused() -> None:
    """``created_at`` carries ``server_default=func.current_timestamp()``.

    The fourth accounting, exercised: unlike ``data`` above, nothing has to send this column
    for the row to be complete, so a mapper that names it nowhere is still total.
    """
    mapper = _define(repository_owned=_OWNED - {"created_at"})
    assert "created_at" not in mapper.repository_owned


def test_a_repository_owned_name_that_is_also_a_domain_field_refuses() -> None:
    """``name`` is a pinned ``CreativeAsset`` field, so claiming it as repository-owned is ambiguous."""
    with pytest.raises(TypeError) as excinfo:
        _define(repository_owned=_OWNED | {"name"})
    assert "name" in str(excinfo.value)
    assert "overrides" in str(excinfo.value)


def test_an_override_resolves_the_ambiguity_a_shadowed_name_raises_on() -> None:
    """``status`` is in exactly that position and the real mapper declares it an override.

    So the refusal above is a refusal to GUESS, not a prohibition: the escape hatch is the
    accounting that makes the author state which fact the column holds.
    """
    mapper = _define(repository_owned=_OWNED | {"status"})
    assert "status" in mapper.overrides


def test_a_mapper_declaring_one_of_domain_and_row_refuses() -> None:
    """A misspelled ``row`` is indistinguishable from an intermediate base, so "one" is a typo."""
    with pytest.raises(TypeError, match="domain/row"):
        type("HalfDeclared", (RowMapper,), {"domain": CreativeAssetRequest})


def test_an_intermediate_base_naming_neither_is_not_checked() -> None:
    """The other direction of the same clause: a shared implementation maps no table."""

    class SharedBase(RowMapper[Any, Any]):
        def to_domain(self, row: Any, /) -> Any:
            raise NotImplementedError

    assert SharedBase.repository_owned == frozenset()

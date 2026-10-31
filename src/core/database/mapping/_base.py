"""The one translation between a domain model and its row, and the rule that it is total.

A domain model is the pinned SDK class the seller extends. A row is what Postgres holds.
Those are different shapes with different owners, and the translation between them is this
layer -- see [docs/design/persistence-mapping-layer.md](../../../../docs/design/persistence-mapping-layer.md).

WHY A CLASS AND NOT A FUNCTION PER REPOSITORY. The translation already existed as a function
per repository, and that is how it went wrong: ``repositories/creative.py`` grew
``_identity_columns`` only after ``create`` and ``set_identity`` were found writing different
column combinations for the same identity. A function can be called by one path and not
another. A mapper that OWNS every column of its table cannot be partially applied, because
construction fails unless every column is accounted for.

WHY A BASE CLASS AND NOT A ``Protocol``. The design states the interface as a Protocol and
states, in the next paragraph, that the coverage rule "raises ``TypeError`` at import time".
Only one of those is achievable: structural typing means a Protocol's implementers never
execute anything the Protocol declares, so a Protocol can describe the shape and cannot
refuse one. The stated RULE is what earns the layer, so the rule wins and this is a base
class. Nothing is lost -- the members the design's snippet declares are declared here.

THE COVERAGE RULE IS THE POINT. A column is accounted for by exactly one of four things,
and the four are ordered, because a name can appear in more than one place and a reader has
to know which one decided:

1. ``overrides`` -- this mapper translates the column by hand, and says why. An override is
   the answer whenever a column and a same-named domain field are not the same fact (a
   format identity spread over four columns; a seller-written ``status`` that the pin also
   publishes; a pinned field with no column of its own, held in a JSON blob).
2. ``repository_owned`` -- supplied by the caller at write time and never carried on the
   domain model (``tenant_id``, ``created_at``, ``approved_by``).
3. a field of the same name on the domain model -- copied.
4. a ``server_default`` -- the DATABASE fills the column in, so nothing has to send it.

A column that is none of those raises ``TypeError`` AT CLASS DEFINITION, naming it. That is
deliberate rather than a test: a by-name row copy that omits silently has already produced a
defect in this repository, in a shape that could widen auth, and a test only reports it after
someone runs the suite. This refuses to import. It also means a migration that adds a column
breaks the mapper loudly instead of writing NULL quietly -- the omission set is DERIVED from
``__table__.columns`` and is never a list anyone maintains.

TWO THINGS THE RULE DELIBERATELY DOES NOT EXCUSE, each because excusing it would let an
omission back in through the accounting itself:

* **A SQLAlchemy-side ``default=``.** The design's fourth accounting is "a column the
  database defaults", and a ``default=`` is not that: SQLAlchemy applies it when the ORM
  builds a row WITHOUT the attribute, which is precisely the omission this check exists to
  catch -- it just lands as ``{}`` or ``'pending_review'`` instead of as NULL. ``Creative``
  has two such columns, ``status`` and ``data``, and ``data`` is the JSON blob that holds
  nine pinned fields; excusing it would let a mapper forget the blob entirely and still
  import. Only ``server_default`` is excused, which is the database actually doing it.
* **A name in ``repository_owned`` that is ALSO a field of the domain model.** Then two of
  the accountings claim the column and the mapper has not said which fact it holds. Moving
  it to ``overrides`` is the resolution, because that is what such a column always turns out
  to need: the row's value and the domain model's field are two different things that happen
  to share a name.

THE RULE RUNS BACKWARDS TOO. A declared name that is no column of the row is refused, which
is the mirror image: the forward direction catches a column nobody accounted for, and this
catches an accounting for no column. A migration that DROPS a column leaves its entry behind,
and a stale entry is a claim that something is handled sitting in the very list a reader
consults to learn what is handled. The repository's rule for its guard allowlists is that
they only shrink and that a stale entry gets reported; this is that rule derived instead of
audited.

A mapper that declares one of ``domain``/``row`` and not the other is refused too. Skipping
the check whenever it cannot find both is what makes an intermediate base possible, and a
misspelled attribute looks exactly like an intermediate base -- so "neither" is a shared
implementation and "one of the two" is a typo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel
    from sqlalchemy.orm import DeclarativeBase

__all__ = ["RowMapper", "shadowed_columns", "undeclared_names", "unmapped_columns"]


def unmapped_columns(
    row: type[Any],
    domain: type[Any],
    owned: frozenset[str],
    overrides: frozenset[str],
) -> set[str]:
    """Columns of ``row`` that nothing accounts for.

    Derived from ``row.__table__.columns`` so the question is asked of the live table rather
    than of a list someone remembered to update. See the module docstring for the four
    accountings and for the two things that are deliberately not among them.
    """
    accounted = set(domain.model_fields) | owned | overrides
    return {
        column.name
        for column in row.__table__.columns
        if column.name not in accounted and column.server_default is None
    }


def shadowed_columns(
    row: type[Any],
    domain: type[Any],
    owned: frozenset[str],
    overrides: frozenset[str],
) -> set[str]:
    """Columns claimed by ``repository_owned`` that are also fields of the domain model.

    Two accountings for one column, with nothing saying which fact it holds. An explicit
    override is the resolution, so a name already in ``overrides`` is not shadowed, and a name
    that is not a column of this row is :func:`undeclared_names`' business rather than this
    one's.
    """
    columns = {column.name for column in row.__table__.columns}
    return (set(domain.model_fields) & owned & columns) - overrides


def undeclared_names(row: type[Any], owned: frozenset[str], overrides: frozenset[str]) -> set[str]:
    """Declared accountings that name no column of ``row``.

    The mirror of :func:`unmapped_columns`, and it is a refusal for the same reason the
    forward direction is. A migration that DROPS a column leaves its entry behind, and a
    stale entry is not inert: it is a claim that something is handled, sitting in the list a
    reader consults to find out what is handled. The repository's own rule for its guard
    allowlists is that they only shrink and that a stale entry is reported -- this is that
    rule for the mapper, derived rather than audited.

    It also catches the cheaper mistake: a typo in a declared name reads as an unaccounted
    column AND an unknown entry, so the two messages together name both halves.
    """
    columns = {column.name for column in row.__table__.columns}
    return (set(owned) | set(overrides)) - columns


class RowMapper[DomainT: BaseModel, RowT: DeclarativeBase]:
    """Base for one aggregate's domain <-> row translation.

    A subclass declares ``domain``, ``row``, ``repository_owned`` and ``overrides``, and
    implements :meth:`to_domain` plus a ``to_row`` of its own. Declaring them is what makes
    the coverage check possible, and the check runs when the subclass is defined.

    WHY ``to_row`` IS NOT DECLARED HERE. The design states it as
    ``to_row(self, obj, /, **owned: Any) -> RowT``, and that signature cannot carry the
    obligation the design states for it one sentence later -- "a caller cannot forget
    ``tenant_id``; it is a required argument, not a field someone might leave unset". A
    ``**owned`` makes every owned value optional at every call site, and a concrete mapper
    that spells its owned values as real required keywords is a NARROWER override, which
    mypy rejects. Since the owned set differs per aggregate -- that is the whole reason the
    values are passed rather than read -- there is no one signature to inherit, and a base
    declaration here would buy an unenforceable promise at the cost of forbidding the
    enforceable one. So the write direction is declared by each mapper, with its own columns
    as its own parameters, and ``repository_owned`` is the declaration this class checks.
    """

    #: The domain model this maps. The pinned SDK class the seller extends.
    domain: ClassVar[type[Any]]
    #: The ORM class. Private to persistence; nothing above a repository names it.
    row: ClassVar[type[Any]]
    #: Columns the caller supplies at write time and the domain model never carries.
    repository_owned: ClassVar[frozenset[str]] = frozenset()
    #: Columns this mapper translates by hand, each mapped to a reason a reader can check.
    overrides: ClassVar[Mapping[str, str]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = [name for name in ("domain", "row") if getattr(cls, name, None) is not None]
        if not declared:
            # An intermediate base that names neither is a shared implementation, and the
            # subclass that names both is where the check belongs.
            return
        if len(declared) == 1:
            raise TypeError(
                f"{cls.__name__} declares {declared[0]!r} and not the other of domain/row, so "
                "its columns cannot be checked. A mapper names both; a shared base names "
                "neither."
            )
        owned = frozenset(cls.repository_owned)
        override_names = frozenset(cls.overrides)
        if unknown := undeclared_names(cls.row, owned, override_names):
            raise TypeError(
                f"{cls.__name__} declares {sorted(unknown)}, which {cls.row.__name__} has no "
                "column for. Either the name is a typo, or a migration dropped the column and "
                "the entry outlived it; a declaration that names nothing still reads as a "
                "column someone handled."
            )
        if shadowed := shadowed_columns(cls.row, cls.domain, owned, override_names):
            raise TypeError(
                f"{cls.__name__} names {sorted(shadowed)} in repository_owned, and "
                f"{cls.domain.__name__} declares the same field(s). Two accountings claim the "
                "column and neither says which fact it holds: move it to overrides and state "
                "what the row's value is and what the domain model's field is."
            )
        if missing := unmapped_columns(cls.row, cls.domain, owned, override_names):
            raise TypeError(
                f"{cls.__name__} does not account for {cls.row.__name__} column(s) "
                f"{sorted(missing)}: name each on the domain model, in repository_owned, or "
                "in overrides. A column left out is written as NULL -- or as its "
                "SQLAlchemy-side default -- with nothing reporting it."
            )

    def to_domain(self, row: RowT, /) -> DomainT:
        """Build a validated domain object from a row.

        Validated on purpose: a row that cannot produce a valid domain model is a loud failure
        at the read, not a malformed response discovered three layers up.
        """
        raise NotImplementedError

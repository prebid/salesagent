"""The ``CreativeAsset`` <-> ``creatives`` row translation.

The pilot of the persistence mapping layer -- see
[docs/design/persistence-mapping-layer.md](../../../../docs/design/persistence-mapping-layer.md).
``Creative`` is the pilot because its sixteen columns split cleanly into three facts with
three owners, and because the divergence between the pinned shape and the table was already
measured: twelve pinned ``CreativeAsset`` fields have no column of their own.

WHICH ``CreativeAsset``. ``adcp.types.domains.core.creative_asset.CreativeAsset``, through the
one local subclass of it (:class:`CreativeAssetRequest`). The public ``adcp.types.CreativeAsset``
binds to the canonical module, which STRIPS ``format_id`` -- and this table retains the
buyer's ``format_id`` assertion in two columns, so a domain model without the field could not
describe a row. The local subclass rather than the library class because the subclass carries
the pin's ``oneOf`` (``format_id`` XOR ``format_kind``), which is what makes
:meth:`to_domain`'s validation mean something: a row that would produce a domain object
carrying both arms is refused here instead of being refused by the response model three
layers up.

THE THREE FACTS, and why ``status`` is an override rather than either of the other two:

* **the buyer's asset** -- ``creative_id``, ``name``, the format identity, and the eleven
  pinned fields held in ``data``.
* **seller state** -- ``tenant_id``, ``principal_id``, ``group_id``, ``strategy_id``, the four
  timestamps and ``approved_by``: the caller supplies them and the domain model never carries
  them.
* **both, under one name** -- ``status``. The pin declares ``status`` on ``CreativeAsset`` and
  the row's column is SELLER workflow state (``pending_review`` -> ``approved``), written by
  the seller and never by the buyer. So the column is repository-owned on the way in and the
  pinned field on the way out, which is an asymmetry neither "domain field" nor
  "repository_owned" can express -- hence an override, which is the accounting that exists
  for a column whose two sides are different facts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from src.core.database.mapping._base import RowMapper
from src.core.database.models import Creative as CreativeRow
from src.core.schemas._base import CreativeFormatIdentity
from src.core.schemas.creative import CreativeAssetRequest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

logger = logging.getLogger(__name__)

__all__ = ["CREATIVE_MAPPER", "CreativeMapper"]

#: The column that holds every pinned field without a column of its own.
_BLOB = "data"

#: ``format_id`` has no column of its own either, and is NOT in the blob: the identity holds
#: it, verbatim in ``legacy_format_id`` and canonically in ``legacy_format_key``. Named here
#: so the blob set below can be derived rather than listed.
_HELD_BY_THE_IDENTITY = frozenset({"format_id"})


def _blob_fields() -> frozenset[str]:
    """The pinned fields this table keeps inside ``data``, DERIVED from the two shapes.

    A pinned field is in the blob exactly when the table gives it no column and the identity
    does not already hold it. Deriving it means a pin that adds a field to ``CreativeAsset``
    starts persisting in the blob rather than being dropped on the way in -- which is the
    behaviour the layer is for, and the opposite of what a maintained list would give
    (today's write path lists its keys by hand, and nine of these eleven never reach the row
    at all).
    """
    columns = {column.name for column in CreativeRow.__table__.columns}
    return frozenset(CreativeAssetRequest.model_fields) - columns - _HELD_BY_THE_IDENTITY


class CreativeMapper(RowMapper[CreativeAssetRequest, CreativeRow]):
    """The one translation between a creative asset and its row."""

    domain: ClassVar[type[CreativeAssetRequest]] = CreativeAssetRequest
    row: ClassVar[type[CreativeRow]] = CreativeRow

    #: Seller state. Each is a column the caller supplies and the pinned asset never carries.
    repository_owned: ClassVar[frozenset[str]] = frozenset(
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

    #: Columns this mapper translates by hand, each with the reason.
    overrides: ClassVar[Mapping[str, str]] = {
        "format_kind": "the format identity: one object, four columns, one projection direction",
        "format_option_ref": "the format identity: the stored option document",
        "legacy_format_id": "the format identity: the buyer's format_id assertion, verbatim",
        "legacy_format_key": "the format identity: that assertion's canonical comparison key",
        "status": "seller workflow state on the way in, the pinned CreativeAsset field on the way out",
        _BLOB: "the eleven pinned fields with no column of their own, plus the seller's own document",
    }

    #: The pinned fields held inside ``data``. Derived; see :func:`_blob_fields`.
    blob_fields: ClassVar[frozenset[str]] = _blob_fields()

    def identity_columns(self, identity: CreativeFormatIdentity) -> dict[str, Any]:
        """THE ONE mapping from a format identity to the four columns that store it.

        The identity is one object, so there is no arm to pick and nothing for a create and an
        update to disagree about: the kind IS the identity, and the retained assertion lands in
        both of the columns its two obligations need -- ``legacy_format_id`` verbatim, because
        the echo owes the buyer its own ``format_id`` back, and ``legacy_format_key`` canonical
        and indexed, because ``core/format-id.json`` makes canonicalizing before a comparison a
        MUST.

        It lives on the mapper rather than beside the two writers it used to serve. That
        function was WRITTEN because ``create`` and ``set_identity`` were found assigning
        different column combinations for one identity -- the failure a per-repository
        function invites, because nothing makes the next writer call it. Here the four columns
        are declared overrides of a mapper that cannot be defined while a column is
        unaccounted for.

        A repository is a persistence edge, which is where a model legitimately becomes a
        document; nothing upstream serializes.
        """
        return {
            "format_kind": identity.kind,
            "format_option_ref": identity.option.document if identity.option else None,
            "legacy_format_id": (
                identity.assertion.model_dump(exclude_none=True) if identity.assertion is not None else None
            ),
            "legacy_format_key": identity.legacy_key,
        }

    def blob_columns(
        self, obj: CreativeAssetRequest, seller_document: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """The ``data`` document: the seller's own keys, then the pinned fields on top.

        Two kinds of key share this column and only one of them is pinned. The seller's keys
        (``url``, ``snippet``, ``concept_id``, the generative bookkeeping) describe work this
        agent did and have no field on ``CreativeAsset``, so they are supplied by the caller
        and passed through untouched. The pinned fields come off the domain object.

        The pinned fields are written LAST and win a collision, because the domain object is
        the value: a seller document carrying a stale ``assets`` must not shadow the asset the
        buyer just sent.

        A FIELD THE BUYER OMITTED LEAVES NO KEY. The absent key and the explicit null read
        back identically through :meth:`to_domain` -- both give the field's default -- so the
        two are the same fact, and writing the null is the one of the two that makes the
        stored document claim the buyer sent something.
        """
        document: dict[str, Any] = dict(seller_document or {})
        for name in self.blob_fields:
            # Plain attribute access, deprecation warning and all. ``placement_ids`` is
            # deprecated in the pin (in favour of ``placement_refs``) and is still a field a
            # buyer may send, so it still has to persist; reading it through the model's own
            # API is what keeps the pin's signal audible instead of silencing it here for
            # every reader.
            value = getattr(obj, name)
            if value is not None:
                document[name] = value
        return document

    def to_row(
        self,
        obj: CreativeAssetRequest,
        /,
        *,
        identity: CreativeFormatIdentity,
        tenant_id: str,
        principal_id: str,
        status: str,
        created_at: datetime,
        seller_document: Mapping[str, Any] | None = None,
        group_id: str | None = None,
        strategy_id: str | None = None,
        updated_at: datetime | None = None,
        approved_at: datetime | None = None,
        approved_by: str | None = None,
    ) -> CreativeRow:
        """Build a row from a creative asset plus the values persistence owns.

        ``identity`` is a parameter rather than something derived here, and that is the one
        place this mapper cannot be total on its own: projecting a legacy ``format_id`` to its
        canonical kind reads the format registry (``creative_identity_of`` with the resolved
        spec), which is a lookup, not a translation. So the caller that has the registry does
        the projection and hands over the result.

        THE FIVE REQUIRED KEYWORDS ARE THE ONES WHOSE ABSENCE IS ALWAYS A DEFECT -- a row
        scoped to no tenant, owned by no principal, in no status, with no creation time, or
        with no format identity. The five defaulted ones are columns a LATER lifecycle step
        writes (an approval, a group assignment, a strategy), and defaulting them is not the
        same leniency: the column is nullable because the state legitimately does not exist
        yet.
        """
        return CreativeRow(
            creative_id=obj.creative_id,
            name=obj.name,
            **self.identity_columns(identity),
            tenant_id=tenant_id,
            principal_id=principal_id,
            status=status,
            data=self.blob_columns(obj, seller_document),
            created_at=created_at,
            updated_at=updated_at,
            group_id=group_id,
            strategy_id=strategy_id,
            approved_at=approved_at,
            approved_by=approved_by,
        )

    def to_domain(self, row: CreativeRow, /) -> CreativeAssetRequest:
        """Build a validated creative asset from a row.

        THE ONE ARM, decided by the identity and not restated here. The row stores BOTH the
        buyer's retained ``format_id`` assertion and the kind it projects to, while the pin
        makes the two exclusive -- so ``wire_format_kind`` is ``None`` whenever an assertion is
        present, and the domain object carries exactly the arm the buyer sent. Handing both to
        the model would be refused by the ``oneOf``, which is the refusal working.

        ``status`` comes off the COLUMN. It is the one field the pin declares that persistence
        also owns (see the module docstring), and the row is the authority for it: the stored
        value is the seller's decision, not whatever the buyer asserted when they synced.
        """
        identity = CreativeFormatIdentity.from_row(row)
        document = row.data or {}
        # The ONE arm present, the other ABSENT rather than null: the item's oneOf counts a
        # present key whatever its value, so handing the unused arm as ``None`` would name
        # both identifiers and be refused.
        arm: dict[str, Any] = (
            {"format_id": identity.assertion}
            if identity.assertion is not None
            else {"format_kind": identity.wire_format_kind}
        )
        return CreativeAssetRequest.model_validate(
            {
                "creative_id": row.creative_id,
                "name": row.name,
                **arm,
                "format_option_ref": row.format_option_ref,
                "status": row.status,
                **{name: document[name] for name in self.blob_fields if name in document},
            }
        )


#: The mapper, as one value. A mapper holds no state, so there is nothing for a second
#: instance to be, and one name is what lets a call site be found.
CREATIVE_MAPPER = CreativeMapper()

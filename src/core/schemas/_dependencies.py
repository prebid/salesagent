"""Enforce a pinned schema's ``dependencies`` keyword on a local subclass of an SDK model.

The SDK's generated models compile ``required``, ``anyOf`` groups and the field constraints, but
not the draft-07 ``dependencies`` keyword: ``adcp.types.FrequencyCap`` accepts
``{"max_impressions": 3}`` although ``core/frequency-cap.json`` declares
``"dependencies": {"max_impressions": ["per", "window"], ...}``. A local subclass supplies the
schema's map as data and this mixin enforces it; nothing else changes.

THE MAP IS COPIED, AND CHECKED. A class states its schema's ``dependencies`` verbatim in
``_SCHEMA_DEPENDENCIES``. ``tests/unit/test_schema_dependencies_match_pin.py`` walks every
request DTO in the tool registry and refuses any reachable class whose map differs from the
pinned schema's, including a class that carries none where the pin declares one, so the copy
cannot drift and a new pinned dependency cannot go unenforced. Read at runtime instead, the map
would be a file read at import (refused by ``test_architecture_no_import_time_fs_io``) or on
the first request.

THE REFUSAL IS A MISSING FIELD. The array form of ``dependencies`` is "when this property is
present, these are required", so a violation is reported as pydantic's own ``missing`` error at
the absent member's location, one entry per absent member, as a missing required field already
is. It leaves the model as a ``ValidationError``, which the boundary answers INVALID_REQUEST
with ``issues[]`` and ``field`` naming the member (``adcp_error_for``).

PRESENCE IS ``model_fields_set``: a key the caller supplied, the same test the SDK's own compiled
``required`` groups apply. A model built ``from_attributes`` sets every field it reads, so a
readback of a stored document is never refused here.

NARROWING A SLOT. A nested SDK model gets the rule only where its slot builds the local subclass.
Every such slot is written one way: the parent's field is redeclared with its own static type,
the element annotated to be built as the local class, and the parent's declaration as the value::

    frequency_cap: Annotated[LibraryFrequencyCap, as_local(FrequencyCap)] | None = narrowed_from(
        TargetingOverlay, "frequency_cap"
    )

Keeping the parent's static type is what lets a ``list[X]`` slot be narrowed at all (mypy refuses
``list[LocalX]`` over ``list[X]``, ``list`` being invariant), and it keeps the published schema
the parent's: a request DTO advertises its JSON Schema, and the local class must not change
what a buyer is told. ``narrowed_from`` keeps what a fresh declaration would drop: the
description, the ``deprecated`` marker and constraints such as ``min_length``.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from functools import partial
from typing import Any, ClassVar, Self

from adcp.types.base import AdCPBaseModel as LibraryAdCPBaseModel
from pydantic import BaseModel, BeforeValidator, ModelWrapValidatorHandler, model_validator
from pydantic_core import InitErrorDetails, ValidationError


class SchemaDependencies(LibraryAdCPBaseModel):
    """Mixin: enforce ``_SCHEMA_DEPENDENCIES``, the pinned schema's ``dependencies`` map.

    List it FIRST among a subclass's bases. It declares no field and no config of its own, but
    it inherits ``AdCPBaseModel``'s ``extra="ignore"``, and pydantic merges base configs in
    ``__bases__`` order, last one wins, so a later position would override the library
    parent's extra policy.
    """

    _SCHEMA_DEPENDENCIES: ClassVar[Mapping[str, tuple[str, ...]]] = {}

    @model_validator(mode="wrap")
    @classmethod
    def _enforce_schema_dependencies(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        model = handler(data)
        sent = model.model_fields_set
        absent = dict.fromkeys(
            needed
            for present, required in cls._SCHEMA_DEPENDENCIES.items()
            if present in sent
            for needed in required
            if needed not in sent
        )
        if absent:
            raise ValidationError.from_exception_data(
                cls.__name__,
                [InitErrorDetails(type="missing", loc=(name,), input=data) for name in absent],
            )
        return model


def narrowed_from(parent: type[BaseModel], name: str) -> Any:
    """``parent``'s declaration of field ``name``, for a subclass that narrows its type.

    Assign it as the redeclared field's value; the subclass's annotation replaces the type and
    everything else is the parent's.
    """
    return copy(parent.model_fields[name])


def build_as_local(local: type[BaseModel], value: Any) -> Any:
    """Build ``local`` from ``value``: the buyer's mapping, or an instance an in-process caller holds.

    An instance of another model, the pinned parent included, is read back by its supplied
    fields (``model_fields_set``) and extras, attribute by attribute and never through a dump,
    so presence means what it meant on the instance and ``_SCHEMA_DEPENDENCIES`` judges it the
    same way. Keyed by field name, hence ``by_name``.
    """
    if isinstance(value, BaseModel) and not isinstance(value, local):
        supplied = {name: getattr(value, name) for name in value.model_fields_set}
        return local.model_validate({**(value.model_extra or {}), **supplied}, by_name=True)
    return local.model_validate(value)


def as_local(local: type[BaseModel]) -> BeforeValidator:
    """Annotate a slot element whose static type is the pinned parent of ``local`` to be built
    as ``local`` (see the module docstring)."""
    return BeforeValidator(partial(build_as_local, local))

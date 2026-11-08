"""Every model a request DTO reaches enforces exactly its pinned schema's ``dependencies``.

The SDK's generated models do not compile the draft-07 ``dependencies`` keyword, so a local
subclass carries the schema's map in ``_SCHEMA_DEPENDENCIES`` and ``SchemaDependencies``
enforces it (``src/core/schemas/_dependencies.py``). The map is a COPY of the pin, and this
test is what keeps it honest, in both directions:

- a class whose map differs from its schema's fails, so the copy cannot drift;
- a class reachable from a request whose schema declares ``dependencies`` but which carries no
  map fails, so neither a new pinned dependency nor a request slot still typed with the bare SDK
  class can go unenforced.

The population is DERIVED, not listed: every model reachable from a ``TOOLS`` row's DTO by
walking resolved field annotations. A reached class's schema is found through its SDK ancestors:
each generated module names its document in its header, and a ``dependencies`` site in that
document belongs to a generated class when the site's ``properties`` are all fields of the
class (an inline object gets its own class, a ``oneOf`` branch copies the properties it shares).
"""

from __future__ import annotations

import json
import re
import sys
import typing
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from adcp.types import FrequencyCap as LibraryFrequencyCap
from pydantic import BaseModel, BeforeValidator, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from src.core.schemas._dependencies import SchemaDependencies, build_as_local
from src.core.schemas._pinned_fields import _schema_root
from src.core.tools.registry import TOOLS

#: ``dependencies`` sites no generated class carries, because the pin attaches them to a value
#: the SDK types as a free-form dict. The one in the pinned bundle is the image selector's
#: ``params`` width/height pairing under package-request.json's ``if format_kind == "image"``,
#: which the SDK's hand-written ``PackageRequest._validate_format_params`` already enforces.
#: Listed so a new such site is a failure here rather than silently out of reach.
_CONDITIONAL_SITES = {("media-buy/package-request.json", "/allOf/2/then/properties/params")}

_HEADER = re.compile(r"filename:\s+(\S+)")


def _built_as(metadata: Any) -> type[BaseModel] | None:
    """The local class an ``as_local`` annotation builds, or None for any other metadata."""
    if isinstance(metadata, BeforeValidator) and isinstance(metadata.func, partial):
        if metadata.func.func is build_as_local:
            return metadata.func.args[0]
    return None


def _models_in(annotation: Any) -> Iterator[type[BaseModel]]:
    """The models a value of ``annotation`` is built as.

    An ``Annotated`` element carrying ``as_local(Local)`` is built as ``Local``, not as the
    annotated pinned type.
    """
    if typing.get_origin(annotation) is typing.Annotated:
        inner, *metadata = typing.get_args(annotation)
        builders = [local for m in metadata if (local := _built_as(m)) is not None]
        yield from builders or _models_in(inner)
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _models_in(arg)


def _field_annotation(field: FieldInfo) -> Any:
    """The field's annotation with its own metadata put back: pydantic lifts a field-level
    ``Annotated``'s metadata into ``FieldInfo.metadata``, ``as_local`` included."""
    return typing.Annotated[(field.annotation, *field.metadata)] if field.metadata else field.annotation


def _reached() -> dict[type[BaseModel], str]:
    """Every model reachable from a registered tool's request DTO, with one path to it."""
    reached: dict[type[BaseModel], str] = {}

    def walk(model: type[BaseModel], path: str) -> None:
        if model in reached:
            return
        reached[model] = path
        for name, field in model.model_fields.items():
            for sub in _models_in(_field_annotation(field)):
                walk(sub, f"{path}.{name}")

    for tool_name, row in TOOLS.items():
        walk(row.dto, tool_name)
    return reached


def _document(sdk_class: type) -> tuple[str, dict[str, Any]]:
    """The pinned schema document the generated module of ``sdk_class`` was built from."""
    header = _HEADER.search(Path(sys.modules[sdk_class.__module__].__file__ or "").read_text()[:400])
    assert header, f"{sdk_class.__module__} names no source document in its header"
    path = header.group(1).replace("_", "-")
    return path, json.loads((_schema_root() / path).read_text())


def _sites(node: Any, pointer: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every object node in a schema document that declares ``dependencies``."""
    if isinstance(node, dict):
        if isinstance(node.get("dependencies"), dict) and not pointer.endswith("/properties"):
            yield pointer, node
        for key, child in node.items():
            yield from _sites(child, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _sites(child, f"{pointer}/{index}")


def _sdk_ancestors(model: type) -> list[type[BaseModel]]:
    return [k for k in model.__mro__ if k.__module__.startswith("adcp.types.domains.")]


def _wire_names(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}


def _carried(sdk_class: type[BaseModel]) -> dict[str, tuple[str, ...]]:
    path, document = _document(sdk_class)
    pinned: dict[str, tuple[str, ...]] = {}
    for pointer, node in _sites(document):
        if (path, pointer) not in _CONDITIONAL_SITES and set(node.get("properties", {})) <= _wire_names(sdk_class):
            pinned |= {key: tuple(value) for key, value in node["dependencies"].items()}
    return pinned


def _pinned(model: type[BaseModel]) -> dict[str, tuple[str, ...]]:
    """The ``dependencies`` the pin puts on ``model``: its own schema's and, through ``allOf``
    composition (which the SDK generates as inheritance), every ancestor schema's."""
    pinned: dict[str, tuple[str, ...]] = {}
    for sdk_class in _sdk_ancestors(model):
        pinned |= _carried(sdk_class)
    return pinned


def _declared(model: type[BaseModel]) -> dict[str, tuple[str, ...]]:
    if not issubclass(model, SchemaDependencies):
        return {}
    return {key: tuple(value) for key, value in model._SCHEMA_DEPENDENCIES.items()}


@pytest.mark.arch_guard
def test_every_reached_model_enforces_its_pinned_dependencies() -> None:
    reached = _reached()
    wrong = [
        f"{path}: {model.__module__}.{model.__qualname__} declares {_declared(model)}, the pin says {_pinned(model)}"
        for model, path in reached.items()
        if _sdk_ancestors(model) and _declared(model) != _pinned(model)
    ]
    assert wrong == [], (
        "These request-reachable models do not enforce their pinned schema's `dependencies`. "
        "Type the slot with a local subclass listing SchemaDependencies first and copy the "
        "schema's map into _SCHEMA_DEPENDENCIES:\n" + "\n".join(sorted(wrong))
    )
    # The derivation must find something, or a broken walk would pass by matching nothing.
    assert any(_pinned(model) for model in reached), "no request-reachable model has a pinned dependency"


@pytest.mark.arch_guard
def test_conditional_sites_are_exactly_the_listed_ones() -> None:
    documents = dict(_document(k) for m in _reached() for k in _sdk_ancestors(m))
    found = {
        (path, pointer)
        for path, document in documents.items()
        for pointer, node in _sites(document)
        if "properties" not in node
    }
    assert found == _CONDITIONAL_SITES


# ---------------------------------------------------------------------------
# A narrowed slot takes an instance of the pinned parent, and judges it by what it supplied
# ---------------------------------------------------------------------------

_FORMAT_ID = {"agent_url": "https://creative.example.com", "id": "display_static", "width": 300, "height": 250}
_FREQUENCY_CAP = {"max_impressions": 3, "per": "devices", "window": {"interval": 1, "unit": "days"}}
_RECIPIENT = {"identity": "buyer-share", "cloud": "aws", "region": "us-east-1"}
_PROVISION = {"mode": "provision", "provider": {"domain": "databricks.com"}, "access_mode": "open_sharing"}
_SHARE_METHOD = {"pattern": "dataset_share", "transport": "delta_sharing", "orchestration": "producer_managed"}

#: One valid document per local class an ``as_local`` slot builds, keyed by its name.
_SAMPLES: dict[str, dict[str, Any]] = {
    "FormatId": _FORMAT_ID,
    "FrequencyCap": _FREQUENCY_CAP,
    "MediaBuyFrequencyCap": _FREQUENCY_CAP,
    "PinnedTargeting": {"frequency_cap": _FREQUENCY_CAP},
    "PinnedTargetingInput": {"frequency_cap": _FREQUENCY_CAP},
    "GeoProximityItem": {"lat": 40.7128, "lng": -74.006, "radius": {"value": 5, "unit": "km"}},
    "ProductFilters": {"format_ids": [_FORMAT_ID]},
    "CreativeFilters": {"format_ids": [_FORMAT_ID]},
    "ProductMediaBuySupportRequirements": {"frequency_cap": True},
    "CreativeAssetRequest": {"creative_id": "c1", "name": "Banner", "format_id": _FORMAT_ID, "assets": {}},
    "PinnedPackageRequest": {"product_id": "p1", "pricing_option_id": "po1", "format_ids": [_FORMAT_ID]},
    "ReportingShareRecipient": _RECIPIENT,
    "ReportingDatasetShareProvision": {**_PROVISION, "recipient": _RECIPIENT},
    "ReportingDatasetShareMethod": {**_SHARE_METHOD, "destination": {**_PROVISION, "recipient": _RECIPIENT}},
    "ReportingDeliveryConfig": {
        "delivery_config_id": "dc1",
        "delivery_config_version": 1,
        "offering_id": "off1",
        "active": True,
        "feed_purpose": "analytics",
        "report_definition_id": "rd1",
        "reporting_profile": "standard",
        "scope": {"all_media_buys": True},
        "coverage_requirement": "full",
        "required_finality": "official",
        "reconciliation_mode": "delivery_only",
        "schedule": {"period_duration": "P1D", "alignment": "utc", "delivery_sla": "PT6H"},
        "method": {**_SHARE_METHOD, "destination": {**_PROVISION, "recipient": _RECIPIENT}},
    },
}


def _narrowed_slots() -> dict[type[BaseModel], Any]:
    """Every ``as_local`` slot element reachable from a request DTO: local class -> element type."""
    slots: dict[type[BaseModel], Any] = {}

    def visit(annotation: Any) -> None:
        if typing.get_origin(annotation) is typing.Annotated:
            inner, *metadata = typing.get_args(annotation)
            for m in metadata:
                if (local := _built_as(m)) is not None:
                    slots[local] = annotation
            visit(inner)
            return
        for arg in typing.get_args(annotation):
            visit(arg)

    for model in _reached():
        for field in model.model_fields.values():
            visit(_field_annotation(field))
    return slots


@pytest.mark.arch_guard
def test_every_narrowed_slot_builds_its_local_class_from_a_parent_instance() -> None:
    slots = _narrowed_slots()
    assert sorted(local.__name__ for local in slots) == sorted(_SAMPLES), "every narrowed slot needs a sample"
    for local, element in slots.items():
        parent = typing.get_args(element)[0]
        built = TypeAdapter(element).validate_python(parent.model_validate(_SAMPLES[local.__name__]))
        assert type(built) is local, f"{parent.__name__} instance came back as {type(built).__name__}"


def test_a_parent_instance_that_breaks_a_dependency_is_refused_by_the_slot() -> None:
    """The instance supplied max_impressions alone; the slot judges what it supplied."""
    from src.core.schemas import TargetingInput

    parent_cap = LibraryFrequencyCap.model_validate({"max_impressions": 3})
    with pytest.raises(ValidationError) as refused:
        TargetingInput.model_validate({"frequency_cap": parent_cap})
    assert [(e["type"], e["loc"]) for e in refused.value.errors()] == [
        ("missing", ("frequency_cap", "per")),
        ("missing", ("frequency_cap", "window")),
    ]

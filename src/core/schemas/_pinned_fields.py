"""Read field facts from the pinned AdCP schema tree that ships with the SDK.

The always-include set is a fact the pin states: a field is kept on the wire when
null exactly when the schema lists it in ``required`` AND types it nullable. Every
hand-declared copy of that fact is correct on the day it is written and unverified
afterwards — two of the three adopters had drifted from the pin by the time this
was added, and both emitted schema-invalid nulls to buyers.

Reads the SDK's own installed tree, so the fact moves with the ``adcp`` pin in
``pyproject.toml`` rather than with anyone's memory.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path


class UnderivablePinnedSchema(Exception):
    """The pin composes *ref* in a way no complete ``required`` list can be read from.

    Raised rather than returning an empty set, so a caller can never mistake "I could
    not read the list" for "the pin declares none". The two are indistinguishable by
    return value, which is what let the fail-open survive.
    """


@cache
def _schema_root() -> Path:
    import adcp

    major_minor = ".".join(adcp.get_adcp_spec_version().split(".")[:2])
    return Path(adcp.__file__).parent / "_schemas" / major_minor


@cache
def package_update_forbidden_shapes() -> tuple[tuple[tuple[str, ...], frozenset[str]], ...]:
    """The shapes ``media-buy/package-update.json``'s root ``not`` forbids, as the pin states them.

    Each ``anyOf`` branch of that ``not`` is one forbidden shape: the members it ``required``s,
    and the subset of them its ``properties`` additionally constrain to ``type: object``. At the
    pin that is six fully-immutable fields, the pre-GA ``capability_ids``,
    ``budget_cap_timezone``, and ``bidding`` (an object) together with ``bid_price``. Read here
    so the list moves with the ``adcp`` pin instead of with a hand-kept copy -- the hand-kept
    copy named three of the nine.

    Raises:
        UnderivablePinnedSchema: a branch uses a keyword other than ``required`` and an
            object-typed ``properties`` member. Refused rather than skipped, so a branch this
            reader cannot evaluate is never mistaken for a branch that forbids nothing.
    """
    schema = json.loads((_schema_root() / "media-buy" / "package-update.json").read_text())
    shapes = []
    for branch in schema["not"]["anyOf"]:
        members = branch.get("properties", {})
        if set(branch) - {"required", "properties"} or any(spec != {"type": "object"} for spec in members.values()):
            raise UnderivablePinnedSchema(f"package-update.json root not branch {branch!r}")
        shapes.append((tuple(branch["required"]), frozenset(members)))
    return tuple(shapes)


@cache
def geo_proximity_one_of_arms() -> tuple[tuple[frozenset[str], frozenset[str]], ...]:
    """The arms of ``core/targeting.json``'s geo_proximity item ``oneOf``, as the pin states them.

    Each arm is the members it ``required``s and the members its ``not`` forbids. At the pin
    that is one arm per proximity method -- travel time (with the point and a transport mode),
    radius (with the point), pre-computed geometry -- each forbidding the other two methods.

    Raises:
        UnderivablePinnedSchema: an arm uses a keyword other than ``required`` and a ``not``
            whose ``anyOf`` holds only ``required`` branches. Refused rather than skipped, so an
            arm this reader cannot evaluate is never mistaken for one that admits everything.
    """
    schema = json.loads((_schema_root() / "core" / "targeting.json").read_text())
    arms = []
    for arm in schema["properties"]["geo_proximity"]["items"]["oneOf"]:
        negated = arm.get("not", {"anyOf": []})
        forbidden = negated.get("anyOf", [])
        if (
            set(arm) - {"required", "not"}
            or set(negated) != {"anyOf"}
            or any(set(branch) != {"required"} for branch in forbidden)
        ):
            raise UnderivablePinnedSchema(f"core/targeting.json geo_proximity oneOf arm {arm!r}")
        arms.append(
            (frozenset(arm["required"]), frozenset(name for branch in forbidden for name in branch["required"]))
        )
    return tuple(arms)


@cache
def revision_minimum() -> int:
    """The lower bound the pin puts on ``media_buys[].revision``.

    Subscripted, never ``.get()``: if the pin drops the key, a ``KeyError`` here is a
    loud failure rather than a silent substitute bound.
    """
    schema = json.loads((_schema_root() / "media-buy" / "get-media-buys-response.json").read_text())
    return schema["properties"]["media_buys"]["items"]["properties"]["revision"]["minimum"]


@cache
def request_schema_description(sdk_request: type) -> str:
    """The pinned request schema's own ``description`` for the SDK request model *sdk_request*.

    The file is located from the model's module, which the SDK generates from the schema
    tree: ``adcp.types.domains.media_buy.list_creative_formats_request`` is
    ``media-buy/list-creative-formats-request.json``. Subscripted, so a model whose file
    does not exist, or a schema without a description, fails loudly rather than answering
    with a substitute.

    Raises:
        UnderivablePinnedSchema: *sdk_request* is not a generated ``adcp.types.domains``
            request model, so no schema file can be named for it.
    """
    prefix = "adcp.types.domains."
    module = sdk_request.__module__
    if not module.startswith(prefix) or module.count(".") != 4:
        raise UnderivablePinnedSchema(f"{sdk_request!r} is not a generated adcp.types.domains request model")
    domain, name = module.removeprefix(prefix).split(".")
    path = _schema_root() / domain.replace("_", "-") / f"{name.replace('_', '-')}.json"
    return json.loads(path.read_text())["description"]

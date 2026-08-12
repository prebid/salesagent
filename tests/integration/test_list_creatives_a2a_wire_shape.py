"""A2A wire shape for list_creatives: format_id must stay a {agent_url, id} object.

Regression for GH #1710-adjacent finding (salesagent PR #1868 review, "A2A list_creatives
format_id serializes as bare string instead of {agent_url, id} object"): the pinned AdCP
spec (3.1, core/format-id.json) types every ``format_id`` as a structured object
(``{agent_url, id}``, the v3.1 format-id federation contract) -- never a bare string.

Root cause (since removed): the A2A explicit-skill success path called
``self._serialize_for_a2a(result)`` first, producing a correctly-nested
``artifact_data`` dict (``creatives[i].format_id`` a plain ``{agent_url, id}`` dict at
that point) -- and then fed the SAME dict, by reference, back through a
``ListCreativesResponse(**artifact_data)`` round trip purely to regenerate a
human-readable ``__str__()`` text part. pydantic-core validates the
``creatives: list[Creative]`` field by handing each list item's dict to ``Creative``'s
``@model_validator(mode="before")`` (``validate_format_id``) WITHOUT a defensive copy,
and that validator did ``values["format_id"] = upgrade_legacy_format_id(format_val)``,
MUTATING the shared dict in place: ``artifact_data["creatives"][i]["format_id"]``
became a live ``FormatId`` Python object.

The task/artifact construction that follows (``Part(data=_dict_to_value(artifact_data))``)
then handed ``_dict_to_value`` a dict containing a live, non-JSON-native object.
``_dict_to_value``'s ``json.dumps(d, default=str)`` fallback silently stringified it:
``FormatId.__str__`` returns ``self.id`` -- exactly the observed bare-string symptom.

Both halves are now closed: the validators copy before mutating
(``copy_before_mutating``, guarded by
tests/unit/test_guards_before_validator_no_mutation.py), and the outbound round trip
is gone -- the TextPart is read straight from the payload's stamped ``message``
(pinned by
tests/integration/test_a2a_skill_invocation.py::test_artifact_text_part_is_the_data_part_message).

This reproduces deterministically with a single creative (not scale-dependent at the
mechanism level) via the real in-process A2A pipeline
(``AdCPRequestHandler.on_message_send`` -> explicit skill dispatch ->
``_serialize_for_a2a`` -> ``_dict_to_value``), exercised end-to-end by the harness's
``_run_a2a_handler``.
"""

from __future__ import annotations

import pytest

from tests.factories.core import TenantFactory
from tests.factories.creative import CreativeFactory
from tests.factories.creative_asset import build_assets, image_spec
from tests.factories.principal import PrincipalFactory
from tests.harness.assertions import assert_omits_paths
from tests.harness.creative_list import CreativeListEnv
from tests.harness.transport import Transport
from tests.helpers.pinned_schema import validate_against_pinned_schema

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _list_creatives_via_a2a(count: int, *, assets: dict | None = None):
    """Store *count* creatives and return the real A2A artifact DataPart wire.

    ``assets`` overrides each stored creative's ``data["assets"]`` slot map — pass
    ``build_assets(...)`` output so asset shapes stay declared through AssetSpec.
    """
    extra = {"data": {"assets": assets}} if assets is not None else {}
    with CreativeListEnv() as env:
        tenant = TenantFactory(tenant_id="test_tenant")
        principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
        for i in range(count):
            CreativeFactory(tenant=tenant, principal=principal, creative_id=f"cr_a2a_wire_{i:03d}", **extra)
        result = env.call_via(Transport.A2A, limit=50)
    assert result.is_success, f"Expected success but got error: {result.error}"
    wire = result.wire_response
    assert wire is not None, "A2A dispatch must stash the real artifact DataPart wire"
    return wire


def _assert_format_ids_are_objects(creatives) -> None:
    """Every creative's format_id is a full ``{agent_url, id}`` object, not a bare string.

    One implementation for every creative count: a strengthening here lands on
    all of them at once, which a hand-rolled copy per count does not guarantee.
    """
    for i, item in enumerate(creatives):
        format_id = item.get("format_id")
        assert isinstance(format_id, dict), (
            f"creatives[{i}].format_id must be a {{agent_url, id}} object on the A2A wire "
            f"(spec 3.1 core/format-id.json types it object), got {format_id!r} "
            f"(type {type(format_id).__name__})"
        )
        assert "agent_url" in format_id and "id" in format_id, (
            f"creatives[{i}].format_id object is missing agent_url/id: {format_id!r}"
        )


@pytest.mark.parametrize("count", [1, 15])
def test_a2a_wire_format_id_is_object_not_string(integration_db, count):
    """Every creative's format_id on the real A2A wire is a {agent_url, id} object.

    Graded at both counts: the mechanism is not scale-dependent (count=1 is the
    minimal deterministic reproduction), but count=15 is the scale the original
    report used, so a scale-sensitive regression stays covered.

    Mutation check: revert the ``Creative.validate_format_id`` defensive-copy fix
    (make it mutate its input ``values`` dict again) -> this test goes red, with
    format_id observed as a bare string.
    """
    wire = _list_creatives_via_a2a(count=count)
    creatives = wire.get("creatives")
    assert isinstance(creatives, list) and len(creatives) == count, (
        f"A2A wire must carry {count} creative(s), got {creatives!r}"
    )
    _assert_format_ids_are_objects(creatives)


@pytest.mark.parametrize("null_field", ["alt_text", "provenance"])
def test_a2a_wire_omits_null_asset_fields(integration_db, null_field):
    """A stored asset's unset optional field is ABSENT from the wire, never null.

    ``Creative.assets`` is an untyped ``dict[str, Any]``, so Pydantic's
    ``exclude_none=True`` default never reaches inside it — without
    ``Creative.model_dump()``'s ``strip_none_deep`` pass the stored ``None``
    rides all the way out as a literal wire ``null``, which the pinned AdCP
    asset schemas do not accept.

    This is the wire-level oracle for that fix; the fast unit-level sibling is
    ``tests/unit/test_creative_response_serialization.py::test_creative_model_dump_omits_null_fields_inside_assets``.
    Mutation check: delete the ``strip_none_deep`` call in
    ``src/core/schemas/creative.py`` -> this goes red.
    """
    wire = _list_creatives_via_a2a(
        count=1,
        assets=build_assets(image_spec("banner").with_fields(**{null_field: None})),
    )
    creatives = wire.get("creatives")
    assert isinstance(creatives, list) and creatives, f"A2A wire must carry the creatives array, got {creatives!r}"

    banner = creatives[0].get("assets", {}).get("banner")
    assert isinstance(banner, dict), (
        f"the stored banner asset must reach the wire as an object so its null-omission is "
        f"observable, got {banner!r} — if assets stopped being emitted, this test is vacuous"
    )
    assert_omits_paths(banner, [null_field], context=f"A2A creatives[0].assets.banner (full asset: {banner!r})")
    # Negative control: the pass must strip only the nulls, not the asset itself.
    assert banner["asset_type"] == "image"
    assert banner["url"] == "https://example.com/banner.png"


def test_a2a_wire_validates_against_pinned_response_schema(integration_db):
    """The A2A list_creatives wire validates against the pinned list-creatives-response.json."""
    wire = _list_creatives_via_a2a(count=3)
    # Strip the A2A envelope fields (message, success) that _serialize_for_a2a adds --
    # not declared on the ListCreativesResponse payload model.
    payload = {k: v for k, v in wire.items() if k not in ("message", "success")}
    validate_against_pinned_schema("list-creatives-response.json", payload)

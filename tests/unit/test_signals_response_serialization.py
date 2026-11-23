"""Test that GetSignalsResponse re-serializes its nested Signal objects by INSTANCE.

The subject is ``NestedModelSerializerMixin``: a parent whose field is declared with the
LIBRARY type must still dump the local subclass's own fields, because the value's class and
the field's declared class differ. ``GetSignalsResponse.signals`` is exactly that shape --
declared ``list[Signal]`` where ``Signal`` narrows the pinned type, and whose
``deployments`` in turn narrows to the local ``SignalDeployment``.

- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: All response models with nested Pydantic models
- Pattern: the one serializer re-dumps a child by its instance, not its declared type

WHAT THIS FILE USED TO ASSERT, and why it no longer can. ``Signal`` declared four internal
fields -- ``tenant_id``, ``created_at``, ``updated_at``, ``metadata`` -- with
``Field(exclude=True)``, and both tests here passed all four in and asserted they were
excluded from the nested dump. The fields are DELETED, so the exclusion question is moot and
a stronger fact replaces it: they are not declared at all, so ``Signal`` (``extra="forbid"``
outside production) REFUSES one rather than silently dropping it.

The four were dead in the strongest available sense -- nothing under ``src/`` or ``scripts/``
constructs ``Signal`` at all. ``get_signals`` is a tool this seller CALLS rather than serves,
and the module that dials it (``src/core/signals_agent_registry.py``) parses the reply with
the library ``GetSignalsResponse``; this local type survives only in
``schema_validation.create_schema_registry`` and a schema hook. The pin agrees from the other
side: ``core/signal-listing.json`` declares none of the four, and ``tenant_id`` appears on no
node in the pinned bundle at all. CLAUDE.md pattern #4 is the rule they broke even so -- a
field that must persist belongs on a repository-owned carrier, and ``exclude=True`` strips a
value from PERSISTENCE and the idempotency hash as well as the wire, so a tenant id parked
there would have been dropped by the very marker meant to protect it.
"""

import pytest
from pydantic import ValidationError

from src.core.schemas import GetSignalsResponse, Signal, SignalDeployment

#: The four deleted internal fields, named once so both the refusal test and the absence
#: test read the same list. Re-adding one to ``Signal`` reddens both.
DELETED_INTERNAL_FIELDS = ("tenant_id", "created_at", "updated_at", "metadata")

#: The same shape one level down. ``SignalDeployment`` carried ``scope`` and
#: ``decisioning_platform_segment_id`` as ``Field(exclude=True)``; ``core/deployment.json``
#: declares neither, and no site under ``src/`` or ``scripts/`` constructed this model at
#: all -- the only constructions and the only reads were in this file and two sibling
#: tests, so the fields existed to be asserted. Deleted with salesagent-gweku.
DELETED_DEPLOYMENT_FIELDS = ("scope", "decisioning_platform_segment_id")


def _make_signal(signal_agent_segment_id: str = "signal_123", **overrides) -> Signal:
    """Build a valid Signal with adcp 3.9 pricing_options."""
    defaults = {
        "signal_id": {"source": "catalog", "data_provider_domain": "testprovider.com", "id": signal_agent_segment_id},
        "signal_agent_segment_id": signal_agent_segment_id,
        "name": "Test Signal",
        "description": "Test signal description",
        "signal_type": "marketplace",
        "data_provider": "TestProvider",
        "coverage_percentage": 85.5,
        "deployments": [
            SignalDeployment(
                platform="gam",
                account=None,
                is_live=True,
                type="platform",
                estimated_activation_duration_minutes=None,
            )
        ],
        "pricing_options": [
            {"pricing_option_id": "cpm_usd", "cpm": 2.50, "currency": "USD", "model": "cpm"},
        ],
    }
    defaults.update(overrides)
    return Signal(**defaults)


@pytest.mark.parametrize("field", DELETED_INTERNAL_FIELDS)
def test_signal_does_not_declare_the_deleted_internal_fields(field):
    assert field not in Signal.model_fields


@pytest.mark.parametrize("field", DELETED_INTERNAL_FIELDS)
def test_signal_refuses_a_deleted_internal_field(field):
    """Refused, not dropped — which is the difference the deletion buys.

    ``Signal`` carries ``extra=get_pydantic_extra_mode()``: "forbid" in dev and CI so a stale
    caller fails loudly, "ignore" in production so a newer sender is served (critical
    pattern #7). This asserts the dev/CI arm, which is the environment the suite runs in.
    """
    with pytest.raises(ValidationError) as exc:
        _make_signal(**{field: "whatever"})

    assert any(e["type"] == "extra_forbidden" and e["loc"] == (field,) for e in exc.value.errors()), (
        f"expected {field} to be refused as an undeclared field, got {exc.value.errors()}"
    )


@pytest.mark.parametrize("field", DELETED_DEPLOYMENT_FIELDS)
def test_deployment_does_not_declare_the_deleted_internal_fields(field):
    assert field not in SignalDeployment.model_fields


@pytest.mark.parametrize("field", DELETED_DEPLOYMENT_FIELDS)
def test_deployment_refuses_a_deleted_internal_field(field):
    """Refused, not dropped, for the reason the ``Signal`` pair above gives.

    ``SignalDeployment`` carries ``extra=get_pydantic_extra_mode()`` too, so re-adding one
    of these at a construction site fails loudly in dev and CI instead of the value
    becoming an extra. That matters more here than on ``Signal``: the deleted pair was
    ``exclude=True``, so while the fields existed the attribute was their ONLY observable
    and a value that never arrived looked exactly like one doing its job.
    """
    with pytest.raises(ValidationError) as exc:
        SignalDeployment(platform="gam", is_live=True, type="platform", **{field: "whatever"})

    assert any(e["type"] == "extra_forbidden" and e["loc"] == (field,) for e in exc.value.errors()), (
        f"expected {field} to be refused as an undeclared field, got {exc.value.errors()}"
    )


def test_nested_deployment_dumps_the_pinned_members_through_the_parent():
    """The nested re-dump still runs, and the deleted pair does not come back through it.

    This is deliberately NOT a test that the mixin preserves a local child's extra wire
    fields: with ``Signal``'s four internal fields deleted and ``SignalDeployment``'s two
    deleted as well, this response has no local-subclass field left to grade either way.
    ``SyncCreativesResponse`` — the original bug this file cites — is where that half is
    graded. What is asserted here is that the nested child reaches the wire AT ALL, so the
    absences above are a deletion and not an empty dump.
    """
    result = GetSignalsResponse(signals=[_make_signal()]).model_dump()

    deployment = result["signals"][0]["deployments"][0]
    for field in DELETED_DEPLOYMENT_FIELDS:
        assert field not in deployment
    assert deployment["platform"] == "gam"
    assert deployment["is_live"] is True


def test_get_signals_response_carries_the_pinned_signal_fields():
    result = GetSignalsResponse(signals=[_make_signal()]).model_dump()

    signal_data = result["signals"][0]
    assert signal_data["signal_agent_segment_id"] == "signal_123"
    assert signal_data["name"] == "Test Signal"
    assert signal_data["description"] == "Test signal description"
    assert signal_data["signal_type"] == "marketplace"
    assert signal_data["data_provider"] == "TestProvider"
    assert signal_data["coverage_percentage"] == 85.5
    assert "deployments" in signal_data
    assert "pricing_options" in signal_data


def test_get_signals_response_with_multiple_signals():
    """Every signal in the list is serialized, and the strip applies to each — not just the first."""
    signals = [
        _make_signal(
            signal_agent_segment_id=f"signal_{i}",
            name=f"Test Signal {i}",
            description=f"Description {i}",
            signal_type="marketplace" if i % 2 == 0 else "custom",
            data_provider=f"Provider{i}",
            coverage_percentage=float(80 + i),
        )
        for i in range(3)
    ]

    result = GetSignalsResponse(signals=signals).model_dump()

    assert len(result["signals"]) == 3
    for i, signal_data in enumerate(result["signals"]):
        assert signal_data["signal_agent_segment_id"] == f"signal_{i}"
        assert signal_data["name"] == f"Test Signal {i}"
        # The nested strip holds on EVERY element, which is the list case a declared-type
        # dump gets wrong: the parent re-serializes each child by its own instance.
        assert "decisioning_platform_segment_id" not in signal_data["deployments"][0]
        assert signal_data["deployments"][0]["platform"] == "gam"
        for field in DELETED_INTERNAL_FIELDS:
            assert field not in signal_data

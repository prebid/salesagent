"""Unexpected-type behavior of the ``schema_helpers`` wire-object coercions.

``_coerce_wire_object`` backs five ``to_*`` helpers. Four of them degrade a
non-dict, non-model value to ``None``; ``to_account_reference`` alone rejects it
as a typed ``AdCPValidationError``.

The asymmetry is deliberate. The A2A skills read ``account`` straight off raw
``parameters`` with no model in front of them, so a silently-dropped account
skips identity enrichment, leaves ``identity.sandbox`` ``False``, and dispatches
a sandbox request to the LIVE adapter — a quiet failure on the axis account
isolation exists to defend. ``context``, by contrast, is exempt by spec: it is
opaque correlation data the pinned schema says is "never parsed by AdCP agents",
so hard-failing a non-dict ``context`` would contradict the spec.

The remaining converters degrade for reasons that have NOT been individually
established. ``to_property_list_reference`` in particular is reached from a raw
A2A read of ``parameters`` — the same unmodelled surface that motivated the
account narrowing — where a non-dict ``property_list`` degrades to ``None`` and
silently drops the buyer's scoping request. Whether that should also be strict
needs a spec cross-check and is tracked at #1997; the tests below pin only
today's behavior, and are not an argument that it is correct.
"""

import contextlib
import inspect
from typing import Any
from unittest import mock

import pytest
from adcp.types import (
    AccountReference,
    ContextObject,
    PropertyListReference,
    PushNotificationConfig,
    ReportingWebhook,
)
from pydantic import BaseModel

from src.core import schema_helpers
from src.core.exceptions import AdCPValidationError
from src.core.schema_helpers import (
    require_push_notification_config,
    to_account_reference,
    to_context_object,
    to_property_list_reference,
    to_push_notification_config,
    to_reporting_webhook,
)

# Values that are neither ``None``, nor a dict, nor the target model.
_UNEXPECTED_TYPES = ["acc_123", 123, ["acc_123"], 4.5, True, ("acc_123",)]

# The four converters that must keep degrading to ``None``.
_DEGRADING_CONVERTERS = [
    (to_context_object, ContextObject),
    (to_reporting_webhook, ReportingWebhook),
    (to_push_notification_config, PushNotificationConfig),
    (to_property_list_reference, PropertyListReference),
]

# How a wire-value converter is spelled in this module. Every spelling is here on
# purpose: ``coerce_creative_filters`` already establishes that a converter need
# not be spelled ``to_*``, and ``require_push_notification_config`` that it need
# not be spelled ``to_*``/``coerce_*`` either — so keying membership on a subset of
# the prefixes was escapable, the exact escape this pin exists to close. ``ensure_``
# matches no converter today (future-proofing) — the escape is answered by WIDENING
# the rule, not by hand-listing the one instance.
_CONVERTER_PREFIXES = ("to_", "coerce_", "require_", "ensure_")

# Converters that own their dict → model boundary directly instead of routing
# through ``_coerce_wire_object``. They cannot inherit the ``strict=False``
# default, so they are not a fail-open hazard — but they ARE exported converter
# names, so the membership pin needs them classified explicitly. They are
# invisible to the delegation pin by construction.
_OWN_BOUNDARY_CONVERTERS = {"to_brand_reference", "coerce_creative_filters"}

# The converters that reach ``_coerce_wire_object`` — the SHARED core both pins
# classify. Kept in one place so the two pins cannot drift: the name pin adds only
# its legitimate extra (``_OWN_BOUNDARY_CONVERTERS``, which own their boundary and
# so never delegate), the delegation pin adds nothing.
_CLASSIFIED_DELEGATING = {converter.__name__ for converter, _ in _DEGRADING_CONVERTERS} | {
    "to_account_reference",  # strict: rejects non-dict (tested below)
    # Non-optional wrapper over the degrading ``to_push_notification_config``: a
    # non-dict degrades to None inside the delegate, then the require-guard raises
    # rather than returning None. Rejects (never returns None) — pinned below.
    "require_push_notification_config",
}


def test_every_exported_converter_is_classified() -> None:
    """Pin 1 of 2 (NAME) — every exported converter name is accounted for.

    ``strict=False`` is ``_coerce_wire_object``'s default, so a converter added
    tomorrow degrades a non-dict to ``None`` by default AND is absent from
    ``_DEGRADING_CONVERTERS`` above — untested on exactly the axis that made the
    account case a fail-open.

    This pin and the delegation pin below are COMPLEMENTARY, not alternatives:
    they catch disjoint escapes. Membership fires on the NAME, so it holds
    whatever the new helper does internally — a hand-rolled ``return None`` that
    never touches ``_coerce_wire_object``, or a signature the delegation pin
    cannot probe positionally (keyword-only first parameter), is still an
    exported converter name here. Both of those are invisible to a
    behavior-keyed pin.

    The price of that reach is the two hand-written ``_OWN_BOUNDARY_CONVERTERS``
    exemptions, which is why ``to_brand_reference`` does need classifying here.
    """
    exported = {
        name
        for name in schema_helpers.__all__
        if name.startswith(_CONVERTER_PREFIXES) and inspect.isfunction(getattr(schema_helpers, name, None))
    }
    classified = _CLASSIFIED_DELEGATING | _OWN_BOUNDARY_CONVERTERS
    assert exported == classified, (
        "an exported converter is unclassified — decide whether it degrades (add it to "
        "_DEGRADING_CONVERTERS), rejects (strict=True, and test it), or owns its own "
        "boundary (add it to _OWN_BOUNDARY_CONVERTERS)"
    )


def test_every_coercing_converter_is_classified() -> None:
    """Pin 2 of 2 (DELEGATION) — every helper reaching the delegate is classified.

    Complements the membership pin above rather than replacing it. Membership is
    keyed on the name, so a helper that delegates under neither spelling in
    ``_CONVERTER_PREFIXES`` escapes it entirely; this pin is keyed on BEHAVIOR,
    observed by CALLING each exported helper with the delegate spied — never by a
    name prefix and never by reading source.

    Attribution is by sentinel IDENTITY: a helper is credited only when the
    delegate receives the exact probe object that helper was handed. That keeps
    the real discovery intact — a request builder reaching the delegate only
    transitively is attributed to the converter, not to itself
    (``create_get_products_request`` forwards its own ``account`` argument to
    ``to_account_reference``, never the probe) — and, unlike reading the calling
    frame, it still credits a converter that delegates through a private
    intermediate, where the frame read named the intermediate and failed open.
    """
    probe = {"__probe__": True}  # identity, not value, is the attribution key
    delegating: set[str] = set()
    real = schema_helpers._coerce_wire_object
    probing = ""

    # Annotated to mirror ``_coerce_wire_object``'s own signature, so a change to
    # the delegate that this spy no longer matches is visible at the seam.
    def spy[ModelT: BaseModel](value: Any, model_cls: type[ModelT], context: str, **kwargs: Any) -> ModelT | None:
        if value is probe:
            delegating.add(probing)
        return real(value, model_cls, context, **kwargs)

    candidates = [
        name
        for name in schema_helpers.__all__
        if inspect.isfunction(getattr(schema_helpers, name, None))
        and getattr(schema_helpers, name).__module__ == schema_helpers.__name__
    ]
    with mock.patch.object(schema_helpers, "_coerce_wire_object", spy):
        for probing in candidates:
            fn = getattr(schema_helpers, probing)
            params = list(inspect.signature(fn).parameters.values())
            if not params or params[0].kind not in (params[0].POSITIONAL_ONLY, params[0].POSITIONAL_OR_KEYWORD):
                continue
            with contextlib.suppress(Exception):
                fn(probe)  # a dict reaches the delegate on every coercing helper

    classified = _CLASSIFIED_DELEGATING
    assert delegating == classified, (
        "a helper delegating to _coerce_wire_object is unclassified — decide whether it "
        "degrades (add it to _DEGRADING_CONVERTERS) or rejects (strict=True, and test it)"
    )


@pytest.mark.parametrize("value", _UNEXPECTED_TYPES)
def test_to_account_reference_rejects_unexpected_type(value: object) -> None:
    """A non-dict account raises instead of coercing to ``None`` (fail-loud)."""
    with pytest.raises(AdCPValidationError) as excinfo:
        to_account_reference(value)  # type: ignore[arg-type]
    assert excinfo.value.suggestion, "typed rejection must carry a top-level suggestion"


@pytest.mark.parametrize("value", _UNEXPECTED_TYPES)
def test_require_push_notification_config_rejects_unexpected_type(value: object) -> None:
    """A non-dict config raises rather than resolving to ``None`` (require-contract).

    The optional ``to_push_notification_config`` degrades a non-dict to ``None``; the
    non-optional ``require_`` wrapper turns that ``None`` into a named ValueError so a
    caller annotated as never receiving ``None`` cannot silently be handed one.
    """
    with pytest.raises(ValueError, match="resolved to None"):
        require_push_notification_config(value)  # type: ignore[arg-type]


def test_to_account_reference_rejection_matches_malformed_dict_shape() -> None:
    """Both ways of malforming an account produce the same buyer-facing contract.

    The unexpected-type rejection routes through the same
    ``adcp_validation_boundary`` as a malformed dict, so a buyer sees one
    consistent error shape for "bad account" regardless of which way it was bad:
    the same code, recovery, ``field`` and ``suggestion``, and the same
    ``Invalid account value:`` message prefix. The message *after* that prefix
    still carries the differing pydantic detail — that is not claimed identical.
    """
    with pytest.raises(AdCPValidationError) as from_bad_type:
        to_account_reference("acc_123")  # type: ignore[arg-type]
    with pytest.raises(AdCPValidationError) as from_bad_dict:
        to_account_reference({})

    assert from_bad_type.value.error_code == from_bad_dict.value.error_code
    assert from_bad_type.value.recovery == from_bad_dict.value.recovery
    assert from_bad_type.value.field == from_bad_dict.value.field == "account"
    assert from_bad_type.value.suggestion == from_bad_dict.value.suggestion
    assert str(from_bad_type.value).startswith("Invalid account value:")
    assert str(from_bad_dict.value).startswith("Invalid account value:")


@pytest.mark.parametrize("value", [*_UNEXPECTED_TYPES, {}, {"wrong_key": 1}])
def test_to_account_reference_rejection_names_the_request_field_not_the_model(value: object) -> None:
    """The buyer-visible field/suggestion name ``account``, never the pydantic model.

    ``AccountReference`` is a generated union whose members pydantic reports as
    ``AccountReference1``/``AccountReference2``. Neither name appears in any buyer
    request, so leaking one into ``field`` (a JSONPath-lite path into the REQUEST)
    or into ``suggestion`` tells the buyer to correct a field they never sent.

    Scope, stated so it is not mistaken for more: these two DIRECTIVE channels are
    the whole of the guarantee, and ``message``/``details`` still carry the
    generated names. Which builder puts them in each, how far that actually reaches,
    and why the two rejection paths have DIFFERENT provenance (pre-existing on the
    malformed-dict path, new on the non-dict path) are stated once in
    ``to_account_reference``'s docstring rather than restated — and drifted — here.
    """
    with pytest.raises(AdCPValidationError) as excinfo:
        to_account_reference(value)  # type: ignore[arg-type]

    assert excinfo.value.field == "account"
    assert "AccountReference" not in str(excinfo.value.suggestion)


def test_to_account_reference_still_accepts_valid_inputs() -> None:
    """Narrowing the unexpected-type case leaves the supported inputs untouched."""
    typed = to_account_reference({"account_id": "acc_123"})
    assert isinstance(typed, AccountReference)
    assert to_account_reference(typed) is typed
    assert to_account_reference(None) is None


@pytest.mark.parametrize(("converter", "model_cls"), _DEGRADING_CONVERTERS)
@pytest.mark.parametrize("value", _UNEXPECTED_TYPES)
def test_other_converters_still_degrade_to_none(converter, model_cls: type, value: object) -> None:
    """The four non-account converters keep their long-standing ``None`` fallback.

    This is the regression the account narrowing was scoped to avoid: the
    ``strict`` flag is opt-in, so flipping the shared fallback would show up here.
    """
    assert converter(value) is None


@pytest.mark.parametrize(
    ("converter", "model_cls"),
    # ``ContextObject`` is excluded: its schema sets ``extra="allow"`` because
    # context is opaque correlation data, so no dict is malformed for it. That
    # permissiveness is pinned separately below.
    [pair for pair in _DEGRADING_CONVERTERS if pair[1] is not ContextObject],
)
def test_other_converters_still_reject_malformed_dicts(converter, model_cls: type) -> None:
    """Degrading on a non-dict does not mean degrading on a malformed dict."""
    with pytest.raises(AdCPValidationError):
        converter({"definitely_not_a_field": object()})


def test_to_context_object_accepts_arbitrary_keys_by_design() -> None:
    """Context is opaque correlation data — arbitrary keys are valid, not malformed.

    This is why ``to_context_object`` must NOT adopt the account converter's
    strict rejection: the pinned schema describes context as never parsed by
    AdCP agents, merely preserved and returned.
    """
    result = to_context_object({"buyer_trace_id": "t-1", "anything": {"nested": True}})
    assert isinstance(result, ContextObject)
    assert result.model_dump()["anything"] == {"nested": True}

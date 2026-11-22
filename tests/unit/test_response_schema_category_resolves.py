"""A tool's pinned response type must be DERIVED, and must be the one production answers with.

Two consumers derive it from the pinned SDK, both through
:mod:`tests.helpers.adcp_domain_types`, and this module grades both:

- ``tests/helpers/response_schemas._category`` -> the schema DIRECTORY, so a
  response is graded against the right file;
- ``tests/harness/spec_models.spec_response_model`` -> the parse-back CLASS, so the
  harness reads the seller's wire body through a contract the seller actually claims.

The second property is the stronger one, and it is the one that would have caught
the adcp 9.0.0 regression on its own. A bare-filename schema resolution that happens
to hit the right file fails loudly on the next basename collision. A parse-back class
that is WRONG BUT STILL PARSES fails only when the two shapes diverge — which is
exactly what adcp 9 made happen, silently, for four tools. So class identity is
asserted against production's own response type, never against a name: the
pre-existing name check
(``spec_response_model("get_products").__name__ == "GetProductsResponse"``) passed
throughout the regression, because both the right and the wrong class carry that name.

Production's response type is read STATICALLY, off the registry row's ``impl`` return
annotation, so nothing here dispatches a tool.

How the category half fails, and why a passing resolution is not enough: when
``_category`` returns nothing, ``response_schema_ref`` degrades to a bare basename and
the loader searches the whole pinned tree for it. That SILENTLY WORKS right up to the
first basename that exists in two categories, and then refuses. At adcp 9.0.0 all
seven affected tools lost their category; six kept passing because their basenames are
unique, and only ``list-creative-formats-response.json`` collides — ``creative/``
(titled "List Creative Formats Response (Creative Agent)") against ``media-buy/``.

THE SIX THAT KEPT PASSING ARE WHAT THIS GRADES. A bare-filename resolution that lands
on the right file is graded by accident and nothing reports it, so a bare ref is a
FAILURE here even when it loads: the assertion is that the category was DERIVED, not
that the schema was eventually found.

Scope note: this grades the derivation, not the schema contents. Whether
``media-buy/list-creative-formats-response.json`` is the right contract for this
seller is a spec question, answered where the ref is consumed.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel

from src.core.tools.registry import TOOLS
from tests.harness.spec_models import spec_response_model
from tests.helpers.adcp_domain_types import _declarations, response_declaration
from tests.helpers.adcp_domain_types import tool_camel as _camel
from tests.helpers.pinned_schema import load
from tests.helpers.response_schemas import _category, response_schema_ref


def _production_response_type(tool: str) -> type[BaseModel]:
    """The model production answers *tool* with, read off the registry row's impl.

    The return annotation IS the answer and it is available without dispatching
    anything: ``ToolSpec.impl`` is the function object, and every implementation
    annotates its return. That is what makes the identity check below a static one.
    """
    annotation = typing.get_type_hints(TOOLS[tool].impl).get("return")
    assert isinstance(annotation, type) and issubclass(annotation, BaseModel), (
        f"{tool}: the registry's impl must annotate a BaseModel return for its response "
        f"type to be checkable, got {annotation!r}"
    )
    return annotation


#: Tools the pinned AdCP tree does not describe at all, with the reason. These have no
#: SDK response type under ANY spelling, so there is no domain package to read a category
#: off and no category to derive — unlike the v9 regression, where the type existed and
#: the derivation was looking in the wrong place.
#:
#: May only SHRINK. A tool listed here that starts resolving is caught below as a stale
#: entry; a tool that stops resolving gets the derivation fixed, never a row here.
_NO_PINNED_RESPONSE = {
    "complete_task": (
        "Seller-specific tool, absent from the pinned AdCP 3.2 tree entirely: no "
        "CompleteTaskResponse in adcp.types, in adcp.types.domains.*, or under a Legacy* "
        "spelling, and no complete-task-response.json in the SDK tree. Its schemas are "
        "app-owned under tests/helpers/adcp_pinned_schema.app_schema_root() (schemas/ at "
        "the repo root), where a bare filename is unambiguous because that tree is flat. "
        "Not a derivation defect and not a DTO/public-name mismatch — the registry row "
        "spells CompleteTaskRequest, which is the name the SDK would use if it had one."
    ),
}


@pytest.mark.parametrize("tool", sorted(set(TOOLS) - set(_NO_PINNED_RESPONSE)))
def test_response_schema_ref_is_category_qualified(tool: str):
    """The ref must carry a derived category, and that ref must load.

    Both halves matter and neither implies the other. A bare ref can load (six tools
    did, through all of adcp 9.0.0's regression) and a qualified ref could name a
    directory that is not there.
    """
    category = _category(tool)
    assert category is not None, (
        f"{tool}: no schema category could be derived. {_camel(tool)}Response is declared "
        f"by no package under adcp.types.domains, so response_schema_ref() falls back to "
        f"the bare filename {tool.replace('_', '-')}-response.json and the loader searches "
        f"the whole pinned tree for it. That resolves by luck until the basename collides. "
        f"Fix the derivation in tests/helpers/adcp_domain_types — do not add "
        f"a row to _NO_PINNED_RESPONSE, which is only for tools the pinned tree omits."
    )

    ref = response_schema_ref(tool)
    assert ref == f"{category}/{tool.replace('_', '-')}-response.json", (
        f"{tool}: derived category {category!r} but the ref is {ref!r}"
    )
    load(ref)  # PinnedSchemaError if the derived category names nothing


@pytest.mark.parametrize(("tool", "reason"), sorted(_NO_PINNED_RESPONSE.items()))
def test_no_pinned_response_entries_are_not_stale(tool: str, reason: str):
    """An allowlisted tool that acquires a pinned response type loses its row.

    Without this, a spec release that adds the tool leaves it permanently exempt from
    the check above — the allowlist outliving its reason is how it stops shrinking.
    """
    assert tool in TOOLS, f"{tool} is no longer a registered tool; drop its _NO_PINNED_RESPONSE row"
    assert reason.strip(), f"{tool}: _NO_PINNED_RESPONSE rows must carry a reason"
    assert not _declarations(tool), (
        f"{tool}: {_camel(tool)}Response is now declared by "
        f"{[domain for domain, _ in _declarations(tool)]}, so the pinned tree describes this "
        f"tool and its category derives. Remove its _NO_PINNED_RESPONSE row so the category "
        f"and parse-back checks cover it."
    )


def test_two_domains_declaring_one_response_name_raises(monkeypatch):
    """Ambiguity must REFUSE, not resolve to whichever package sorts first.

    This is the failure mode the v9 regression was a mild version of: a category
    picked by accident grades a seller against a contract that is not theirs, and
    looks green doing it. ``list_creative_formats`` is the realistic shape — two
    schemas, two actors ("(Creative Agent)" vs the media-buy seller), one basename —
    so the refusal is graded here rather than left to be discovered.
    """
    import adcp.types.domains as adcp_domains

    # get_products is declared by media_buy alone; pretend creative declares it too.
    assert [domain for domain, _ in _declarations("get_products")] == ["media_buy"], (
        "fixture assumption broke: get_products is no longer media_buy-only"
    )
    import adcp.types.domains.creative as creative_domain

    monkeypatch.setattr(creative_domain, "GetProductsResponse", object, raising=False)
    monkeypatch.setattr(adcp_domains, "DOMAINS", ("creative", "media_buy"), raising=False)
    _declarations.cache_clear()
    try:
        with pytest.raises(ValueError, match="declared by 2 domain packages"):
            _category("get_products")
        with pytest.raises(ValueError, match="declared by 2 domain packages"):
            spec_response_model("get_products")
    finally:
        _declarations.cache_clear()


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_spec_response_model_is_a_class_production_answers_with(tool: str):
    """The harness's parse-back model must be one production's response SATISFIES.

    THE ASSERTION IS CLASS IDENTITY AGAINST PRODUCTION, not a name, and the
    distinction is the whole value of this test. At adcp 9.0.0 the harness resolved
    ``get_products`` to ``adcp.types.canonical_creative.GetProductsResponse`` while
    production answered with ``src.core.schemas.product.GetProductsResponse``, which
    inherits ``LegacyGetProductsResponse`` — the ``domains.media_buy`` class. Both
    candidates are named ``GetProductsResponse``, so every name-based check passed
    while the harness graded the seller against a contract the seller never claimed.
    The canonical model then refused legacy ``format_ids`` outright.

    MRO membership is the right relation rather than equality: production extends the
    pinned model (critical pattern #1), so the pinned class is an ancestor, never the
    same object.
    """
    model = spec_response_model(tool)
    if model is None:
        return  # covered by test_spec_response_model_returns_none_only_when_explained
    production = _production_response_type(tool)
    assert model in production.__mro__, (
        f"{tool}: the harness parses responses back through {model.__module__}.{model.__name__}, "
        f"which is NOT in the MRO of {production.__module__}.{production.__name__} — the model "
        f"production actually answers with. The harness would grade this seller against a "
        f"contract it never claimed to satisfy, and such a model can still PARSE, so nothing "
        f"else reports it. Production's pinned ancestors are "
        f"{[f'{b.__module__}.{b.__name__}' for b in production.__mro__ if b.__module__.startswith('adcp.')]}."
    )


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_spec_response_model_returns_none_only_when_explained(tool: str):
    """A ``None`` must be explained by the SDK's declaration, never accidental.

    ``spec_response_model`` returns ``None`` for a tool whose pinned response is a
    UNION of outcome variants — there is no single class to parse into, and guessing a
    member is worse than declining. That is legitimate. What is not legitimate is a
    ``None`` arriving because the lookup went to the wrong place, which is how
    ``list_creative_formats`` read at adcp 9.0.0: its flat name had been renamed away,
    so the harness silently had no parse-back model for a tool that has one.

    So the two reasons are pinned to the SDK's own declaration: a union, or nothing
    declared at all. Anything else means the derivation broke again.

    Observation, deliberately not a rule: ``create_media_buy`` and ``update_media_buy``
    answer with ``CreateMediaBuyResult`` / ``UpdateMediaBuyResult``, which inherit NO
    branch of their SDK union, whereas ``sync_accounts`` and ``sync_creatives`` inherit
    branch 1. Requiring branch membership would therefore need an exception list for
    the first two, and a rule with a carve-out for what it finds permits whatever it
    finds. The class-identity check above covers every tool that has a single class,
    which includes all four the regression broke.
    """
    model = spec_response_model(tool)
    _, declared = response_declaration(tool)
    if model is not None:
        assert declared is model, f"{tool}: returned {model!r} but the SDK declares {declared!r}"
        return
    assert declared is None or not isinstance(declared, type), (
        f"{tool}: spec_response_model returned None, but the pinned SDK declares "
        f"{declared!r} — a single class, which the harness could and should parse into. "
        f"A None here is only correct for an SDK-level union or a tool the pinned tree "
        f"does not describe. Fix the derivation in tests/helpers/adcp_domain_types."
    )

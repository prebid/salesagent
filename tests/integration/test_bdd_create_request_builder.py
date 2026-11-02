"""One create_media_buy request builder, and one pricing_option_id helper.

``tests/bdd/steps/generic/_create_request.build_create_request_kwargs`` (UC-002
idempotency replay, UC-019 post-create poll) and
``tests/bdd/steps/generic/given_media_buy._ensure_request_defaults`` (~60 Given
steps) assemble the same base request dict against the same seeded product. The
lane makes the second delegate to the first — the safe direction, on layering:
``_create_request.py`` is a private, step-free helper module, so it can be imported
by a step module without a step module becoming a dependency of a helper.

Delegation covers ONLY the base dict literal. Three behaviours of the caller are
load-bearing and stay with the caller, so each gets a pin here rather than a
comment:

  * the if-absent guard — ``ctx["request_kwargs"]`` is an ACCUMULATOR every Given
    mutates, so a second call must not rebuild it (the builder overwrites
    unconditionally, by design, for its own callers);
  * the ``ctx.get`` fallback to ``guaranteed_display`` / ``cpm_usd_fixed`` — the
    builder indexes ``ctx["default_product"]`` directly, so a naive delegation turns
    a fallback into a KeyError;
  * the per-scenario-stable ``idempotency_key`` — a genuinely different operation
    from ``MediaBuyCreateEnv._ensure_idempotency_key``'s fresh-per-CALL key. UC-002's
    replay scenarios dispatch ``dict(ctx["request_kwargs"])`` twice and rely on both
    dispatches carrying the SAME key to hit the idempotency cache; the harness default
    relies on two dispatches being two buys. Collapsing them inverts one of the two.
    The builder therefore stays key-free so it cannot become a third minting site.

Equality is graded as SAME KEY SET, SAME VALUE SHAPES — not "byte-identical". The
builder takes one ``now`` and derives both timestamps from it; the caller makes two
separate ``datetime.now(UTC)`` calls, so the two ends differ by microseconds. That
difference is behaviourally irrelevant and not worth engineering away. The real gate
on the delegation is the uc002 + uc019 BDD run, with the replay-hash scenarios as the
canary.

GH #1941 (review finding F19, incl. BINDING #3)
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.bdd.steps.generic import _create_request, given_media_buy
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The two builders read the clock independently; only a difference large enough to
# mean "a different day was computed" is a real divergence.
_TIMESTAMP_TOLERANCE_SECONDS = 60


@pytest.fixture
def seeded_ctx(integration_db):
    """A ctx carrying the product/pricing rows ``setup_media_buy_data()`` seeds.

    Mirrors what tests/bdd/conftest.py puts on ctx for a create scenario
    (``default_product`` / ``default_pricing_option``), built from the real
    factories through the harness rather than hand-rolled.
    """
    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, pricing_option = env.setup_media_buy_data()
        yield {"default_product": product, "default_pricing_option": pricing_option}


def _seconds_apart(left: str, right: str) -> float:
    return abs((datetime.fromisoformat(left) - datetime.fromisoformat(right)).total_seconds())


class TestOneRequestBuilder:
    """The caller's base dict is the builder's base dict."""

    def test_caller_and_builder_produce_the_same_base_dict(self, seeded_ctx: dict[str, Any]):
        """Same key set (modulo the caller's own idempotency_key), same value shapes.

        RED before the lane on the key set: the builder writes ``po_number: None``
        when no PO number is supplied, a key the caller's dict does not have.
        """
        built = _create_request.build_create_request_kwargs(dict(seeded_ctx), po_number=None)
        accumulated = given_media_buy._ensure_request_defaults(dict(seeded_ctx))

        assert set(built) == set(accumulated) - {"idempotency_key"}, (
            "the caller's base dict and the shared builder's disagree on which keys they write"
        )
        assert built["brand"] == accumulated["brand"]
        assert built["packages"] == accumulated["packages"]
        assert _seconds_apart(built["start_time"], accumulated["start_time"]) < _TIMESTAMP_TOLERANCE_SECONDS
        assert _seconds_apart(built["end_time"], accumulated["end_time"]) < _TIMESTAMP_TOLERANCE_SECONDS

    def test_builder_omits_po_number_when_none_and_writes_it_when_given(self, seeded_ctx: dict[str, Any]):
        """``po_number`` is absent, never ``None``.

        ``_create_request``'s own docstring keeps ``po_number`` explicit "for
        idempotency-hash + cross-transport parity", so writing it as ``None`` is a
        payload change rather than a no-op — and it would put a key in the dict that
        dict-level assertions in ~60 Given steps do not expect.
        """
        omitted = _create_request.build_create_request_kwargs(dict(seeded_ctx), po_number=None)
        supplied = _create_request.build_create_request_kwargs(dict(seeded_ctx), po_number="PO-4471")

        assert "po_number" not in omitted, f"po_number written as {omitted.get('po_number')!r} instead of omitted"
        assert supplied["po_number"] == "PO-4471"

    def test_builder_mints_no_idempotency_key(self, seeded_ctx: dict[str, Any]):
        """The shared builder must not become a third idempotency-key minting site.

        The two that exist are genuinely different operations (see the module
        docstring); a key minted here would silently override whichever of them the
        caller intended.
        """
        built = _create_request.build_create_request_kwargs(dict(seeded_ctx), po_number="PO-4471")
        assert "idempotency_key" not in built


class TestCallerBehavioursThatDelegationMustNotEat:
    """Three properties of ``_ensure_request_defaults`` that survive the delegation."""

    def test_an_existing_request_kwargs_is_not_rebuilt(self, seeded_ctx: dict[str, Any]):
        """``ctx["request_kwargs"]`` is an accumulator: earlier Given steps' edits stand.

        The builder overwrites ``ctx["request_kwargs"]`` unconditionally, so the caller
        keeps the if-absent guard. Without it, every Given that runs after another Given
        has already shaped the request would silently discard that shaping.
        """
        ctx = dict(seeded_ctx)
        ctx["request_kwargs"] = {
            "brand": {"domain": "already-set.example"},
            "packages": [{"product_id": "prod_1", "budget": 1000, "pricing_option_id": "po_1"}],
            "po_number": "PO-EARLIER",
        }

        result = given_media_buy._ensure_request_defaults(ctx)

        # Each assertion compares against the INPUT, not against a literal. An earlier
        # version asserted `result["packages"] == []` — a constant that happened to equal
        # the input, so it graded nothing about pass-through and silently survived a
        # fixture change to the input alone. This test is named for the caller's shaping
        # NOT being eaten; comparing to the input is the only form that grades that.
        assert result["brand"] == ctx["request_kwargs"]["brand"]
        assert result["packages"] == ctx["request_kwargs"]["packages"]
        assert result["po_number"] == ctx["request_kwargs"]["po_number"]

    def test_missing_seed_data_falls_back_instead_of_raising(self):
        """No ``default_product`` on ctx resolves to the documented fallback ids.

        The builder indexes ``ctx["default_product"]`` directly. A delegation that
        forwarded a bare ctx would turn this fallback into a KeyError, breaking every
        Given step that runs before (or without) the product-seeding fixture.
        """
        result = given_media_buy._ensure_request_defaults({})

        package = result["packages"][0]
        assert package["product_id"] == "guaranteed_display"
        assert package["pricing_option_id"] == "cpm_usd_fixed"

    def test_the_scenario_idempotency_key_is_stable_across_calls(self, seeded_ctx: dict[str, Any]):
        """One key per scenario — this is what makes UC-002's replay scenarios replay.

        ``uc002_create_media_buy.py`` dispatches ``dict(ctx["request_kwargs"])`` twice;
        both dispatches must carry the same key to reach the idempotency cache.
        """
        ctx = dict(seeded_ctx)

        first = given_media_buy._ensure_request_defaults(ctx)["idempotency_key"]
        second = given_media_buy._ensure_request_defaults(ctx)["idempotency_key"]

        assert first == second
        assert first.startswith("bdd-key-")


class TestHarnessRePinDoesNotClobberADeliberateOverride:
    """``harness_create_request_kwargs`` re-pins PLACEHOLDER ids and nothing else.

    Its job is to rescue a request built before the harness seeded its rows. Written
    unconditionally it also overwrote a package a Given had deliberately pointed at
    another option — an auction option, a EUR option, a second product — and the
    scenario went on grading the default fixed option while reading as if it graded what
    it asked for. A silent wrong answer, not a failure, which is why it needs a test
    rather than a comment.
    """

    def test_a_placeholder_built_request_is_repinned_to_the_seeded_rows(self, seeded_ctx: dict[str, Any]):
        """The behaviour the re-pin exists for: no seed at build time, seed by dispatch time."""
        ctx: dict[str, Any] = {}
        given_media_buy._ensure_request_defaults(ctx)
        assert ctx["request_kwargs"]["packages"][0]["product_id"] == "guaranteed_display", (
            "precondition: a ctx with no seeded product must build against the placeholder ids"
        )

        ctx.update(seeded_ctx)
        package = given_media_buy.harness_create_request_kwargs(ctx)["packages"][0]

        assert package["product_id"] == seeded_ctx["default_product"].product_id
        assert package["pricing_option_id"] == _create_request.pricing_option_id(seeded_ctx["default_pricing_option"])

    def test_a_deliberate_override_survives_the_repin(self, seeded_ctx: dict[str, Any]):
        """A Given that chose another product/option keeps it — that choice IS the scenario."""
        ctx = dict(seeded_ctx)
        given_media_buy._ensure_request_defaults(ctx)
        ctx["request_kwargs"]["packages"][0]["product_id"] = "standard_video"
        ctx["request_kwargs"]["packages"][0]["pricing_option_id"] = "cpm_usd_auction"

        package = given_media_buy.harness_create_request_kwargs(ctx)["packages"][0]

        assert package["product_id"] == "standard_video"
        assert package["pricing_option_id"] == "cpm_usd_auction"


class TestOnePricingOptionIdHelper:
    """``pricing_option_id`` exists once, not once per module (BINDING #3)."""

    def test_given_media_buy_imports_the_helper_rather_than_redefining_it(self):
        """RED before the lane: ``given_media_buy`` carries a character-identical copy.

        Landing "one request builder" while leaving two copies of the id helper that
        builder depends on is the same finding one level down. The identity assertion
        is what makes the collapse real — re-exporting a wrapper of the same shape would
        still be two definitions to keep in step.
        """
        source = Path(given_media_buy.__file__).read_text()
        local_defs = [
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name.lstrip("_") == "pricing_option_id"
        ]
        assert local_defs == [], f"given_media_buy still defines its own pricing_option_id copy: {local_defs}"
        assert given_media_buy.pricing_option_id is _create_request.pricing_option_id

    def test_both_call_sites_format_the_id_the_same_way(self, seeded_ctx: dict[str, Any]):
        """The behaviour the collapse must preserve: ``{model}_{currency}_{fixed|auction}``.

        Pinned against a real ``PricingOption`` row so the collapse cannot quietly change
        the id format the seeded product is addressed by.
        """
        option = seeded_ctx["default_pricing_option"]
        expected = f"{option.pricing_model}_{option.currency.lower()}_{'fixed' if option.is_fixed else 'auction'}"

        assert _create_request.pricing_option_id(option) == expected
        built = _create_request.build_create_request_kwargs(dict(seeded_ctx), po_number="PO-4471")
        accumulated = given_media_buy._ensure_request_defaults(dict(seeded_ctx))
        assert built["packages"][0]["pricing_option_id"] == expected
        assert accumulated["packages"][0]["pricing_option_id"] == expected

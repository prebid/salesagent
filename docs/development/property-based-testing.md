# Property-Based Testing with Hypothesis

> **Status**: RFC prototype on `tests/hypothesis`. 8 property functions,
> ~1,210 generated cases per run, ~5.7s against a real Postgres container.
> Framed as a suggestion to the developer group, not a merge request.

## TL;DR

Property-based testing (Hypothesis) slots into our existing harness
cleanly. The prototype exercises five surfaces — boundary integration,
schema roundtrip, metamorphic equivalence, cross-transport differential,
and stateful tenant isolation — without changing production code.

The **most important lesson** from building this prototype is
methodological: **a property test is only as correct as the test author's
reading of the contract.** Our first cut of the boundary test asserted
that `_impl` *returns* a `CreateMediaBuyError` envelope on validation
failure. That looked reasonable — the return type annotation says
`-> CreateMediaBuyResult`, and one other site in the file uses the
pattern. It passed. We even "fixed" an `_impl` site to match it.

It was wrong. The actual contract — from the BDD Gherkin features, the
transport wrappers, `tests/unit/test_no_toolerror_in_impl.py`, CLAUDE.md
Pattern #5, and 83 `raise AdCPError` sites across `src/core/tools/` vs
5 legacy outliers — is:

> `_impl` functions **raise** `AdCPError` subclasses on validation
> failure. Transport wrappers catch and translate to transport-specific
> error structures.

The "bug" Hypothesis "found" was phantom — our property was the bug.
The shrinker dutifully minimized the wrong assertion to a clean
reproducer of the code doing exactly what it was supposed to do.

This document captures the lesson and the recipe for doing it right.

## How Hypothesis works (briefly)

Three pieces:

1. **Strategy** — a recipe for generating valid inputs of some shape
   (`st.integers()`, `st.text()`, `@composite` for compound types).
2. **Property** — an assertion that holds for *every* valid input,
   written as a normal `pytest` test decorated with `@given(...)`.
3. **Shrinker** — when an input fails the property, Hypothesis
   automatically minimizes it to the smallest reproducer, prints it,
   and replays the failing example first on the next run as a
   regression check.

Example:

```python
@given(amount=st.decimals(min_value=0.01, max_value=10_000_000.0),
       currency=st.sampled_from(["USD", "EUR", "GBP", "JPY"]))
def test_budget_shape_equivalence(amount, currency):
    """All three input shapes must produce the same (amount, currency)."""
    a1 = extract_budget_amount(amount, default_currency=currency)
    a2 = extract_budget_amount({"total": amount, "currency": currency}, "ZZZ")
    a3 = extract_budget_amount(Budget(total=amount, currency=currency), "ZZZ")
    assert a1 == a2 == a3 == (amount, currency)
```

That single property replaces what would otherwise be ~5 hand-picked
parametrize cases and explores boundaries we wouldn't think to write.

## The methodological lesson

Property tests codify the test author's claim about the contract.
**If you misread the contract, your property is wrong — and "all
examples pass" is false confidence.**

This failure mode is worse than example-test omission because property
tests feel rigorous (1,000 cases!) while actually asserting something
incorrect. Example tests have the same failure mode, but the rigor
signal is honest — no one says "my five examples prove correctness."

### The recipe that survives this

Anchor every property to a source that is **higher authority than the
code under test**, in roughly this priority order:

1. **Gherkin features** (`tests/bdd/features/`). If a scenario says
   "the operation should fail / error_code should be X," the property
   must match the same observable outcome. Transport mechanism
   (raise vs return) is a translation detail; the outcome is the
   contract.
2. **Structural guards** (`tests/unit/test_architecture_*.py`,
   `test_no_toolerror_in_impl.py`, etc.). They codify conventions
   that apply repo-wide.
3. **Documented patterns** (CLAUDE.md's 7 critical patterns, docs/
   development/patterns-reference.md).
4. **Transport wrappers** — the immediate consumer of `_impl` output.
   Whatever they assume is the live contract.
5. **Type annotations and docstrings** — useful hints, but not
   authoritative on their own. Especially suspect on older code.
6. **Same-file precedent** — weakest source. A single other site
   doing something may be an outlier, not a pattern.

Before writing a property, walk down the list. If priorities 1-4 are
silent and you're inferring from 5-6, **stop and ask**. That's the
signal that the contract isn't actually clear, and your property will
encode a guess.

## What's in the prototype

Six files under `tests/integration/property/`:

| File | Surface | Cost | Cases / run |
|---|---|---|---|
| `test_create_media_buy_property.py` | Boundary integration: two properties (valid→success, overflow→raises) | ~3s | 2 × 25 |
| `test_schema_roundtrip_property.py` | In-memory schema roundtrips (no DB) | <100ms | 6 × 100 |
| `test_budget_metamorphic_property.py` | Metamorphic equivalence: input shape doesn't change result | <100ms | 4 × 100 |
| `test_cross_transport_parity_property.py` | Transport differential: IMPL vs A2A dict-identical on success | ~2s | 25 |
| `test_tenant_isolation_sequence_property.py` | Stateful: 2-8 ops across 2 tenants, isolation holds after sequence | ~2s | 10 (up to 80 real creates) |

Plus one supporting harness class: `tests/harness/media_buy_create.py`
(`MediaBuyCreateEnv`) with three dispatch paths (`call_impl`,
`call_a2a_as_dict`, `call_mcp_as_dict`).

### Surface 1: Boundary integration — contract-anchored

```python
@given(payload=_payload_with(valid_budget_strategy))    # $100 .. $9,000
def test_valid_payload_returns_success_with_roundtrip(integration_db, payload):
    result = env.call_impl(req=CreateMediaBuyRequest(**payload))
    assert isinstance(result.response, CreateMediaBuySuccess)
    # python/JSON roundtrip equality, buyer_ref preservation

@given(payload=_payload_with(overflow_budget_strategy))  # $10,000.01 ..  $50,000
def test_inventory_overflow_raises_validation_error(integration_db, payload):
    with pytest.raises(AdCPValidationError) as exc_info:
        env.call_impl(req=CreateMediaBuyRequest(**payload))
    assert exc_info.value.details["error_code"] == "ADAPTER_VALIDATION_FAILED"
```

Two properties split by input class, each with a sharp assertion
anchored to the live contract (raise on validation failure). Catches
contract drift in either direction.

### Surface 2: Schema roundtrips

Universal property `Model.model_validate(m.model_dump()) == m` over
`Budget`, `Error`, `CreateMediaBuySuccess`. 100 cases each, <1ms per
case. Replaces ~20 parametrize blocks in the regression suite.

Catches Pattern #4 violations, Decimal/datetime drift, field exclusion
bugs.

### Surface 3: Metamorphic equivalence

Three input shapes to `extract_budget_amount` (float, dict, Budget
object) must produce identical `(amount, currency)`. Existing
`test_budget_format_compatibility.py` asserts this for ~5 hand-picked
values; Hypothesis generalizes to thousands.

Catches branch-specific divergence, float precision loss, default-currency
fallback bugs.

### Surface 4: Cross-transport differential

Same payload through IMPL and A2A must produce dict-identical responses
(modulo volatile keys: random ids, timestamps). Excludes MCP in v1
because the MCP auth chain drops `testing_context` — architectural gap
documented in the PR body.

Catches wrapper bugs, serialization asymmetry, header-handling quirks,
identity-translation differences.

### Surface 5: Stateful tenant isolation

Generates 2-8 random `create_media_buy` ops split across two tenants,
asserts after the sequence: isolation (no leakage) and completeness
(each view contains exactly its own creates).

Catches repository tenant-filter bugs, singleton/cache leakage between
calls, ContextVar bleed, uniqueness-check races.

## Suggested next penetration surfaces

Ranked by leverage:

### Tier 1 — high value, low-to-medium effort

1. **`hypothesis-jsonschema` against AdCP JSON schemas.** Spec-faithful
   generation for free; every spec revision updates the test surface
   automatically. This is the highest-leverage investment because it
   explicitly anchors strategies to the authoritative source
   (the schema).

2. **`RuleBasedStateMachine` for the media-buy lifecycle.** Rules:
   `create → update → add_creative → cancel → list`. Invariants
   checked at every transition: workflow state monotonicity, soft-
   deleted creatives invisible in `list_creatives` but visible in
   history, sum of package budgets after K updates equals latest total.
   Catches sequencing bugs single-call properties can't find.

3. **Negative-space properties.** Don't just assert "valid → success."
   Assert "this class of bad input → this *specific* error code with
   this *specific* error shape." The boundary test (surface 1) is an
   early example — extend to all validation categories:
   datetime-in-past, duplicate product_id, unsupported currency, etc.

### Tier 2 — strategy-quality boosters

4. **Push to the edges.** Replace `st.sampled_from(["USD","EUR"])`
   with `st.text(min_size=3, max_size=3)`. Let Hypothesis explore the
   full type-valid space; the shrinker minimizes on failure.

5. **Adversarial primitives.** Full Unicode in strings (including RTL,
   normalization-sensitive, control characters). DST-transition
   timestamps, leap seconds, timezone boundaries. Very long sequences.

6. **Mutation-based generation.** Start from a recorded valid payload,
   mutate minimally (drop a key, swap a type, inject an unknown
   nested field). Catches "malformed proxy" bugs that pure-synthesis
   strategies don't generate.

### Tier 3 — architecturally significant

7. **Concurrency / race injection.** K parallel `create_media_buy`
   calls with the same `buyer_ref`. Does the uniqueness check hold
   under contention?

8. **Failure injection.** Adapter raises mid-call. DB times out
   between workflow-step write and media-buy write. What partial
   state is visible?

9. **Differential against a previous version.** Pickle responses from
   `main` for a fixed input set; on PR branches, assert parity for
   the same inputs. Catches accidental behavior changes no one
   explicitly tested.

## Cost & CI integration

Hypothesis profiles (`hypothesis.settings.register_profile(...)`) allow
the same test to run at different budgets per environment without code
changes:

| Suite | Profile | When |
|---|---|---|
| In-memory roundtrips | `default` (100 examples) | Every PR, in `tox -e unit` |
| Integration boundary | `fast` (10 examples) | Every PR, in `tox -e integration` |
| Integration boundary | `thorough` (200 examples) | Nightly |
| Stateful machines | `nightly` (50 examples) | Nightly only |

## When Hypothesis is the wrong tool

Skip these cases:

- **Structural guards** (`tests/unit/test_architecture_*.py`). They
  assert about *your code*, not your *data*. No input space to explore.
- **Adapter call-capture tests** (`test_gam_*`). Mock SOAP returns; the
  input space is too constrained to be interesting.
- **Mock-heavy behavioral tests**. The mocks pin the answer; there's
  nothing to explore.
- **BDD scenarios themselves**. Scenario outlines already enumerate; the
  multi-transport matrix already multiplies. Hypothesis belongs *inside*
  step implementations, not in the Gherkin layer.

## Recipe for writing a new property test

1. **Identify the contract.** Walk the priority list above (Gherkin →
   guards → patterns → wrappers → annotations → precedent). If you end
   up on 5-6, stop and ask before proceeding.

2. **State the property as one sentence** *at the observable contract
   level*, not at the implementation level. "For valid inputs, _impl
   raises X on class Y and returns Z on class W" is a contract
   statement. "`response.errors` has length > 0" is an implementation
   statement.

3. **Build a strategy** for the input. Start narrow (only what you
   need). Use `@composite` to thread random draws through compound
   shapes.

4. **Assert sharply.** Not "success or error" (that's a type check);
   assert the specific shape, code, and message the contract requires.

5. **Run with `--hypothesis-show-statistics`** to see example count and
   runtime distribution. Tune `max_examples` and `deadline` to fit CI
   budget.

6. **When it fails, read the falsifying example first.** Hypothesis
   prints the minimal reproducer. Then decide: is the *code* wrong, or
   is my *property* wrong? Both are possible; the investigation must
   go back to the contract source, not the code.

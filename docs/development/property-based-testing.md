# Property-Based Testing with Hypothesis

> **Status**: Prototype on `tests/hypothesis` branch. ~1,040 generated test cases run in ~3.4s.
> **Branch**: [`tests/hypothesis`](.) | **Tracking issue**: [`salesagent-81u4`](#bugs-found)

## TL;DR for the developers' group

We added property-based testing to the existing harness. Five test functions across three files generate ~1,040 cases per run and have already pinned two contract bugs in `_create_media_buy_impl` — one of them shrunk to the literal off-by-one boundary at **budget = $10,000.01**.

The pitch: example tests prove "the system works for the inputs we thought of." Property tests prove "the system upholds invariant X across the entire input space." The same harness, the same factories, the same assertions — but generated input.

## Why this fits our codebase

We already have unusually rich invariants (not just "it works"). Walk through `tests/integration/test_get_products_filter_semantics.py` and you'll see explicit lattice subsets, partition disjointness, and sort stability — all asserted against hand-picked examples. We also have a heavy serialization surface: 22+ regression files named after escaped schema bugs (`test_create_media_buy_roundtrip.py`, `test_format_id_subscript_regression.py`, `test_null_field_exclusion.py`, ...). Both shapes are exactly what Hypothesis is for.

The harness made the integration story painless: `IntegrationEnv` already owns mock setup, factory session binding, identity, and transport dispatch. A Hypothesis strategy plugs in at the boundary (request payload) and everything downstream runs unchanged — real Postgres, real validators, real adapter, real response.

## What's in the prototype

Three files, all under `tests/integration/property/`:

| File | Surface | Cost | Examples / run |
|---|---|---|---|
| `test_create_media_buy_property.py` | Boundary integration: real DB + adapter + serialization | ~3s for 40 examples | 40 |
| `test_adapter_validation_returns_error.py` | Deterministic regression for the bug Hypothesis found | <100ms | 1 |
| `test_schema_roundtrip_property.py` | In-memory schema roundtrips (no DB) | <100ms for 600 cases | 6 × 100 |
| `test_budget_metamorphic_property.py` | Metamorphic equivalence: input shape doesn't change result | <100ms for 400 cases | 4 × 100 |

Plus one supporting harness class: `tests/harness/media_buy_create.py` (`MediaBuyCreateEnv`).

## Bugs found

### `salesagent-81u4` — `_create_media_buy_impl` raised validation errors instead of returning `CreateMediaBuyError`

**Discovered by** `test_create_media_buy_property.py` on the very first run. Hypothesis shrunk the failing input to:

```python
budget = 10000.01    # $10 CPM × 1,000,001 impressions
                     # = exceeds mock adapter inventory cap by 1
```

The function's contract says it returns `CreateMediaBuyResult(response: CreateMediaBuySuccess | CreateMediaBuyError, ...)`. At line 2844 (and three other sites — see ticket), it instead `raise`d `AdCPValidationError`. Callers expecting the contract crashed.

**Fix**: convert `raise AdCPValidationError(...)` → `return CreateMediaBuyResult(response=CreateMediaBuyError(errors=[Error(...)]), status=failed)`. One site landed in this PR; three remain (tracked in the ticket).

The fix is verified two ways:
- Deterministic regression (`test_adapter_validation_returns_error.py`) — pins the exact `$10,000.01` case.
- Generalized property — now asserts "response is `CreateMediaBuySuccess` *or* `CreateMediaBuyError` with structured errors[]". Hypothesis runs both sides of the boundary in 40 examples and never crashes.

### Ancillary findings (not bugs, but visible couplings)

- `tenant_id` containing underscores produces `subdomain` containing underscores, which propagates to `publisher_domain` and trips a DNS regex at hydration time. No existing test asserts this coupling — Hypothesis exposed it. Worth either tightening `TenantFactory` defaults or relaxing the regex.
- Mock adapter inventory cap (1,000,000 impressions) is undocumented and only visible by triggering it.

## The three surfaces explained

### 1. Boundary integration (`test_create_media_buy_property.py`)

```python
@given(payload=create_media_buy_payload())
def test_create_media_buy_response_roundtrips(integration_db, payload):
    with MediaBuyCreateEnv(...) as env:
        _setup_catalog(env)
        result = env.call_impl(req=CreateMediaBuyRequest(**payload))

        # Disjunction property -- the function is total.
        assert isinstance(result.response, (CreateMediaBuySuccess, CreateMediaBuyError))

        if isinstance(result.response, CreateMediaBuyError):
            assert result.response.errors
            return

        # Success-only invariants
        assert _python_roundtrip_equal(result.response)
        assert _json_roundtrip_equal(result.response)
        assert result.response.buyer_ref == payload["buyer_ref"]
```

**What it catches**: contract violations (raise vs return), nested `model_dump` propagation, `apply_testing_hooks`-style response-shape drift, buyer_ref preservation across the entire pipeline. **Cost**: ~75ms per example with real Postgres.

### 2. Schema roundtrips (`test_schema_roundtrip_property.py`)

```python
@INMEM
@given(b=budget_strategy())
def test_budget_python_roundtrip(b: Budget) -> None:
    dumped = b.model_dump()
    rebuilt = Budget.model_validate(dumped)
    assert rebuilt.model_dump() == dumped
```

**Property (universal)**: `for any valid m: Model, Model.model_validate(m.model_dump()) == m`.

**What it catches**: Pattern #4 violations, `Decimal`/`datetime` JSON drift, field-exclusion bugs, `extra="forbid"` tripping on legitimate roundtrip output. One property per model replaces ~10 hand-written example tests.

**Cost**: <1ms per example. 600 cases run in ~50ms total.

### 3. Metamorphic equivalence (`test_budget_metamorphic_property.py`)

```python
@given(amount=money_strategy, currency=currency_strategy)
def test_budget_shape_equivalence(amount, currency):
    """Three input shapes must produce identical output."""
    amt_f, cur_f = extract_budget_amount(amount, default_currency=currency)
    amt_d, cur_d = extract_budget_amount({"total": amount, "currency": currency}, default_currency="ZZZ")
    amt_o, cur_o = extract_budget_amount(Budget(total=amount, currency=currency), default_currency="ZZZ")
    assert amt_f == amt_d == amt_o == amount
    assert cur_f == cur_d == cur_o == currency
```

**Property (metamorphic)**: an input transformation (which shape we use) is irrelevant to the output. The existing `test_budget_format_compatibility.py` asserts this for ~5 hand-picked values; Hypothesis generalizes to thousands of `(amount, currency)` pairs and adds boundary exploration (very small, very large, currency case sensitivity).

**What it catches**: branch-specific divergence, float precision loss in dict→Budget conversion, default-currency fallback bugs.

## Suggested next penetration surfaces

Concrete files we could add next, ranked by leverage:

### High-value, low-effort

1. **Forward-compat acceptance** at the HTTP boundary — recursive strategy adds arbitrary extra keys to a payload at random nesting depths, asserts response preserves all known fields. Replaces the 4 hand-enumerated parametrize blocks in `tests/harness/test_forward_compat_acceptance.py:137-296`.

2. **Filter-AND lattice** for `get_products` — generate filter pairs `(f1, f2)`, assert `result(f1 ∧ f2) ⊆ result(f1) ∩ result(f2)`. Generalizes the hand-picked examples in `test_get_products_filter_semantics.py:57-98`.

3. **Aggregation = sum of parts** for delivery — generate `K` random `DeliveryMetrics`, assert `aggregate(list).<field> == sum(m.<field> for m in list)` for every numeric field. Currently asserted in 4 places with fixed fixtures (`test_delivery_poll_behavioral.py`, `test_delivery.py`, two BDD steps).

4. **Tenant isolation** — generate operations across two tenants, assert reads in tenant A never surface state from tenant B. Replaces N implicit assumptions.

### High-value, medium-effort

5. **`RuleBasedStateMachine`** for media-buy lifecycle — rules: `create_media_buy`, `update_media_buy`, `add_creative`, `cancel`. Invariants: workflow state never regresses, soft-deleted creatives don't appear in `list_creatives` but do in history queries, sum of package budgets after K random updates equals the latest computed total. Catches *sequencing* bugs that single-call property tests miss entirely.

6. **Multi-transport dispatch** — same property, parametrized over `[Transport.IMPL, Transport.MCP, Transport.A2A, Transport.REST]`. Pins behavior parity across all four wrappers.

### High-value, higher-effort

7. **Stateful targeting overlay merge** — generate two `Targeting` objects, apply overlays in either order, assert commutativity (or document non-commutativity explicitly). Currently asserted nowhere.

8. **Polyfactory-driven AdCP request fuzz** — auto-generate strategies from the AdCP JSON schemas via `hypothesis-jsonschema`, fire through MCP/A2A. Effectively a structured fuzzer with semantic oracles.

## Cost & CI integration

Strategy for fitting this into our existing pipeline:

| Suite | Profile | When |
|---|---|---|
| In-memory roundtrips | `max_examples=100` (default) | Every PR, in `tox -e unit` |
| Integration boundary | `max_examples=20` (PR profile) | Every PR, in `tox -e integration` |
| Integration boundary | `max_examples=200` (nightly profile) | Nightly, separate workflow |
| Stateful machines | `max_examples=50` (nightly only) | Nightly |

Hypothesis profiles (`hypothesis.settings.register_profile(...)`) let us swap budgets per environment without changing test code. The shrinker means a failing nightly run produces a pinned PR-profile reproducer.

## When Hypothesis is the wrong tool

Skip these cases:

- **Structural guards** (`tests/unit/test_architecture_*.py`). They assert about *your code*, not your *data*. No input space to explore.
- **Adapter call-capture tests** (`test_gam_*`). Mock SOAP returns; the input space is too constrained to be interesting.
- **Mock-heavy behavioral tests**. The mocks pin the answer; there's nothing to explore.
- **BDD scenarios themselves**. Scenario outlines already enumerate; the multi-transport matrix already multiplies. Hypothesis belongs *inside* step implementations, not in the Gherkin layer.

## How to write a new property test

1. **Pick an invariant** that's currently asserted with hand-picked examples. The clearest sources:
   - "These three shapes must produce the same result" → metamorphic
   - "This response model_dumps to a dict that reconstructs to the same model" → roundtrip
   - "This filter operation is a subset of intersection of inputs" → algebraic
   - "After K random operations, this aggregate property holds" → stateful

2. **Build a strategy** for the input. Start narrow (only what you need), expand later. Use `@composite` to thread random draws through complex shapes.

3. **Assert the property** against the generated input. If a `with` block is involved (like the harness), run setup once per example — the cost is what it is.

4. **Run with `--hypothesis-show-statistics`** to see example count and runtime distribution. Tune `max_examples` and `deadline` to fit your CI budget.

5. **When it fails, read the falsifying example**. Hypothesis prints the minimal reproducer. Often the input alone tells you the bug.

## References

- File the prototype lives in: `tests/integration/property/`
- Harness class added: `tests/harness/media_buy_create.py`
- Bug fix: `src/core/tools/media_buy_create.py:2844-2862`
- Bug ticket: `salesagent-81u4`
- Hypothesis docs: <https://hypothesis.readthedocs.io>

# buyer_ref reconciliation (pinned spec `04f59d2d5`)

Source of truth for the `buyer_ref` cleanup. Beads reference this; do not put the
decisions only in beads.

## Ground truth — pinned message shapes (`v3.1-04f59d2d5`)

`buyer_ref` / `buyer_refs` is **not a field on any message** at the pin
(create/update media-buy request+response, package, package-request,
package-update, get-media-buy-delivery-request). `context` is an opaque freeform
object (`additionalProperties: true`) on every message.

Required fields (relevant): create-media-buy-request → `account, brand, end_time,
idempotency_key, start_time`; package-request → `budget, pricing_option_id,
product_id`; package-update → `package_id`; update-media-buy-request →
`account, idempotency_key, media_buy_id`.

Full field lists were extracted from `~/projects/adcp` via
`git show 04f59d2d5:static/schemas/source/media-buy/*.json` (and `core/package.json`,
`core/context.json`).

## The rule (owner-directed)

- **No top-level `buyer_ref` / `buyer_refs`** in any scenario.
- Where it was an **identity/resolution** mechanism → use `media_buy_id` (media
  buys) / `package_id` (package updates) / `product_id` (package create), or
  **drop** the scenario if that partition is already covered by the id-based sibling.
- Where it was **correlation echo** → **drop** (no replacement).
- **Do NOT introduce `context.buyer_ref`** — not in the pinned schema.
- **Keep:** `buyer_campaign_ref` (a real field); "response should NOT contain
  `buyer_ref`" absence assertions.
- **Pin wart:** `error-code.json` at the pin says "verify package_id or buyer_ref".
  Our production generates its own suggestion strings, so emit `package_id` /
  `product_id, budget, pricing_option_id` (no `buyer_ref`) and assert that — we do
  not have to echo the spec's doc wart.

## Per-UC change list (TARGET = `~/projects/adcp-req/tests/features/`)

| UC | Change |
|----|--------|
| BR-UC-002 create | Drop `\| buyer_ref \| … \|` request rows + "response includes buyer_ref" assertion; edit "track via media_buy_id and buyer_ref" → "via media_buy_id". |
| BR-UC-003 update | Keep "response should NOT contain buyer_ref"; "without package_id or buyer_ref" → "without package_id"; drop `buyer_ref_only`/`both_provided` resolution partitions (keep `media_buy_id`). |
| BR-UC-004 delivery | "media buy with buyer_ref" → identify by `media_buy_id`; drop `buyer_refs only`/`both provided` partitions; "without media_buy_ids or buyer_refs" → "without media_buy_ids". |
| BR-UC-009 perf-index | Drop the `buyer_ref` resolution scenario + `buyer_ref_legacy` partitions (already `@abstract-rejection`); retire **BR-RULE-021** in `docs/test-obligations/business-rules.md`. |
| BR-UC-019 query | "include buyer_ref and buyer_campaign_ref" → drop `buyer_ref`, keep `buyer_campaign_ref`. |
| BR-UC-022 creative-delivery | `buyer_ref` / `media_buy_buyer_refs` → `media_buy_id` / `media_buy_ids`, or drop. |
| BR-UC-026 package | Keep deprecation comment; drop `\| buyer_ref \| pkg-* \|` columns + "package should contain buyer_ref"; "neither package_id nor buyer_ref" → "without package_id" + suggestion `package_id`; required-fields suggestion `buyer_ref, product_id, budget, pricing_option_id` → `product_id, budget, pricing_option_id`. |
| BR-COMPAT-001 | No change (`buyer_campaign_ref` is real). |

## salesagent step / production cleanup (paired with each UC)

Delete the masking no-op / buyer_ref steps so regenerated (buyer_ref-free) scenarios bind correctly:
- `uc003_update_media_buy.py:409-411` (`given_buyer_ref_resolves`), `:1299-1300`
- `uc004_delivery.py:163-167`, `:706-708`, `:2751-2757`
- `uc019_query_media_buys.py:250, 1117`
- `uc026_package_media_buy.py:490-493, 689-692, 715-716, 721`
- `then_media_buy.py:749` (drop `buyer_ref` from success-assert helper)
- `uc003_ext_error_scenarios.py:229-231` → key on `package_id`
- Drop `buyer_ref` from any package datatable parser that accepts it.

Production suggestion text (`src/`): remove `buyer_ref` from the package-required
and package-update-missing-identifier error suggestions.

## How to apply — REVERSED flow (salesagent-first, then copy up)

**Key fact:** the lockfile-cached merge (`adcp-req/scripts/run_phase5_merge.sh`)
exists *only* to preserve salesagent's pre-existing step-def bindings while
absorbing upstream changes. TARGET (adcp-req) and LEGACY (salesagent) are
otherwise the **same scenarios**. So for a deliberate, mechanical change like this
we do **not** edit upstream and re-run the LLM merge (that would cache-miss every
bound `buyer_ref` scenario → a semantic-merge call each). Instead we **author the
change on the salesagent side, verify bindings by running the tests, then copy the
result up to adcp-req so TARGET == LEGACY** — the merge then becomes a no-op and
the result is correct by construction.

Per UC:

1. **Edit salesagent feature files** `tests/bdd/features/BR-UC-*.feature` directly —
   remove top-level `buyer_ref` (migrate to `media_buy_id`/`package_id`/`product_id`
   or drop), per the table above.
2. **Paired salesagent edits**: delete the `buyer_ref`/no-op step defs (list below),
   the package datatable parser entries, and the production error-suggestion text.
3. **Run the wire BDD for that UC locally** — confirm edited scenarios bind and pass
   (or honest xfail), no new failures. Fast, deterministic, **no LLM**. This is the
   verification that the merge would otherwise have protected.
4. **Copy the edited feature file(s) up** to `adcp-req/tests/features/BR-UC-*.feature`
   so TARGET == the new salesagent LEGACY (semantically + byte identical).
5. **Reconcile the lockfile** in `adcp-req/phase5-lockfile/UC-*.yaml`: `delete`
   entries for dropped scenarios (+ prune traceability); then run
   `run_phase5_merge.sh --uc UC-0XX` (or `compile_bdd.py --merge --verify`) and
   confirm it reports **zero** new NEEDS-SEMANTIC-MERGE (TARGET≡LEGACY ⇒ trivial)
   and a clean render. Refresh any stale lockfile entries to the identical decision.
6. **Commit** salesagent (features + steps + suggestions) and adcp-req
   (features + lockfile) separately.

Why this is safe: step 3 verifies bindings directly (the only thing the merge
protects), and step 5's `--verify` proves TARGET≡LEGACY so no decision is being
guessed by an LLM. Pre-req: salesagent tree clean before step 5's merge check.

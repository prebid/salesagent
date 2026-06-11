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
| BR-UC-009 perf-index | Drop the `buyer_ref` resolution scenario + `buyer_ref_legacy` partitions (already `@abstract-rejection`); retire **BR-RULE-021** in `docs/requirements/business-rules/BR-RULE-021.md`. |
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

## How to apply (the lockfile-merge workflow — NOT a plain overwrite)

The salesagent features are produced by a 2-layer, lockfile-cached merge
(`adcp-req/scripts/run_phase5_merge.sh`, epic `rzgb4`):
`compile_bdd.py --merge` (TARGET ⨝ LEGACY, hash-keyed) → lockfile hit =
RESOLVED-FROM-LOCKFILE; miss + WIRED+bound = NEEDS-SEMANTIC-MERGE
(`drive_merges.py`: LLM merge + verifier, **upserts** `phase5-lockfile/UC-*.yaml`);
unwired/unbound = TARGET-WINS → `render_features.py` → no auto-commit.

The cache key includes `target_sha256` **and** `binding_index_sha256`, so **both**
editing a TARGET scenario **and** changing its step defs invalidate the lockfile
entry → re-merge. Therefore per UC:

1. Edit the TARGET scenarios in `adcp-req/tests/features/BR-UC-*.feature` (per the table).
2. Make the paired salesagent step-def / parser / production-suggestion edits.
3. `bash ~/projects/adcp-req/scripts/run_phase5_merge.sh --uc UC-0XX` (ADCP_SPEC_PIN=04f59d2d5).
4. Review the re-merged lockfile entries (`phase5-lockfile/UC-0XX.yaml`) — confirm
   `merged_gherkin` is buyer_ref-free and verifier verdict CORRECT; review
   `MERGE-UNRESOLVED-REPORT.md`. Dropped scenarios: confirm their lockfile +
   traceability entries are pruned.
5. Run the wire BDD for that UC; confirm no new failures.
6. Commit **adcp-req** (TARGET edits + updated `phase5-lockfile/UC-0XX.yaml`) and
   **salesagent** (regenerated features + step/production edits) separately.

Pre-req: salesagent working tree clean before the merge (the orchestrator checks).

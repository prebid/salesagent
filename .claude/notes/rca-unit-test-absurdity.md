# RCA: Unit-test absurdity audit (tests/unit)

Repo: `.`, branch `feature/spec-gaps-1210`, 2026-08-19.
Method: Pass 1 names-only (AST), Pass 2 body verification of a sample per flagged category,
Pass 3 delete-vs-migrate split, Pass 4 the inverse. No test file was modified.

## Population (measured, not assumed)

AST scan (`ast.FunctionDef`/`AsyncFunctionDef` named `test_*`, including methods inside
`Test*` classes, `__pycache__` excluded):

- **545 files scanned, 5,594 test functions, 0 parse errors.**
- The brief's figure was 5,245. I could not reconcile the 349 difference; my count is an
  AST count of *definitions*, which is what the brief specified. It is not a pytest
  collection count (parametrize expansion, collection-time skips, and `conftest`-level
  deselection would all move that number). Everything below uses 5,594.

Distribution: `tests/unit` 5,355 · `adapters/broadstreet` 159 · `adapters` 45 · `admin` 25 ·
`adapters/utils` 10. Largest files: `test_creative.py` 170, `test_media_buy.py` 134,
`test_product_schema_obligations.py` 101, `test_adcp_contract.py` 86.

Raw name list: `/private/tmp/claude-501/.../scratchpad/all_test_names.txt` (scratch),
classification: `classified2.json`.

---

# Section 1 — Category counts from names alone

Classifier is an ordered rule set over the function name, plus one file-path rule (any test
living in `test_architecture_*.py` / `test_guards_*.py` / the runner-config files is a
guard). Ordering matters: first rule wins.

| # | Category | Count | % of 5,594 |
|---|----------|------:|-----------:|
| E1 | Structural guard scanning `src/` | 753 | 13.5% |
| E2 | Meta-test of a guard's own detector (tests the test infra) | 339 | 6.1% |
| B | Asserts a field/constant/symbol merely EXISTS or has a literal value | 254 | 4.5% |
| C | Serialization: round-trip, `model_dump`, null-omission, "is serializable" | 156 | 2.8% |
| D | Trivial accessor / pass-through / no-op | 125 | 2.2% |
| A | Third-party library behavior (pydantic, enum, datetime, stdlib) | 60 | 1.1% |
| I | Schema-contract / `extends_library` / `adcp_compliance` | 43 | 0.8% |
| F | Regression pin for one named failure mode | 37 | 0.7% |
| H | Not matched — presumed genuine behavioral | 3,827 | 68.4% |
| G | *(overlay)* duplicate names: 126 distinct names on 296 functions; 50 same-file collisions | — | — |

**Flagged by name alone: 1,767 (31.6%).** Excluding the two guard families: 675 (12.1%).

### E1 — structural guard scanning `src/` (753)
`test_no_new_get_db_session_in_impl` · `test_no_import_time_filesystem_io_anywhere_in_the_tree` ·
`test_no_broad_except_relabels_as_validation_error` · `test_no_unimported_class_usage_in_src` ·
`test_mcp_wrappers_pass_all_impl_params` · `test_no_service_imports_in_app_lifespan` ·
`test_no_request_field_diverges_across_transports` · `test_swept_admin_files_use_safe_error_message` ·
`test_value_error_sites_within_caps` · `test_six_suite_list_is_single_sourced` ·
`test_every_adapter_raised_error_has_a_raise_site_test` · `test_create_media_buy_has_account_param`

### E2 — meta-test of a guard's own detector (339)
`test_guard_detects_duplicate_pydantic_validator` · `test_detector_catches_known_bad_snippets` ·
`test_negative_typed_raise_passes` · `test_positive_direct_construction` ·
`test_guard_passes_when_account_present` · `test_flags_prebound_container` ·
`test_advisory_detector_passes_known_good` · `test_allowlist_diff_guard_catches_direct_set_diff` ·
`test_regex_slip_getattr_other_attribute_is_not_flagged` · `test_ignores_logging_inside_blanket_handler` ·
`test_would_be_missed_case_handler_enumerating_all_but_one_kwarg` · `test_flags_the_original_disease_form`

### B — existence / literal-value (254)
`test_middleware_class_exists` · `test_impl_function_exists` · `test_accounts_field_exists` ·
`test_product_card_detailed_importable_from_schemas` · `test_base_adapter_has_default` ·
`test_response_has_required_fields` · `test_auth_optional_tools_defined` ·
`test_update_media_buy_package_creatives_field_exists` · `test_auth_context_exists` ·
`test_handler_uses_constant` · `test_agent_card_has_required_fields` · `test_response_has_jsonrpc_field`

### C — serialization (156)
`test_sync_creatives_response_json_serializable` · `test_json_dumps_model_dump` ·
`test_budget_object_serialization` · `test_to_dict` · `test_collection_list_survives_json_roundtrip` ·
`test_to_dict_includes_overridden_recovery` · `test_to_dict_preserves_raw_error_code` ·
`test_format_ids_list_serializes_to_json` · `test_roundtrip_reconstruct` ·
`test_model_dump_excludes_none` · `test_delivery_response_json_serializable` · `test_roundtrip_from_dict`

### D — trivial accessor / pass-through (125)
`test_a2a_wrapper_passthrough_dict_unchanged` · `test_to_brand_reference_dict_form_unchanged` ·
`test_dict_access_works` · `test_empty_dict_returns_none` · `test_none_returns_none` ·
`test_no_pin_requested_is_noop` · `test_upgrade_format_id_object_passthrough` ·
`test_video_content_passthrough` · `test_timestamp_passthrough` · `test_fold_position_passthrough` ·
`test_empty_registry_is_a_noop` · `test_all_known_formats_pass_through_unchanged`

### A — third-party library behavior (60)
`test_auth_context_is_frozen` · `test_schema_classes_are_pydantic` · `test_headers_not_mutatable` ·
`test_assign_creative_with_timezone_aware_overrides` · `test_list_id_missing_raises_validation_error` ·
`test_none_raises_validation_error` · `test_brand_manifest_field_raises_validation_error` ·
`test_create_workflow_step_accepts_pydantic_model` · `test_channels_coerces_strings_to_enum` ·
`test_collection_list_dict_form_coerces` · `test_url_normalization_handled_by_pydantic` ·
`test_enum_member_returns_value`

### I — schema contract (43)
`test_list_accounts_response_extends_library` · `test_extends_library` ·
`test_get_media_buy_delivery_request_extends_library` · `test_targeting_adcp_compliance` ·
`test_creative_adcp_compliance` · `test_create_media_buy_response_adcp_compliance` ·
`test_list_creatives_request_adcp_compliance` · `test_sync_creatives_response_adcp_compliance` ·
`test_error_response_schema_compliance` · `test_property_adcp_compliance` ·
`test_creative_policy_adcp_compliance` · `test_format_schema_compliance`

### F — regression pin (37)
`test_tool_context_creation_does_not_fail` · `test_jump_to_event_only_does_not_raise` ·
`test_no_get_current_auth_context_in_module` · `test_no_set_current_tenant_in_resolve_identity` ·
`test_backward_compat_enhanced_still_works` · `test_legacy_format_still_works` ·
`test_stale_variable_bug_demonstration` · `test_url_string_still_accepted` ·
`test_no_auth_context_var_in_module` · `test_no_principal_in_context` ·
`test_creative_policy_without_provenance_required_backward_compat` · `test_no_context_in_request`

### G — duplicate names (296 functions across 126 names)
`test_known_violations_not_stale` ×9 · `test_allowlist_entries_still_exist` ×7 ·
`test_allowlist_entries_still_violate` ×6 · `test_construction` ×5 · `test_scan_scope_is_pinned` ×4 ·
`test_field_names` ×4 · `test_extends_library` ×4 · `test_to_dict` ×4 · `test_type_error_propagates` ×4 ·
`test_internal_fields_excluded` ×2 (same file, `test_response_shapes.py:207` and `:748`) ·
`test_success_true_when_no_errors` ×3 · `test_detector_catches_known_bad_snippets` ×3

---

# Section 2 — Verification (bodies read) and confirmation rates

CONFIRMED = the test has no business being a unit test as written (either it tests something
that isn't ours, or it restates a declaration, or its assertion is reachable from outside).
REFUTED = it exercises real branching logic of ours at the right level.

| Category | Sampled | CONFIRMED | Rate | Extrapolated absurd |
|----------|--------:|----------:|-----:|--------------------:|
| B existence | 18 | 14 | 78% | 198 |
| A library | 14 | 9 | 64% | 39 |
| C serialization | 15 | 8 | 53% | 83 |
| I schema contract | 6 | 3 | 50% | 22 |
| G duplicates (non-guard) | 8 | 3 | 38% | — (overlaps others) |
| **H "genuine"** | **43** | **15** | **34.9%** | **1,336** |
| D trivial | 14 | 4 | 29% | 36 |
| F regression pin | 7 | 2 | 29% | 11 |
| E1 guard scan | 12 | 0 | 0% | 0 |
| E2 guard meta | 12 | 0 | 0% | 0 |

### What CONFIRMED looks like (representative, with the reason)

- `tests/unit/test_new_product_filters.py:136` `test_channels_filter_with_adapter_defaults` —
  builds a mock product, **never uses it**, then asserts `{"display"} & gam_defaults` is
  truthy. It tests Python set intersection. The filter under test is never called.
- `tests/unit/test_budget_migration_integration.py:146`
  `test_mock_adapter_budget_validation_with_budget_object` — the test **reimplements** the
  validation inline (`is_invalid = budget_amount <= 0`) and asserts its own arithmetic.
  Production validation is never invoked.
- `tests/unit/test_adcp_contract.py:1721` `test_create_media_buy_response_adcp_compliance` —
  contains `required_fields = []` followed by `for field in required_fields:` — the assertion
  loop has **zero iterations**. It is a vacuous pass.
- `tests/unit/test_formatid_media_package.py:152` `test_format_ids_list_serializes_to_json` —
  copies the production serialization expression into the test, then asserts `json.dumps`
  succeeds. Tests stdlib.
- `tests/unit/test_a2a_function_call_validation.py:200`
  `test_core_function_can_be_called_with_mock_context` — its own docstring says
  *"We're not testing the business logic, just that the function can be called."*
- `tests/unit/test_sync_creatives_async_fix.py:49` `test_multiple_sequential_calls` — calls the
  same helper three times with identical input and asserts the identical output three times.
- `tests/unit/test_adcp_exceptions.py:368` `test_to_dict_includes_overridden_recovery` — passes
  `recovery="terminal"` into the constructor, asserts `to_dict()["recovery"] == "terminal"`.
- `tests/unit/test_adcp_25_creative_management.py:143`
  `test_update_media_buy_package_creatives_field_exists` — asserts fields exist on
  **`adcp.types.PackageUpdate`**, a third-party model. Tests the SDK.
- `tests/unit/test_account_schemas.py:20` `test_list_accounts_response_extends_library` —
  `assert issubclass(X, LibraryX)`. Already enforced globally by
  `tests/unit/test_architecture_schema_inheritance.py`.
- `tests/unit/test_auth_context.py:43` `test_auth_context_is_frozen` — asserts a frozen
  dataclass raises `FrozenInstanceError`. Tests `dataclasses`.
- `tests/unit/test_creative_status_serialization.py:88` `test_creative_status_string_passthrough`
  — docstring: *"Verify Pydantic coerced string to enum internally."*
- `tests/unit/test_collection_list_targeting.py:180` `test_additional_properties_forbid_matches_spec`
  — asserts the third-party spec JSON contains `additionalProperties: false` and that
  `model_config["extra"] == "forbid"`. A config restatement.

### Genuine refutations — the willingness to be wrong mattered

- `test_detects_port_6543_in_url` (`test_pgbouncer_detection.py:192`) was routed to E2 by its
  `test_detects_*` name; it actually tests `src/core/database/database_session.py`. **Name-only
  classification is genuinely lossy — this is the failure mode of Pass 1.**
- `test_mock_adapter_rejects_none_tenant_id`, `test_empty_string_rejected` (SSRF validator),
  `test_provenance_as_pydantic_model`, `test_context_as_pydantic_model` all look like
  pydantic tests by name and are real branch coverage of our functions.
- `test_to_dict_includes_recovery_for_all_subclasses` (`test_error_boundary_translation.py:812`)
  is a 9-subclass mapping table — real logic despite a "serialization" name.
- Most `_returns_none` / `_returns_empty` names (my D pattern) mark a real guard branch —
  D had the **lowest** confirmation rate (29%), i.e. my name heuristic was worst here.

### The H bucket is where the mass is

H was sampled three times (14 + 15 + 14 distinct tests, n=43) precisely because it dominates
the arithmetic. 15/43 = **34.9% absurd**, 95% CI **20.6%–49.2%** → **788–1,883** absurd tests
hiding in the bucket my name classifier called "genuine". Point estimate **1,336**.

**This is the headline correction to a names-only pass: names under-detect by roughly 4×.**
Names flagged 675 non-guard tests; bodies say the true non-guard figure is ~1,725.

### The two guard families: measured, and judged separately

E1 (753) and E2 (339) are formally "tests of the test infrastructure" — the brief's category.
I read 12 of each and confirmed **zero** as should-not-exist:

- E1 scans production source for architecture violations (`no raw select`, `no import-time FS
  I/O`, `wrappers forward all params`). This behavior is **not observable from any transport** —
  BDD cannot grade "no `get_db_session()` in `_impl`". They are policy, mandated by
  `CLAUDE.md`, and they are the only mechanism keeping the allowlists ratcheting down.
- E2 self-tests the AST detectors. A detector whose regex silently stops matching passes
  forever; the meta-test is the only thing that makes the guard non-vacuous. Removing E2
  makes E1 untrustworthy.

I am reporting them, but I am **not** counting them in the absurd total. If the owner counts
them anyway, add 339 (E2 only) or 1,092 (both).

**Caveat on the E1/E2 split:** 4 of the 12 tests I sampled from E1 were actually detector
meta-tests my regex missed (e.g. `test_inline_subscript_is_flagged`,
`test_derived_expression_passes`). The true split is closer to E1 ≈ 630 / E2 ≈ 460. The
**combined** 1,092 is solid; the split between them is ±130.

---

# Section 3 — Delete vs. migrate

Two different claims with two different remedies, applied to the 1,725 confirmed-absurd:

| Remedy | Count | What it is |
|--------|------:|-----------|
| **(a) Delete — should not exist at all** | **~1,493** | tests a library, a constant, a declaration, or the test's own inlined copy of production |
| **(b) Migrate — our behavior, wrong level** | **~232** | the same assertion is or should be graded by a BDD scenario through a transport |

Derived per-category from the sampled dispositions:
A 39/0 · B 170/28 · C 62/21 · D 27/9 · F 11/0 · I 22/0 · H-residual 1,162/174.

### The larger migration debt is *outside* the absurd set

Applying the owner's own criterion ("a unit test is justified only when the behavior cannot be
verified from outside") to the tests I **refuted** as genuine: of 28 genuine H tests read, 6
(21%) assert something plainly observable at the wire — impl error codes, response field
contents, auth-optional dispatch. Extrapolated: **~530 more tests** that are correct but at the
wrong level.

**Consolidated: delete ~1,493 · migrate ~762 · keep ~3,339.** (1,493 + 762 + 3,339 = 5,594.)

### BDD spot-check — 10 migrate candidates against `tests/bdd/**/*.feature` (46 files)

| # | Unit test | BDD scenario exists? |
|---|-----------|----------------------|
| 1 | `test_response_shapes.py:748` internal `principal_id` excluded | **NO** — UC-018 covers only "no delivery snapshot" |
| 2 | `test_response_shapes.py:207` `workflow_step_id` excluded | **NO** — `BR-UC-002-create-media-buy.feature:72` says that assertion was *retired* |
| 3 | `test_create_media_buy_behavioral.py:115` push_notification_config pass-through | **YES** — `BR-UC-023:497`, `BR-UC-011:700`, `BR-UC-002-manual-overrides:20` |
| 4 | `test_a2a_transport_contract.py:156` agent-card required fields | **NO** |
| 5 | `test_a2a_transport_contract.py:238` `jsonrpc: "2.0"` envelope | **NO** — only a method-naming scenario at `BR-UC-010:1122` |
| 6 | `test_datetime_string_parsing.py:59` tz-offset `start_time` | **YES (partial)** — `BR-UC-002:41` uses a Z-suffixed `start_time`; the `-08:00` offset form is not exercised |
| 7 | `test_unknown_targeting_fields.py:16` unknown targeting field rejected | **YES** — `BR-UC-002-create-media-buy.feature:245` |
| 8 | `test_auth_consistency.py:230` formats without auth | **YES** — `BR-UC-005:352` |
| 9 | `test_delivery.py:473` partial `media_buy_ids` → errors for missing | **YES — and it CONTRADICTS** |
| 10 | `test_delivery.py:886` custom date range → `reporting_period` | **YES** — `BR-UC-004:194-199`, near-verbatim |

**6 of 10 already covered.** Two findings fall out of this:

- **#10 is a literal duplicate.** The unit test asserts `reporting_period.start == 2025-03-15`;
  `BR-UC-004-deliver-media-buy-metrics.feature:194` asserts the same thing at the wire across
  four transports. The unit test adds nothing.
- **#9 is a live contradiction, not a duplication.** `test_delivery.py:473` documents
  *"Current impl silently drops missing IDs. Correct: return errors"* while
  `BR-UC-004:91` pins *"BR-RULE-030 INV-5: partial resolution, missing silently omitted"*.
  The unit test and the graded scenario encode **opposite** expectations and both are green.
  This is worth a ticket on its own, independent of any deletion.

---

# Section 4 — What should stay

**~3,339 tests should stay**, of which:

- **1,092** are the guard families (E1 + E2) — architecture policy over `src/`, structurally
  unreachable from any transport.
- **~2,247** are behavioral unit tests over pure functions, internal seams, and side effects
  (audit writes, adapter call arguments, "no DB write happened") that no outside observer can
  reach.

Ten concrete keepers, matching the owner's stated exception exactly — the behavior cannot be
verified from outside:

1. `tests/unit/test_idempotency_canonical.py:153` `test_rfc_sample_canonicalization` — canonical
   payload hash against a fixed RFC sample. Pure function; the hash never crosses the wire.
2. `tests/unit/test_gam_macros.py:159` `test_click_url_macros` — `substitute_macros` rewrites
   `{CLICK_URL}` → `%%CLICK_URL_ESC%%`. Pure string transform inside the GAM adapter.
3. `tests/unit/test_device_platform_targeting.py:24` `test_desktop_platforms` — normalizer maps
   `["windows","macos","linux","chromeos"]` → `device_type_any_of == ["desktop"]`.
4. `tests/unit/test_targeting_normalizer.py:108` `test_city_none_of_sets_flag` — sets the
   internal `had_city_targeting` flag, which is `exclude=True` and by construction never on the wire.
5. `tests/unit/test_product_conversion_pricing.py:295` `test_cpa_with_explicit_event_type` —
   `convert_pricing_option_to_adcp` branch selection over pricing model + parameters.
6. `tests/unit/test_idempotency_race_detection.py:89`
   `test_unrelated_integrity_error_re_raises_unchanged` — asserts `caught.value is exc`,
   i.e. identity preservation of an exception object. Not observable downstream.
7. `tests/unit/test_audit_logger_import_safety.py:120`
   `test_logging_does_not_raise_when_the_backup_file_cannot_be_opened` — spawns a fresh
   interpreter against a `chmod 000` log file. Cannot be staged through a transport.
8. `tests/unit/test_naming_unawaited_coroutine.py:25`
   `test_generate_auto_name_returns_ai_name_in_async_context` — event-loop reentrancy; the bug
   is invisible at the wire because the fallback name looks plausible.
9. `tests/unit/test_performance_index_behavioral.py:191` `test_product_to_package_mapping` —
   asserts the `product_id` → `package_id` remap in the **adapter call arguments**, a seam
   below the response.
10. `tests/unit/test_update_media_buy_behavioral.py:2044` `test_state_unchanged_on_auth_failure`
    — asserts `update_media_buy.assert_not_called()` and `update_fields.assert_not_called()`.
    "Nothing was written" is not observable from a response.

Also legitimately unit-level: `tests/unit/adapters/broadstreet/test_advertisements_manager.py:504`
(`_build_template_source_params` gallery expansion), `tests/unit/test_token_extraction_consistency.py:139`
(`Bearer ` with empty token → `None`, an internal `AuthContext` invariant), and
`tests/unit/test_enhanced_custom_targeting.py:498` (GAM criterion tree construction).

---

# Section 5 — Honest total vs. the owner's 2,000

```
                                          count      basis
  total test functions (AST)              5,594      measured
  ─────────────────────────────────────────────────────────────────
  A library            60 × 64%    =         39      14 bodies read
  B existence         254 × 78%    =        198      18 bodies read
  C serialization     156 × 53%    =         83      15 bodies read
  D trivial           125 × 29%    =         36      14 bodies read
  F regression pin     37 × 29%    =         11       7 bodies read
  I schema contract    43 × 50%    =         22       6 bodies read
  H "genuine"       3,827 × 34.9%  =      1,336      43 bodies read
  ─────────────────────────────────────────────────────────────────
  CONFIRMED ABSURD                       1,725       30.8% of the suite
    (a) delete                           1,493
    (b) migrate to BDD                     232
  ─────────────────────────────────────────────────────────────────
  + genuine-but-wrong-level (21% of 2,491
    refuted-H)                             530       6/28 bodies read
  ─────────────────────────────────────────────────────────────────
  TOTAL "should not be a unit test"      2,255       delete 1,493 + migrate 762
  ─────────────────────────────────────────────────────────────────
  guard families, reported separately    1,092       E1 753 + E2 339
```

**Answer: ~1,725 tests are absurd, and ~2,255 should not exist as unit tests once
wrong-level tests are counted. The owner's hypothesis of "at least 2,000" is supported —
but only on the broader claim, and it is not supported by the categories he named.**

Three things sharpen that:

1. **The absurdity is diffuse, not clustered.** Only 675 non-guard tests are catchable by
   name. The other ~1,050 are scattered through files with perfectly reasonable names —
   `test_creative.py`, `test_media_buy.py`, `test_delivery.py`. There is no file you can
   delete wholesale. Any cleanup is a body-by-body pass.
2. **The 5,594-vs-2,743 ratio is inflated by 1,092 guards** that are not behavioral tests at
   all and cannot be BDD scenarios. The honest behavioral-unit-test count is ~4,500.
3. **Uncertainty is real and it is concentrated in H.** The H rate (34.9%, n=43) carries a
   95% CI of 20.6%–49.2%, which puts the confirmed-absurd total between **1,177 and 2,272**.
   The owner's 2,000 sits inside that band, near the top. Everything outside H is small
   enough that its sampling error moves the total by under ±60.

## What I could not determine

- **The 5,594 vs 5,245 discrepancy.** My AST count is reproducible; I could not identify what
  produced 5,245 without a pytest collection run, which I did not do.
- **Whether any confirmed-absurd test is currently failing or vacuously passing at runtime.**
  I read bodies; I ran nothing. `test_create_media_buy_response_adcp_compliance` is provably
  vacuous by inspection (empty loop), but there may be more that only a mutation run reveals.
- **The exact E1/E2 boundary** (±130, see Section 2). The combined 1,092 is solid.
- **Migration feasibility per test.** I spot-checked 10 against the feature files. I did not
  check whether the remaining ~750 migrate candidates have a *wireable* harness path, which is
  what determines whether "migrate" is a day of work or a quarter.
- **Whether the `test_delivery.py:473` ↔ `BR-UC-004:91` contradiction is a spec bug, a stale
  unit test, or a dormant scenario.** It needs resolution against the pinned AdCP 3.1.1 spec
  before either side is touched.

# PR #1389 (property_list targeting) — review follow-ups

Captured from the gold-standard review of #1389. Items implemented in-PR are NOT
listed here. These are the deliberately-deferred items, with verified facts so
they don't need re-deriving. Each is LOW severity and out of the property_list
critical path; none gate #1389.

---

## F20 — partial Kevel narrowing is silent (no per-package advisory)

**What:** When a buyer's `property_list` resolves to SOME but not all Kevel
`Site`s (some domains supported-type but not onboarded), the unresolved
identifiers fall out of `siteIds` and only `unresolvable_values` is logged as a
count (`kevel_site_resolver.py` `ResolvedSiteIds.unresolvable_values`). The
zero-overlap advisory (`_build_property_list_advisories`) fires only on
whole-product `zero_match`, so a PARTIAL drop yields no buyer signal. Kevel-only.

**Why deferred (not a one-liner):** the advisory pipeline runs in the async
`_create_media_buy_impl` validation transaction and is built from the
PropertyIntersection, BEFORE the Kevel adapter compiles `siteIds` synchronously
inside `create_media_buy`. Surfacing `unresolvable_values` cleanly needs a new
adapter-base hook (e.g. `property_list_narrowing(packages) -> {key: [values]}`;
Kevel reads its `_property_list_cache`, base returns `{}`) threaded back into the
response builder after the adapter call. That touches the central create path and
the adapter return contract — modest but real blast radius for a LOW item.

**When to do it:** alongside any future change to the create-path advisory wiring,
or if operators report confusion from silent partial narrowing. Reuse the existing
`property_list_drop_advisory` builder + `ext.prebid.property_list_advisories` slot.

---

## F27 — Targeting.model_dump does not hide internal fields on all paths (pre-existing)

**What:** `Targeting.model_dump` reliably hides internal fields (tenant_id,
key_value_pairs, metadata) only on the direct `t.model_dump()` path. It is
bypassed on nested serialization through a parent's default serializer, on
`model_dump_json`, and when `exclude` is passed as a dict (the
`isinstance(exclude, set)` guard skips it). `src/core/schemas/_base.py`.

**Why deferred:** PRE-EXISTING on origin/main; #1389 adds NO new exposure
(key_value_pairs is managed-only, the buyer-facing wire uses the library type with
no internal fields, and `get_media_buys` is protected by
`GetMediaBuysResponse`'s `NestedModelSerializerMixin` wrap-serializer). Out of
scope for a property_list-scoped PR.

**When to do it:** defense-in-depth hardening pass. Mark
tenant_id/created_at/updated_at/metadata `Field(exclude=True)`; key_value_pairs
needs a `@model_serializer(mode="wrap")` that drops it unless
`info.context` requests internal (mirror `CreateMediaBuySuccess`).

---

## F12 — collection_list accepted-persisted-but-never-compiled (pre-existing, #1314/#1315 continuation)

**What:** `collection_list` / `collection_list_exclude` are accepted at the
boundary, persisted, and echoed as "persisted", but never compiled and never
rejected — no `collection_list` capability is declared anywhere and Kevel never
references it. A buyer sending `collection_list` gets 200/success and may believe
content-collection targeting applied while ads serve unrestricted.

**Why deferred:** PRE-EXISTING on origin/main; #1389 is honestly property_list-
scoped (title + body), an explicit continuation of the #1314/#1315
collection_list series. NOT introduced or touched here.

**When to do it:** when collection-list capability infra lands. Either add a
boundary gate symmetric to `raise_if_property_list_unsupported` that rejects
`collection_list`/`collection_list_exclude` with `UNSUPPORTED_FEATURE` until a
compile path exists AND declare the capability in `get_adcp_capabilities`, or keep
the deferral but document accepted-and-dropped explicitly.

---

## F03 follow-up — full update→flight reconciler (optional hardening)

**Done in #1389:** property_list flight PUTs are deferred to a single post-commit
push phase (`_update_media_buy_impl`), so a live flight is never ahead of a
rolled-back `package_config`. The only residual is the benign direction —
`package_config` committed but the post-commit PUT failed (config ahead of
flight) — which surfaces a transient error and heals forward on a buyer retry.

**Optional future hardening:** a background reconciler that re-pushes
`package_config.targeting_overlay` to the live flight for any media buy whose last
push failed, so the heal-forward does not depend on a buyer retry. Mirrors create's
documented orphan-reconciliation precedent. Not required for correctness — the
retry path already heals — but closes the gap autonomously.

---

## F09 — merge-order coordination with #1274 (NOT a #1389 code change)

#1389 renames `resolve_property_list -> resolve_property_list_typed` (name AND
return type: `list[str]` -> `list[Identifier]`) and DELETES
`filter_products_by_property_list` / `should_include_product_for_property_list`
(`src/core/property_list_resolver.py`, `src/core/tools/products.py`). In-flight
**#1274** still `from src.core.property_list_resolver import resolve_property_list`
and defines the two deleted helpers (pr-1274 `products.py:97,132,464-468`).

The trailing pre-push full-suite gate catches this after rebase (not silent-to-
main), but the reconciliation is non-trivial (different matching semantics:
ID-set membership vs Identifier.value grammar). **Recommendation:** land #1389
first; rebase #1274 onto `resolve_property_list_typed` + `PropertyIntersection`.
A heads-up snippet for the #1389 PR body is prepared for the maintainer to add.

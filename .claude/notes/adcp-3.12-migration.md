# AdCP 3.10.0 → 3.12.0 Migration Plan (rc.2 → rc.3)

**Status:** Planning. `pyproject.toml` already bumped to `adcp>=3.12.0` in this worktree — imports currently fail. DO NOT revert; the failures are the work list.

**Source of truth used:** `/Users/konst/projects/salesagent-develop/.venv/lib/python3.12/site-packages/adcp/` (installed 3.12.0 library).

## TL;DR — Impact Summary

| Change | Blast radius | Risk | Data migration |
|---|---|---|---|
| `FormatCategory` removed | 3 prod files, mock/fallback UI, tests | High (admin UI feature loss) | None (format.type is derived/static, not stored) |
| `DeliverTo` removed | 1 prod file | Low (1 construction site) | None |
| `BrandManifest` removed | 2 prod files, alias + imports | Medium (type alias used via isinstance) | None |
| `buyer_ref` removed from CreateMediaBuyRequest / PackageRequest / Package | 10+ prod files, DB column + unique constraint, many tests | **Critical** (core lifecycle identifier) | **Yes — column + unique index** |
| `buyer_refs` filter removed from GetMediaBuysRequest | 1 prod file | Low | None |
| `UpdateMediaBuyRequest.buyer_ref` removed (media_buy_id becomes required) | media_buy_update.py, oneOf validator | High (behavioral change) | None |

**Recommendation:** Multiple PRs. Landing in a single commit is too risky because `buyer_ref` touches the DB schema, HTTP boundary, internal ID generation, and audit log formatting. See "Recommended PR structure" below.

---

## 1. Affected files — exhaustive enumeration

Grep was not available in the environment used for this investigation; enumeration below is derived from directed reads of every file referenced by the 3.10 aliases. Anyone running this plan should verify with `grep -rn` before committing.

### 1.1 `FormatCategory` / `FormatTypeEnum` usages

| File | Line(s) | Usage |
|---|---|---|
| `src/core/schemas/_base.py` | 23–25 | `from adcp.types import FormatCategory as FormatTypeEnum` — **IMPORT BREAKS** |
| `src/core/schemas/_base.py` | 771 | `type=FormatTypeEnum.display` — fallback Format construction in `convert_format_ids_to_formats()` |
| `src/core/creative_agent_registry.py` | 29 | `from adcp.types import FormatCategory as FormatType` — **IMPORT BREAKS** |
| `src/core/creative_agent_registry.py` | 52 | `_create_mock_format(..., format_type: FormatType, ...)` — function signature |
| `src/core/creative_agent_registry.py` | 78 | `Format(..., type=format_type, ...)` — mock Format construction |
| `src/core/creative_agent_registry.py` | 97–107 | `FormatType.display`, `FormatType.video` in `_get_mock_formats()` |
| `src/core/creative_agent_registry.py` | 276–278 | Request-side `typed_format_type: FormatType = FormatType(type_filter)` in `_fetch_formats_from_agent()` |
| `src/core/creative_agent_registry.py` | 289, 698 | `request = ListCreativeFormatsRequest(..., type=typed_format_type)` then `if fmt.type != type_filter` |
| `src/core/format_resolver.py` | 169, 182, 213 | `type_filter: str \| None` parameter piped into `list_all_formats` — not broken by import removal but orphaned once the upstream filter goes away |
| `src/admin/blueprints/format_search.py` | 30–51 | `type=...` query param → `type_filter=...` → `search_formats()` |
| `src/admin/blueprints/format_search.py` | 66, 119, 151 | **Emits `fmt.type` as a JSON API field** (admin UI consumer) |

**Tests referencing Format.type (incomplete — verify with grep):** `tests/unit/test_adcp_contract.py`, tests for `creative_agent_registry`, and any test factory that builds a Format with `type=...`.

### 1.2 `buyer_ref` / `buyer_campaign_ref` / `campaign_ref` usages

These are not all the same kind: some are our *subclass overrides* that add the field back onto our wrappers, some are pure usage, some are internal-only storage.

| File | Line(s) | Kind | Notes |
|---|---|---|---|
| `src/core/schemas/_base.py` | 237–238 | Our subclass uses it | `CreateMediaBuySuccess.__str__` falls back to `self.buyer_ref` when `media_buy_id` is missing. The inherited library field is now gone — this crashes. |
| `src/core/schemas/_base.py` | 306–310 | Our AffectedPackage | `buyer_package_ref: str \| None = Field(..., exclude=True)` — our internal legacy field, not from library. Not affected by the library removal but semantically redundant now. |
| `src/core/schemas/_base.py` | 1301 | Docstring only | `"Library PackageRequest required fields... budget, buyer_ref, ..."` — **out of date**, library PackageRequest no longer has `buyer_ref` (see §2). |
| `src/core/schemas/_base.py` | 1506–1518 | Our CheckMediaBuyStatusRequest | `buyer_ref` is a local field on a local class (not inheriting from library). Still works. Consider whether to keep. |
| `src/core/schemas/_base.py` | 1542 | Our MediaPackage | `buyer_ref: str \| None = None` — local field on local type. Still works. |
| `src/core/schemas/_base.py` | 1700–1711 | `validate_identification_xor` on UpdateMediaBuyRequest | Enforces oneOf(media_buy_id, buyer_ref). 3.12 UpdateMediaBuyRequest has `media_buy_id: str` as **required** and no `buyer_ref`. Our subclass will re-add it (we inherit library) but **the library validation will fail first** because `media_buy_id` is now required at the base level. |
| `src/core/schemas/_base.py` | 2322, 2344 | GetMediaBuysMediaBuy / GetMediaBuysPackage | Local types. Still works. |
| `src/core/schemas/_base.py` | 2368 | GetMediaBuysRequest | `buyer_refs: list[str] \| None` — our local field (library `GetMediaBuysRequest` doesn't have it). Still technically works; drift question. |
| `src/core/schemas/creative.py` | 289–294 | AddCreativeAssetsRequest | Our local type, still works. |
| `src/core/database/models.py` | 894 | `MediaBuy.buyer_ref: Mapped[str \| None]` | **Real DB column, non-nullable index** |
| `src/core/database/models.py` | 952–957 | `UniqueConstraint("tenant_id", "principal_id", "buyer_ref", name="uq_media_buys_buyer_ref")` | **DB constraint** — deduplicates by buyer_ref. Semantic replacement is `idempotency_key` per spec. |
| `src/core/database/models.py` | 97 | `order_name_template: ... server_default="{campaign_name\|brand_name} - {buyer_ref} - {date_range}"` | Default template string references `buyer_ref` macro |
| `src/core/tools/media_buy_list.py` | 34, 65–67 | `_MediaBuyData.buyer_ref`, `GetMediaBuysMediaBuy.buyer_ref` | Exposed in response payload |
| `src/core/tools/media_buy_create.py` | — | Need full read; definitely constructs MediaBuy with `buyer_ref=req.buyer_ref` | **Will KeyError/AttributeError** when 3.12 library strips buyer_ref from incoming CreateMediaBuyRequest |
| `src/core/tools/media_buy_update.py` | — | `UpdateMediaBuyRequest.buyer_ref` checks | Dead after migration |
| Admin UI templates | — | `{{ media_buy.buyer_ref }}` likely in `templates/media_buys.html` etc. | **Verify with grep once available** |

**FIXME — files that must be grep'd before starting implementation:**
Run these greps first thing:
```
grep -rn "buyer_ref\|buyer_campaign_ref\|campaign_ref" src/ tests/
grep -rn "FormatCategory\|FormatType\|FormatTypeEnum" src/ tests/
grep -rn "DeliverTo\b" src/ tests/
grep -rn "BrandManifest" src/ tests/
```
The enumeration above is derived from direct file reads of the schemas and tools directories; it is not exhaustive across `templates/`, `tests/bdd/`, and `tests/factories/`.

### 1.3 `DeliverTo` usages

| File | Line(s) | Usage |
|---|---|---|
| `src/core/signals_agent_registry.py` | 36 | `from adcp.types import DeliverTo` — **IMPORT BREAKS** |
| `src/core/signals_agent_registry.py` | 164–172 | `deliver_to = DeliverTo(countries=[...], deployments=[PlatformDestination(...)])` — only construction site |

**`src/services/dynamic_products.py` does NOT import `DeliverTo` despite what the task stated.** Line ~79–85 uses `"deliver_to"` only as a dict key inside `context`, not as a library type. Not affected by the removal.

### 1.4 `BrandManifest` usages

| File | Line(s) | Usage |
|---|---|---|
| `src/core/schemas/_base.py` | 59 | `from adcp.types import BrandManifest as LibraryBrandManifest` — **IMPORT BREAKS** |
| `src/core/schemas/_base.py` | 1239 | `BrandManifest: TypeAlias = LibraryBrandManifest` — re-exports to the rest of the app |
| `src/core/schemas/_base.py` | 1242–1267 | `BrandManifestRef` wrapper type (union inline-or-URL) uses `BrandManifest` |
| `src/services/policy_check_service.py` | 7 | `from adcp import BrandManifest` — **IMPORT BREAKS** |
| `src/services/policy_check_service.py` | 92, 109 | `brand_manifest: BrandManifest \| str \| None = None`; `isinstance(brand_manifest, BrandManifest)` |

### 1.5 `idempotency_key` (already in codebase)

- `src/core/schemas/_base.py` line 1649: comment `# idempotency_key: now provided by adcp library base class (since 3.10)` — already aware of it for `UpdateMediaBuyRequest`.
- No references found in `src/core/tools/media_buy_create.py`, `src/core/database/models.py`, or any repository. **We are NOT currently using idempotency_key as a dedup mechanism** — `buyer_ref` plays that role today (via the `uq_media_buys_buyer_ref` unique constraint).

---

## 2. What `FormatCategory` was, and what we lose

### What it was (pre-3.12)

`FormatCategory` in 3.10 was an enum `{display, video, audio, native}` carried on `Format.type`. Semantically it said "this creative format is a display banner / video ad / audio ad / native unit."

### Where it lived in our stack

1. **Request filter** — `ListCreativeFormatsRequest.type` in 3.12 is **GONE** from the media-buy-facing request (`adcp/types/generated_poc/media_buy/list_creative_formats_request.py` has no `type` field). It still exists on the creative-agent-side variant (`ListCreativeFormatsRequestCreativeAgent.type`, a **local** `Type` enum with values `{audio, video, display, dooh}`), but the media_buy variant is the one our `creative_agent_registry.py` passes to `client.agent(...).list_creative_formats(...)`.

2. **Response field** — `Format.type` in 3.12 is **GONE**. `Format` in `adcp/types/generated_poc/core/format.py` has no `type`. Categorization is now expressed structurally via `assets[].asset_type` (image/video/audio/html/javascript/…), plus `input_format_ids` and `output_format_ids` (for template formats).

3. **Admin UI exposure** — `src/admin/blueprints/format_search.py` emits `type: str(fmt.type)` in JSON responses at lines 66, 119, 151. Any admin UI template or JS that shows a "Format Type" column or filter chip reads this field. **Verify in `templates/` which pages consume `/api/formats/search` and `/api/formats/list`.**

4. **GAM adapter** — From `src/core/helpers/adapter_helpers.py` and reading `src/adapters/gam/` entry points, GAM line-item-type selection is driven by **pricing model + delivery_type + guarantee**, not by format.type. GAM's `format.type` is used only by the format template picker UI in `format_search.py::get_format_templates()` (lines 190–230), which is **hardcoded** (not read from library). **No functional regression in adapter logic.**

5. **Database** — `products.format_ids` stores `[{agent_url, id}]` dicts (from `src/core/database/models.py:232`); it does NOT store format type/category. The `products.channels` column (line 249) holds `["display", "video", "native"]` which overlaps semantically but is a separate concept (product media channel, not format category).

### What we lose

| Capability | Affected user path | Mitigation |
|---|---|---|
| Admin UI filter "show only video formats" | `/api/formats/search?type=video`, `/api/formats/list?type=display` | **NEEDS HUMAN DECISION**: either (a) derive type from assets heuristically (format has `asset_type='video'` asset → "video"), (b) drop the filter, or (c) keep a local enum and maintain a manual mapping. |
| Mock format construction in tests | `_get_mock_formats()` in `creative_agent_registry.py` and `convert_format_ids_to_formats()` in `_base.py` | Remove `type=` argument; mocks work without it. |
| Format.type field in JSON response | `/api/formats/*` consumers | Derive or drop; see above. |
| Filtering `ListCreativeFormatsRequest` by type | `creative_agent_registry._fetch_formats_from_agent` | Drop; adcp 3.12 `list_creative_formats_request` (media_buy variant) has no `type` field. |

**NEEDS HUMAN DECISION (FormatCategory-1):** Do we want to preserve a "format type" concept in the admin UI? Options:
- **A. Kill the filter outright.** Easiest. Matches upstream spec intent. UI loses a triage feature.
- **B. Derive from `assets[].asset_type`.** Slightly lossy: a single format can have image + text + video (e.g., native), so the derivation is heuristic. Example rule: `any(a.asset_type == "video" for a in fmt.assets) → "video"`, else `any(asset_type == "audio") → "audio"`, else `"display"`.
- **C. Maintain a local enum + manual mapping from format_id.** Duplicates spec intent; adds drift surface.

Recommendation: **B** with an explicit one-line derivation helper, because it preserves admin UX without drift risk (the derivation is always correct from the structural data we already have).

---

## 3. `buyer_ref` analysis — the hardest change

### 3.1 Database footprint

From `src/core/database/models.py:886–962`:

```python
class MediaBuy(Base):
    ...
    buyer_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # line 894
    ...
    __table_args__ = (
        ...
        UniqueConstraint("tenant_id", "principal_id", "buyer_ref", name="uq_media_buys_buyer_ref"),  # line 952
        ...
    )
```

This is a real PG column with:
- A standalone btree index (`index=True`)
- A composite unique constraint `(tenant_id, principal_id, buyer_ref)` — the dedup/idempotency mechanism today

**MediaPackage** (line 965 onwards) does NOT have a `buyer_ref` column. `buyer_package_ref` exists only as a Pydantic internal field.

### 3.2 Current idempotency story

There is **no existing `idempotency_key` column** on `media_buys`. The dedup mechanism is:
1. Buyer generates a `buyer_ref` (e.g., `"acme-q1-2026-push"`)
2. Passes it in `CreateMediaBuyRequest.buyer_ref`
3. We store it
4. Unique constraint prevents double-create for the same `(tenant, principal, buyer_ref)` tuple

3.12 spec replaces this with `CreateMediaBuyRequest.idempotency_key` (UUID v4, 16–255 chars, per-seller). The seller assigns `media_buy_id`; the buyer controls `idempotency_key` for retries.

### 3.3 Per-site impact in `_base.py`

- Line 237–238: `CreateMediaBuySuccess.__str__` uses `self.buyer_ref` in its summary when `media_buy_id` is missing. In 3.12 the parent class `AdCPCreateMediaBuySuccess` no longer exposes `buyer_ref`. This method will `AttributeError` at call time. **Fix:** remove the fallback; since `media_buy_id` is now always present in a success response (per spec), the else branch is dead code.
- Line 1698–1711: `validate_identification_xor` will run *after* Pydantic validation already failed, because 3.12 `UpdateMediaBuyRequest` has `media_buy_id: str` (required). Our subclass sets `media_buy_id: str | None = None` via override (not present — verify), but the library constructor still validates. **Fix:** delete the xor validator; `media_buy_id` is now unambiguously required.

### 3.4 Data migration plan

**An Alembic migration is required.**

Recommended approach (two revisions):

**Revision 1 — additive:**
```python
def upgrade():
    op.add_column(
        "media_buys",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_media_buys_idempotency_key",
        "media_buys",
        ["tenant_id", "principal_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Do NOT drop buyer_ref yet — keep it for backward compat during rollout.

def downgrade():
    op.drop_index("idx_media_buys_idempotency_key", table_name="media_buys")
    op.drop_column("media_buys", "idempotency_key")
```

**Revision 2 — destructive (one release later):**
```python
def upgrade():
    op.drop_constraint("uq_media_buys_buyer_ref", "media_buys", type_="unique")
    op.drop_index("ix_media_buys_buyer_ref", "media_buys")  # index=True auto-names this
    op.drop_column("media_buys", "buyer_ref")

def downgrade():
    op.add_column("media_buys", sa.Column("buyer_ref", sa.String(100), nullable=True))
    op.create_index("ix_media_buys_buyer_ref", "media_buys", ["buyer_ref"])
    op.create_unique_constraint("uq_media_buys_buyer_ref", "media_buys", ["tenant_id", "principal_id", "buyer_ref"])
```

**NEEDS HUMAN DECISION (buyer_ref-1):** Can we drop `buyer_ref` in the same release as the library upgrade, or do we need the intermediate "both columns" release? Dropping immediately is cleaner but loses ability to roll back without data loss once new media buys are created. Recommendation: sequenced release.

**NEEDS HUMAN DECISION (buyer_ref-2):** The `Tenant.order_name_template` default value (models.py:97) contains the `{buyer_ref}` macro. Who consumes this? Check `src/adapters/` for order name templating. Either:
- Replace macro with `{media_buy_id}` (works post-create, not ideal for order naming since it's seller-generated)
- Replace with `{idempotency_key}` (works but opaque UUID in order name is ugly)
- Add a new optional `{external_ref}` or free-form field if buyers still want human-readable order names

### 3.5 AdCP spec context (how buyers identify media buys now)

From `adcp/types/generated_poc/media_buy/create_media_buy_request.py`:
- `media_buy_id` is seller-assigned, returned in the success response.
- `idempotency_key` is client-assigned (UUID v4, optional), used only for retry dedup.
- There is no buyer-chosen identifier in the request. Buyers track their own media buys by keeping the returned `media_buy_id` in their own system.

`GetMediaBuysRequest` no longer has `buyer_refs` filter — buyers retrieve by `media_buy_ids` or paginate over their account's buys filtered by status.

`UpdateMediaBuyRequest.media_buy_id: str` is **required** (not optional). No more oneOf(media_buy_id, buyer_ref).

---

## 4. `DeliverTo` / signals changes

### 4.1 Current shape (3.10)

`src/core/signals_agent_registry.py:164–172` constructs:
```python
deliver_to = DeliverTo(
    countries=["US"],
    deployments=[PlatformDestination(type="platform", platform="all")],
)
request = GetSignalsRequest(signal_spec=signal_spec)  # does not currently pass deliver_to
```

**Observation:** the `deliver_to` object is constructed but NOT actually attached to the request — the `GetSignalsRequest(...)` call only passes `signal_spec`. So the construction has been dead code for some time. Verify via git history.

### 4.2 New shape (3.12)

`adcp/types/generated_poc/signals/get_signals_request.py` `GetSignalsRequest` fields:
- `account: AccountReference | None`
- `context: ContextObject | None`
- `countries: list[Country] | None` (inline list of ISO-3166-1 alpha-2, min_length=1)
- `destinations: list[Destination] | None` (inline; `Destination` is a `RootModel[Destination1 | Destination2]` where `Destination1` is `{type: "platform", platform: str, account?: str}` and `Destination2` is `{type: "agent", agent_url: AnyUrl, account?: str}`)
- `ext: ExtensionObject | None`
- `filters: SignalFilters | None`
- `max_results: int | None`
- `pagination: PaginationRequest | None`
- `signal_ids: list[SignalId] | None`
- `signal_spec: str | None`

**No more `DeliverTo` wrapper** — `countries` and `destinations` are flat on the request.

### 4.3 Fix

Delete the `DeliverTo(...)` construction block (lines 161–172) in `signals_agent_registry.py`. Since the current code never actually uses it, there is no behavioral change. If we want to pass countries/destinations in the future, add them directly: `GetSignalsRequest(signal_spec=..., countries=[...], destinations=[PlatformDestination(type="platform", platform="all")])`.

`PlatformDestination` is an alias for `Destination1` (confirmed in `adcp/__init__.py:316`) — it's still importable. Keep the `PlatformDestination` import; drop only `DeliverTo`.

### 4.4 `dynamic_products.py` (not affected)

`src/services/dynamic_products.py` does NOT import `DeliverTo`. Line ~79–85 uses `"deliver_to"` only as a dict key in `context`. The stringly-typed dict is passed downstream and consumed by `registry.get_signals()` which currently ignores it (see §4.1). No code change required.

---

## 5. `BrandManifest` analysis

### 5.1 Current usage

1. `src/core/schemas/_base.py:59`: imported as `LibraryBrandManifest`.
2. `src/core/schemas/_base.py:1239`: re-exported as local TypeAlias `BrandManifest`.
3. `src/core/schemas/_base.py:1242–1267`: `BrandManifestRef` wrapper class for "inline object OR URL" semantics.
4. `src/services/policy_check_service.py:7, 92, 109`: type annotation + `isinstance(brand_manifest, BrandManifest)` runtime check in `check_brief_compliance()`.

### 5.2 What replaces it in 3.12

The `Brand` class in `adcp/types/generated_poc/brand/__init__.py:1071` is the 3.12 equivalent — full brand identity (logos, colors, fonts, properties, etc.). There is also a `BrandReference` (just `{domain, brand_id?}`) used by request schemas.

The 3.12 module `adcp.types.generated_poc.brand` does NOT export `BrandManifest`. The name is gone entirely.

Per `src/core/schemas/_base.py:1238` comment, `BrandManifest` was a library alias we just re-exported. There is no local extension value, so the alias can either:
- Be retargeted to `adcp.types.generated_poc.brand.Brand`, OR
- Be deleted (and `BrandManifestRef` with it, if it's not used elsewhere)

### 5.3 `BrandManifestRef` — is it dead?

From reading `schemas/_base.py:1242–1267`, the class exists but I could not find call sites via directed read. This is likely dead code — the task author suggested it may be obsolete. **NEEDS HUMAN DECISION (BrandManifest-1):** verify `BrandManifestRef` is unused (grep for it) and delete it along with the `BrandManifest` alias. If used, retarget to `Brand`.

### 5.4 `policy_check_service.check_brief_compliance` impact

Line 92: `brand_manifest: BrandManifest | str | None = None`. Line 109: `if isinstance(brand_manifest, BrandManifest):`.

This API accepts either a BrandManifest object or a URL string. In 3.12, the natural replacement is `Brand` (the full inline object) or `BrandReference` (for a domain-level reference).

**NEEDS HUMAN DECISION (BrandManifest-2):** Should `check_brief_compliance` take `Brand | str | None` (full manifest) or `BrandReference | str | None` (just domain)? Upstream requests (`GetProductsRequest.brand`, `CreateMediaBuyRequest.brand`) carry `BrandReference`. If the policy service resolves the brand from the reference internally, use `BrandReference`. If callers actually pass full manifests (unusual), use `Brand`.

The code today extracts `.name` and `.tagline` via `getattr`, which `Brand` has (`names: list[LocalizedName]`, `tagline: str | Tagline | None`) — but the access pattern would change (no longer a plain string for `.name`).

---

## 6. Semantic / schema analysis — concrete 3.12 structures

### 6.1 `Format.format_card` (new, does NOT replace FormatCategory)

`adcp/types/generated_poc/core/format.py:166, 182`:
```python
class FormatCard(AdCPBaseModel):
    format_id: FormatId  # typically "format_card_standard"
    manifest: dict[str, Any]  # rendering manifest

class FormatCardDetailed(AdCPBaseModel):
    format_id: FormatId
    manifest: dict[str, Any]
```

`Format.format_card: FormatCard | None` and `Format.format_card_detailed: FormatCardDetailed | None` are **display artifacts** (how to render a preview card of the format itself in a UI). They have nothing to do with display/video/audio/native categorization.

**Format categorization in 3.12 lives entirely in `Format.assets[].asset_type`** (enum: `image|video|audio|text|markdown|html|css|javascript|vast|daast|url|webhook|brief|catalog`). There is no top-level category field.

### 6.2 3.12 `Format` shape (full field list)

From `adcp/types/generated_poc/core/format.py:441–558`:
- Required: `format_id`, `name`
- Optional: `accepts_parameters`, `accessibility`, `assets`, `delivery`, `description`, `disclosure_capabilities`, `example_url`, `format_card`, `format_card_detailed`, `input_format_ids`, `output_format_ids`, `renders`, `reported_metrics`, `supported_disclosure_positions`, `supported_macros`
- NOT PRESENT: `type` (was FormatCategory)

### 6.3 3.12 `CreateMediaBuyRequest` shape

From `adcp/types/generated_poc/media_buy/create_media_buy_request.py:127`:
- Required: `account: AccountReference`, `brand: BrandReference`, `end_time`, `start_time`
- Optional: `advertiser_industry`, `artifact_webhook`, `context`, `ext`, `idempotency_key`, `invoice_recipient`, `io_acceptance`, `packages`, `plan_id`, `po_number`, `proposal_id`, `push_notification_config`, `reporting_webhook`, `total_budget`
- NOT PRESENT: `buyer_ref`, `buyer_campaign_ref`, `campaign_ref`

Note: `account` is required — we previously made it optional via override (`src/core/schemas/_base.py:1439`) because identity resolves at the transport layer. Migration does NOT change this; keep the override.

### 6.4 3.12 `PackageRequest` shape

From `adcp/types/generated_poc/media_buy/package_request.py:20`:
- Required: `budget`, `pricing_option_id`, `product_id`
- Optional: everything else
- NOT PRESENT: `buyer_ref`

### 6.5 3.12 `Package` (response) shape

From `adcp/types/generated_poc/core/package.py:44`:
- Required: `package_id`
- NOT PRESENT: `buyer_ref`

### 6.6 3.12 `UpdateMediaBuyRequest` shape

From `adcp/types/generated_poc/media_buy/update_media_buy_request.py:21`:
- Required: `media_buy_id: str`
- Optional: `canceled`, `cancellation_reason`, `context`, `end_time`, `ext`, `idempotency_key`, `invoice_recipient`, `new_packages`, `packages`, `paused`, `push_notification_config`, `reporting_webhook`, `revision`, `start_time`
- NOT PRESENT: `buyer_ref`, `budget` (campaign-level budget is GONE from update spec)

**Behavioral note:** We currently treat `budget` as a convenience field on `UpdateMediaBuyRequest` (schemas/_base.py:1638). In 3.12 spec there is no campaign-level budget field on update. Per-package budgets are updated via `packages[].budget`. **NEEDS HUMAN DECISION (update-budget-1):** keep the local `budget` convenience field for backward compat, or require callers to pass per-package budgets. Spec-aligned answer: drop the convenience field. Behavioral answer: keep it with a deprecation warning for one release.

### 6.7 3.12 `GetMediaBuysRequest` shape

From `adcp/types/generated_poc/media_buy/get_media_buys_request.py:29`:
- No required fields (all optional)
- Fields: `account`, `context`, `ext`, `include_history`, `include_snapshot`, `media_buy_ids`, `pagination`, `status_filter`
- NOT PRESENT: `buyer_refs`

---

## 7. Dependency ordering — step-by-step plan

Each step should leave the test suite in a runnable state. Tox `unit` must pass after each numbered checkpoint.

### Step 1 — Fix imports that break the interpreter
These unblock basic `import src` sanity.

1.1. `src/core/schemas/_base.py:24` — delete `from adcp.types import FormatCategory as FormatTypeEnum`.
1.2. `src/core/schemas/_base.py:59` — delete `from adcp.types import BrandManifest as LibraryBrandManifest`.
1.3. `src/core/schemas/_base.py:1238–1239` — delete `BrandManifest: TypeAlias = LibraryBrandManifest` (and optionally delete `BrandManifestRef` if grep confirms no users).
1.4. `src/core/signals_agent_registry.py:36` — delete `from adcp.types import DeliverTo`.
1.5. `src/core/creative_agent_registry.py:29` — delete `from adcp.types import FormatCategory as FormatType`.
1.6. `src/services/policy_check_service.py:7` — change `from adcp import BrandManifest` to import whatever replaces it (TBD by §5.4 decision).

**Checkpoint:** `uv run python -c "from src.core import schemas, signals_agent_registry, creative_agent_registry" ` should succeed.

### Step 2 — Remove field uses at construction sites

2.1. `src/core/schemas/_base.py:771` — in `convert_format_ids_to_formats`, the fallback `Format(..., type=FormatTypeEnum.display, ...)` drops `type=`.
2.2. `src/core/creative_agent_registry.py:52–108` — `_create_mock_format` and `_get_mock_formats`: drop `format_type` parameter; drop `FormatType.*` references in the mock list (still construct mocks for each category name, but category name goes into `name=` or a tag, not `type=`).
2.3. `src/core/creative_agent_registry.py:276–290` — `_fetch_formats_from_agent`: drop `typed_format_type` local + the `type=typed_format_type` kwarg to `ListCreativeFormatsRequest(...)` (field no longer exists on 3.12 media_buy variant).
2.4. `src/core/creative_agent_registry.py:698` — `search_formats`: either drop the `type_filter` argument entirely or implement the asset-type heuristic (§2 option B).
2.5. `src/core/format_resolver.py:169, 182, 213` — drop `type_filter` from `list_available_formats` signature or forward it to the heuristic.
2.6. `src/admin/blueprints/format_search.py:66, 119, 151` — delete `"type": str(fmt.type)` JSON field OR replace with heuristic-derived value. Delete `type_filter` request param handling if going with §2 option A.
2.7. `src/core/signals_agent_registry.py:161–172` — delete the `deliver_to = DeliverTo(...)` block (it was never actually attached to the request).

**Checkpoint:** `tox -e unit` runs (may have failures in tests that reference `format.type` or `buyer_ref`, which will be fixed in later steps).

### Step 3 — Schema / wrapper classes

3.1. `src/core/schemas/_base.py:237–238` — remove `buyer_ref` fallback in `CreateMediaBuySuccess.__str__`. Return `f"Media buy {self.media_buy_id} created successfully."` unconditionally.
3.2. `src/core/schemas/_base.py:1698–1711` — delete `validate_identification_xor` on `UpdateMediaBuyRequest`. `media_buy_id` is required at the library level, so the validator is redundant and will conflict.
3.3. `src/core/schemas/_base.py:1636–1638` — per §6.6 decision, either delete the local `budget` convenience field on `UpdateMediaBuyRequest` or add a deprecation warning in a validator.
3.4. `src/core/schemas/_base.py:1301, 1423` — update docstrings that still reference `buyer_ref` as a required library field.
3.5. `src/core/schemas/_base.py:2368` — consider dropping `GetMediaBuysRequest.buyer_refs` local field (library no longer has it; `test_all_request_schemas_match_library` will drift).

### Step 4 — Production code that reads/writes buyer_ref

4.1. `src/core/tools/media_buy_create.py` — full read required. Locate `buyer_ref=req.buyer_ref` type constructions. Replace with:
   - Remove write of `buyer_ref` to `MediaBuy.buyer_ref` column (column still exists until §8 migration lands).
   - Generate server-side `media_buy_id` (already done per schemas).
   - If `req.idempotency_key` is set, check for existing media buy with same `(tenant_id, principal_id, idempotency_key)`; return existing if found.
4.2. `src/core/tools/media_buy_update.py` — remove handling of `req.buyer_ref` (will no longer exist on the request). Require `req.media_buy_id`.
4.3. `src/core/tools/media_buy_list.py:34, 65–70` — `_MediaBuyData.buyer_ref` and `GetMediaBuysMediaBuy.buyer_ref` carry it through responses. Decision point: remove from response or keep as a compat leftover. Spec says remove; doing so is a breaking change for response consumers. **NEEDS HUMAN DECISION (list-response-1):** drop from response payload or keep with deprecation?
4.4. Admin UI templates — grep for `buyer_ref` in `templates/` and `src/admin/templates/`; replace with `media_buy_id` display.
4.5. `src/core/database/models.py:97` — change `order_name_template` default from `{... - {buyer_ref} - ...}` to `{... - {media_buy_id} - ...}` (or per §3.4 decision).

### Step 5 — Tests

5.1. `tests/unit/test_adcp_contract.py:147` — `assert lib_fields == local_fields` for `CreateMediaBuyRequest`: currently expects library and local to match. After migration they should still match (both will lose buyer_ref).
5.2. `tests/unit/test_adcp_contract.py:162–165` — `GetSignalsRequest` comparison: update expected fields (no more `deliver_to`).
5.3. `tests/unit/test_adcp_contract.py:216` — `test_create_media_buy_request_brand_required` uses `LibraryCreateMediaBuyRequest(buyer_ref="test")` in a `pytest.raises(ValidationError)`. Change to a valid construction (without brand) to exercise the same validation.
5.4. Factories in `tests/factories/` — remove `buyer_ref=...` defaults on `MediaBuyFactory`.
5.5. BDD step definitions that set `buyer_ref` — grep for them under `tests/bdd/steps/`. Update to use `media_buy_id` or `idempotency_key` as appropriate.
5.6. Any `Format.type` assertions in unit tests — drop them.

### Step 6 — New functionality (idempotency_key)

6.1. Implement idempotency lookup in `_create_media_buy_impl`:
   - If `req.idempotency_key` is set, `SELECT media_buy_id FROM media_buys WHERE tenant_id=? AND principal_id=? AND idempotency_key=?`
   - If found, return existing success response; do not re-provision adapter.
6.2. Add repository method `MediaBuyRepository.find_by_idempotency_key(tenant_id, principal_id, idempotency_key)`.
6.3. Unit test: creating the same media buy twice with the same idempotency_key returns the same `media_buy_id` and does NOT create a second DB row.

### Step 7 — Alembic migration (Revision 1: additive)

7.1. Generate migration adding `idempotency_key` column + partial unique index (see §3.4).
7.2. Test migration on local PG: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`.
7.3. Ensure `test_architecture_migration_completeness.py` passes (upgrade and downgrade both non-empty).

### Step 8 — Alembic migration (Revision 2: destructive)

Ship in a **subsequent release** to allow rollback during rollout. Steps exactly as §3.4 Revision 2.

---

## 8. Risk assessment

| Change | Behavior-change risk | UX impact | Data migration | Test coverage |
|---|---|---|---|---|
| Drop FormatCategory import | None (import only) | None | None | Adequate — most tests use mock formats |
| Drop FormatCategory from Format construction sites | Low — removes a field from responses | **Admin UI loses "filter by type" chip** unless mitigated (§2) | None | Low — no test currently asserts Format.type survives a roundtrip through the admin API |
| Drop DeliverTo construction | None (was dead code) | None | None | No coverage (dead code) |
| Drop BrandManifest alias | Low — unless `BrandManifestRef` or isinstance checks have hidden callers | Policy check signature change (if §5.4 decision changes type) | None | Low — `policy_check_service` test coverage unknown |
| Drop `buyer_ref` from CreateMediaBuyRequest usage | **HIGH** — buyers currently rely on buyer_ref for idempotency; without careful `idempotency_key` implementation, retries double-book | **HIGH** — any buyer tooling that passes `buyer_ref` as an API parameter will 422 (since `extra="forbid"` in non-prod) | **Yes** — DB column + unique index, 2-step migration | Medium — schema contract tests exist, but end-to-end retry semantics probably untested |
| Drop `buyer_ref` from GetMediaBuysResponse | Medium — response payload changes | **HIGH** — admin UI columns using buyer_ref break | None | Medium |
| `UpdateMediaBuyRequest.media_buy_id` required | Medium — previously oneOf with buyer_ref | Any code path that updated by buyer_ref now fails | None | Low — xor validator test may need inversion |
| Drop UpdateMediaBuyRequest.budget convenience field | Low-medium — depends on caller count | Internal tools that update a campaign-level budget break | None | Low |

### Highest-risk concrete issues

1. **Silent double-create**: If step 6.1 (`idempotency_key` lookup) is skipped or buggy, buyers who previously used `buyer_ref` for deduplication will now succeed-create the same buy twice. This was previously defended by the unique DB constraint; drop the constraint without implementing the replacement and you lose dedup entirely.

2. **Order name templating**: The `Tenant.order_name_template` default uses `{buyer_ref}`. Any tenant whose config has `order_name_template=NULL` uses the server_default, which breaks after column drop. Migration must either update the server_default or all tenants' templates.

3. **Schema contract tests as false-positive safety net**: `test_all_request_schemas_match_library` will pass after migration because lib AND local both drop `buyer_ref` together. It won't catch a stale local override that accidentally re-introduces it.

---

## 9. Recommended PR structure

**Single PR is wrong.** Three PRs, landed in order, each self-contained:

### PR 1 — `chore: remove dead imports and types (adcp 3.12 compat, part 1)`
- Step 1 entirely (import removal)
- Step 2 entirely (construction-site removal, including the dead DeliverTo block)
- Step 3.1–3.4 (schema wrapper cleanup)
- Step 5.1–5.2, 5.6 (test adjustments for imports + Format.type)
- **Does NOT touch buyer_ref.** buyer_ref remains on our local schemas via our overrides.

Expected size: ~15 files, ~200 LOC. Pure cleanup. All tests green.

### PR 2 — `feat: introduce idempotency_key for media buy dedup (adcp 3.12 compat, part 2)`
- Step 6 entirely (idempotency_key implementation)
- Step 7 (additive migration)
- New unit + integration tests for idempotent retry
- **Both columns coexist.** `buyer_ref` still accepted via local override; `idempotency_key` preferred.

Expected size: ~8 files, ~150 LOC + 1 migration. Behavioral change, needs thorough testing.

### PR 3 — `refactor!: drop buyer_ref, align with adcp 3.12 spec (part 3)`
- Step 3.5, 4, 5 (full buyer_ref removal)
- Step 8 (destructive migration)
- Admin UI template updates
- Updates to `order_name_template`

Expected size: ~30+ files, ~400 LOC + 1 migration. **BREAKING CHANGE** — release notes required. Ship on a minor version bump.

---

## 10. Decisions (all made)

All decision points resolved. No open questions.

1. **FormatCategory**: Drop the UI type filter entirely. Formats come from the creative agent registry (discovered, not enumerated). No heuristic derivation.

2. **buyer_ref DB migration**: Single-release destructive migration. Ship as 1.8.0 with BREAKING CHANGES in release notes. Consumer count is small, staged rollout unnecessary.

3. **order_name_template**: Replace `{buyer_ref}` macro with `{media_buy_id}`. `media_buy_id` is always present and spec-canonical; `external_ref` and `idempotency_key` are optional.

4. **BrandManifest**: Gone from the latest schema (removed after 3.0.0-beta.3). **Kill it entirely** — `BrandManifest` alias, `BrandManifestRef` wrapper, all imports, all usages. No retargeting, no replacement. If code currently uses it, migrate to `Brand` where semantically meaningful; otherwise delete.

5. **policy_check_service**: `check_brief_compliance` signature becomes `Brand | str | None`. Access patterns shift to the new `Brand` structure (localized `names` list instead of single `.name`, `Tagline` object instead of plain string).

6. **UpdateMediaBuyRequest.budget**: Drop the local convenience field. Per-package budgets only, spec-aligned.

7. **GetMediaBuysResponse.buyer_ref**: Drop from response payload. Spec-aligned, breaking change is acceptable per decision #2.

## 11. PR structure

Single PR: **`feat!: migrate to adcp 3.12 (rc.3 spec)`** shipping as version 1.8.0.

- All steps 1–8 in dependency order
- Both Alembic migrations in one release (additive `idempotency_key` + destructive `buyer_ref` drop)
- UI format type filter removed
- Order name template updated to `{media_buy_id}`
- `BrandManifest` / `BrandManifestRef` deleted
- Release notes: BREAKING CHANGES section

Expected size: ~50 files, ~600 LOC, 2 Alembic migrations.

---

## Appendix A — verification greps to run first

The environment used for this investigation had no ripgrep. These should be the first commands the implementer runs, to catch anything this plan missed:

```bash
grep -rn "FormatCategory\|FormatType\|FormatTypeEnum" src/ tests/ --include='*.py'
grep -rn "buyer_ref\|buyer_campaign_ref\|campaign_ref" src/ tests/ --include='*.py'
grep -rn "buyer_ref\|buyer_campaign_ref\|campaign_ref" templates/ src/admin/templates/ --include='*.html' --include='*.js'
grep -rn "DeliverTo\b" src/ tests/ --include='*.py'
grep -rn "BrandManifest\b" src/ tests/ --include='*.py'
grep -rn "idempotency_key" src/ tests/ --include='*.py'
grep -rn "BrandManifestRef" src/ tests/ --include='*.py'
grep -rn "fmt\.type\b\|format\.type\b" src/ tests/ --include='*.py'
grep -rn "{buyer_ref}" src/ alembic/ --include='*.py'
```

---

## Appendix B — key file:line citations

**Library 3.12 evidence:**
- `.venv/lib/python3.12/site-packages/adcp/types/__init__.py:45–360` — full 3.12 exports (no FormatCategory, no BrandManifest, no DeliverTo)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/core/format.py:441–558` — 3.12 `Format` (no `type`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/media_buy/create_media_buy_request.py:127–217` — 3.12 `CreateMediaBuyRequest` (no `buyer_ref`, has `idempotency_key`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/media_buy/package_request.py:20–101` — 3.12 `PackageRequest` (no `buyer_ref`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/core/package.py:44–145` — 3.12 `Package` (no `buyer_ref`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/media_buy/update_media_buy_request.py:21–93` — 3.12 `UpdateMediaBuyRequest` (no `buyer_ref`, no `budget`; `media_buy_id` required)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/signals/get_signals_request.py:23–66` — 3.12 `GetSignalsRequest` (flat `countries` + `destinations`, no `DeliverTo`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/media_buy/get_media_buys_request.py:29–74` — 3.12 `GetMediaBuysRequest` (no `buyer_refs`)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/core/destination.py:1–60` — 3.12 `Destination` (platform/agent discriminator)
- `.venv/lib/python3.12/site-packages/adcp/types/generated_poc/brand/__init__.py:1071–1146` — 3.12 `Brand` (replaces BrandManifest conceptually)

**Our codebase evidence:**
- `src/core/schemas/_base.py` — imports, wrapper classes, local validators
- `src/core/signals_agent_registry.py:34–180` — DeliverTo usage
- `src/core/creative_agent_registry.py:29–108, 276–294, 698` — FormatType usage
- `src/admin/blueprints/format_search.py:22–160` — admin UI format.type exposure
- `src/services/policy_check_service.py:7–140` — BrandManifest usage
- `src/core/database/models.py:97, 886–962` — buyer_ref column + constraint + template macro
- `src/core/tools/media_buy_list.py:34, 65–70` — buyer_ref response flow
- `tests/unit/test_adcp_contract.py:1–170` — schema contract tests

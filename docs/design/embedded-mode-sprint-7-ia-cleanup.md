# Sprint 7 Spec: IA Cleanup — Tenant Settings vs Configure

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [Sprint 4 (UI hardening)](./embedded-mode-sprint-4-ui-hardening.md), [Sprint 5 (Buyer Routing UX)](./embedded-mode-sprint-5-buyer-routing-ux.md)
**Status:** Phase 1a + 1b + 4a + 4b landed; Phases 2, 3, 4c, 4d not yet started. Phase 4 reframed 2026-05-16 around instance-level capability flags (see Phase 4 below).
**Last updated:** 2026-05-16

## Why this sprint exists

The admin UI has two parallel configuration surfaces that drifted apart over the prior sprints:

1. **Top-bar `Configure` dropdown** — added during Sprint 5 as the umbrella for standalone configuration pages (Inventory: Browse / Profiles / Targeting / Sync; Buying: Buyer routing; Delivery: Webhooks; Workspace: Settings).
2. **`Settings` page (`/tenant/<id>/settings`)** — the original kitchen-sink tenant-config page with its own internal sidebar nav (Account, Ad Server, Policies & Workflows, Integrations, Publishers, Products, Inventory, Buyer Agents, Signing Keys, Danger Zone). Two entries (Setup Checklist, Users & Access) are already standalone-page links inside the sidebar.

Settings is **one entry inside Configure**, not a peer. But Settings still contains a half-dozen in-page sections that conceptually belong as Configure peers — and several of them duplicate concepts that already have a peer entry (e.g., Settings → Inventory vs Configure → Inventory; Settings → Buyer Agents vs Configure → Buyer Routing). The result is two competing IAs glued together: Configure peers for things promoted out of Settings, Settings sub-sections for things that never were.

Sprint 5 made this worse by half-promoting features (advertiser↔buyer-agent mapping moved from Settings → Advertisers to Configure → Buyer Routing) without removing the old surface — see `templates/tenant_settings.html:2096-2109` for the in-app "Advertiser mapping moved to Buyer Routing" banner that hints at the unfinished move.

This sprint completes the move: **everything that's an entity or distinct workflow becomes a peer page under Configure; Settings shrinks to just tenant-identity config and is hidden entirely on embedded tenants** (because tenant-identity is platform-managed in embedded mode).

## The endgame

```
Primary nav:  Dashboard | Media Buys | Products | Creatives | Workflows | Reports
Configure ▼
  Setup
    └─ Setup Checklist                       (standalone today)
  Inventory
    └─ Browse                                (standalone today)
    └─ Inventory Profiles                    (standalone today)
    └─ Targeting Criteria                    (standalone today)
    └─ Sync                                  (standalone today, hidden on embedded)
  Buying
    └─ Buyer Routing                         (standalone today)
  Delivery
    └─ Webhooks                              (standalone today)
  Workspace
    └─ Publishers                            (promoted by Phase 2)
    └─ Users & Access                        (standalone today)
    └─ Signing Keys                          (promoted by Phase 2; hard-hidden on embedded — Phase 4c)
    └─ Policies & Workflows                  (promoted by Phase 2; subsections capability-gated — Phase 4b)
    └─ Integrations                          (promoted by Phase 2; subsections capability-gated — Phase 4b)
    └─ Tenant Settings    ← hidden on embedded after capability flags collapse it (Phase 4d)
```

Sections that **leave Settings entirely** (folded into existing primary-nav pages, not promoted):
- **Products** (Settings sub-section) → fold into primary-nav `Products` page settings tab. The sub-section today is just "default product config"; Products page is the canonical surface.
- **Inventory** (Settings sub-section) → fold into Configure → Inventory group; the sub-section overlaps directly with the four existing Inventory peers.

Sections that **stay in Tenant Settings** (genuine tenant-identity config, all already gated on `not embedded_view`):
- Account (tenant name, subdomain, billing email)
- Ad Server (GAM credentials, network code, refresh token)
- Danger Zone (delete/deactivate)

Sections that **disappear** entirely (already hidden in embedded; redundant on standalone):
- Buyer Agents — Sprint 5 promoted advertiser↔buyer-agent mapping to Buyer Routing; the remaining Principal-admin (access tokens) can move to a small standalone "API Tokens" page under Workspace, or fold into Users & Access. Phase 2 decides.

## Why hide Tenant Settings entirely on embedded

After Phase 2 entity promotions, the three sections that remain in Tenant Settings (Account, Ad Server, Danger Zone) are all already individually `{% if not embedded_view %}` gated — they're tenant-identity config that the upstream platform owns via the Tenant Management API. Once those three are the only contents, hiding the page entrypoint in the Configure menu is the natural completion: no entry → no broken-feeling page where 100% of the content is "your platform manages this." It also removes the Phase 1a Buyer-Agents-style bug where stale internal links (e.g., setup checklist actions, in-app banners) point to a section that no longer renders.

The same logic applies to the new standalone Configure peer pages created by Phase 2 (Policies & Workflows, Integrations) whose subsections are individually gated by capability flags in Phase 4b — when every subsection inside them is owned by the storefront, the page renders empty and the nav entry should drop too. Phase 4d handles both.

## Phasing

This sprint ships in four phases, each independently mergeable. The order minimizes blast radius: small UI-text fixes first, entity promotions middle, fold-ins last.

### Phase 1a — Tenant Settings rename + Buyer Agents hide on embedded ✅ LANDED

**Shipped in PR `bokelley/embedded-buyer-agents-visibility`** — 2026-05-14.

Changes:
- `templates/base.html` — Configure menu entry renamed `Settings` → `Tenant Settings`.
- `templates/tenant_settings.html` — sidebar `Buyer Agents` nav tab and the entire `<div id="advertisers">` section gated on `{% if not embedded_view %}`.
- `tests/integration/test_embedded_ui_hardening.py` — `TestAdvertisersDirectoryReadOnlyOnEmbedded` → `TestAdvertisersDirectoryHiddenOnEmbedded`; flipped three assertions from visible-on-embedded to hidden-on-embedded; new docstring records the Sprint 7 rationale.
- `docs/design/embedded-mode-sprint-4-ui-hardening.md` — header status line marked partially superseded; new "Settings → Advertisers reversal (Sprint 7)" section at the end explaining why the Sprint 4 "read-only directory stays visible" call is reversed.

This reversed the Sprint 4 "the read-only directory stays visible permanently" call. Justification: Sprint 5 made Buyer Routing the canonical home for advertiser↔buyer-agent mapping, so the Settings → Buyer Agents tab on embedded became duplicate read-only data plus an informational banner — noise rather than a useful surface.

### Phase 1b — Audit cleanup ✅ LANDED

Shipped in the same PR. Catches Phase-1a-introduced inconsistencies in surfaces that link into the now-hidden Buyer Agents tab:

- `src/services/setup_checklist_service.py` — `principals_created` task skipped on embedded tenants in both `_check_critical_tasks` and `_build_critical_tasks`. Principal provisioning on embedded is platform-managed (Tenant Management API), so the task is not actionable by the publisher operator, and its `action_url` pointed at the now-hidden `/settings#advertisers` anchor.
- `tests/integration/test_setup_checklist_service.py` — new `TestSprint7PrincipalsCreatedHideOnEmbedded` class with two regression tests.

Surfaces left alone (deliberately not in scope for Phase 1b):

- `src/admin/services/tenant_status_service.py:_PLATFORM_KEYS_WHEN_MANAGED` — the `/status` JSON envelope still tags `principals_created` as `publisher` scope on embedded. Morally it should be `platform` on embedded, but `/status` is the external contract Storefront consumes. A scope-flip needs coordinated rollout. Tracked as Phase 1c (below) if/when needed.
- `templates/add_product.html:201` and `templates/add_product_gam.html:273` — "Add a buyer agent in Settings → Buyer Agents before restricting product access" copy is correct. It only renders in the `{% else %}` branch (not embedded AND no principals exist); on embedded the upper branch renders the right thing.
- `templates/tenant_settings.html:2115` — "This page (Settings → Buyer Agents) remains for managing the …" is now dead code on embedded (the whole `<div id="advertisers">` is hidden), but still accurate copy on standalone. Will be removed in Phase 4 when the section is folded out entirely.

### Phase 1c — `/status` envelope scope-flip (deferred)

Add `principals_created` to `_PLATFORM_KEYS_WHEN_MANAGED` in `src/admin/services/tenant_status_service.py` so the `/status` JSON correctly tags it as `platform` scope on embedded. This changes what Storefront (and other Tenant Management API consumers) see in their setup feed — coordinate the rollout with the upstream consumer before shipping.

### Phase 2 — Entity promotion to Configure peers

Promote the three remaining entity-shaped Settings sub-sections to standalone pages under Configure → Workspace:

| Entity | Today | After |
|--------|-------|-------|
| Publishers | `/settings#publishers` in-page section (~110 lines) | New blueprint + template at `/tenant/<id>/publishers`; Configure → Workspace entry |
| Signing Keys | `/settings#signing-keys` in-page section (~95 lines); already has deep-link affordances (`default_section == 'signing-keys'`) | New blueprint + template at `/tenant/<id>/signing-keys`; Configure → Workspace entry |
| Policies & Workflows | `/settings#business-rules` in-page section (~676 lines, complex form with multiple sub-forms: Budget Controls, Naming Conventions, Approval Workflows, Currency Limits) | New blueprint + template at `/tenant/<id>/policies`; Configure → Workspace entry. Multiple POST handlers — extract each as its own form action endpoint. |
| Integrations | `/settings#integrations` in-page section (~405 lines: Slack + Signals Agents) | New blueprint + template at `/tenant/<id>/integrations`; Configure → Workspace entry. Slack already POSTs to a dedicated `settings.update_slack` endpoint — extraction is clean. |

For each promotion:
1. Extract section markup to its own template (`templates/<entity>.html`).
2. Add Flask blueprint at `src/admin/blueprints/<entity>.py` with the GET handler and any section-specific POST handlers (most already exist).
3. Add Configure → Workspace nav entry in `templates/base.html`.
4. Delete the section from `templates/tenant_settings.html`.
5. Update tests in `tests/integration/test_embedded_ui_hardening.py` and `tests/integration/test_tenant_settings_comprehensive.py` to hit the new URL.
6. Update setup checklist `action_url` references (`_settings_url("publishers")`, `_settings_url("business-rules")`) to point at the new standalone routes.

Recommend shipping each entity promotion as its own PR to keep review focused.

### Phase 3 — Fold-in to existing primary-nav pages

- **Products section** → fold defaults config into the primary-nav `Products` page as a Settings tab.
- **Inventory section** → merge into Configure → Inventory (the four existing peers already own the entity model).

After Phase 3, Tenant Settings contains only: Account, Ad Server, Danger Zone.

### Phase 4 — Headless capability flags + selective section hide on embedded

**Why this changes from the prior spec.** "Hide Tenant Settings on embedded" was too coarse. Embedded mode is heading toward a *headless salesagent*: the storefront (Scope3 Interchange and any peer host) progressively absorbs every workflow that isn't ad-server-specific. The salesagent's job collapses to "execute against the ad server"; the storefront owns the business workflows on top of it.

The split is the inverse of what the original `embedded-mode.md` "publisher-managed vs platform-managed" table suggests. The right axis is **ad-server-specific vs ad-server-generic**:

| Stays in salesagent (publisher-managed in the salesagent tenant) | Migrates up to storefront over time |
|---|---|
| Ad-server credentials, network code | Creative approval workflow |
| Products (mapped to ad-server inventory) | Slack / task notifications |
| Principals / advertisers (mapped to ad-server companies) | Advertising policy, prohibited tactics/advertisers |
| Naming templates (produce ad-server order + line-item names) | Product ranking |
| Measurement providers (attached to ad-server line items) | Brand manifest |
| Inventory profiles + targeting criteria | Signals / creative agent allowlists |
| Publisher partnerships (publisher-of-record assertions) | AI services (storefront's own AI handles workflows it owns) |
| Currency limits (set at provisioning — ad-server contract) | |
| | **Dead on embedded regardless of storefront:** signing keys, OIDC config |

Different storefronts will absorb different workflows on different timelines. Scope3 may ship centralized creative approval before centralized Slack notifications; another storefront may do the opposite. The mechanism must let each capability flip independently. It is **instance-level, not per-tenant** — one embedded salesagent instance corresponds to one storefront operator, and the storefront decides once which workflows it owns across all of its tenants.

**Capability flags — instance-level config.**

New env var `EMBEDDED_CAPABILITIES`, JSON-encoded:

```bash
EMBEDDED_CAPABILITIES='{
  "creative_approval": "storefront",
  "slack": "storefront",
  "advertising_policy": "storefront",
  "product_ranking": "storefront",
  "brand_manifest": "storefront",
  "signals_agents": "storefront",
  "creative_agents": "storefront",
  "ai_services": "storefront"
}'
```

- Every capability defaults to `"publisher"` if unset.
- On open instances (`MANAGED_INSTANCE=false`), the variable is ignored entirely — `capability_owner()` always returns `"publisher"`, gating is a no-op.
- Existing embedded tenants get unchanged behavior at upgrade time; the storefront opts each workflow in by flipping the env var, no per-tenant migration needed.

Resolution helper (proposed location: `src/admin/utils/embedded.py`):

```python
def capability_owner(name: str) -> Literal["publisher", "storefront"]:
    """Returns 'storefront' if the embedded storefront owns this workflow,
    'publisher' otherwise. Always 'publisher' on open instances."""
    if not settings.managed_instance:
        return "publisher"
    return settings.embedded_capabilities.get(name, "publisher")
```

Exposed to Jinja as `{{ capability_owner('creative_approval') }}` plus a convenience: `{{ publisher_owns('creative_approval') }}` (sugar for `== 'publisher'`).

**Phase 4a — Capability infrastructure.** ✅ LANDED (2026-05-16)

- Parse `EMBEDDED_CAPABILITIES` into `settings.embedded_capabilities: dict[str, str]` at startup. Fail loud on malformed JSON.
- Add `capability_owner()` and `publisher_owns()` helpers; register both as Jinja globals.
- Unit tests: defaults, open-instance no-op, malformed JSON failure, individual capability resolution.
- No UI or template changes in this phase. Mergeable on its own.

**Phase 4b — Per-section flag gating.** ✅ LANDED (2026-05-16)

Wrap each migrating subsection in `{% if publisher_owns('<capability>') %}`. POST handlers reject writes with `403 Forbidden` and a banner ("This is managed by your platform") when the capability is `storefront`.

| Capability | Surface today |
|---|---|
| `creative_approval` | `tenant_settings.html#business-rules` → Creative Review subsection (lines 1452-1611) plus the Approval Workflow checkbox (lines 1441-1450) |
| `slack` | `tenant_settings.html#integrations` → Slack subsection (lines 2263-2312) |
| `advertising_policy` | `tenant_settings.html#business-rules` → Advertising Policy subsection (lines 1672-1720) |
| `product_ranking` | `tenant_settings.html#business-rules` → Product Ranking subsection (lines 1722-1776) |
| `signals_agents` | `tenant_settings.html#integrations` → Signals Discovery Agents subsection (lines 2652-2673) |
| `creative_agents` | `tenant_settings.html#integrations` → Creative Agents subsection (lines 2629-2650) |
| `ai_services` | `tenant_settings.html#integrations` → AI Services subsection (lines 2314-2627) |
| `brand_manifest` | (not yet rendered — gate the section when added) |

If Phase 2 has already promoted Policies & Workflows / Integrations to standalone Configure peer pages by the time Phase 4b lands, apply the same gates inside the new templates. Each test gets a pair: `capability=publisher → section visible and writable`; `capability=storefront → section hidden, POST returns 403`.

**Phase 4c — Unconditional removals on embedded.**

Three surfaces never make sense on embedded regardless of which storefront is the wrapper — no `"publisher"` answer is correct. No capability flag; hard gate on `not embedded_view`:

- **Signing keys** (`tenant_settings.html#signing-keys`, lines 2757+, plus its standalone Phase-2 page). The salesagent does not issue webhooks under its own domain in embedded mode; the storefront signs. Self-signing inside the storefront's perimeter is dead code.
- **OIDC blueprint** registration. Already unused on embedded per `embedded-mode.md`. Gate route registration on `not settings.managed_instance` (or at least the nav entry).
- **Buyer Agents tab.** Already shipped in Phase 1a — listed here for completeness.

**Phase 4d — Tenant Settings page collapse.**

After 4b + 4c land and the reference storefront has flipped its capability flags, the Scope3 embedded instance will see:
- Account, Ad Server, Danger Zone: already `not embedded_view` gated.
- All Business Rules subsections: capability=storefront → hidden.
- All Integrations subsections: capability=storefront → hidden.
- Signing Keys: hard-gated → hidden.

Result: the Tenant Settings page renders empty on embedded for the Scope3 deployment. Land the original spec's bullet now — gate the Configure → Workspace → Tenant Settings nav entry and the `/tenant/<id>/settings` route on `not embedded_view`. The same logic applies to any Phase-2-promoted standalone Configure peer page (Policies & Workflows, Integrations) where every subsection inside it has flipped to storefront: if all subsections are gated off, the page itself goes too.

Don't land 4d until 4b has shipped and the storefront operator has confirmed its capability set produces a complete empty-page state. Otherwise an embedded operator could lose access to a workflow the storefront hasn't actually absorbed yet.

**Migration safety.**

- New env var defaults to all-publisher → existing tenants behave identically at upgrade.
- Capability flips are env-var-level, reversible without code or DB changes.
- Phase 2 (entity promotion) and Phase 4 (capability gating) are independent. Phase 2 serves publishers who still own the workflow; Phase 4 hides UI for workflows the storefront has taken over. They compose: a Phase-2-promoted page can have Phase-4-gated subsections.
- The original spec's Constraint 4 ("Embedded operators must never lose access mid-phase") becomes "Embedded operators must never lose access to a workflow that hasn't actually been taken over upstream." Capability flag flips at the storefront's pace, not the salesagent release pace.

## Constraints during the rollout

1. **Don't break setup checklist links.** Every time a section moves to a new URL, the corresponding `_settings_url(...)` call in `src/services/setup_checklist_service.py` and the `_CONFIGURE_PATHS` entry in `src/admin/services/tenant_status_service.py` must update in the same PR. The structural guard at `tests/unit/test_architecture_obligation_coverage.py` doesn't catch this — coverage is purely on test assertions.

2. **Don't break in-app cross-links.** Search-and-update `url_for('tenants.tenant_settings', ...)` + `#<section>` anchors when sections promote. Current call sites (non-exhaustive): `templates/tenant_settings.html:2099` (the moved-to-Buyer-Routing banner), `src/services/setup_checklist_service.py` (action URLs), `templates/add_product.html:201` + `add_product_gam.html:273` (deep-link copy).

3. **Phase 2 + 3 are real form-extraction work.** Each in-page section has POST handlers that today share the `/tenant/<id>/settings` form-post infrastructure (CSRF, flash messages, redirect-back). Extracting cleanly means each new blueprint owns its own POST contract — verify against existing tests in `tests/integration/test_tenant_settings_comprehensive.py` and `tests/admin/test_comprehensive_pages.py` before deleting the source section.

4. **Embedded operators must never lose access mid-phase.** Phase 4 (hide Tenant Settings entirely on embedded) cannot land until Phase 2 promotions are complete and all embedded-accessible sections (Policies & Workflows, Integrations, Publishers, Signing Keys) have peer Configure entries. The fold-ins (Products, Inventory) are non-blocking — embedded tenants already access those concepts elsewhere.

## Cross-references

- [Sprint 4 (UI hardening) — Settings → Advertisers reversal](./embedded-mode-sprint-4-ui-hardening.md#settings--advertisers-reversal-sprint-7) — the Sprint 4 design decision this sprint reverses.
- [Sprint 5 (Buyer Routing UX)](./embedded-mode-sprint-5-buyer-routing-ux.md) — the half-completed promotion of advertiser mapping out of Settings, which motivated this sprint.
- `templates/tenant_settings.html` — the mega-page being decomposed.
- `templates/base.html` — the Configure menu being populated.

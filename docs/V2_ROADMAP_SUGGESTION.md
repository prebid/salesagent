# Prebid Sales Agent — v2.0 Roadmap Suggestion

**Date:** 2026-03-18 | **Status:** Draft for team discussion

---

## What We Agreed On (March 18 Meeting)

The goal is a stable, schema-compliant release that covers the core buyer-seller workflow: discover products, create/manage media buys, upload creatives, and report on delivery. The UI doesn't need to be perfect — it's easy to iterate later. Security has no known major blockers. Deployability (blank slate problem) needs attention but is secondary to protocol correctness.

**Where we stand today:** 14 of 37 in-scope AdCP operations implemented (38%). ~1,954 behavioral scenarios derived from the spec; 30% tested, 53% not implemented. Two critical PRs pending review ([#1138](https://github.com/prebid/salesagent/pull/1138) — adcp 3.10.0 migration, [#1146](https://github.com/prebid/salesagent/pull/1146) — BDD assertion overhaul). The full feature matrix is browsable at [main.d1tyvbf1at6iao.amplifyapp.com](https://main.d1tyvbf1at6iao.amplifyapp.com/).

---

## Immediate Actions (This Week)

1. **Review & merge [PR #1138](https://github.com/prebid/salesagent/pull/1138)** — aligns schemas with adcp 3.10.0. All 5 test suites pass. Without this, nothing downstream is spec-compliant.
2. **Review & merge [PR #1146](https://github.com/prebid/salesagent/pull/1146)** — fixes false-green BDD assertions. Without this, our test results overstate actual coverage.
3. **Create a GitHub milestone** for v2.0 and tag the issues listed below into it. Currently there are 0 milestones.
4. **Assign owners** to the four feature areas below. There's room for everyone — Post Industria, Chris, Sigma, Nicholas — but work must be coordinated against the same BDD scenarios.

---

## In-Scope Feature Areas

### 1. Account Management (Must Go First)

AdCP 3.10.0 makes `account` **required** on `CreateMediaBuyRequest` and `SyncCreativesRequest`. PR #1138 made it `Optional` as a stopgap. Until accounts are implemented, we're not schema-compliant — tests pass only because they don't enforce this.

**Spec:** [UC-011 — Manage Accounts](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-011) (55 BDD scenarios) | [Feature file](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-011-manage-accounts.feature)
**Schema:** `Account`, `ListAccountsRequest`, `SyncAccountsRequest`, `CreateMediaBuyRequest.account`

| Existing Issues | What's Missing |
|----------------|----------------|
| [#1012](https://github.com/prebid/salesagent/issues/1012) — Separate Agent and Account (foundation) | Admin UI for account management |
| [#1011](https://github.com/prebid/salesagent/issues/1011) — Implement list_accounts | BDD obligation docs for UC-011 |
| [#1029](https://github.com/prebid/salesagent/issues/1029) — Implement sync_accounts | Remove `Optional` workaround after accounts land |

**Dependency chain:** #1012 → #1011 → #1029 → make `account` required

### 2. Media Buy (Largest Effort — Refactoring Required First)

The core buyer workflow: create, update, query, package. Currently ~60% tested. The main blocker is `media_buy_create.py` — a **3,905-line file** with a 2,413-line god function, 14 separate DB transactions, and 3x duplicated logic for package creation, notifications, and audit logging. This must be decomposed before feature work can proceed safely.

**Spec:** [UC-002 — Create](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-002) (101 scenarios) | [UC-003 — Update](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-003) (80) | [UC-019 — Query](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-019) (44) | [UC-026 — Package](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-026) (79) | [UC-001 — Products](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-001) (40)
**Feature files:** [UC-002](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-002-create-media-buy.feature) | [UC-003](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-003-update-media-buy.feature) | [UC-019](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-019-query-media-buys.feature) | [UC-026](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-026-package-media-buy.feature)
**Schema:** `CreateMediaBuyRequest`, `UpdateMediaBuyRequest`, `GetMediaBuysRequest`, `PackageRequest`, `GetProductsRequest`

| Existing Issues (Bugs) | Existing Issues (Features) | Existing Issues (Refactoring) |
|------------------------|---------------------------|------------------------------|
| [#1089](https://github.com/prebid/salesagent/issues/1089) — date/budget sync broken | [#1075](https://github.com/prebid/salesagent/issues/1075) — idempotency key | [#1088](https://github.com/prebid/salesagent/issues/1088) — principal at boundary |
| [#1041](https://github.com/prebid/salesagent/issues/1041) — update broken w/ manual approval | [#1073](https://github.com/prebid/salesagent/issues/1073) — buying_mode refine | [#1078](https://github.com/prebid/salesagent/issues/1078) — error handling migration |
| [#1038](https://github.com/prebid/salesagent/issues/1038) — creative_ids don't sync to GAM | [#1026](https://github.com/prebid/salesagent/issues/1026) — CPA/TIME pricing | [#1119](https://github.com/prebid/salesagent/issues/1119) — repository pattern (292 violations) |
| [#1067](https://github.com/prebid/salesagent/issues/1067) — new product server error | [#1001](https://github.com/prebid/salesagent/issues/1001) — get_media_buy_artifacts | [#1108](https://github.com/prebid/salesagent/issues/1108) — DRY cleanup |
| [#1045](https://github.com/prebid/salesagent/issues/1045) — stale targeting values | | [#787](https://github.com/prebid/salesagent/issues/787) — service layer extraction |
| [#1034](https://github.com/prebid/salesagent/issues/1034) — custom_targeting_keys | | [#1069](https://github.com/prebid/salesagent/issues/1069) — decouple adapter execution |

**What's missing:** A single issue tracking the god function decomposition (no function >500 lines, no duplicated logic, ≤2 DB transactions). Also: 6 documented [spec divergences](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-002) in UC-002 that need fixing, and ~127 scenarios across UC-002/003/026 that need tests written.

### 3. Creatives

Sync creatives (UC-006) is already 97% covered — best in the project. The gap is **creative delivery** (UC-022): 78 scenarios, 100% not implemented.

**Spec:** [UC-022 — Creative Delivery](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-022) (78 scenarios) | [UC-006 — Sync Creatives](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-006) (111) | [UC-005 — Formats](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-005) (27) | [UC-018 — List Creatives](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-018) (53)
**Feature files:** [UC-022](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-022-creative-delivery-features.feature) | [UC-006](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-006-sync-creatives.feature)
**Schema:** `GetCreativeDeliveryRequest`, `SyncCreativesRequest`, `Creative`, `CreativeManifest`, `ListCreativesRequest`

| Existing Issues | What's Missing |
|----------------|----------------|
| [#1030](https://github.com/prebid/salesagent/issues/1030) — get_creative_delivery tool | Full UC-022 implementation (78 scenarios, extends #1030) |
| [#1092](https://github.com/prebid/salesagent/issues/1092) — async creative lifecycle | UC-018 test gaps (19 code_only scenarios) |
| [#947](https://github.com/prebid/salesagent/issues/947) — repeatable group assets | BDD obligation docs for UC-022 |
| [#363](https://github.com/prebid/salesagent/issues/363) — GAM native ad format | |

### 4. Delivery

Media buy delivery (UC-004) is 52% tested. Main gaps: creative-level breakdowns, date range filtering, forecasting.

**Spec:** [UC-004 — Delivery Metrics](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-004) (83 scenarios) | [UC-009 — Performance Feedback](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-009) (41)
**Feature files:** [UC-004](https://github.com/nickkua/adcp-req/blob/main/tests/features/BR-UC-004-deliver-media-buy-metrics.feature)
**Schema:** `GetMediaBuyDeliveryRequest`, `DeliveryMetrics`, `DeliveryForecast`, `ProvidePerformanceFeedbackRequest`

| Existing Issues | What's Missing |
|----------------|----------------|
| [#1028](https://github.com/prebid/salesagent/issues/1028) — delivery forecasting | 23 not-implemented UC-004 scenarios |
| [#1033](https://github.com/prebid/salesagent/issues/1033) — cursor-based pagination | |

---

## Out of Scope for v2.0

| Use Case | Scenarios | Reason |
|----------|-----------|--------|
| [UC-008 Audience Signals](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-008) | 78 | Dead code, deregistered ([#1003](https://github.com/prebid/salesagent/issues/1003)) |
| [UC-012 Content Standards](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-012) | 48 | Governance, lower priority |
| [UC-014 Sponsored Intelligence](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-014) | 101 | Entirely new protocol domain |
| [UC-016 Sync Audiences](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-016) | 51 | Depends on accounts, capability-gated |
| [UC-017 Account Financials](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-017) | 103 | Depends on accounts, capability-gated |
| [UC-020](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-020)/[021](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-021) Build/Preview Creative | 178 | AI agent tools, standalone not required |
| [UC-023 Sync Catalogs](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-023) | 78 | Capability-gated, not discussed |
| [UC-024](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-024)/[025](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-025) Content/Property Compliance | 152 | Governance features |
| [UC-007 Authorized Properties](https://main.d1tyvbf1at6iao.amplifyapp.com/#BR-UC-007) | 67 | Deprecated in v3.0.0-rc.1 |
| **Total** | **856** | BDD scenarios preserved for future releases |

---

## How We Work: BDD-Driven Development

Every feature in scope has BDD scenarios derived from the AdCP spec ([browsable](https://main.d1tyvbf1at6iao.amplifyapp.com/), [Gherkin source](https://github.com/nickkua/adcp-req/tree/main/tests/features)). These are the acceptance criteria.

- **Touching a feature = bring its BDD pass rate up.** No PR that decreases the passing scenario count.
- **No code without a BDD scenario.** If a scenario is wrong, raise an issue against adcp-req — don't skip it.
- **Target: 95% BDD coverage** on in-scope features. The 5% exception is infrastructure code that doesn't map to protocol behavior.
- **Tests are wired against real infrastructure** (PostgreSQL, adapters), not mocked. Integration tests, not unit tests with mocks.

---

## Also Tracked Separately

- **Security:** 7 issues ([#1127](https://github.com/prebid/salesagent/issues/1127)–[#1133](https://github.com/prebid/salesagent/issues/1133)), assigned to Sigma. Partially addressed by merged [#1103](https://github.com/prebid/salesagent/pull/1103) and [#1125](https://github.com/prebid/salesagent/pull/1125).
- **Admin UI:** Acceptable for now. One auth blocker: [#1123](https://github.com/prebid/salesagent/issues/1123) (OIDC callback 404).
- **Deployability:** Blank slate problem — onboarding friction for publishers. Needs new issues for automated inventory-to-product mapping, templates, GAM report analysis.
- **Additional protocol features** (post-core): [#1076](https://github.com/prebid/salesagent/issues/1076) device targeting, [#1074](https://github.com/prebid/salesagent/issues/1074) AI provenance, [#1031](https://github.com/prebid/salesagent/issues/1031) daypart targeting, [#997](https://github.com/prebid/salesagent/issues/997) property list CRUD, [#1027](https://github.com/prebid/salesagent/issues/1027) conversion tracking.
- **Refactoring debt:** 12 structural guards with allowlisted violations. [Full list of refactoring issues](https://github.com/prebid/salesagent/issues?q=is%3Aopen+label%3Arefactor).

---

## Suggested Sequence

```
Week 0:   Merge PRs #1138 + #1146, create milestone, assign owners
Week 1-2: Account management (#1012 → #1011 → #1029)
Week 1-3: Media buy refactoring (god function → services) [parallel with accounts]
Week 3-5: Media buy bugs + scenarios, creative delivery, delivery gaps [parallel tracks]
Week 5-6: BDD import for remaining UCs, deployability, docs
```

This is a suggestion — the sequence depends on who picks up what. The non-negotiable part is: **accounts before media buy features** (schema compliance), and **refactoring before media buy scenarios** (test reliability).

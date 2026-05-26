# SpringServe Adapter

Connect the Prebid Sales Agent to Magnite's SpringServe ad server for
direct-sold CTV, online video, and audio inventory.

> **Why direct-to-ad-server (not via the SSP)?** Magnite runs an AdCP seller
> agent on the Magnite SSP. Routing direct-sold campaigns through the SSP
> agent imposes SSP fees. This adapter talks to SpringServe's ad-server API
> directly, preserving the publisher's direct-sold economics. The first
> production customer is Talpa (Netherlands) for audio inventory across
> Radio 538 / Sky Radio / Radio 10, with video expansion across the
> SBS6 / Net5 / Veronica portfolio as the strategic priority.

## Status

| Stage | Goal | Status |
|---|---|---|
| 1 | Skeleton + auth + dry-run | ✅ shipped |
| 2 | Live Campaign + Demand Tag create | 🟡 code complete, **blocked on write scope grant** |
| 3 | Creatives (incl. audio MIME negotiation) | 🟡 code complete, **blocked on write scope grant** |
| 4 | Reporting cache + sync | 🟡 code complete, **blocked on reporting scope grant** |
| 5 | Inventory cache + admin UI + typed embedder config | 🟡 code complete, **blocked on supply-side read scope grant** |

See `.context/springserve-adapter-plan.md` for the full plan, risks, and
open questions.

## Entity mapping (Mapping A)

| AdCP entity | SpringServe entity | Endpoint |
|---|---|---|
| MediaBuy | Campaign | `POST /api/v0/campaigns` |
| Package | Demand Tag | `POST /api/v0/demand_tags` (one per package, `campaign_id` parent) |
| Creative (asset) | Video / Audio Creative | `POST /api/v0/videos` (MP4 upload OR remote URL) — OR — VAST tag URL on the demand tag |
| Targeting | Demand-tag fields | `PATCH /api/v0/demand_tags/{id}` |
| Delivery / reporting | Reporting API | `POST /api/v0/report` |
| Inventory taxonomy | Supply Tags + Supply Partners | `GET /api/v0/supply_tags`, `GET /api/v0/supply_partners` |

SpringServe has no "Insertion Order" layer above Campaign — the Campaign IS
the buy. We do not synthesise one.

## Authentication

Two paths, exactly one required:

1. **Email + password (canonical).** Set `email` + `password` in the
   adapter config. The transport mints a token at `POST /api/v0/auth` on
   first use, caches it with a 2-hour TTL, and refreshes on 401 or expiry.

2. **Pre-minted API token (escape hatch).** Set `api_token` in the adapter
   config. Useful when a partner provides a token out-of-band. No
   auto-refresh — rotate manually when the 2-hour TTL expires.

> SpringServe uses the raw token in the `Authorization` header — NOT
> `Authorization: Bearer <token>`. The transport handles this correctly;
> don't try to "fix" it.

## Capabilities

- **Pricing models:** CPM, FLAT_RATE
- **Channels:** OLV, CTV, streaming audio, podcast
- **Targeting:** geo countries / regions / DMAs, device types, player sizes,
  environments, supply-tag inclusion (postal targeting NOT supported —
  use DMAs or regions)
- **Delivery measurement:** SpringServe-native

## Audio support

Audio tag delivery is exposed as the canonical `audio_vast` format. In
`demand_class=tag` mode, `create_media_buy` requires each package to carry
exactly one assigned VAST/DAAST creative; the adapter injects that creative's
URL into SpringServe's `vast_endpoint_url` before creating the Demand Tag.

Hosted audio (`audio/mp4`, `audio/mpeg`, ≤500 MB) is not buyer-facing yet.
SpringServe's API shape suggests hosted audio can live on the same creative
records as video, but we keep `audio_15s` / `audio_30s` / `audio_60s` hidden
until upload and bind behavior is proven against a live account.

## Scope coverage (live probe, 2026-05-14)

Token mint succeeds; per-endpoint scope on the operator's test account:

| Endpoint | Method | Status | Verdict |
|---|---|---|---|
| `/auth` | POST | ✅ 200 | Token mint works (2-hour TTL) |
| `/campaigns` | GET | ✅ 200 | Stage 1 reads unblocked |
| `/campaigns` | POST | ❌ 403 | **Stage 2 write scope needed — `"You are not authorized to access this page."`** |
| `/demand_tags` | GET | ✅ 200 | Stage 1 reads unblocked |
| `/demand_tags` | POST | ❌ 403 | **Stage 2 write scope needed (same grant covers both)** |
| `/videos` | GET | ✅ 200 | Stage 3 reads unblocked; POST scope TBD |
| `/supply_tags` | GET | ❌ 403 | **Stage 5 blocked — request supply-side read scope** |
| `/supply_partners` | GET | ❌ 403 | **Stage 5 blocked — same grant unblocks both** |
| `/report` | GET | ⏳ 404 | POST-only endpoint; probe shape replaced with real POST in Stage 4 |

### Scope grants to request from SpringServe support

The Talpa API user has read scope on Campaigns / Demand Tags / Videos but
no write scope and no supply-side read scope. Open a SpringServe support
ticket asking for the following on the API user attached to the Talpa
demand partner (88061):

1. **WRITE scope on Campaigns + Demand Tags + Videos** — unblocks Stage 2
   live `create_media_buy` end-to-end (Stage 2 code is complete and
   verified by mocked unit tests; the live cycle test skips with a clear
   message until this grant lands).
2. **READ scope on Supply Tags + Supply Partners** — unblocks Stage 5
   (inventory cache + admin product config UI).
3. **Reporting API access** — covered in Stage 4; ask for it at the same
   time to avoid a second round-trip.

## Rate limits

SpringServe enforces 240 req/min per account on the general API and 10 req/min
on the Reporting API. The transport surfaces 429 as `SpringServeRateLimitError`;
the inventory and reporting sync jobs (Stages 4–5) will respect these limits.

# Handoff — merging `main` into `feat/rfc9421-request-signing`

**Branch:** `merge/main-into-rfc9421` (worktree `/srv/ws/salesagent/a3-merge-main`)
**Not** fast-forwarded onto `feat/rfc9421-request-signing`.
**State:** 21 commits, tree clean. The 3 `[e2e_rest]` failures are FIXED and
mutation-verified. **A different blocker took their place: the 8-worker in-network gate
now WEDGES.** See §0.

| suite | last known result |
|---|---|
| unit | 7490 passed, 0 failed (local) |
| integration | 0 failed (local, in the combined 5935-passed run) |
| bdd_inprocess | 0 failed (local) |
| **bdd_e2e** | **589 passed, 0 failed** — in-network, single-server, local (was 586 / 3) |
| e2e / admin / ui | last green at `sa-e80d3f9d` / `test-results/innet_040926_1206/` |

---

## 0. GREEN — all seven suites, zero failures, at `f6dd2ad6b`

| suite | passed | failed | errors | run |
|---|---|---|---|---|
| unit | 7735 | 0 | 0 | `innet_060926_1850` |
| integration | 3511 | 0 | 0 | `innet_060926_1922` |
| bdd_inprocess | 2444 | 0 | 0 | `innet_060926_1850` |
| bdd_e2e | 588 | 0 | 0 | `innet_060926_1850` |
| e2e | 161 | 0 | 0 | `innet_060926_1850` |
| admin | 138 | 0 | 0 | `innet_060926_1850` |
| ui | 5 | 0 | 0 | `innet_060926_1850` |
| security-audit | OK | | | both |

Two runs, ONE tree (`f6dd2ad6b`, nothing committed between them). The first got six suites
green; its `integration` collected ZERO tests — it aborted when a co-tenant saturated the
box and the live server's `/health` stopped answering — so it was re-run alone and came
back `exit=0`, 3511 passed. Cassini's own verdict on the first run was "NOT the tests — 0
failures/errors across 7 suite(s)".

**Read the counts, not the exit code.** A contended run can exit non-zero having proven
nothing; cassini says so rather than publishing stale numbers, and a suite reporting
`0 collected` is a suite that did not run.

### The box is shared — check before believing a red

```bash
ssh hetzner2-vm "docker ps --format '{{.Names}}' | sed -E 's/^(sa-[a-z0-9]+).*/\1/' | sort -u; free -g | sed -n 2p; uptime"
```

Contention produced ALL of the following at some point, none of them real: `bdd_inprocess
exit -9` (OOM kill), `unit exit 3` (pytest INTERNALERROR, zero FAILED rows), `integration`
collection abort (`E2E_BASE_URL ... /health did not answer` plus a consequent "Different
tests were collected between gw5 and gw2"), and two `test_outbound_http.py` TIMING rows.

Separately, the box's rootless Docker hit the kernel keyring cap
(`unable to join session keyring ... disk quota exceeded`, uid 1003 at 200/200 keys) and
could not start ANY container until `kernel.keys.maxkeys` was raised. If `docker run
hello-world` fails on the box, that is the first thing to check — the leak that fills it
is still unfixed.

### Four real defects fixed, in dependency order

Each was hidden by the one before it; all were latent until the first was fixed, because
until then no webhook delivery ever left the server over `e2e_rest`.

1. **`868f9c06b`** — a gate reading a flag nothing set (our gate + #1802's new issuing
   path — a semantic merge conflict no textual conflict could show), two capture addresses
   for one endpoint, and a header-blind reader over a service that was never header-blind.
   Mutation-verified on all three legs.
2. **`0464a9504`** — the delivery-report sender omitted the required `idempotency_key`
   (webhooks.mdx :195/:253, graded by `webhook-emission.yaml`); the admin trigger reported
   "Sent" for a webhook refused before any connection.
3. **`721843f67`** — a DB transaction held open across the entire delivery INCLUDING the
   retry ladder, deadlocking the harness's per-scenario TRUNCATE. Violated the repo's own
   #1757 rule from behind a comment claiming the opposite. Diagnosed from
   `pg_stat_activity` (`idle in transaction` + two `Lock/relation` waiters, server at 0.01%
   CPU), not inferred.
4. **`45a331a4b`** — the same TRUNCATE could still lose an ordinary lock-order race;
   bounded retry on a DETECTED deadlock only, everything else still raises.

Plus `4fe71c41e` (park the circuit-breaker scenarios — see §0.5) and `9b9a5a990` (a guard
meta-test was planting a specimen in the real `src/` while a parallel worker scanned it).

---

## 0.5 CAPABILITY GAP — `webhook_activity[]` is not implemented

Not a test concern; a missing AdCP capability. `get_media_buys` takes
`include_webhook_activity: true` and returns `webhook_activity[]` per media buy. The
pinned request schema states the purpose in its own words:

> "Used by **buyer agents** to verify whether a publisher actually fired against the
> buyer's registered endpoint and what the endpoint returned — **closes the
> operator-ticket loop** for webhook debugging."

`webhooks.mdx :528` says the same from the buyer's side ("no operator ticket required").
So this is a buyer self-service surface we owe, and `grep -c webhook_activity src/` is
**0**.

**Cost is small, because we already persist almost all of it.** `webhook_delivery_log`
maps nearly 1:1 onto `core/webhook-activity-record.json`: `webhook_url`->`url`,
`sequence_number`, `notification_type`, `attempt_count`->`attempt`, `status`,
`http_status_code`, `error_message`, `payload_size_bytes`, `response_time_ms`,
`completed_at`, `created_at`->`fired_at`. Two real gaps:

* **`idempotency_key`** — REQUIRED by the record schema. We now generate it per delivery
  (commit `0464a9504`) but do not store it on the log row.
* **one record per attempt** — the spec says sellers MUST emit one record per attempt;
  we write one row carrying `attempt_count`.

Plus the normative 30-day retention, and the `propagation_surfaces` opt-out: a seller not
declaring `webhook` MUST omit the field entirely rather than return a short window.

**Why it matters beyond the capability itself.** It is the ONLY spec-defined observable
for webhook delivery behaviour, and it is an AdCP READ SURFACE — so a Then can assert on
it identically over mcp / a2a / rest / e2e_rest, with no cross-process poking and no
test-only endpoint. It is what the parked circuit-breaker scenarios (§0) should be
rewritten against, at which point all five `E2EUnsupportedSetup` declarations come out.
Shape they should take:

```gherkin
Given a media buy "mb-001" with an active reporting_webhook
And the webhook endpoint returns 500
When the seller has attempted delivery 5 times
Then webhook_activity contains 5 records for that idempotency_key
And every record has status "failed" with http_status_code 500
```

Every clause is a spec obligation with a citation, and every one is readable through a
production API a real buyer calls.

Storyboard: `webhook_activity` appears in `dist/compliance/3.1.1/` only in the creative
lifecycle scenarios — this delivery-report use is UNGRADED, so the obligation is the
prose plus the schema.

---

## 1. Merge status

Five upstream PRs merged in dependency order (#2091, #1941, #1858, #1802, #2141), one
merge commit each with a resolution ledger. Zero merge-created test losses, proven by
bare node-id comparison against both parents.

**`origin/main` has since moved 5 commits ahead of the merge base** — it was an ancestor
when the merge was made and is not one now. Decide whether to pull those in before the
fast-forward:

```bash
git log --oneline $(git merge-base origin/main HEAD)..origin/main
```

**Central design decision** (owner-approved): the RFC 9421 arm was wired *into* the egress
seam rather than kept as a second boundary in front of it. `outbound_http.py` shipped with
a `sign:` hook that had zero callers; that hook is what signing now uses.

---

## 2. The three `[e2e_rest]` failures — SOLVED

Root-caused by driving the in-network stack locally and reading the SERVER's log (the
runner cannot see why a delivery did not happen). It said
`Cannot trigger report: No reporting_webhook configured for mb-001` — the delivery never
started. Three defects in a chain, each hidden by the one before it; see commit
`868f9c06b` for the full account. Verified by mutation on all three legs (wrong-but-
present bearer token, dropped 9421 signing arm, wrong legacy HMAC secret — each turns
its leg RED). Note the server holds the module in memory: a mutation needs a container
restart, and the first attempt without one survived and proved only that.

## 3. What was done this session

Four commits, each verified before the next.

### `b2453c063` — posture declared through the writer that owns it

Replaced the two hand-rolled posture pokes the previous handoff flagged as "may be lying".
**The previous handoff's diagnosis was wrong and its prescribed fix does not work.** It
proposed `SigningConfig.verifier_enabled=False` everywhere on the grounds that a declared
`supported: false` under-declares an agent-level fact. It does not: the pinned
signed-requests storyboard gates all 28 negative vectors on `request_signing.supported: true`
alone, so a seller advertising `false` is OUTSIDE the rule (security.mdx :1465 routes it to
log-and-alarm). The `agent_level_posture` warning is about the DEFAULT for a tenant that
declared nothing — a different case.

What was actually wrong: both pokes reimplemented `BaseTestEnv.declare_request_signing(bucket="unsupported")`
badly — bare `{"supported": false}` with no derived `identity.brand_json_url`, skipping
`ensure_declarable_identity_host`. A single-label `virtual_host` derives `http://`, which
REFUSES the whole declaration and silently returns every operation to the `supported`
bucket. A declaration that fails open is worse than none.

**Two levers, and the split is measured:**

* `tests/bdd/conftest.py` `@egress` routes → `declare_request_signing`. Those scenarios carry
  **15 e2e_rest params**, 5 of which register webhook credentials, and that server is a
  separate process — a config patch in the runner cannot reach its verifier, but the
  declaration can (the writer uses the env's own session, bound to the live server's DB).
* `tests/integration/test_webhook_hmac_credentials_ingest_refusal.py` → new
  `inbound_verifier_disabled` (`tests/helpers/signing.py`), autouse. Every transport that
  module dispatches on is in-process. It **cannot** use the declaration:
  `ensure_declarable_identity_host` assigns the shared constant `SIGNING_AGENT_HOST` and
  `virtual_host` is uniquely indexed, so the one test that opens three envs dies on a
  `UniqueViolation`.

Each helper's docstring names the other and says why it is not usable there.

### `0cfe0ae8e` — wire-presence from the declaration, not the transport enum

Closes `test_no_step_module_keys_behavior_on_transport_impl`; `tests/bdd/steps/` no longer
names `Transport.IMPL`. `_wire_body` now delegates to `_wire_or_none` — the positive
`TransportResult.has_wire` predicate that was sitting in that module UNCALLED, waiting for
this caller.

The old guard had two defects in one expression: it inferred wire-presence from transport
IDENTITY, and a ctx carrying the IMPL member with no `TransportResult` reached the
serializer with no dispatcher having declared anything — GH #1744's hole in a second
spelling. Contract tests moved with it and got stronger; `TestUnsetTransportIsNotImpl`
became `TestNoDeclarationIsNotADeclaredAbsence` and gained
`test_a_transport_key_alone_does_not_buy_the_fallback`.

`Transport.IMPL` itself still exists (69 refs, mostly integration suites that legitimately
dispatch it). The enum's own deletion is the remainder of salesagent-a1-uc004/1210.

### `0464a9504` — the delivery-report sender must carry `idempotency_key`

**The previous handoff's §4 "contradiction" (status 200 + `captured == 0`) does not
reproduce** — the test it was observed on passes; commit `8cc1ccb9d` landed after the
observation. Measuring the four that remained gave two ordinary causes.

1. **Production gap, fixed in production.** The three senders disagreed:
   `protocol_webhook_service` merged the key at the seam call, `order_approval_service` put
   it in the payload upstream, `webhook_delivery_service` passed the payload UNTOUCHED
   behind a comment asserting a "dispatch-level value" does not belong in a request body.
   The pin says otherwise: `docs/building/by-layer/L3/webhooks.mdx` @ v3.1.1 :195 ("Every
   webhook payload carries a required `idempotency_key`") and :253, which names THIS
   sender's events ("For delivery-report data events such as `scheduled`, `final`,
   `delayed`, and `adjusted` … dedupe the transport event with `idempotency_key`"). Graded
   by `dist/compliance/3.1.1/universal/webhook-emission.yaml` step `idempotency_key_presence`.
   `tenant_id` is routing and stays off the body; the key is part of the document.

2. **Production right, test re-graded.** `AuthenticationScheme` @ v3.1.1 is exactly
   `["Bearer", "HMAC-SHA256"]`, and #1802's seam answers a stored `hmac-sha256`/`bearer`/
   `basic` row with `refused_auth`/`scheme_not_in_spec` before serializing. Three rows
   asserting the opposite moved to
   `test_a_scheme_outside_the_pinned_enum_is_refused_not_downgraded`, which grades the
   refusal on three axes (zero captures, `delivered is False`, the refusal announced naming
   the scheme) — each alone vacuous, together ruling out the silent downgrade.

Plus three missed sites of classes already fixed: the retired dict passed where a typed
`WebhookTaskContext` was expected (`test_signing_capability_honesty`); an expected
`SignalsAgent` leaving `tenant_id` at its default, which is the field `config_for` reads to
SELECT THE SIGNING KEY; and an allowlist that went stale and empty when both uc011 bypasses
were extracted into `_self_dispatch_list`.

### `637071300` — the invoice-recipient scenario xpassed on a refusal it does not grade

`T-UC-003-ext-t` XPASSED on `[mcp]` alone. Not a graduation — the xpass was vacuous. The
scenario asked only for "fails / VALIDATION_ERROR / has a suggestion", and on MCP an
unrelated refusal arrives: `update_media_buy` types its parameters, so the spec-defined
`invoice_recipient` field is rejected as an "Unexpected keyword argument" before any
authorization check runs.

The field is NOT over-specified against the pin — it is a top-level property of
`update-media-buy-request.json` @ v3.1.1, so refusing it for its shape is itself the gap.
Per the xpass-graduation protocol the scenario was corrected first:
`And the suggestion should contain "authorized"` pins the refusal to BR-RULE-214's own
POST-F3, which a schema-shape rejection cannot say. All three transports now xfail on the
real gap; the ledger entry is unchanged.

---

## 4. Tooling — read this, it will save you hours

### Use cassini. Do not grind locally.

```bash
cd /srv/ws/salesagent/a3-merge-main
cassini run --fail-if-running     # detached; all 7 suites on the box
cassini status sa-<id>            # reconciles + prints the per-suite table
```

`cassini status` blocks for a while on a running job — give it a `timeout` and re-poll
rather than assuming it hung.

### Local DB

```bash
.claude/skills/agent-db/agent-db.sh up > /tmp/a3db.env   # then `source /tmp/a3db.env`
```

Shell state does not persist between tool calls; sourcing a file is what makes it repeatable.

### Traps that cost real time

- **BDD locally: run serial** (`-n 0`). xdist deadlocks on a single agent-db. `-p no:xdist`
  does NOT work — it breaks `tests/bdd/scenario_liveness.py`'s hook registration.
- **`tests/integration/test_creative_agent_live.py` errors locally** (20 of them) — it needs
  the full stack. Clean on cassini. Not real.
- **`run_all_tests.sh` leaves `test-results/` root-owned**, which breaks its own JSON report
  extraction and local BDD runs. Script bug still unfixed.
- **`--showlocals` (`-l`) is the fastest diagnostic here**, and `--lf` after a long suite
  re-runs only the failures in seconds.
- A failure that reproduces in isolation is a different animal from one that only appears in
  the full run. Two of the five integration failures passed under `--lf`; both were
  order-dependent (see the signing-provider cache note in memory).

---

## 5. Recommended order

1. §0 — the wedge. It is the only thing between this branch and a green gate.
2. `cassini run`, confirm green.
3. Consider graduating the e2e_rest scenarios that now xpass (16 -> 17), ONE AT A TIME
   under `.claude/rules/workflows/xpass-graduation.md`. Deliberately not done in §3's
   change; several of them only xpass because deliveries became observable, which is
   exactly the situation that protocol exists to inspect rather than rubber-stamp.
4. Decide on the 5 new `origin/main` commits (§1).
5. **Only then** fast-forward `feat/rfc9421-request-signing` to this branch.

Do not fast-forward while red — it buries the remaining work in a branch that looks finished.

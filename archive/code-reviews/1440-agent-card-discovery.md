# Orientation — #1440 (HALF: canonical agent-card paths only)

Measured on `fix/1440-fix-a2a-agent-card-discovery-serves-only-one-non` @ `1d93e3721`,
2026-09-01. All numbers below were run, not recalled.

## Scope (from SOURCE.md, not from the issue body)

IN: `/.well-known/agent.json` and `/agent.json` must serve the card.
OUT: the v0.3 dual-emit (second `AgentInterface` at `protocol_version=0.3`) —
decided against 2026-08-31, issuecomment-5476083375. Prior implementation exists
at `c5379a515`; **do not cherry-pick**.

Note the issue BODY predates that decision and still prescribes the dual-emit
(its "Implementation guidance" bullet 1, and its Verification line
`len(supportedInterfaces) >= 2`). SOURCE.md and the 2026-08-31 comment supersede
the body. Verification for this branch is 200-on-three-paths only.

## Defect reproduced at this head (not just the issue's `19d3efd54`)

```
/.well-known/agent-card.json  200  3585 bytes
/.well-known/agent.json       404
/agent.json                   404
WARNING src.app:_replace_routes: expected SDK routes not found for paths:
        ['/.well-known/agent.json', '/agent.json']
```

Site: `src/app.py:397-417`. `_replace_routes()` swaps handlers for paths already
in `app.routes`; `create_agent_card_routes()` (a2a-sdk, `AGENT_CARD_WELL_KNOWN_PATH
= '/.well-known/agent-card.json'`) mounts exactly one. The other two members of
`_AGENT_CARD_PATHS` (`src/app.py:394`) are only warned about.

## Spec grounding

Pin: `adcp==6.6.0` → spec **3.1.1** (`pyproject.toml:10`, `docs/adcp-spec-version.md:3`).
`dist/docs/` at tag `v3.1.1` has no `3.1.1` dir; newest prose is `3.1.0`.

- **Mandate**: `dist/docs/3.1.0/building/by-layer/L0/a2a-guide.mdx:782` —
  "A2A agents advertise capabilities via Agent Cards at `.well-known/agent.json`."
  Example at `:898` fetches `https://sales.example.com/.well-known/agent.json`.
- **The other path** is the A2A-1.0 SDK constant `/.well-known/agent-card.json`
  (`a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH`), already served.
- **`/agent.json` (root)** is named by neither the pinned AdCP prose nor the
  a2a-sdk. Its only warrant in-repo is `_AGENT_CARD_PATHS` itself. See Open
  question 1.
- **Graded?** **Ungraded.** `dist/compliance/3.1.1/` contains no step referencing
  an agent card or a well-known path (grep over the whole compliance tree: zero
  hits). Discovery is a runner *precondition*, not a storyboard check.

## Coverage that exists (counts)

- **BDD: 0 scenarios.** No feature file mentions an agent card or a well-known
  agent path (only `brand.json` / `adagents.json` / `adcp` property URLs do).
  42 feature files; the discovery UC (`BR-UC-010`, 78 scenarios) is about
  `get_*` capability tools, not card fetch. Nothing to graduate.
- **e2e_rest ledger**: 137 lines, none about the card.
- **Storyboard ledger**: exactly **1** a2a entry,
  `a2a::_runner::agent_reachability::graded_checks_produced`
  (`tests/storyboard/known_failures.txt:222`).
- **Unit**: `tests/unit/test_a2a_transport_contract.py` — 5 card tests, all
  hitting `/.well-known/agent-card.json` only (L151, L157, L508, L517, L524).
  `tests/unit/test_fastapi_app_regression.py:187,209` — 2 card tests, same path.
- **e2e**: `tests/e2e/test_a2a_endpoints_working.py` — 4 live-card tests
  (L37, L130, L148, L161), all `/.well-known/agent-card.json`.

## Two things measurement turned up

1. **The app already advertises the 404.** `src/landing/landing_page.py:235,252`
   builds `{base}/.well-known/agent.json` and
   `src/landing/templates/tenant_landing.html:276` renders it as a clickable
   link. `tests/unit/test_virtual_host_landing_page.py:86,125,138` asserts that
   link. So a green unit test today asserts we publish a path that 404s.
2. **Two security assertions are vacuous.**
   `test_fastapi_app_regression.py:207-208,225+` assert
   `"passwd" not in card.get("url", "")`. The served card has **no top-level
   `url`** (keys verified: capabilities, defaultInputModes, defaultOutputModes,
   description, documentationUrl, name, skills, supportedInterfaces, version).
   The injected host would land in `supportedInterfaces[0].url`. Both asserts
   pass on `""` and would pass on a successful injection. Same routes we touch.

## Route ordering — the issue's warning does not apply

The issue says new routes must "sit before the admin catch-all mount".
`_install_admin_mounts()` (`src/app.py:62-88`) runs in the **lifespan** hook and
re-appends `Mount("/")` last, after stripping prior copies. At import time the
route table ends at `/debug/root-logic` with no `/` mount. Appending card routes
in `_replace_routes()` is therefore safe. (My TestClient probe ran without
lifespan, which is why the 404s came from Starlette's default, not Flask.)

## Files the change touches

- `src/app.py:394-419` — `_AGENT_CARD_PATHS` / `_replace_routes()`: create the
  missing routes with the same `dynamic_agent_card` endpoint; retire or invert
  the permanent warning.
- `tests/unit/test_a2a_transport_contract.py` — parametrize the card tests over
  all served paths; assert byte-identical bodies.
- `tests/e2e/test_a2a_endpoints_working.py` — the file #1440's sequencing note
  names as shared; add the live 200-on-all-paths check.
- `tests/unit/test_fastapi_app_regression.py` — de-vacuum the two injection
  asserts (point them at `supportedInterfaces[*].url`).

## Core Invariant

**Every path the app advertises as agent-card discovery serves the same
dynamically-host-derived card, byte for byte, from one handler — no path is
declared without being routed, and no route carries a second copy of the card
logic.**

## What this change does NOT do

It will not graduate `a2a::_runner::agent_reachability::graded_checks_produced`.
The runner's base url is `http://proxy:8000`; `buildCardUrls()` will now get 200
on `/.well-known/agent.json` instead of falling through, but its bundled
`@a2a-js/sdk 0.3.14` reads a **top-level `url`**, which the 1.0-shaped card still
does not carry (verified above). The ledger entry stays failing, and the
narrative at `known_failures.txt:195-215` stays correct — its phrase "the
card-discovery fix that made this axis gradable" means the **dual-emit**
(`c5379a515`), not the path fix. Do not claim graduation; do not let a green
storyboard expectation be written on this.

## Open questions (would change the approach)

1. **Serve `/agent.json` at root, or drop it from `_AGENT_CARD_PATHS`?**
   SOURCE.md's Verify step says all three return 200, so the default is: serve
   it. But nothing in the pinned AdCP prose or the a2a-sdk asks for a root-level
   card, and it is one more permanent public path. The issue itself left this
   open (its "Decisions needed" #3). Proceeding as: serve all three, per
   SOURCE.md.
2. **Warning → assertion, or delete?** SOURCE.md offers both. Once
   `_replace_routes()` creates what it cannot find, "missing" can only mean a
   path in `_AGENT_CARD_PATHS` that failed to be built — which is a programming
   error, so a hard failure at import is defensible. Preference: make the
   post-condition an assertion over the final route table, so it can actually
   fail.

---

# Addendum — four follow-up questions, measured 2026-09-01

## Q1. What does the standard actually say?

Three authorities disagree, and the disagreement is the reason this ticket exists.

| Authority | Path it names | Evidence |
|---|---|---|
| **A2A spec** (`a2aproject/A2A`, `docs/specification.md` §8.2 Discovery Mechanisms) | `/.well-known/agent-card.json` — **only** | "Accessing `https://{server_domain}/.well-known/agent-card.json`". `/.well-known/agent.json` is not mentioned at all, not even as deprecated. §14.3 registers the well-known URI. |
| **a2a-sdk (python, ours)** | `/.well-known/agent-card.json` | `a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH` |
| **a2a-js 0.3.14 AND 1.1.0** | `/.well-known/agent-card.json` | `AGENT_CARD_PATH = ".well-known/agent-card.json"` in both |
| **AdCP pinned prose (3.1.0, shipped at tag v3.1.1)** | `/.well-known/agent.json` | `building/by-layer/L0/a2a-guide.mdx:782` + example at `:898` |
| **@adcp/sdk 11.0.0 runtime** | tries `agent.json` **first**, then `agent-card.json` | `dist/lib/utils/a2a-discovery.js`: `A2A_CARD_PATHS = ['/.well-known/agent.json', '/.well-known/agent-card.json']`, commented "**current A2A spec**" / "**legacy**" |

**@adcp/sdk's comment is inverted relative to the A2A spec.** `agent.json` is the
historical pre-0.3 path; `agent-card.json` is what the spec and every A2A SDK
(both languages, both major versions) actually use. AdCP's own guide follows the
historical spelling, and AdCP's own SDK probes it first.

Practical consequence for us is unchanged and unambiguous: **both must be served**,
because both are read by a client we are graded by. Neither is safe to drop.

**`/agent.json` at root has no authority at all.** RFC 8615 well-known URIs live
under `/.well-known/`; a root `/agent.json` is not one. It appears in the A2A
spec, in neither SDK, and in no AdCP prose — its only warrant is
`_AGENT_CARD_PATHS` itself and SOURCE.md's "assert 200 on all three". Open
question 1 stands, now with the evidence that nothing external asks for it.

## Q2. Where is the card generated, and why there?

Two places, and the split is deliberate:

1. **`create_agent_card()` — `src/a2a_server/adcp_a2a_server.py:2177`.** Built once
   at import (`src/app.py:298`). Name, version, skills, the AdCP `AgentExtension`
   (uri keyed on `get_adcp_spec_version()`), and one `AgentInterface` whose url is
   the static `get_a2a_server_url()` fallback. This is the *template*.
2. **`_create_dynamic_agent_card(request)` — `src/app.py:346`.** Per request:
   `CopyFrom` the template, then overwrite `supported_interfaces[0].url` from
   `Apx-Incoming-Host` / `Host` / `X-Forwarded-Proto`.

**Why:** we are multi-tenant with subdomain routing behind a proxy. A card baked
at import cannot know which host the buyer used, and an agent card whose url
points at the wrong host is useless. But `create_agent_card_routes()` (the a2a-sdk
route factory) accepts only a *static* card — so `_replace_routes()` swaps the
SDK's static handler out for the dynamic one, matching **by path**.

That swap-not-create design **is** the defect: `_AGENT_CARD_PATHS` declares three
paths, the SDK only ever creates one, and a replace loop has nothing to replace
for the other two.

## Q3. Is this connected to the signing-key trust root in #1757?

**Same namespace and same ordering constraint; different routes; no functional
dependency. But there is a real conflict surface and a pattern worth copying.**

Measured on `fork/feat/rfc9421-request-signing`:

- It adds `src/routes/well_known.py` — an `APIRouter` serving
  `/.well-known/{brand,adagents,jwks,governance-revocations}.json`, wired at
  `src/app.py:581` with the comment: *"Registered at import time so it is matched
  before `_install_admin_mounts()` re-appends the Flask '' catch-all at lifespan
  startup."* That independently confirms the ordering analysis above.
- **It does not fix #1440.** `_AGENT_CARD_PATHS` (`:500`) is byte-identical to
  main — same three paths, same replace-only loop, same permanent warning.
- **It does not put signing material in the agent card.** The card gains no
  signature, no jwks pointer, no new capability. The trust root is discovered by
  its own well-known paths, not through the card.
- **It does rewrite the function next door.** `_create_dynamic_agent_card` on that
  branch gained `_canonical_a2a_url()` / `_card_with_url()` and a DB read, and
  `dynamic_agent_card` became `await asyncio.to_thread(...)`. That is the same
  ~40-line region we are editing → expect a conflict when the two lines meet.
  Our edit should stay inside `_replace_routes()` and not touch
  `_create_dynamic_agent_card`, to keep the conflict trivial.

Worth considering: whether the card paths should move into that same
`well_known_router` rather than staying a hand-rolled route-list splice. That is a
design question for the plan, not a fact.

## Q4. Can we pin the runner's `@a2a-js/sdk` to 1.x?

**No. It is not a version-range problem — the API `@adcp/sdk` calls was deleted in 1.0.**

- `@a2a-js/sdk` is a **peerDependency** of `@adcp/sdk`, not a transitive dependency
  (`package-lock.json:76`). So we *could* force any version via npm `overrides`.
  The issue comment's phrasing ("pulled in transitively") is imprecise on this
  point; npm 7+ auto-installing the peer is what produced 0.3.14.
- `@adcp/sdk 11.0.0` does `require('@a2a-js/sdk/client')` and calls
  **`A2AClient.fromCardUrl(cardUrl, { fetchImpl })`** (`dist/lib/protocols/a2a.js:277`).
- **`fromCardUrl` does not exist anywhere in `@a2a-js/sdk` 1.1.0.** Grepped the
  whole published `dist/` — zero hits. 1.x replaced it with a multi-transport
  `Client` / `TransportFactory` / `ClientConfig` construction API. Forcing 1.1.0
  makes the runner throw on the first A2A call.
- **Upstream has not moved either.** `@adcp/sdk@13.0.0` (latest, released since the
  2026-08-31 comment) still declares `"@a2a-js/sdk": "^0.3.13"` as its peer. The
  recommendation in that comment — raise it upstream — is still open and still
  correct.

**The exact rejection, for the record** (`v0.3.14 dist/client/index.js:349-352`):

```js
if (!agentCard.url) {
  throw new Error("Provided Agent Card does not contain a valid 'url' for the service endpoint.");
}
```

Path-independent. Serving `/.well-known/agent.json` changes which URL returns 200;
it does not add a top-level `url`. **Confirms: this branch cannot graduate the a2a
ledger entry.**

### What could actually unblock a2a grading, ranked

1. **Patch the runner** (`patch-package`, precedent exists at
   `tests/storyboard/runner/patches/@adcp+sdk+11.0.0.patch`): make `@adcp/sdk`
   fall back to `supportedInterfaces[0].url` when the card has no top-level `url`,
   and construct `new A2AClient(cardWithUrl)`. Local, reversible, no production
   surface. **Note 1.1.0 also ships `@a2a-js/sdk/compat/v0_3/client`** — a compat
   client that is not what `@adcp/sdk` imports, but which shows upstream intends
   this bridge to exist on the client side.
2. **Fix upstream** — port `@adcp/sdk`'s a2a client to the 1.x API, or have it read
   `supportedInterfaces`. Fixes every agent the runner grades, not just ours.
3. **Dual-emit the card** — rejected 2026-08-31; adds permanent legacy surface to
   production for one outdated test client. Still the wrong trade.

None of the three is in this branch's scope. Recording so the option isn't
rediscovered as an oversight.

---

# Addendum 2 — the upstream fix already exists (measured 2026-09-01)

**Your instinct was right: it was filed and implemented. It is just not released
to `latest`.**

## The evidence (npm registry manifests, checkable)

```
@adcp/sdk dist-tags:  latest = 13.0.0    beta = 14.0.0-beta.25
```

| version | `@a2a-js/sdk` peer | published |
|---|---|---|
| 13.0.0 (**latest stable**) | `^0.3.13` | 2026-08-16 |
| 13.0.0-rc.26 | `^0.3.13` | — |
| 14.0.0-beta.10 | `^0.3.13` | 2026-08-25 |
| **14.0.0-beta.11** | **`^1.0.1`** | **2026-08-26** |
| 14.0.0-beta.25 | `^1.0.1` | 2026-08-31 |

The bump landed in **`14.0.0-beta.11` on 2026-08-26 — five days BEFORE** the
2026-08-31 comment on #1440 that recommended "raise it upstream at
adcontextprotocol/adcp". Nobody checked the `beta` dist-tag.

## It is a real port, not a range bump

`@adcp/sdk@14.0.0-beta.25`, `dist/lib/protocols/a2a.js`:

```js
var import_client = require("@a2a-js/sdk/client");
...
const factory = new import_client.ClientFactory({ ... });   // 1.x API
const legacyA2AClientTestShim = { fromCardUrl: defaultTestFromCardUrl };  // test-only
```

`A2AClient.fromCardUrl` is gone from the production path, replaced by
`ClientFactory` — the 1.x construction API. `fromCardUrl` survives only as a
`NODE_ENV === "test"` shim.

## And it fixed the path ORDER too

```js
// @adcp/sdk 11.0.0
const A2A_CARD_PATHS = ["/.well-known/agent.json", "/.well-known/agent-card.json"];
// @adcp/sdk 14.0.0-beta.25
const A2A_CARD_PATHS = ["/.well-known/agent-card.json", "/.well-known/agent.json"];
```

Flipped to A2A-spec order — `agent-card.json` first, which is the path we
**already serve**.

## What this changes

Under `@adcp/sdk >= 14`, the a2a conformance axis becomes gradable against this
agent **with no production change at all** — not the dual-emit, and not even the
canonical-path fix. The runner's first probe hits the path we already serve, and
the 1.x client reads `supportedInterfaces`.

Consequences:

1. **Do not file a new upstream ticket.** It is done. Track `14.0.0` reaching
   `latest` instead (upstream release trains: PR #7007 open, "Version Packages
   (beta)"; #6808 open, stable).
2. **The 2026-08-31 decision against the dual-emit is now permanently correct**,
   and for a stronger reason than it gave: the sole identified consumer of the
   top-level `url` fixed itself upstream. Nothing should reopen it.
3. **#1440's canonical-path half still stands on its own merits** — the AdCP
   guide names `/.well-known/agent.json` (`a2a-guide.mdx:782`), the tenant
   landing page publishes a clickable link to it
   (`src/landing/landing_page.py:235`), and it 404s. That justification is
   independent of the runner and unaffected by this finding.
4. **The patch-package bridge is now an interim with a known expiry**, and the
   alternative is a runner dependency bump. `14.0.0` is a breaking major that
   will also move the `mcp` axis (93 graded checks) and churn
   `tests/storyboard/known_failures.txt` — a separate ticket, not this branch.

---

# Addendum 3 — the runner patch, as built (2026-09-01)

`tests/storyboard/runner/patches/@a2a-js+sdk+0.3.14.patch` (new, untracked).

**Patched `@a2a-js/sdk`, not `@adcp/sdk`.** `@adcp/sdk` calls
`A2AClient.fromCardUrl` at three sites across two files
(`protocols/a2a.js:277`, `core/SingleAgentClient.js:429` and `:2676`);
`@a2a-js/sdk` has exactly one owner of the concept — the `A2AClient`
constructor. One helper there fixes all three call sites and any future one.
Both build outputs (`dist/client/index.cjs`, `dist/client/index.js`) get the
same helper at both of their throw sites; the runner only loads the `.cjs`, but
leaving the ESM build inconsistent is a trap for no saving.

**The field is `protocolBinding`, not `transport`** — verified in both
`@a2a-js/sdk@1.1.0`'s `AgentInterface` d.ts and our own
`a2a.types.AgentInterface` (`['url', 'protocol_binding', 'tenant',
'protocol_version']`). Values are an open string, core set JSONRPC / GRPC /
HTTP+JSON. Our card omits it (protobuf drops the empty default), so the helper
treats an absent binding as JSON-RPC.

**Verified** (`node -e` against the installed, patched package):

| case | result |
|---|---|
| 1.0 card, `supportedInterfaces` only (what we serve) | resolves `http://proxy:8000/a2a` |
| two interfaces, GRPC first | picks the JSONRPC one |
| legacy 0.3 card with top-level `url` | top-level still wins |
| card with neither | still throws the original error |

Survives `rm -rf node_modules && npm ci` — both patches re-apply
(`@a2a-js/sdk@0.3.14 ✔`, `@adcp/sdk@11.0.0 ✔`).

**Not verified end to end.** Whether the a2a axis actually grades needs the
in-network storyboard job (compose `tests` service against `proxy:8000`); that
is what `storyboard-conformance` in CI exists to answer.

**Expected CI consequence** — `a2a::_runner::agent_reachability::graded_checks_produced`
XPASSes and the axis's real checks arrive un-ledgered and red. Designed
behaviour of `_no_graded_checks`, not a regression. Tracked:

- `salesagent-0yvxe` (P2) — triage the newly-graded a2a checks, graduate the
  reachability ledger entry.
- `salesagent-mkn0l` (P3) — evaluate `@adcp/sdk` 14.x and delete this patch.

**No upstream ticket filed** — the fix already exists upstream (Addendum 2);
filing would ask for work that is done and awaiting a stable release.

---

# Addendum 4 — the verification run, and what it found instead (2026-09-01)

Ran `./run_all_tests.sh storyboard` in-network via cassini (`sa-94579f36`) — the
exact command the CI job runs. Result:

```
exit=0
storyboard  1 passed  1 skipped  (2 collected)
skipped: test_storyboard_check[environment-not-configured]
         reason: config: missing env: STORYBOARD_COMPLIANCE_DIR, STORYBOARD_SCHEMA_ROOT
```

**It did not grade the patch. It has never graded anything.**

`_missing_env()` (`test_storyboard_conformance.py:119-120`) gates collection on
both vars being non-empty. `tox.ini:223-224` defaults both to `""`. The CI job
(`ci.yml:393-465`) sets neither — it relies on `_adcp-bundle` extracting into the
bind-mounted tree, and the action's `bundle-dir` output is never mapped to env.
`grep -rn STORYBOARD` over every yml/yaml/env/sh/ini in the repo returns
`tox.ini` only; `git log -S"STORYBOARD_COMPLIANCE_DIR" -- .github/workflows/ci.yml`
returns zero commits — it was never there.

So `_bundle_path()`'s derivation (env → in-repo bundle → `~/projects/adcp`) is
unreachable in any automated context: the gate short-circuits first. `_missing_env`
landed with `1d93e3721` (#1858), the commit that landed the job.

Consequence: the ledger's 96 mcp checks and its one a2a entry were seeded from a
hand-run session with the vars exported. Since then the job has graded nothing and
reported green — the silent-skip failure the module's own comments are written
against. **Every conclusion drawn from a green storyboard-conformance run since
#1858 is unsupported**, and the a2a patch cannot be observed by CI until this is
fixed. Filed as `salesagent-bim1k.4` (P0), blocking `salesagent-0yvxe`.

The patch itself is unaffected — its four verified behaviors stand. What is
unverified is only whether the a2a axis grades end to end, and that is now
blocked on .4 rather than on the patch.

# The egress gateway and the SDK boundary

This document describes where the gateway is, what the `adcp` SDK owns, what this
repo implements on top, and which parts of that are temporary.

Read it when you are changing the gateway, or deciding whether a concern
belongs here or upstream. The rule for anyone who only needs to make a request —
which entry point to call, the supplement ranges, the test-only override, and the
three layers that stop a raw HTTP client — is
[Outbound egress: one gateway](../security/outbound-egress.md). That document
states each of those once; this one does not restate them.

## Contents

- [The module map](#the-module-map)
- [What comes from the SDK](#what-comes-from-the-sdk) — what `adcp` owns and this repo never reimplements
- [Workarounds carried until upstream provides them](#workarounds-carried-until-upstream-provides-them) — two dated workarounds, each with a named retirement condition
- [The validation split: two verdicts, one predicate](#the-validation-split-two-verdicts-one-predicate) — `check_registration` versus `resolve_for_dial`, and the edge cases around them
- [Provenance is carried, not decided](#provenance-is-carried-not-decided) — `UrlProvenance` as a type, not an inference
- [Retry and backoff belong to the gateway](#retry-and-backoff-belong-to-the-gateway) — including why the gateway calls the signer inside the retry loop
- [One vocabulary for webhook auth](#one-vocabulary-for-webhook-auth) — `AuthenticationScheme` comes from the spec, not a local choice
- [Address logic stays in the egress package](#address-logic-stays-in-the-egress-package)
- [A destination is a typed constant, never an environment read with a URL default](#a-destination-is-a-typed-constant-never-an-environment-read-with-a-url-default)
- [Replace a validator without opening a gap](#replace-a-validator-without-opening-a-gap) — ordering rules for deleting or swapping an egress guard
- [Decide where a concern belongs](#decide-where-a-concern-belongs) — the questions to ask, in order
- [Related](#related)

## The module map

```
src/core/security/
  outbound_http.py          the gateway. send / asend. the only public entry point
  egress/
    policy.py               the address and scheme verdicts, and the one predicate they share
    attempts.py             the retry schedule as pure values — no I/O, no httpx
    response.py             OutboundResult — the closed response type consumers read
    destination.py          the typed record of where a URL came from, at construction time
```

Two sibling gateway modules build on this package rather than living in it:
`src/core/utils/mcp_client.py` (MCP connections, pinned through
`guarded_client_factory`) and `src/core/security/webhook_egress.py` (signed
webhook delivery, transmitting the signed bytes through `send` / `asend`).

Each module owns exactly one decision, and the split is deliberate:

- **`attempts.py` has no `httpx` import and does no sleeping.** It is a state
  machine returning retry / success / terminal, which `send` and `asend` drive
  *identically* — one loop definition, so the sync and async paths cannot
  drift apart. The two differ in five lines, all of them I/O verbs, and
  `asend`'s docstring states the count, so a sixth difference reads as a
  policy decision written twice.
- **`response.py` closes the response type.** `OutboundResult` exposes no
  live `httpx.Response`: a result that leaks the raw response lets call
  sites program against httpx with the import ban still satisfied — true and
  useless at the same time. It replicates `httpx.Response.text`'s decode rule
  with the same stdlib primitives httpx uses, so the two call sites that read
  `.text` see byte-identical decoding. The closed type is what gives "no raw
  egress" its meaning.
- **`destination.py` answers a different question from `UrlProvenance`, at a
  different moment.** `UrlProvenance` answers *"who do I blame in this
  refusal"* when a connection attempt fails, and deliberately never carries the
  URL; `VendorConstant` answers *"where in source did this constant come from"*
  when a call site builds a URL, and does carry it. A call site may
  legitimately use both.

One thing in `policy.py` belongs to neither verdict: `RESERVED_TLDS` and
`is_reserved_tld_host`, the DNS-free answer to "can this hostname ever name a
real host?" for the RFC 2606 / RFC 6761 reserved TLDs. Both verdicts *accept* a
URL under one — `buyer.example.com` fixtures have to register. The single
reader is the notification activation prover
(`src/services/notification_proof_service.py`), which treats such a host as
unprovable. It sits here because it is the same kind of value as the hostname
blocklist and because `ruff-egress.toml` names this module as the owner of host
classification. Do not fold it into the address predicate.

## What comes from the SDK

The `adcp` SDK owns address validation and connection pinning. This repo
imports these mechanisms; it never reimplements them:

| From `adcp` | What it owns |
|---|---|
| `signing.resolve_and_validate_host` | resolve once, classify the resolved address, block the cloud-metadata set whatever `allow_private` says |
| `signing.SSRFValidationError` | the SDK's refusal — translated here, never leaked |
| `signing.IpPinnedTransport` / `AsyncIpPinnedTransport` | connecting to the address the SDK validated |
| `types.*`, `types.generated_poc.*` | the wire schema |
| `canonical_formats` | format identity |
| `webhook_receiver.verify_webhook_hmac` | the AdCP 3.x HMAC fallback verification |
| `types.AuthenticationScheme` | the single webhook auth vocabulary — see [One vocabulary for webhook auth](#one-vocabulary-for-webhook-auth) |

Because the SDK resolves once and pins that IP into the transport, the address
it validated is the address the transport connects to. Any code that validates a
hostname and then hands the *hostname* to a client has reintroduced
DNS-rebinding, however thorough its checks are —
[Outbound egress](../security/outbound-egress.md#why-a-gateway-rather-than-a-helper-everyone-calls)
carries the argument. Treat it as the reason "add a check here" is a different,
broken design rather than a smaller version of the right one.

### One banned SDK symbol

`adcp.webhooks.get_adcp_signed_headers_for_webhook` is on the TID251 ban list.
It discards the body bytes it signs, so its own documented usage — sign, then
send `json=payload` separately — reintroduces the signed-bytes-versus-wire-bytes
divergence. Sign and send through the gateway with the signed byte string instead.

That an SDK ships an unsafe helper is consistent with "the SDK owns this": the
SDK owns address validation and pinning, which is a narrower claim than every
helper in it being safe. Treat the SDK as authoritative where it is the
mechanism, and as a cross-check elsewhere.

## Workarounds carried until upstream provides them

The gateway carries two workarounds, both marked in the source. Each is dated
and has a named retirement condition.

### Five of the six supplement ranges

```python
# FIXME(adcontextprotocol/adcp-client-python#974): drop this whole
# frozenset (except the CGNAT entry above) once we adopt a release
# that carries these ranges upstream.
```

`_SUPPLEMENT_NETWORKS` holds 6to4 relay, AS112-v4, AMT, AS112 direct, and
ORCHIDv2 only until the SDK classifies them. **CGNAT
`100.64.0.0/10` stays**, because AdCP 3.1.1 names it explicitly as a range a
fetcher MUST reject.
[Outbound egress: the supplement ranges](../security/outbound-egress.md#the-supplement-ranges-and-the-check-no-configuration-relaxes)
lists the six with their RFCs.

Retirement: adopt the release, delete the five, keep CGNAT, and confirm that
the range-to-URL table in `tests/integration/test_outbound_http.py` still covers
the production set exactly — `test_the_supplement_oracle_table_covers_the_production_set_exactly`
checks both directions, so removing a range without removing its row fails, and
adding a range without adding a row fails too.

This repo defers the version bump deliberately: it is a major version jump, and
the owner's call was that it is not worth the risk for this alone.

### Operator agent connections do not use the SDK client

`creative_agent_registry` and `signals_agent_registry` reach an
operator-configured agent through `src/core/utils/operator_mcp.py::call_operator_mcp_tool`,
which dials `mcp_client.call_mcp_tool` — a real MCP handshake that is IP-pinned
and redirect-refusing — rather than `adcp.ADCPMultiAgentClient`.

The reason is concrete rather than stylistic: adcp 6.6.0 exposes no transport
injection point, so the SDK client builds its own connection — following
redirects included — and that connection passes through **none** of this
application's egress policy. No module under `src/` imports either SDK client;
the TID251 rows exist so that stays true.

Retirement: **adcp-client-python#1004**, which adds the injection point. The
citation sits at `src/core/creative_agent_registry.py` and
`src/core/signals_agent_registry.py`, where the code makes the choice, not only
here.

## The validation split: two verdicts, one predicate

Callers ask the gateway two different questions at two different times, so there
are two verdicts:

| | `check_registration` | `resolve_for_dial` |
|---|---|---|
| When | A URL is **stored** (webhook registration) | Something is **about to connect** |
| DNS | None — never resolves | Resolves once, pins the result |
| Catches | A literal `10.0.0.1`, a bad scheme, a blocked hostname | What a name actually points at |
| Cannot catch | What `evil.example.com` resolves to | Nothing it was given a chance to see |
| Returns | Nothing; raises `AdCPBlockedUrlError` | A `PinnedHost`, so the caller does not resolve twice |
| `allow_private` | **No such parameter** | Test-only override, and it never opens the supplement set |

They read the **same** `_blocked_address` predicate, the same hostname
blocklist, and the same scheme rule. That is the point: two independently
maintained copies of "what is a bad address" is the defect this module exists
to prevent, and the reason the registration and connection checks cannot drift
into disagreement. The flag half of `_blocked_address` mirrors
`resolve_and_validate_host`'s own rejection set rather than a superset invented
here, which is what makes "registration verdict equals dial verdict" true by
construction. On the registration path, which never reaches the SDK, that half
is the only thing still refusing a literal `127.0.0.1`.

Registration is DNS-free **on purpose**, and it is worth stating why.

It runs when a buyer hands you a URL and there is no request to attach a refusal
to yet. A registration-time resolution is never binding: DNS moves between
registration and the first connection, so the gateway must *accept* a hostname
that is public but unresolvable, and re-check it when it actually connects to
the callback. Resolving at storage time is a side effect of storing data that
proves nothing.

The admin route that registers a principal's webhook runs the same single
validation path every protocol surface runs, deliberately giving up
registration-time DNS — two gates that can disagree give the same URL two
verdicts, depending on which one answers.
`tests/integration/test_admin_ingest_url_policy.py` pins the decision: it
asserts that registration makes **zero** resolver calls, and it records what
the trade costs (the repo stores a hostname that resolves into RFC 1918, and
refuses it at dial time instead). Reinstating resolution has to be a deliberate
act with a failing test in front of it, not an innocent-looking "validate
earlier" change.

That technique generalizes: when you keep a behavior out on purpose, pin its
absence with a test — an asserted absence makes the decision defend itself.

### Open question: the loopback rescue

`check_registration(url, *, allow_loopback=False)` carries one relaxation: the
loopback allowance for capture servers, threaded in by
`webhook_validator._adcp_testing()`, which reads the typed
`settings.loopback_webhooks_allowed` (`ADCP_TESTING`) rather than the
environment directly. The settled part is its narrowness, and the docstring of
`_is_rescuable_loopback` (`egress/policy.py`) states it well: the rescue is a
**post-check over the hostname and literal-IP refusal branches only**, never a
flag threaded into `_blocked_address` — a threaded flag rescues every supplement
range and every RFC 1918 address at registration, not only loopback. It never
rescues a scheme refusal, which `check_registration` evaluates first and
unconditionally.

The unsettled part: the rescue is structurally close to an SSRF trust bypass of
the form "trust whatever hostname a setting names" — no helper of that form,
such as `_is_trusted_test_host`, exists here, deliberately. There is a real
distinction — the code re-derives the loopback property structurally from the
URL, rather than reading it from configuration — but no commit states that
distinction as a decision. Until that sentence exists, do not widen
`allow_loopback`, and do not "harmonize" it toward a trusted host named by
configuration.

A trap for anyone touching the tests: `tests/conftest.py` sets `ADCP_TESTING`
on for the whole suite, and the address cases in
`tests/unit/test_webhook_security.py` (`test_blocks_localhost`,
`test_blocks_127_0_0_1`, and the RFC 1918 cases) switch it back off through an
autouse fixture so they grade the address policy instead of the allowance.
`TestLocalhostAllowanceUnderTestingMode` grades the allowance on its own.

### One place re-validates a stored row

A related split follows the same reasoning about who is present to fix a
problem: **ingest validates, rehydration does not.** Reading a stored
registration carries the document through the library type with
`model_construct` (`src/core/webhooks/registration.py`), nested models
included, so the value is always typed while its contents are unvalidated.

At ingest a buyer is present to correct a rejection. A stored row has no buyer,
is *already delivering*, and the delivery path fails closed on its own. Routing
the whole document through the validating model instead stops already-delivering
rows over values that stored data can carry — a short `token`, a malformed
`operation_id`, fields the earlier gate ignored.

The exception is the authentication block, and it is principled: `from_stash`
validates that block explicitly through `PushAuthentication`, the same type the
egress seam validates against, so ingest, delivery, and rehydration cannot hold
three answers to "is this signed?". A stored block the pinned type refuses
therefore stops delivering: `HMAC-SHA256` with no usable secret, a credential
under the spec's 32-character minimum, a non-canonical spelling such as
`hmac-sha256`, an unrecognized scheme, an empty `schemes` array. The refusal
names the offending sub-field and records the stored scheme in
`details.rejected_value`, so an operator can enumerate the affected rows. The
delivery seam reaches the same verdicts from the stored primitives in
`_authentication_or_refusal`, returning a `refused_auth` outcome whose reason
(`no_credentials`, `credentials_too_short`, `scheme_not_in_spec`,
`multi_scheme`, `no_scheme`) is the one greppable key for that row.

`from_stash`'s own docstring still describes the block as unvalidated and lists
those shapes as ones that keep delivering. The body is the authority: it calls
`PushAuthentication.model_validate` on the block. Treat the docstring's list as
history until someone reconciles it.

### Refusals say nothing

AdCP 3.1.1 `building/by-layer/L1/security.mdx` point 6: a fetcher MUST never
echo the refusal cause back to the party that supplied the URL. A per-cause
message at the buyer surface lets whoever supplied the URL scan the network it
refused to reach.

This is structural, not conventional. A refused URL raises
`AdCPBlockedUrlError` — an alias for `AdCPUrlNotAllowedError`, which emits the
published `VALIDATION_ERROR`. That constructor takes no `message`
parameter in any spelling: the buyer-facing sentence is the code's own
`CODE_TABLE` entry, read as a property, so a second wording of the refusal is
unrepresentable. What the buyer gets is the code plus `field`, the request path
that names which input to fix. The cause is not lost — every verdict logs a
fixed label at WARNING beside the refusal, and the URL reaches that log through
`webhook_url_for_log`, the one sanitizer, which drops credentials and query.

**Do not assert on refusal message text in tests**; assert on the code, the
`field`, and at most on structured details carried with the error.

### Exception text never reaches stored or buyer-visible fields

The same disclosure rule extends past refusal messages: never put a caught
exception's text into a persisted or buyer-visible field on an egress path.
An `IpPinnedTransport` `RuntimeError` names both the pinned host and the host
it refused, and the delivery log records a sender's `detail` verbatim in
`webhook_delivery_log.error_message` and emits it as an audit warning — so
`detail=str(e)` discloses a destination into durable storage.

Senders use `WebhookDeliveryOutcome.unexpected(exception_type)`
(`src/core/webhooks/delivery.py`) — a named constructor carrying the
exception **type** only. The exception's own message still reaches the
operator in the adjacent log line, which is the right place for it. The
docstring states the residual risk and accepts it knowingly: the outcome is
a public frozen dataclass, so a caller can still construct `detail=str(e)` by
hand — the sanitized form is the named and convenient one, not the only
expressible one.

## Provenance is carried, not decided

`UrlProvenance = CounterpartyUrl | OperatorEndpoint` (`outbound_http.py`).
`send`, `asend`, and `validate_url` all take
`provenance: UrlProvenance | None` — whose URL this is, as a **type**, not an
inference from whether some optional string happened to be `None`. A call
site passes a `UrlProvenance` and chooses the member deliberately, because the
two members produce genuinely different buyer-facing outcomes
(`src/core/helpers/outbound_error_mapping.py`):

- `CounterpartyUrl` re-raises the gateway's own classification unchanged. The
  refusal stays correctable and buyer-facing.
- `OperatorEndpoint` yields `CONFIGURATION_ERROR`, terminal, naming a **role**
  rather than an address — the buyer did not choose that address and cannot
  fix it. Its constructor rejects any name containing `://`, so a caller cannot
  smuggle a URL through as an operator label.

The field exists because of the opaque refusal: the message says nothing about
the cause on purpose, so `error.field` is the **only** channel that can name
the offending input — without it the error is correctable in name only. And
the gateway cannot compute that path itself: it sees a URL string, never a
request document, and the namespace differs per call site. Hence carried, not
decided.

`_checked_field(provenance, url)` guards the leak direction: `field` is
buyer-visible, so a call site that passes the URL — or anything containing it
— as the field bypasses the opacity that the message maintains. It
**raises** rather than quietly dropping the value, because naming a URL where
a request path belongs is a call-site bug worth surfacing, not one worth
shipping as a silently fieldless envelope. The containment check runs in the
leak direction only: a fixed field constant appearing inside a
buyer-controlled URL must receive the policy verdict, not a manufactured
`ValueError`.

Never fabricate a path. When a URL comes from the request document but has no
canonical path, construct `CounterpartyUrl` **without** a field — a
made-up locator such as `creative:{id}.agent_url` names a path that does not
exist in the pinned request schema. And choose the member deliberately, never
by default: a fallback connection path that re-classifies a buyer-supplied URL
as operator configuration on a cache miss turns a correctable refusal into a
terminal one.

Two public helpers keep the union's meaning in one place: `refusal_field(provenance)`
is the field-or-nothing derivation, and `is_counterparty(provenance)` is the
predicate itself, typed as a `TypeIs` so it narrows both branches and is a
drop-in for the `isinstance` it replaces. Call sites that raise their own error
at the locator a gateway refusal carries (`creative_agent_registry`)
read from these rather than re-deriving the URL's ownership.

## Retry and backoff belong to the gateway

Retry, backoff, and `Retry-After` handling are gateway policy. An outbound
call site passes `max_attempts` and lets the gateway sleep; a geometric sleep
anywhere else under `src/` fails `make quality`. Anyone tuning backoff edits
`attempts.py`.

The schedule lives in `src/core/security/egress/attempts.py`
(`_backoff_seconds`, `_wait_seconds`, `Attempts`), **not** where a reader
expects; `outbound_http.py` re-exports `_RETRYABLE_STATUSES` and
`_MAX_HONOURED_RETRY_AFTER_SECONDS` as a test-facing facade with no `src/`
consumer. The public gateway surface is `sleep_backoff(attempts)` and
`terminal_client_error_status(exc)`. Retryable statuses are exactly
`{429, 500, 502, 503, 504}`; every other status, 3xx and 4xx included, is
terminal.

Five points are worth knowing:

- **The ordering, and why the obvious form is wrong.** `_wait_seconds`
  returns `max(backoff, min(retry_after, 60.0))`: the ceiling clamps the
  Retry-After **contribution** only, and Retry-After can only lengthen a wait.
  The natural-looking `min(max(backoff, retry_after), CEILING)` applies the
  ceiling to the whole wait, so whenever the geometric wait exceeds the
  ceiling the gateway sleeps **less** than the rule requires — silently, in the
  module that owns the rule, and reachable through the public `max_attempts`
  parameter: the schedule is 1, 2, 4, 8, 16, 32, 64 s, so it crosses a 60 s
  ceiling at seven attempts.
- **Two different bounds apply to `Retry-After`, and they are not variants of
  each other.** 60 s (`_MAX_HONOURED_RETRY_AFTER_SECONDS`) is the most of a
  counterparty's header this gateway actually *waits*. `clamp_retry_after`
  (`src/core/exceptions.py`) clamps the value that rides out to a buyer on
  `AdCPSalesAgentError.retry_after` separately, to the spec's [1, 3600]; the
  failure builder in `attempts.py` calls it. Changing one does not change the
  other.
- **The gateway sleeps rather than publishing the number.** A public
  `backoff_seconds()` lets `sleep(backoff_seconds(1))` — or a scaled variant —
  drift invisibly, because the guard's detector follows same-module names
  only. `sleep_backoff` awaits the wait and returns nothing, and it takes the
  whole `Attempts` instance rather than an index, so a caller cannot separately
  compute or hold a duration.
- **`Attempts` reifies the loop, not only the decisions.** One loop
  definition serves `send`, `asend`, and the MCP client wrapper — a loop written per
  consumer is where they drift. `record_oversized_response` pins
  `last_retry_after=None`, so an oversized response right after a 429 cannot
  leak that attempt's `Retry-After` onto an unrelated failure.
- **The guard bans the class of defect, not one form of it.**
  `tests/unit/test_architecture_no_call_site_backoff.py` resolves the inline
  form, a local-variable binding, and one hop of same-module helper calls,
  because authors nearly always assign backoff to a variable first or hide it
  behind a helper — a detector that reads only the inline sleep argument
  reports almost nothing. Its list is `NON_HTTP_BACKOFF`, pinned at exactly
  three entries — a GAM forecasting poll, a GAM SOAP retry, and a database
  connection retry: a fixed taxonomy of correct designs that are genuinely not
  outbound HTTP, not a debt list anyone expects to shrink.

`terminal_client_error_status` exists because the obvious
`400 <= status < 500` predicate is wrong for 429: it makes a rate-limited
endpoint log "will not retry" after a single attempt. `ADCP_OUTBOUND_BACKOFF_BASE_SECONDS`
shortens the base for test speed only — it is deliberately absent from
`tox.ini`'s `pass_env` and from both compose files, so a suite-wide value
cannot silently shorten the schedule.

### The gateway calls the signer inside the retry loop

`send` and `asend` take `sign: SignAttempt | None`, a
`(method, url, body) -> headers` callback the gateway calls once **per attempt**,
inside the loop, after `client.build_request`. Three consequences follow from
where it sits, and they are the reason it exists:

- The gateway hands the signer `request.content` — the exact bytes httpx
  transmits — and the post-params `request.url`, so signed bytes and wire bytes
  are one object rather than two that agree. A `sign=` caller may therefore use
  `json=` or `content=`.
- A scheme whose signature must not be replayed (RFC 9421 covers a `nonce` a
  conformant receiver rejects twice) needs a fresh signature per attempt. Before
  this hook, such a caller had a reason to open its own `httpx` client and leave
  the pinned transport. Injection removes the reason: the caller never receives
  a client it can point elsewhere.
- Framing stays with httpx. The gateway drops a returned `Content-Length`,
  `Transfer-Encoding`, or `Host` with a warning, because a signer that re-frames
  the body can desync the bytes it signed from the bytes the receiver reads.

The callback's shape is `adcp`'s `WebhookAuthStrategy.build_auth_headers`
verbatim, so `sign=strategy.build_auth_headers` works with no adapter. The cost
of matching that shape, recorded here so nobody rediscovers it: the SDK protocol
accepts method, URL, and body only, so the signer cannot see headers it may be
covering — which is why an explicit `Content-Type` on the `content=` path is the
caller's obligation, and why widening the callback later means diverging from
the SDK.

No call site under `src/` or `tests/` passes `sign=`. The legacy
shared-secret path signs once instead, in
`webhook_egress.prepare_signed_request`, which is correct for a signature that
verifies on replay inside its window.

## One vocabulary for webhook auth

A webhook's signing scheme comes from `adcp.types.AuthenticationScheme`
— a **spec vocabulary, not a local choice**. Its two members, `Bearer` and
`HMAC-SHA256`, are what AdCP 3.1.1 defines (`_schemas/3.1/enums/auth-scheme.json`
in the pinned `adcp==6.6.0` package, generated from the spec rather than
written here; the schema marks both as legacy, removed in AdCP 4.0, with new
integrations directed at the RFC 9421 profile). This repo does not get to add to
it — no seller-local members, and no tolerant case-folding for spellings that
appear in real traffic: such rows always exist, and each folded form is a second
spelling of one fact, which senders then disagree about.

The sender (`webhook_egress.py::_headers_for`) matches on the enum members with
`match`/`case`, case-sensitively, and the fall-through raises an
`AssertionError` naming the scheme. That is the mechanism that keeps the
vocabularies aligned: when the spec adds a member and the pin moves, the first
delivery under that scheme fails loudly at the unhandled branch — rather than
silently falling through to a default and sending nothing.

Delivery refuses a stored row naming a scheme AdCP 3.1.1 does not define —
`scheme_not_in_spec` — until its operator re-registers. That is the intended
outcome, not collateral: a row that cannot be signed correctly must not be
signed incorrectly.

Three sub-rules apply, and the third is a trap worth knowing:

- **Decide on the enum member, never on its text.** `mypy.ini` sets
  `strict_equality`, and a guard
  (`tests/unit/test_architecture_enum_not_compared_to_string.py`) catches what
  mypy structurally cannot — StrEnum-to-string comparisons are legal to mypy,
  because the members *are* strings.
- **A migration writes values read from the enum, not literals.** A hand-typed
  literal is one typo away from persisting a spelling nothing in `src/`
  compares against.
- **Never add an import-time assertion restating enum values.** It puts one fact
  in two places, which is the defect this rule removes, and it is actively
  dangerous: alembic imports every module under `versions/`, so an import-scope
  `assert` fails `alembic heads` and `alembic upgrade` on any member rename.
  That breaks the migration system to guard a rename that has not happened.

## Address logic stays in the egress package

Pattern #9 and the gateway's own docstring both state the rule "do not write
address classification outside `egress/`". Two mechanisms also enforce it,
because one mechanism cannot see both forms — and a rule enforced by writing
alone is how a second, independently maintained CIDR range set appears with no
check failing:

| Form | Caught by |
|---|---|
| `import ipaddress`, `socket.gethostbyname` outside `egress/` | TID251 bans in `ruff-egress.toml` |
| A hostname blocklist written as an inline `set`/`frozenset` literal | `tests/unit/test_architecture_no_hostname_blocklist_duplication.py` |

An import ban sees imports. It cannot see a set of hostnames written inline —
the likelier form of this defect — so shipping only the TID251 half leaves the
likelier half unguarded. The AST guard keys on two members of the real
blocklist as sentinels and asserts that they are still members of it, so the
detector cannot keep passing after production's set stops containing the
address the whole rule is about.

The `socket.gethostbyname` ban carries the deeper rule in its message:
resolve-then-check is a time-of-check to time-of-use (TOCTOU) pattern the egress
package does not use, because `adcp.signing` pins the resolved IP in one step.
So the ban reaches further than
"don't duplicate policy" — it says **don't reintroduce the two-step pattern at
all**.

**If you must block another range:** add it to
`_SUPPLEMENT_NETWORKS` in `src/core/security/egress/policy.py`, never at the
call site. Importing `ipaddress` elsewhere under `src/` fails the egress lint
line with a message pointing there. Both modules that legitimately need it —
the policy module and the vendored IDNA canonicalizer — bind it privately or
carry a file-scoped row, and they are the only two occurrences under `src/`.

## A destination is a typed constant, never an environment read with a URL default

`VendorConstant` (`src/core/security/egress/destination.py`) is the typed home
for a fixed vendor endpoint. `APPROXIMATED_BASE_URL`
(`src/services/approximated_client.py`) and `GOOGLE_TOKEN_URL`
(`src/services/google_oauth_client.py`) are its two instances.

The pattern it forbids is worth naming, because it looks entirely ordinary:

```python
APPROXIMATED_BASE_URL = os.environ.get("APPROXIMATED_BASE_URL", "https://cloud.approximated.app")
```

That is a **credential-bearing destination, silently redirectable at import time
by one environment variable**. The destination-rewrite guard
(`tests/unit/test_architecture_no_destination_rewrite.py`) therefore runs two
detectors: stdlib URL-reassembly calls (`urlunparse`, `urlunsplit`,
`._replace(netloc=…)` / `._replace(scheme=…)`), and any module-level constant
sourced from `os.environ.get(...)` / `os.getenv(...)` with a URL-like default,
anywhere under `src/` — an environment read with a URL-like default matches none
of the reassembly calls, so the first detector alone misses it.

**Both detectors have an empty exempt set, and nobody can pad either list
quietly:** the shared scanner raises on an exemption that suppresses nothing, so
a row has to correspond to a live violation for the scanner to accept it at all.
The one exemption that used to sit there, `src/app.py`'s CORS `allow_origins`,
is gone because reading the environment once at startup moved every env access
to the settings loader — the detector finds nothing in that file.
`creative_agent_registry.py`'s sanctioned connection alias sits outside both
detectors' scope by shape — it swaps a whole string inside a function, and it
reads `settings.integrations.creative_agent_url` rather than the environment.
`tests/unit/test_creative_agent_connection_alias.py` grades its bounds
behaviorally.

`VendorConstant` is deliberately a single member, not a union. Do not add
sibling members or a `Destination` union alias to make the vocabulary look
symmetrical: the counterpart concepts already live in `UrlProvenance`, and a
parallel type that nothing constructs gives one concept two representations.

## Replace a validator without opening a gap

Two ordering rules apply for anyone deleting or replacing an egress guard:

- **Delete a validator last, after you migrate its callers.** Removing it
  earlier leaves counterparty URLs validated by nothing while you are still
  wiring its replacement. And check which sibling you are deleting:
  `WebhookURLValidator` (`src/core/webhook_validator.py`) is a thin
  `(bool, str)` wrapper over `check_registration` — a caller of the gateway,
  rather than a rival validator — kept only because two call sites depend on
  that return shape.
- **Keep the mechanism that proves a ban is not vacuous.** The two AST scans
  that used to cover raw network libraries and SDK/MCP client constructors are
  gone; both checks are TID251 rows in `ruff-egress.toml`, with no allowlist to
  read as empty. `tests/unit/test_ruff_egress_bans.py` replaced the scans'
  coverage: it fires every entry on every resolving import spelling and computes
  the suppressed set with ruff itself. If you move a check into that table, move
  its liveness proof with it — ruff accepts a mistyped `banned-api` key and
  reports nothing forever.

## Decide where a concern belongs

Ask these questions in order:

1. **Does the SDK already own it?** Address classification, resolution,
   pinning, and signing do. Import it. If the SDK is wrong, fix it upstream — a local
   copy is how the two go out of step.
2. **Is it a policy the gateway should decide once?** Then it belongs in
   `egress/`, in the module that owns that decision, expressed as a value rather
   than an effect where possible (`attempts.py` is the model: no I/O, a pure
   state machine).
3. **Is it a call-site concern?** Then it is probably not a policy — pass it in.
   If you are about to write address logic at a call site, that is the pattern
   this package exists to remove.

## Related

- [Outbound egress: one gateway](../security/outbound-egress.md) — the rule, the entry points, and the three enforcement layers
- `CLAUDE.md` Pattern #9 — the same, for agents

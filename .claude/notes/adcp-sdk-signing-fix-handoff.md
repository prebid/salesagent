# Handoff: fixing the four request-signing conformance bugs in `adcp-client-python`

**For:** an agent working in `adcontextprotocol/adcp-client-python`.
**From:** #1291 B3 in `prebid/salesagent`, which found all four by RUNNING the shipped conformance
data against `adcp==6.6.0`.

The four issues (#976, #977, #978, #979) are open and unfixed. This note says what order to fix them
in, why that order, and where working code already exists.

---

## 0. Before anything: land #980

**PR #980 vendors the complete 3.1.1 request-signing vector set and pins it against drift. Nothing
else here should merge before it.**

That is not a preference. Issue #975 is precisely that the vendored vectors are incomplete *and the
loaders cannot detect it* — so today a fix for #977 or #979 would merge with nothing upstream
grading it. Fixing a conformance bug in a repo that cannot run the conformance data reproduces the
defect that allowed the bug. #980 is approved at 19/20; land it, then everything below has an oracle.

Once it is in, the two files that matter are:

- `test-vectors/request-signing/canonicalization.json` — **31 cases**, of which **8 fail** on
  `adcp==6.6.0`. These grade #977, #978 and #979.
- `test-vectors/request-signing/negative/{021,022,023,026}.json` — these grade #976.

Every case carries `expected_target_uri` / `expected_authority` (positive) or `reject: true` plus an
error code (negative). Assert byte-for-byte. Include each case's `rule` string in the assertion
message so a failure names the canonicalization step that broke.

---

## 1. Order, and why

### #978 first — malformed authorities + the missing constant

**Why first:** it introduces `request_target_uri_malformed`. #977's U-label rejection raises *the
same code*. Do #977 first and you either invent a second constant or block. #978 also carries the
largest behavioural surface — five authority shapes currently accepted that the spec says MUST be
rejected — so it is the fix that moves the most cases green.

**Cases it turns green:** all 6 `reject: true` cases. Five are accepted outright today; the sixth,
`malformed-ipv6-missing-closing-bracket`, is refused by `urlsplit` with a **bare `ValueError`
carrying no code**, so it fails on the wrong exception type rather than passing. Normalising that
into the typed error is part of the fix, not a detail — a test that accepts any exception would pass
today and prove nothing.

### #977 second — IDNA A-label conversion and raw U-label rejection

Depends on #978's constant. Two cases: `idn-to-punycode`, `idn-mixed-case-to-punycode`.

**The subtlety that decides the whole design:** these are **producer-side**. `url-canonicalization.mdx`
step 2 is explicit — *"A host containing raw non-ASCII bytes that has not been ToASCII-normalized by
the producer MUST be rejected by the comparer — receivers do not silently re-normalize."* So the
verifier path must **reject**, not convert. A comparer that re-normalizes picks one of several
legitimate UTS-46 outcomes and disagrees with whoever signed. The **signer** path is where the
conversion belongs.

Note the SDK already has `adcp.signing._idna_canonicalize.canonicalize_host`, and **four sibling
modules call it** — `_canon_authority` simply never does. Much of #977 may be wiring an existing
helper into the signer path rather than writing new IDNA logic. Check that before writing anything.
The pin is UTS-46 Nontransitional with `CheckHyphens` / `CheckBidi` / `UseSTD3ASCIIRules`.

### #976 third — step-1 structured-field rejection

Independent of the other three; only third because it is a different code path and the two above
share a constant. Do it in parallel if you have the hands.

**Cases:** `negative/021`, `022`, `023`, `026`. Measured behaviour on `adcp==6.6.0`:

| vector | expects | SDK returns today | why |
|---|---|---|---|
| 021 duplicate `Signature-Input` dict key | `request_signature_header_malformed` | `request_signature_components_incomplete` | the RFC 8941 parser last-wins on the duplicate key |
| 022 multi-valued covered non-list field | `request_signature_header_malformed` | `request_signature_invalid` | no single-value check on a covered non-list field |
| 023 multi-valued `Content-Digest` | `request_signature_header_malformed` | `request_signature_invalid` | no RFC 9530 duplicate-algorithm check |
| 026 non-ASCII `Host` | `request_signature_header_malformed` | `request_signature_invalid` | no A-label enforcement on the authority path |

**Note 026 expects a DIFFERENT code from the canonicalization reject set**, even though the
underlying rule is the same. A wire-header rejection is checklist step 1
(`request_signature_header_malformed`); a canonicalization rejection is
`request_target_uri_malformed`. Share the *predicate*, never the code. Collapsing them loses a graded
artifact in each direction.

### #979 last — trailing empty query

One case, `trailing-empty-query-preserved`: `canonicalize_target_uri` drops a trailing `?`, so `/p?`
and `/p` produce the same signature base. Smallest surface, no dependencies, and the one least
likely to be observable to most callers — see §3 on why we could not grade it at all.

---

## 2. Where to take the code from

Two files in `prebid/salesagent`, branch `feat/rfc9421-request-signing`. Both are spec-cited
line by line and graded by the same conformance data you will be running.

### For #978 — `src/core/signing/canonical.py`

The whole module is ~140 lines and exists solely to add the rejection set the SDK lacks. Take:

- **`malformed_authority_reason(authority) -> str | None`** — the complete rule for steps 2–3:
  userinfo-but-no-host, no host at all (`https:///p`, `https://:443/p`), bracketed host missing its
  closing bracket, bare IPv6 outside brackets, IPv6 zone identifier (`%25` inside `[...]`), and raw
  non-ASCII host. It returns a **reason string rather than a bool** on purpose, so every caller's
  message names the rule that fired — worth preserving when you port it.
- **`_bracketed_host_reason(host)`** — step 2's two IPv6-literal rejections.
- **`reject_malformed_target(url)`** — including the `urlsplit` `ValueError` normalisation described
  above.
- **`REQUEST_TARGET_URI_MALFORMED = "request_target_uri_malformed"`** — this is #978's missing
  constant. It belongs in the SDK's error-code module alongside the rest of the taxonomy.

Upstream, the gate belongs *inside* `canonicalize_target_uri` / `canonicalize_authority`, not wrapped
around them — our module wraps only because it must not fork the SDK's algorithm.

### For #976 — `src/core/signing/request_verifier_middleware.py::_strict_header_precheck`

Plus its companion table `_MALFORMED_VALUE_RULES` and `_SINGLE_LINE_SIGNED_HEADERS`.

**The one thing to carry across above all else:** it runs over the **raw header list**
(`list[tuple[bytes, bytes]]`), never a collapsed dict. Every dict view of headers **last-wins** on a
repeated name rather than joining it. The vectors express "multi-valued" as a single comma-joined
value, so a gate written over the dict passes all four vectors while missing the threat their own
`$comment`s describe: a proxy inserting a **second header line**, which last-wins before any check
runs. We grade both forms of each shape for exactly this reason.

The rules are a **predicate table**, not an if-chain — that is what lets the authority rule have one
definition shared with the canonicalization gate while raising a different code at each site.

---

## 3. Three cases we could NOT grade, and what that means for you

Our verifier accounts for all 31 canonicalization cases with **0 skipped and 0 xfailed**, but three
of them run as named blocker tests rather than conformance, because they are not observable at a
verifier boundary at all:

- **the two IDN cases (#977)** — producer-side, per step 2 above. A verifier can only reject.
- **`trailing-empty-query-preserved` (#979)** — destroyed before we see it. ASGI hands
  `query_string=b""` for both `/p` and `/p?`, so the distinction does not survive to the boundary,
  and `_verify_url` drops the `?` unconditionally.

**So no downstream seller repo can prove a fix for #977 or #979.** They must be fixed *and* tested
inside the SDK, against `canonicalization.json` directly. That is the strongest argument for doing
this work upstream rather than waiting for a consumer to demonstrate it — no consumer can.

---

## 4. Pitfalls, each one measured rather than guessed

- **The vector README's worked example is stale.** It shows the `reject: true` cases expecting
  `request_signature_header_malformed`. The shipped DATA says `request_target_uri_malformed`. **Data
  wins.** Do not "correct" the code from the prose — this is the same class of defect as
  `adcp` #6076/#6078, where a hand-written doc table listed seven codes that do not exist.
- **`positive/004` does not ship `expected_signature_base`**, though the README implies every
  positive does. The set that carries it is **13 files**, not 12 — the README misses `negative/010`.
  Pin the exact set by name; do not compute it from a glob and assume.
- **Do not assert on `failed_step`.** The storyboard calls it informational. The graded artifact is
  the code string.
- **Do not write a second canonicalizer.** If you gate before delegating (as our seam does), keep the
  algorithm single-sourced. The reason this whole family of bugs is dangerous is that a signer and a
  verifier computing `@target-uri` two ways agree only by luck, and disagree silently until a
  production interop failure.
- **A narrow gate beats a broad one.** Anything the gate cannot *prove* malformed should be handed
  through unchanged. Rejecting traffic the spec requires you to accept is worse than the bug being
  closed. In particular leave `Signature-Input` values you cannot parse alone (`negative/011`,
  `negative/024`) — the SDK already returns the right code for those.

---

## 5. Why this matters on a clock

While these four are open, every downstream implementer either fails the vectors or carries a local
seam like ours. Ours is currently *the* implementation rather than a workaround, and the longer that
is true the more likely it calcifies into a permanent second canonicalizer — the exact thing the
spec's single-algorithm design exists to prevent. When these land and we bump the `adcp` pin, our
seam is meant to **shrink** to whatever remains un-implemented upstream. Landing #978 and #976 alone
would already remove most of it.

**Related upstream, for context:** `adcontextprotocol/adcp` #6071 (stale vector counts — the spec
said 28, there are 40), #6076 (seven documented signing error codes that do not exist), #6075 (docs
restate machine-readable spec facts by hand with nothing checking they agree). Same root disease as
#975: a hand-written subset misleads twice, once by being wrong and once by looking exhaustive.

# Resolution ledger — merge/main-into-rfc9421 → feat/rfc9421-on-1721

Running ledger for the staged semantic merge. Each stage records which files were
**mechanical** (and the rule applied) versus **semantic** (union / which side subsumed, and
why), plus every verdict **overturned during resolution** — the overturn rate is the evidence
for how far the remaining verdicts can be trusted.

## The doctrine every verdict is decided against

Measured, not assumed: upstream `main` is **fully contained** in `pr1721`, and **zero** main
commits are unique to `merge/main-into-rfc9421`. Therefore THEIRS' only unique value is the
RFC 9421 signing epic itself; every non-signing difference is either already subsumed by
#1721 or old-architecture scaffolding #1721 has since rewritten.

So each file reduces to two questions:

1. does it carry signing capability that must survive?
2. does its merged content reference a symbol #1721 deleted or reshaped?

`TAKE-MINE` answers no/no. `PORT` answers yes and requires re-expression on #1721's shape.
`UNION` means both sides carry distinct surviving value. `DELETE` means #1721 retired the
subject on purpose. `MECHANICAL` means the file is generated or tool-owned and is pinned or
regenerated, never content-merged.

## Surface

| | count |
|---|---|
| files THEIRS changed vs merge-base (full surface) | 725 |
| — touched by both sides | 426 |
| — → git flags as conflicting | 180 |
| — → git auto-merges silently | 246 |
| — ours-only, taken verbatim with no review | 299 |

Reviewing only the 180 would have missed 545 files, including the ones git auto-merged
across the architecture change.

## Classification (phase 6), after adversarial refutation

725 files classified by 30 batch agents; every verdict that ASSERTS SAFETY (`TAKE-MINE`, and
any not-`broken_on_head` call) re-checked by a second agent instructed to refute it.

| verdict | after refutation | first pass |
|---|---|---|
| TAKE-MINE | 269 | 298 |
| PORT | 163 | 151 |
| MECHANICAL | 136 | 124 |
| UNION | 89 | 71 |
| DELETE | 68 | 81 |

**86 of 475** safety-asserting verdicts were overturned; **54** changed the verdict outright.
`broken_on_head` (merged content referencing deleted/reshaped symbols): **213**.

The overturns that mattered most:

- **21 × TAKE-MINE → UNION** — a literal `git checkout :2:` would have silently deleted THEIRS
  content that still applies.
- **10 × TAKE-MINE → PORT** — signing capability the first pass missed.
- **11 × DELETE → MECHANICAL** — the vendored `3.1.1/` schema and conformance-vector trees are
  pinned to THEIRS as one unit, not deleted.
- **7 × broken_on_head false → true**, e.g. `scripts/ops/provision_signing_key.py` interpolates
  `f"{exc}"` from an `AdCPConfigurationError`, consuming the `message` contract #1721 removed.

The single best illustration of why the auto-merged files were the real risk —
`src/adapters/gam/managers/reporting.py`, from its refuter:

> "The conflict is NOT comment-only and HEAD did NOT win the body. Git auto-merged THEIRS'
> hunks OUTSIDE the conflict region, so the file currently contains BOTH mechanisms."

Its conflict region and its auto-merged region contradict each other, and the file does not
parse. Nothing in git's conflict list points at it.

## Commit structure — a constraint, stated

Git refuses to commit while any path is unmerged, so "one commit per stage" is not expressible
inside a single `git merge`. The merge is therefore resolved stage by stage with the coherence
gate run between stages, and lands as ONE merge commit carrying this ledger's per-stage
sections. The alternative — committing the merge early and re-adding capability afterwards —
was rejected: it would record `merge/main-into-rfc9421` as fully merged while 252 files of its
capability were still absent, which is a lie a future merge would inherit.

---

## Stage 0 — inbound RFC 9421 verification (landed before this merge, commit 2aeca7b0c)

Re-engineered onto #1721's boundary ahead of the merge: verification moved into
`_resolve_identity`, the capture middleware, the signature-code taxonomy, the namespace split.
95 files of the surface are attributed to this stage and need no further work.

---
## Stage 1 — the error contract and its raise sites (35 files)

**Gate: layers 0–2 (markers / parse / retired-contract), scoped to the stage.** PASS, with all
three layer-2 positive controls firing — an exclusion rule that silently swallowed everything
would make this layer pass by doing nothing, which is the failure this whole gate exists to
catch. Whole-tree debt still owed by later stages at this boundary: **72 lines**.

### Mechanical (rule applied, no judgment)

| files | rule |
|---|---|
| 18 × TAKE-MINE | `main ⊂ pr1721` + no signing capability ⇒ #1721 subsumes. Restored from HEAD. |
| 11 × DELETE | #1721 retired the subject; confirmed absent from the tree. |
| 1 × MECHANICAL | `test_architecture_no_value_error_in_impl.py` — guard owned by #1721's `_impl` contract. |

`src/core/exceptions.py` itself is TAKE-MINE, taken whole: verified zero `message:` parameters
survive and `CODE_TABLE` is read at 4 sites as the sole authority.

### Semantic (8 files, union / subsumption with reasons)

| file | resolution | why |
|---|---|---|
| `src/core/http_utils.py` | UNION | auto-merged; both sides' helpers survive |
| `src/adapters/mock_ad_server.py` | UNION | literal `checkout :2:` would have deleted a THEIRS block |
| `tests/unit/test_architecture_error_recovery_enum_conformance.py` | UNION | OURS base (already admits the 27 generated `SignatureErrorCode` members) + 3 grader meta-tests from THEIRS |
| `tests/integration/test_credential_block_is_logged.py` | PORT | security.mdx :1464 log obligation re-expressed against `registers_webhook_credentials` over the validated request |
| `tests/unit/test_capability_declarations_signing_relations.py` | PORT | re-pointed at `_reject_mixed_namespaces` / `is_block_declarable` on #1721's `ConfigurationDetails` |
| `src/a2a_server/adcp_a2a_server.py` | **overturn** PORT → TAKE-MINE | see below |
| `tests/unit/test_error_envelope.py` | **overturn** UNION → TAKE-MINE | THEIRS' only delta mocks the deleted `resolve_identity`; every retired assertion has a named successor |
| `tests/unit/test_signals_agent_silent_empty_bug.py` | **overturn** PORT → DELETE | upstream #1802 deleted it and re-homed the obligation; THEIRS resurrected it |

### Overturn rate at this stage: 3 of 8 (38%)

Classification-phase overturn was 18%; resolution-phase is higher on a small sample. Both are
evidence the remaining verdicts need the same resolve→refute treatment rather than direct
application.

**The one that justifies the whole method** — `src/a2a_server/adcp_a2a_server.py`. THEIRS'
module-scope `SKILL_HANDLERS` block **auto-merged cleanly, outside any conflict region**, and
was an import-time bomb: its `_UNBOUND_SKILLS` check would have named all 18 skills, because
`AdCPRequestHandler` defines zero `_handle_*_skill` methods on #1721, raising `RuntimeError` at
import and taking the entire A2A server down. #1721 strictly subsumes the intent — it derives
both the agent card (`_derived_skills()`) and the dispatch from the same `TOOLS` rows. Git
flagged nothing.

### Owed forward (recorded, not deferred silently)

- **stage 2/3** — `src/core/signing_contract/` landed verbatim from THEIRS and must go; its
  replacements already exist as `src/core/signing/{canonical,vocabulary}.py`. Consumers still
  importing it: `signing/provider.py`, `signing/algorithms.py`, `database/models.py`,
  `database/repositories/signing_key.py`.
- **stage 2** — `src/core/signing/capture.py:235` should call `headers_from_asgi_scope` rather
  than inline the identical comprehension (DRY ratchet).
- **stage 2** — a decision on A2A protocol push notifications: THEIRS threads `identity.tenant_id`
  into `WebhookTaskContext` so the RFC 9421 sender has a tenant to sign for, whereas #1721
  declines push notifications outright (`PushNotificationNotSupportedError` on all four
  handlers, `push_notifications=False` on the card). If they are to be signed, the plumbing is
  rebuilt on #1721's boundary, not restored from THEIRS.
- **stage 5** — `tests/unit/test_architecture_signing_operations.py::test_the_dispatch_table_is_importable_at_module_scope`
  hard-imports the now-deleted `SKILL_HANDLERS`; delete or repoint at `TOOLS`.

---
## Stage 2 — outbound RFC 9421 signing (73 files, 68 semantic)

**Gate: layers 0–3 (markers / parse / retired-contract / ruff incl. all five structural
configs), scoped to the stage.** PASS. Whole-tree debt still owed by later stages fell from
72 lines to **54**.

The stage was resolved against ONE fixed seam design agreed before any file was touched, so
68 resolvers could not each invent their own. The design's load-bearing findings:

- **`sign=` was already shipped.** `src/core/security/outbound_http.py` on #1721 already
  carries `SignAttempt`, the `sign:` parameter on `send`/`asend`, invocation inside the retry
  loop after `client.build_request`, and `_SIGNER_RESERVED_HEADERS`. Stage 2 adds nothing to
  it. The signer sees `request.content` — the exact wire bytes — so signed bytes and sent
  bytes are one object, and each retry attempt re-signs with its own nonce.
- **One seam, widened by one arm.** `webhook_egress._headers_for` gains a third branch
  selected by the ABSENCE of an `authentication` block (security.mdx @ v3.1.1 :1424) and a
  third return slot. Exactly one of `(headers, hmac_secret, sign)` is ever non-empty, so
  :1425's "never signed both ways" is a shape rather than a rule anyone must remember.
- **The big deletion.** Everything in THEIRS' `webhook_sender_factory.py` that exists to build
  or feed an `adcp.webhooks.WebhookSender` is DELETED, not ported — `build_webhook_sender`,
  `_rfc9421_sender`, `_unauthenticated_sender`, `deliver_adcp_webhook`, `legacy_auth_mode`,
  `_HMAC_SCHEMES` and the rest. `WebhookSender` opens its own httpx client, which the egress
  rule forbids, and every sender on #1721 already dials `deliver_webhook`/`adeliver_webhook`.
  The per-tenant resolution that survived it lives in the new `src/core/signing/outbound.py`.

### Mechanical (rule applied)

3 × TAKE-MINE, 2 × DELETE by the `main ⊂ pr1721` rule.

### Semantic: 68 files resolved, each then adversarially verified

### Overturn rate at this stage: 5 of 68 (7%) — plus 1 cross-stage

| file | overturn | why |
|---|---|---|
| `src/core/signing_contract/_upstream/errors.py` | PORT → DELETE | one surviving row, re-homed rather than a vendored module kept for one string |
| `tests/helpers/mcp_signing_server.py` | PORT → DELETE | subsumed |
| `tests/unit/test_architecture_signing_layer_boundary.py` | PORT → DELETE | subsumed |
| `tests/unit/test_normalize_agent_url.py` | PORT → DELETE | subsumed |
| `src/core/property_list_resolver.py` | UNION → MECHANICAL | documentation-only delta |
| `src/core/security/url_validator.py` (**stage 3**) | PORT → DELETE | found by a stage-2 guard; see below |

### The verify pass found 4 unsound resolutions; all four fixed

1. **A latent crash in my own stage-0 slice.** `reject_malformed_target` raises
   `TargetUriMalformedError` with code `request_target_uri_malformed`, and the verifier's
   `_refuse` does `CODE_BY_VALUE[exc.code]` — but that code was never in `CODE_TABLE`, because
   the SDK's `REQUEST_TO_WEBHOOK_CODE` omits the row at `adcp==6.6.0` (upstream fix:
   adcp-client-python PR #987 / fbab8f44). A malformed signed target URI would have raised
   `KeyError` instead of answering 401. The taxonomy is now built from
   `{**REQUEST_TO_WEBHOOK_CODE, REQUEST_TARGET_URI_MALFORMED: WEBHOOK_TARGET_URI_MALFORMED}` —
   28 codes, 27 still from the SDK — and the local row disappears when the pin advances.
2. `operator_mcp.py` re-wrapped `MCPSigningError` in a fresh `AdCPConfigurationError`, losing
   the typed details and forcing `probe_failure` to discriminate on `exc.__cause__` — a weaker
   test than the type it was reconstructing. `MCPSigningError` already IS an
   `AdCPConfigurationError`; it now propagates and the discriminator is the type.
3. `uc004_delivery.py` carried two docstrings naming `build_webhook_sender` and
   `_rfc9421_sender`, symbols this stage deletes. Repointed at `_headers_for` and
   `delivery_signer_for_tenant`.
4. **The reserved-TLD guard was red, not green** — the resolver had only exercised its
   detector meta-tests. `src/core/security/url_validator.py`, which #1721 deleted, had been
   resurrected by the merge and was a live SECOND declaration of the reserved-TLD policy.
   Deleted; its surviving half now lives once in `egress/policy.py`, widened back to the six
   special-use TLDs THEIRS carried (`.local` RFC 6762 and `.internal` RFC 8375 were missing),
   with `reserved_tld_for_host` as the single matcher and `is_reserved_tld_host` expressed
   over it.

### An architecture rule this stage had to honour rather than bend

`provider.py`'s `env:` key-ref scheme reads an environment variable whose NAME is operator
data on the key row, which `ruff-environment.toml` bans outside `src/core/config.py` — a
config whose exemption list is three files and is explicitly "no allowlist". Rather than grow
it, the read moved to the sanctioned reader as `SigningSettings.secret_from_env(name)`, which
`key_passphrase` now also uses. One environment reader, one named bend, stated where the rule
is stated.

### Gate hardening this stage forced

Layer 0's marker pattern matched an **RST table border** (a run of `=` longer than seven) in a
docstring and reported a resolved file as conflicted. A git marker is exactly seven characters;
the pattern is now `^(<<<<<<< |>>>>>>> |=======$)` with two mandatory controls — one proving it
still catches a real marker, one proving it no longer catches a table border.

---
## Stage 3 — key lifecycle, trust root, revocation publisher, admin UI (145 files, 62 semantic)

**Gate: layers 0–3, scoped.** PASS. Whole-tree debt 54 → **18 lines**.

Interrupted mid-run by a session limit that killed 38 agents (4 resolvers, 34 verifiers). Resumed
from the run id: cached agents replayed, the 38 re-ran, final 125/125 with 0 errors. No file was
staged on the strength of a resolution whose verify pass had died.

### Mechanical (rule applied)

| files | rule |
|---|---|
| 50 × MECHANICAL | pin-to-THEIRS, the vendored `3.1.1/` schema + conformance-vector trees as ONE unit |
| 16 × MECHANICAL | pinned to THEIRS now, **regenerated at stage 6** — never content-merged |
| 6 × TAKE-MINE, 10 × DELETE | the `main ⊂ pr1721` rule |

### `src/core/signing_contract/` is RETIRED

The design's finding is what killed it: the cycle that package was carved out to break —
package `__init__` → keys → database.models → back into a half-initialised package — **cannot
form when `src/core/signing/__init__.py` re-exports nothing**, which is exactly what #1721 made
it. So `algorithms.py` stops being a forwarding shim and becomes the real dependency-free leaf,
every consumer imports it by dotted path, and the package has no job left. Zero files remain.

### Overturn rate at this stage: 5 of 62 (8%), plus 1 process gap

`uow.py`, `capability_declarations.py`, `test_architecture_capability_constant_parity.py`
PORT → UNION; `signing_contract/{_upstream/__init__,algorithms}.py` PORT → DELETE.

### The process gap this stage exposed

**9 stage-0 DELETE verdicts were classified but never applied**, because stage 0 was treated as
"already handled by the inbound slice". They were still sitting in the merged tree.
`src/core/validation.py` was one, and its two live call sites in `media_buy_create.py` were
THEIRS' hunks that auto-merged into a #1721 file **whose own comment sixty lines below says
`normalize_agent_url` was deliberately replaced** by `canonical_agent_url`, because its extra
`/mcp` and `/a2a` stripping "decided an AUTHORIZATION outcome: an agent registered at
`https://x.com` also authorized `https://x.com/mcp`". Reconciled as a union: #1721's canonical
form wins the security argument, THEIRS' malformed-URL handling survives, re-expressed on the
stage-1 error contract (typed `ValidationDetails`, cause on `internal_detail`). All 9 applied.

### A tracking hazard, recorded

A broad `git add -A` marked **104 files carrying live conflict markers as resolved in the index**.
The index is therefore no longer a work tracker for this merge; the marker-CONTENT scan
(`.claude/merge-review/remaining_markers.txt`, regenerated each stage) is the authority, and it
is what the gate's layer 0 reads. Remaining at this boundary: 13 stage-0, 34 stage-4,
38 stage-5, 19 stage-6.

---
## Stage 4 — the BDD harness (56 files, 27 semantic incl. one carried over)

**Gate: layers 0–3, scoped.** PASS. Whole-tree retired-symbol debt: **0 lines across all 399
`src/` + `scripts/` files**.

`Transport.IMPL`, `ImplDispatcher` and `synthesized_error_envelope` stay deleted. 26 TAKE-MINE
+ 2 DELETE by rule; 27 resolved semantically, each adversarially verified.

### Overturn rate: 5 of 27 (19%)

`tests/bdd/conftest.py`, and the BR-UC-004 and BR-UC-011 feature files, PORT → UNION — in each
case #1721 had independently STRENGTHENED assertions that a straight port would have dropped
(the `Then the webhook payload is compliant with the AdCP delivery webhook spec` line on ~20
scenarios; the canonical `Given the Buyer is authenticated` plus a compliance Then). Two more
went PORT/UNION → MECHANICAL, being architecture-blind Gherkin and a pure DRY extraction.

### The verify pass caught a regression-to-xfail

`tests/bdd/conftest.py` listed four UC-010 signing tags — `request-signing-posture`,
`-namespace-split`, `-subset`, `webhook-signing` — in **both** `_UC010_WIRED_TAGS` (Batch 14,
with a correct rationale) **and** `_UC010_DORMANT_TRACKING`. The dormancy map may only SHRINK
as batches land; leaving a tag in both means the wired set says "graded" while the dormancy row
xfails it, and the non-strict xfail wins. Four scenarios whose capability now exists would have
gone on reporting as expected failures with nothing red. This is precisely the class the
node-id comparison exists to catch at the end, caught at the stage instead.

Second finding: `tests/harness/dispatchers.py`'s A2A leg moved from `A2ADispatcher.dispatch` to
a module-level `a2a_transport_result` that declares `has_wire` per construction site, so
`EXPECTED_SITES` in `test_harness_wire_response.py` was repointed at two sites, ordered by the
merged tree's actual source order rather than by copying the pre-merge ordinals.

### A latent stage-0 bug, found by the gate rather than by a test

`src/core/signing/revocation.py` did a FUNCTION-LOCAL import of `AGENT_RESOLUTION_CACHE` from
`src.core.signing.request_verifier_middleware` — the module my own stage-0 port replaced with
`verifier.py`. A function-local import is invisible to every import-time check, so it survived
the port pointing at a module that does not exist, and would have failed only at checklist
step 9: a signed request from a counterparty that publishes a revocation list. No unit test
reaches that. Repointed.

### Gate hardening this stage forced (twice)

Layer 2 was **silently broken in both shell versions**. The first anchored its comment filter
at `^` while `grep -rn` emits `path:lineno:content`, so the filter tested the PATH and was a
no-op — it had been "passing" by letting everything through the prose branch. The second
tripped over backtick quoting. Layer 2 is now a Python scanner
(`.claude/merge-review/retired_scan.py`) that:

- distinguishes USE from MENTION with an AST docstring pass;
- gives deleted MODULE names a right boundary, so `src.core.validation` stops matching
  `src.core.validation_helpers` (the same prefix collision that produced a 22-importer
  overcount earlier in this merge);
- excludes matches inside MULTI-line string literals (a guard's own negative specimen, e.g.
  `test_synthesized_fallback_disjunction_is_flagged`, must contain the forbidden shape) while
  still catching single-line ones (`ctx.get("synthesized_error_envelope")` IS a use);
- runs **four controls on every invocation** — use-detected, prose-ignored, ctx-key-detected,
  specimen-ignored — because an exclusion rule that quietly widens into "ignore every string"
  is how this layer would pass by doing nothing.

Its first clean run over all 399 `src/` files is what surfaced the `revocation.py` bug above.

---

## Stage 5 — remaining tests (90 files)

180 agents, 0 errors. **15 overturns during resolution (17%)**, 13 files refuted by the verify
pass, 23 files reporting a deliberately dropped obligation with its successor named.

**Gate: layers 0–3, scoped to 98 paths.** PASS. Whole-tree retired-symbol debt: **0 lines**.

### Mechanical vs semantic

Mechanical by rule: 6 files (Gherkin reflowing, a DRY extraction of run-all-tests helpers, two
guards whose subject #1721 had already removed). The other 84 were semantic — chiefly tests
that graded `Transport.IMPL` as a fourth transport, or reconstructed-exception assertions that
#1721's wire-envelope boundary made lossy.

### The overturns

Four are worth naming because they invert the verdict rather than adjust it:

- `local-uc011-reserved-tld-normalization.feature` PORT → **DELETE**. Both subjects it graded
  were deleted by #1721; porting it would have installed a scenario with nothing behind it.
- `BR-UC-010-discover-seller-capabilities.feature` TAKE-MINE → PORT: our side never had the
  per-channel invalid-credential pair at all.
- `scripts/audit/storyboard_spec.py` TAKE-MINE → UNION, and `_egress_ingest_helpers.py`
  UNION → PORT.
- Five UNION verdicts collapsed to MECHANICAL once read: the two sides' "conflict" was
  formatting.

### The verify pass found a whole CLASS of silent loss the per-file view could not

Three separate refuters independently reported a dangling import of
`tests.helpers.egress_backoff`. That module is a THEIRS-only ADD, and git had not flagged it
as conflicting — it simply was not in the merged tree. Checking the general case:

```
git ls-tree -r --name-only MERGE_HEAD  minus  merge-base   -> 369 THEIRS-only adds
                                        minus  git ls-files ->  93 missing from the merge
```

Of the 93, **50 are deliberate** (DELETE verdicts: the retired `signing_contract` package, the
superseded middleware, migrations #1721 re-authored). **43 were not**, and every one of them
was already classified — the manifest had a verdict; the verdict was never APPLIED. Zero were
unclassified, which is the one reassuring fact here: the classification pass was complete, the
application pass was not.

Restored, with the reason each was found:

| Path | Verdict | How it would have failed |
|---|---|---|
| `tests/fixtures/adcp_conformance_vectors/**` (44 files) | MECHANICAL | Upstream spec data. `test_adcp_conformance_vectors_pin.py` and `test_signing_conformance_canonicalization.py` graded **nothing** — 40 vectors' worth of RFC 9421 canonicalization, unobserved. |
| `tests/helpers/tls_material.py` (+ its test) | PORT (was MECHANICAL) | `tests/integration/conftest.py:32`. The **entire integration suite** failed collection. |
| `tests/helpers/ports.py` | PORT (was TAKE-MINE) | `tests/integration/conftest.py:633`. Same blast radius. |
| `tests/helpers/egress_backoff.py` | UNION (was TAKE-MINE) | Six integration modules import it. |

A TAKE-MINE verdict on a file that exists ONLY on THEIRS is the trap: it reads as a decision
but means "absent", and nothing downstream distinguishes that from "deleted on purpose".

### `egress_backoff.py` is the merge in miniature

The two sides changed the same four helpers for unrelated reasons. THEIRS moved them out of
`test_outbound_http.py` (which was simultaneously a 67-test suite and a helper library for
fifteen modules — the shape `test_architecture_no_cross_test_module_imports` forbids). #1721
rewrote their BODIES, replacing `monkeypatch.setenv` with typed settings injection, because
the seam has no env surface left to drive: `ruff-environment.toml` bans production env reads,
and writing the variable only reached the seam while nothing had yet built the cached settings
object — an ordering condition no call site could see, which silently disarmed refusal cases.

Restoring THEIRS' file verbatim would have been worse than losing the refactor: six modules
would have written environment variables nothing reads. The resolution is the extraction with
#1721's bodies, and `test_outbound_http.py` now imports from it rather than defining them
twice. The eleven allowlisted importers still reach them through that module, so the guard's
allowlist neither grows nor goes stale.

### Restored: an invariant our side had deleted on grounds THEIRS had already answered

`tests/unit/test_guards_a2a_integer_restoration.py`'s construction-site guard was deleted here
on 2026-08-31 for three stated reasons. THEIRS' version (e6f79e71) had independently fixed the
strongest one — the meta-test planted its specimen in `tmp_path` instead of writing a module
into `src/` while a sibling xdist worker scanned `src/`. The second (the allowlist was the
whole FILE, exempting the likeliest place for a second site) is answered by keying the
allowlist on the enclosing FUNCTION: `_dict_to_value` and `_dict_to_struct`, the two that
build wire data. The third — "it grades a LOCATION as a stand-in for behaviour" — is the one
that does not survive contact: nothing else grades it. The ASGI wrapper is graded at the HTTP
boundary and every route is checked for it, but neither can see a `struct_pb2.Value()` built
somewhere that never passes through `restore_a2a_integer_types`.

### Other must-fixes applied

- `tests/integration/test_create_media_buy_behavioral.py` seeded its malformed-URL case with
  `https://exämple.com`, which the merged canonicalizer **accepts** (IDNA → `xn--exmple-cua.com`).
  The `except TargetUriMalformedError` arm was never executed. Reseeded with `https://[::1`.
- `tests/integration/test_vendor_egress.py` asserted `isinstance(exc.internal_detail, str)`;
  on this branch `internal_detail` is `BaseException | None`, the non-wire CAUSE. The
  obligation ("the vendor is interpolated from the argument, not enumerated in one adapter's
  copy") is re-expressed on `details.provider`, where equality grades it more strictly than
  "other name not in text" ever did.
- `tests/bdd/steps/domain/uc011_accounts.py:4354` still called `get_by_natural_key(operator=,
  brand_domain=)`; the merged repository takes ONE `NaturalKey`. It was the last stale caller.
- `tests/factories/__init__.py` imported `CreativeAgentFactory` from `.creative`, where it does
  not exist — it lives in `core.py` beside `SignalsAgentFactory`, whose docstring says so.
- Two stale citations of `TestWebhookAuthenticationForcesASignature`, a class no side has.
- `BR-UC-003` regained `And the suggestion should contain "credentials"` (CODE_TABLE's
  AUTH_MISSING suggestion is "provide credentials via the auth header and retry"). THEIRS'
  companion assertion on the MESSAGE containing "authentication" was **not** restored: the
  message is "No credentials were presented".

---

## Stage 6 — baselines, config, docs (54 files: 43 semantic + 9 by-rule + 2 deletes)

86 agents, 0 errors. **16 overturns of 43 (37%)** — the highest rate of any stage, and the
reason is structural rather than alarming: a classification pass reading a config or doc file
cannot tell "both sides changed it" from "one side is byte-identical to the base" without
opening all three stages, and five overturns are exactly that correction (UNION or TAKE-MINE →
TAKE-THEIRS / MECHANICAL, because there was no ours-side intent to preserve).

**Gate: whole tree, layers 0–5.** Conflict markers 0 · parse ok · retired contract 0 lines over
398 `src/` + `scripts/` files · ruff + all five structural configs clean · **mypy 0 errors** ·
all five suites collect (unit 6236, integration 3178, bdd 8551, e2e 162, admin 143).

### Ratchets: measured, not merged

The doctrine is that a ratchet is never content-merged — it is regenerated after the code
stages, because a merged baseline is a number nobody measured. Every counter was re-run against
the merged tree and every one landed on its existing value:

| Baseline | Merged-tree measurement |
|---|---|
| `.admin-raw-session-baseline` | `admin_get_db_session: 191`, `admin_session_add: 39` — unchanged |
| `.ruff-complexity-baseline` | C901 152, PLR0912 108, PLR0915 88, F841 23 — unchanged |
| `.mypy-untyped-defs-baseline` | 173 — unchanged |
| `.duplication-baseline` | src 25, tests 43, scripts 0 — unchanged |
| `.fixme-citation-baseline` | 0/0/0/0 — resolved to OURS' 4-key shape |

`.fixme-citation-baseline` was the one that needed a decision rather than a measurement. THEIRS
offered a 2-key object; the merged `check_fixme_citation_count.py` declares a 4-key `KEYS`
tuple, so THEIRS' shape is not merely stale but structurally unreadable. Its
`"tests_fixme_beads": 2` was stale too. The measured `tests_quoted_beads` briefly read 1, and
the tempting fix — raise the baseline — is the one the script's own docstring forbids (that
half is a hard gate at zero). The count was a false positive: the merge pulled in a fixture
value `"FLY_APP_NAME": "salesagent-prod"`, which the quoted-beads-id regex matches. Renamed
the fixture value; the counter reads 0.

`tests/bdd/e2e_rest_known_failures.txt` is a LEDGER, not a counter, so the rule is "the side
that owns the file's purpose". OURS subsumes: its block carries THEIRS' claim as history
(*"set_adapter_channels was in-process-only at the time of that note; #1871 then gave it the
same write-through"*) and then records that all three reads were deleted by `a1b79d22d` — and
it carries 5 ledger entries THEIRS' side does not. Taking THEIRS would have dropped the entries
AND reasserted a superseded fact.

### The gate's own layer 4 was passing vacuously

`mypy ... 2>&1 | grep -E '^[^ ].*error:' && fail 4` reads correctly and is wrong under
`set -o pipefail`: mypy EXITS 1 when it finds errors, pipefail gives the pipeline that status
regardless of grep's success, the `&&` never fires, and the layer prints `ok` having just
printed the errors. It passed vacuously for exactly the input it exists to catch — the third
silently-broken layer in this gate, after layer 2's two shell versions. It now captures first
and tests the capture, with a mandatory control proving the matcher still recognizes an error
line.

Repairing it surfaced a live bug immediately: `src/core/tools/accounts.py:1268` did
`from src.core.exceptions import AdCPError` inside the proof-of-control helper. #1721 renamed
the base to `AdCPSalesAgentError`; the import would have raised `ImportError` the first time a
subscriber could not be proven. Function-local, so no import-time check could see it — the
same shape as the stage-4 `revocation.py` bug, found by a different layer.

### `make quality-ci` was red on five ast-grep findings the merge introduced

Two are genuine new EDGES that the serialization rule predates: `src/core/signing/trust_root.py`
composes the PUBLISHED JWKS and brand.json documents (a third party fetches them, so the dump
IS the wire) and `src/services/notification_proof_service.py` composes the activation-challenge
body sent to the buyer's webhook. The rule says an edge is excluded by PATH and that the list
is mirrored in `ruff-serialization.toml`; both were updated in one change.

Three were credential headers built inline. Two are producers and were migrated to
`credential_headers()`, the single producer — including `tests/helpers/signing.py`'s
`request_headers`, which is the signing harness's own header builder. The third is the
documented exemption: `test_trust_root_documents.py` presents `x-adcp-auth: "not-a-real-token"`
to grade that an UNUSABLE credential does not change the answer on a document served outside
the protected surface. The header IS the subject, `credential_headers()` deliberately never
sends that alias, so routing through it would stop presenting the thing under test. Recorded in
the ban's companion `_RECORDED_EXEMPTIONS`, which is where the rule says to record it.

### Corrections the verify pass forced

- **`CLAUDE.md`**, TAKE-MINE → UNION. THEIRS carries a factual correction OURS never received:
  `dist/docs/<version>/` does not exist for the pinned version. TAKE-MINE would have reinstated
  a path that 404s in the one gate that requires citing the spec before writing protocol
  behaviour. While there, `dist/compliance/<version>/*.yaml` was corrected to
  `<version>/<area>/*.yaml` — verified against the checkout: there are no yaml files directly
  under `<version>/`.
- **`docs/design/signing-vs-request-boundary.md`** cited `security.mdx :1198` for the namespace
  split. At the PINNED tag v3.1.1 that rule is at **:1053**; :1198 is a
  `##### Reference implementations` heading. Verified by reading the tag.
- **`alembic/versions/d5185367920c_...`**, MECHANICAL → **DELETE**. Keeping it left a dangling
  `down_revision` (`6371c0f43f54` has no file in the merged tree) and a SECOND alembic head.
- **`pyproject.toml`**, UNION → TAKE-MINE. THEIRS' one unique hunk was a `[tool.ruff.format]`
  exclude naming `src/core/signing_contract/_upstream/*.py` — a package #1721 deleted — citing
  a test that no longer exists.
- **`tests/integration/test_storyboard_ledger_fitness_real_session.py`**, TAKE-MINE → UNION.
  OURS' `from tests.unit import test_storyboard_ledger_state` violates a guard that exists ONLY
  on THEIRS. THEIRS' change is the fix for a guard our side never saw, and the alternative —
  an allowlist row — is forbidden.
- Four MECHANICAL → UNION on guards whose pinned COUNTS were true on one side and false on the
  merged tree (`21/18/13` declared tuples, `31/37` wired rows, the behavioural-mock caps). Each
  needed re-measurement against the merged tree, which no take-one-side rule produces.

### Six more THEIRS-only files the merge had dropped

The stage-5 sweep found 43; the pinned-schema closure is 6 more —
`3.1.1/core/{provenance,special,talent}.json` and
`3.1.1/enums/{asset-content-type,audio-distribution-type,collection-cadence}.json`. They carried
stage-3 DELETE verdicts, but `_refresh.py`'s ROOTS closure regenerates exactly them, so the
verdict was wrong rather than unapplied. Restored.

## Stage 7 — the unit suite (the first layer that runs code)

Layers 0–5 were green while **28 unit tests were red**. That is the whole argument for layer 6
existing: markers, parse, lint, types and collection all pass on a tree whose behaviour is
wrong, because every one of them is a claim about SHAPE.

14 modules, 28 agents, 0 errors, **0 left failing**. Final: **6169 passed, 37 skipped,
31 xfailed, 0 failed.**

Every failure had the same shape — a test from one side grading a subject the other side moved
— and they sort into four kinds:

**A guard from THEIRS grading a file #1721 deleted.** `test_architecture_signed_target_uri_raw_path`
grades `request_verifier_middleware.py`; #1721 split it into `signing/capture.py` (builds
`@target-uri`) and `signing/verifier.py` (`_strict_header_precheck`, now HANDED
`HttpExchange.raw_headers` rather than reading the scope). Repointed, and both its ratchets
SHRANK (3→2 twice) because the deleted `rest_compat_middleware` row went with it.

**A count or roster true on one side and false on the merged tree.** The e2e_rest webhook pin
(THEIRS 9, OURS 5, merged 5 — the resolved pin said 3, matching no tree), the
`_KNOWN_PLATFORM_CODES` roster, the duplicate-step baseline (24→22), the harness
`realize_e2e` allowlist.

**A half-merged atomic change.** THEIRS decorated five circuit-breaker seams in
`tests/harness/_mixins.py` AND declared them in the escape-hatch pin; the merge took one file
and not the other.

**Two parallel solutions to one problem, both landing.** `tests/bdd/conftest.py` ended up with
`_build_capabilities_env` defined twice and with two `uc010-capabilities` rows. OURS' row sat
first, so the pair already computed the union and no scenario was mis-routed — but the second
definition and the duplicate row were dead weight the duplicate-def guard correctly refused.
Folded into one `_uc010_wired_tags()` = OURS(67) | THEIRS(67) = 71, with
`_UC010_DORMANT_TRACKING` falling 16 → 12.

### What the adversarial pass caught that the fixes did not

5 of 14 fixes were refuted. Two were factual claims rather than code:

- The uc010 agent reported a **production bug — four RFC 9421 scenarios newly graduated**.
  The refuter proved it fabricated: those four were already executing and passing, because
  OURS' row preceded THEIRS' catch-all. Retracted rather than recorded; a phantom graduation
  in a merge ledger is worse than no note at all.
- The e2e_rest ledger entry was re-parked with a reason that was wrong on three counts — it
  described THEIRS' step body, not the merged one. #1721 STRENGTHENED that Then to read the
  inner `result` (`uc004_delivery.py:2211`), and the merge kept it. So the row is not a vacuous
  pass: over e2e_rest the deployed server's `result` is a serialized
  `GetMediaBuyDeliveryResponse`, which always sets `aggregated_totals`
  (`media_buy_delivery.py:596`) — exactly what 3.1.1 `L3/webhooks.mdx` :253 forbids in a
  reporting webhook payload. It is a **real production violation (GH #2058)**, and it unparks
  when production stops emitting the field, not when the Then is changed.

Three were real defects in the fixes:

- **The signing guard's repoint was a strict weakening.** Its two recorder assertions asked
  "does a call to `_target_uri` / a read of `scope['headers']` appear anywhere in `__call__`?"
  That was safe before #1721, when the builder was a separate function with no allowlist row.
  After the move `__call__` ALSO evaluates the surface predicate, which carries an allowlisted
  decoded-path read — so a capture that called `_target_uri` for the predicate while building
  `url=` from `scope["path"]` passed every check while handing the verifier a `@target-uri` the
  client never signed. The refuter demonstrated both holes with mutations that left the module
  at 17 passed. Both assertions now bind to the actual `HttpExchange(...)` KEYWORD ARGUMENT,
  and the `raw_headers` one additionally refuses a `dict()` / `headers_from_asgi_scope()`
  collapse inside that kwarg — last-wins on a repeated header line is the `negative/021, 022,
  023, 026` attack, and it satisfies the gate's raw parameter type with an already-lossy value.
- **The duplicate-step ratchet had a hole the incoming branch did not.** THEIRS graded
  `assert not duplicates` at threshold 3: no body shared by 3+ steps, full stop. OURS replaced
  it with a GROUP-count ratchet, which is better at catching a new pair and blind to an
  existing pair GROWING into a trio — the group count does not move. Demonstrated with a
  planted third clone of the `tenant_id` pair: guard green. A two-sided MEMBER ratchet
  (`_DUPLICATE_MEMBER_BASELINE = 45`) now carries THEIRS' obligation alongside OURS'. Verified
  by re-planting the clone: 22 groups unchanged, 46 members, RED.
- A fixture hand-rolled a `capability_declarations` document that has a single owner
  (`tests/helpers/signing.posture_declaration_document`) and filled it with a
  `brand_json_url` literal production refuses — the pointer is DERIVED from the tenant.

### Two production defects, both fixed at the root

- `src/core/signing/outbound.py` spelled `.strip().lower().replace("_", "-")` twice: once to
  build the `_DELIVERY_AUTH_MODES` key set, once to look up in it. Two independent foldings of
  one concept is how the set and the lookup drift apart.
- Four spec citations in shipped docs and tests named `dist/docs/3.1.1/`, a prose root that
  resolves at no tag, one of them also naming a directory that does not exist. Documentary,
  but inside the guard's own contract — and the same defect class as `CLAUDE.md`'s.

## The signature path across transports — a divergence hypothesis, measured

A storyboard summary appeared to show four `signed_requests` checks failing on A2A and passing
on MCP. Under #1721 that is supposed to be structurally impossible: every transport hands the
same `serve()` the same headers and the same captured exchange, and verification happens once
inside `_resolve_identity`. So the hypothesis was a merge-introduced per-transport fork, with
the exchange-sourcing split as prime suspect — MCP reads it through
`get_http_request().scope`, A2A through `ServerCallContext`, and `/a2a` is a `Route` appended
to the app's route table rather than a `Mount`.

**Measured, and refuted.** The `/a2a` endpoint receives the SAME scope dict object — identical
`id()` — that `SignedExchangeCapture` wrote to, with `adcp_signed_exchange` present. Driving a
well-formed `SendMessage` through the real app, `AdCPCallContextBuilder` places a real
`HttpExchange` carrying the right method, the client-addressed `@target-uri`, seven raw header
LINES and the exact body bytes. A directly-appended `Route` is inside the `add_middleware`
stack exactly as a `Mount` is, and both sourcing paths end at the one `captured_exchange()`.

**And the merge did not touch that path.** `capture.py`, `context_builder.py` and
`adcp_a2a_server.py` are byte-identical to HEAD; `src/app.py`'s whole delta from HEAD is a
`well_known_router` include and one return annotation.

The storyboard numbers that raised the hypothesis came from runs whose own summaries say
`overall_status=partial` with `@adcp/sdk 14.0.0-rc.35` targeting AdCP 3.2.0-rc.1 against a
repo pinned to 3.1.1 — not a verdict set.

### What the investigation did find

**A real A2A-only hazard in the signature path, pre-existing on #1721 rather than introduced
here.** `a2a_messageid_compatibility_middleware` (`src/app.py:546`) is registered with
`@app.middleware("http")` at import time, BEFORE the `add_middleware(SignedExchangeCapture)`
block at line 636. Starlette applies last-registered-outermost, so the true order is

```
AuthChallengeResponder -> CORS -> SignedExchangeCapture -> a2a_messageid_compatibility -> router
```

— the compat middleware is INNER to the capture, and it rewrites the request body for `/a2a`
POSTs whenever the JSON-RPC `id` or `params.message.messageId` is numeric. Both rewrites fire
in practice. The capture's own comment at line 632 says it is *"INNERMOST, so that nothing
between it and the app can rewrite the body it recorded a digest-able copy of"*; that claim is
false and its counter-example is registered ninety lines above it in the same file. For a
signed A2A request the bytes VERIFIED (the capture's) and the bytes EXECUTED (the rewritten
ones) differ — server-side mutation of a message the buyer signed, on one transport only.

Not fixed in this merge: it is not a merge defect, the fix is a design decision (delete the
shim, or move it outside the capture), and this commit's job is the merge. Filed as the
follow-up it is.

**A vacuous test over exactly the path a fork would hide in — fixed here.**
`test_the_capture_lands_where_every_transport_reads_it` names all three transports in its
docstring and drives two: `/mcp` and `/api/v1`. The A2A leg had never been executed, which is
the leg with a different route kind AND a different sourcing path.
`test_the_a2a_leg_really_reads_the_capture` now drives the REAL app over HTTP and asserts on
the exchange the production context builder actually placed — method, target-uri, raw header
lines and exact bytes, each against what the client sent rather than a value the test also
computed. Verified non-vacuous: dropping `/a2a` from `ADCP_SURFACE_PREFIXES` turns it red.

## PARKED — malformed-body ordering vectors (owner decision)

Recorded verbatim, as the owner stated it:

> The vectors in question grade ORDER OF OPERATIONS (did the implementation read the header
> before the body, validate before checking whether it could validate) rather than the
> OBSERVABLE OUTCOME. A malformed body fails validation and the buyer receives
> INVALID_REQUEST — the correct answer — whether or not the signature was examined first.
> That is AdCP-correct processing. Grading internal sequence is not protocol compliance, and
> given the documented entanglement between the HTTP and protocol layers it is not even
> well-defined. The owner's position is that this is a defect in the CONFORMANCE RUNNER, to be
> raised upstream, not a defect in this agent.

**Parking is a ledger entry, not a test edit.** No assertion is deleted or weakened, and no
test is rewritten to accommodate the sequence. One rewrite had already been made by the
behaviour fan-out — `TestTheVerifierIsHandedTheWireBytes._signed_media_buy_request` was
changed to sign a `CreateMediaBuyRequestFactory` payload instead of the sketch body it signed
under #1291's middleware — and it has been REVERTED for this reason. The module is back to the
form it had before that pass.

On this tree `serve()` (`src/core/tools/_boundary.py:258`) evaluates `validated_request(...)`
as an argument to `invoke_tool(...)`, and only `invoke_tool` reaches `_resolve_identity:344`
where the signature is read. So a body the DTO refuses is answered INVALID_REQUEST with the
verifier never called. Every failure below has that, and only that, as its cause.

### Parked vectors

All three live in `tests/integration/test_request_signature_middleware.py`, and all three were
MASKED in the full-stack run `innet_170926_0547` behind the `Principal.access_token` defect —
they surfaced only once that was fixed at the root, which is why the run's own report shows
zero of them.

| Vector | Why it is parked |
|---|---|
| `TestTheVerifierIsHandedTheWireBytes::test_signature_over_wire_bytes_verifies_on_a_body_carrying_route` | Signs a sketch `create_media_buy` body. The DTO refuses it, so INVALID_REQUEST is answered and the SDK verifier is called 0 times. Grades sequence, not outcome. |
| `TestTheVerifierIsHandedTheWireBytes::test_verified_signature_increments_the_verified_counter` | Same class, same sketch body: `adcp_request_signature_verified_total` cannot move because verification never runs. Same cause, same parking. |
| `TestANarrowedNoneRequestThatNeverFinishesArriving::test_a_mid_body_disconnect_passes_through_and_is_still_counted_as_ignored` | A deliberately truncated body on `get_adcp_capabilities`. It cannot parse, so INVALID_REQUEST is answered and `adcp_request_unsigned_total{reason='ignored'}` never moves. The buyer's observable answer is correct. |

The buyer-visible outcome in every one is INVALID_REQUEST on a body that is in fact invalid.

## Phase 7 layer 5 — bare node-id comparison against BOTH parents

The layer a green suite cannot substitute for: it compares what EXISTS, so a test that slides
from passing to expected-failure is visible where no assertion would fail.

**Two traps, both hit before the numbers meant anything.**

The first run reported bdd as 1159 dropped / 8550 gained with ONE id in common, which reads as
catastrophe and means nothing: the parent baselines were collected BARE while the merged tree
was collected with parametrization, so every parametrized test counted as both dropped AND
gained. Both sides are now normalized to bare ids. Parametrization is not discarded — the
report prints the per-suite param ratio beside the bare count (bdd 7.35, unit 1.28, admin 1.05),
so an arm quietly disappearing still shows as a falling ratio.

The second: a drop is only explained if its explanation is CHECKED. The rename entry asserts
its named successor is present in the merged tree, so a stale explanation fails loudly instead
of silencing a real drop.

**Result — zero unexplained drops in any suite against either parent.**

| suite / parent | parent | merged | dropped | explained by |
|---|---|---|---|---|
| unit / ours | 6144 | 4882 | 2003 | 1014 deleted-by-1721, 364 rewritten, 625 in-surface |
| unit / mine | 4591 | 4882 | 18 | 18 in-surface |
| integration / ours | 2802 | 2491 | 450 | 118 deleted, 97 rewritten, 234 in-surface, 1 renamed |
| integration / mine | 2322 | 2491 | 1 | 1 renamed |
| bdd / ours | 1159 | 1163 | 88 | 8 deleted, 65 rewritten, 15 in-surface |
| bdd / mine | 1155 | 1163 | 0 | — |
| e2e / ours | 166 | 136 | 37 | 10 deleted, 2 rewritten, 25 in-surface |
| e2e / mine | 105 | 136 | 0 | — |
| admin / ours | 134 | 136 | 1 | 1 in-surface |
| admin / mine | 136 | 136 | 0 | — |

Against OUR OWN side (`mine`) the merge drops **19 bare ids in total** — 18 in-surface unit ids
and one rename — and gains 1,851. Nothing this branch graded was silently lost.

**The one rename, and it was caught here rather than noticed.**
`test_real_run_records_uc005_format_id_roundtrip_scenarios_as_live` is absent from both parents
and from the merged tree. It was renamed in the stage-7 behaviour pass — by this work, not by
either parent — and STRENGTHENED in the same edit.
`test_real_run_records_uc005_scenarios_as_ledgered_or_live` grades an EXACT partition of the
same ids into ledgered-xfail and live-pass, reads which is which from the live `_XFAIL_TAGS`
routing map rather than a frozen list, and requires a ledgered record to carry the ledger's own
reason verbatim. Its own docstring says why the relaxed "either live or ledgered" predicate it
replaced was not good enough. A stronger successor, named and pinned.

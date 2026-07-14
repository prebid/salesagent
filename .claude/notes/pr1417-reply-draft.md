Thanks for the thorough re-review at `cd3391f12`, @ChrisHuie. All items are addressed, pushed at `33d010934` — **CI green** (all 31 checks). I also caught `origin/main` up (through `b5e487d7a`: `#1543`/`#1519`, `#1506` concept-enrichment, `#1560` ci-residuals), so the branch is no longer `CONFLICTING` and is `MERGEABLE`.

Before touching anything I triaged each finding for scope (blame + `git merge-base --is-ancestor … origin/main`), because two are pre-existing and one didn't reproduce. Item by item:

### Should-fix — fixed

**1. Suggestion parity — REST routes unwrapped** → fixed in `1e33a311c`.
You're right, and it was wider than the two you named: `list_authorized_properties`, `list_accounts`, **`sync_accounts`**, and `get_products` all reconstructed a strict Request from a loose Body outside the boundary. All four now wrap construction in `adcp_validation_boundary(context="<tool> request")`, mirroring `list_creative_formats`. Added 3 red-first parity tests (invalid body → top-level `suggestion` on the envelope) plus an AST guard (`test_guards_rest_request_boundary.py`) that fails if a REST route builds a Request outside the boundary, so this can't regress.
While in there I found the **same disease on the A2A transport** (skill handlers build strict Requests without the boundary). That's a genuinely separate surface, so I filed it as a follow-up rather than widen this PR.

**4. `.type-ignore-baseline` grew 69→82** → fixed in `4d8fec0a5`, baseline now **71**.
Took your suggested approach: coerce each REST body into its SDK request type once at the boundary (`src/core/schema_helpers.py`) instead of forwarding loose dicts field-by-field. That removed the 12 `api_v1.py` `[arg-type]` ignores. Three ignores remain (down from the 13 net-new) and are individually justified in-line, not a blanket ratchet. (First attempt was to widen the typed params — the typed-params guard correctly rejected that, so I moved to boundary coercion.)

**2. Dead `UC-003` dispatch branch** → fixed in `385388920`.
Confirmed unreachable: the `MediaBuyDualEnv` branch (`c8f9d31d7`) was inserted above the older `MediaBuyUpdateEnv` route in the same `elif` chain instead of replacing it, so every `UC-003` scenario matched the earlier branch (or its `xfail` catch-all) first. Deleted the dead branch — the uc003 BDD slice is byte-identical before/after (114 passed / 1249 xfailed). Also added an AST guard banning duplicate test expressions within one `if/elif` chain, and corrected a stale docstring that still claimed `MediaBuyUpdateEnv`.

### Nits — fixed

**Duplicated account-authorization suggestion** → fixed in `a28a1885d`.
One scope correction: this one is **not pre-existing**. The account-resolution scaffolding is old, but the duplicated `suggestion=` literal was added to *both* resolvers by a single `#1417` commit (`44703f395b`) — the duplication was born in this PR. Hoisted the shared access check into `_require_account_access(...)`; added a natural-key non-disclosure test.

**Guard matcher gaps + beads ids in docstrings** → fixed in `5aa5dfded`.
Broadened both matchers with meta-tests: `_dict_has_suggestion_key` now also matches the `dict(suggestion=…)` call form, and the validation-boundary guard now matches `AdCPValidationError` **subclasses**, not just the exact class name (both were 0-occurrence today, so this is hardening). On the docstrings: `#1417` is a GitHub ref and is fine per convention — the actual violation was the co-located beads ids (`salesagent-*`), which I stripped from the touched docstrings while keeping the `#1417` refs.

**`rc.12` spec citations → GA `3.1.0`** → fixed in `c9397459b`.
Verified upstream that published GA `3.1.0`/`3.1.1` grade the field identically first, then refreshed all citations (`docs/adcp-spec-version.md`, `_base.py`, 3 BDD files). No behavior change.

### Not changed — with reasons

**Empty-update GRADUATED label** — this one didn't reproduce. `@T-UC-003-empty-update` is actually routed **live** (it appears in no xfail dict/set in `conftest.py`, the e2e-rest known-failures list, or the traceability sources), the integration test `test_empty_update_surfaces_wire_error_envelope` is live and asserting `INVALID_REQUEST` + top-level suggestion, and the `GRADUATED` comment is accurate. The comment just sits between two unrelated xfail blocks, which I think is what read as a contradiction. No code change; happy to reword the comment to `GRADUATED (live, no xfail)` if you'd like the disambiguation.

**`update_media_buy` drops top-level `targeting_overlay`/`creatives`** — confirmed real, confirmed **pre-existing** (byte-identical on `origin/main`, from 2025 commits; this PR never touched that transport-boundary path). These params are vestigial — no longer AdCP `UpdateMediaBuyRequest` fields since the per-package consolidation — so the correct fix is to *remove* them, not wire them in. Filed as a follow-up so it gets a spec-grounded decision rather than a rushed change here.

**Allowlist-diff guard full-chain self-test** — as you noted, this came in via merged `#1541`, not this PR. Filed a follow-up against that to restore one full-chain known-bad case; not gating this PR on it.

### Verification
CI green on `33d010934` — all 31 checks pass (Unit / Type Check / Quality Gate / all Integration shards / BDD / E2E / Admin / Security). Both new AST guards mutation-checked locally.

One merge-hygiene note, since it touched your repo's new guard: `#1560` added `test_allowed_duplicate_entries_still_exist`, whose `_ALLOWED_DUPLICATES` listed 9 `buyer_ref` step names (uc019/uc026). This branch had already removed those steps when top-level `buyer_ref` was stripped from the media-buy contract (`1670cc2fc`), so on merge those entries were stale. I emptied the allowlist — all 9 verified as steps this branch deleted (live-on-main / absent-here), forward guard passes with an empty allowlist, and a full-tree scan shows zero un-allowlisted 3+ duplicate clusters. Allowlist shrinks, never grows.

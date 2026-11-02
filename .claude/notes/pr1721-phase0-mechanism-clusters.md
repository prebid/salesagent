# PR #1721 — Phase 0: mechanism clusters

Date: 2026-08-19. Branch `feature/spec-gaps-1210`.

**Purpose.** Re-partition the open remediation backlog from *symptoms* (one ticket = one
observation) into *mechanisms* (one cluster = one false assumption in the code). The unit of
execution becomes the invariant; the tickets become acceptance evidence for it, not scope.

**Method.** Read the full text of all 15 open items on the current remediation list, plus the
close reasons of the four just-landed items (prkv.8, prkv.12, prkv.15, prkv.16, prkv.17), and
grouped by *shared false assumption*, not by file, theme, or lane. Cluster sizes are measured by
AST scan where the shape is mechanically detectable, not asserted. Adjacent tickets outside the
list that share a mechanism are named — that they were filed into other epics is precisely the
bookkeeping artifact this exercise exists to correct.

**Prior art in this repo.** `.claude/notes/pr1721-architecture-diagnosis.md` did exactly this at
review round 1 (five disease classes D1–D5, a coherent remedy M1–M6, an enforcement story per
measure, and a §6 process countermeasure whose conclusion was *"prose does not bind agents; gates
do"*). Round 3 produced 16 findings and reverted to lane-by-lane, ticket-by-ticket execution.
The method was already invented here and then dropped; this note restores it.

---

## Summary

15 open items resolve to **6 mechanisms**. Two of them contain four or more tickets each.
Two of them are currently at the *bottom* of the execution order and are the reason the others
stayed invisible.

| # | Mechanism | Tickets on the list | Adjacent (other epics) | Measured size |
|---|-----------|--------------------|------------------------|---------------|
| E | A gate reports a verdict it did not compute | ssg4e, aemue.13, prkv.11 | z6s3, 7con, 1zq3.1, 1zq3.27 | — |
| D | Guards resolve their subject by deriving an identifier instead of importing the artifact | prkv.10, prkv.6 | e0gj.5, 1zq3.11, 1zq3.12, 1zq3.35 | 14 helper-bypassing guards (prkv.10) |
| C | The wire is opt-out, not opt-in | zfm9f, dvx2y, udff5, j6dp8, prkv.9(a) | q9e6.2, q9e6.7, q9e6.11, 1zq3.34, 9phu | **25 sites; primitive at 48% adoption; 0 guards** |
| F | Transport edges are hand-assembled | prkv.5 | hg1lu, gdsk, 9bt1, ooke | — |
| A | The unit of work has no composition law | db4ci, prkv.13 | GH #1644 ×2 (already allowlisted) | 2 live + guard exists |
| B | A predicate the type owns is recomputed at call sites | ka79t | rtapr, ao8dl, u0gy, 9bt1 | — |
| G | Genuinely standalone | prkv.7, prkv.9(b) | — | — |

---

## Mechanism E — A gate reports a verdict it did not compute

**Members.** ssg4e (P1), aemue.13 (P2), prkv.11 (P2, documentation arm).
Adjacent: z6s3, 7con, 1zq3.1, 1zq3.27, and prkv.10's mutation-self-test clause.

**False assumption.** *A green result is evidence the check ran.*

Each member is a different way that assumption fails:

- **ssg4e** — `cassini run` reports `finished, exit=0` while 6 of 7 suites are STALE, reusing an
  older run's report. A second instance reported `finished, exit=1` with an empty results
  directory while containers were still mid-flight.
- **aemue.13** — the Layer-2 ratchet hooks are assigned `stages: [pre-push]`, and this repo's
  documented workflow never pushes. The gate is configured and structurally unreachable. Two
  `type: ignore` additions landed silently over four days as a result.
- **1zq3.1 / 1zq3.27** — scenarios that pass under the opposite policy; a test that does not
  redden on the divergence it names.
- **7con** — a conformance test converts its own findings into `pytest.skip`.
- **prkv.11** — the PR description claims a grading gate the same diff xfails.

**Why this is first and not fifth.** Until this closes, no remediation is verifiable and *every*
"full suite green / zero regressions" claim in this PR's history is unproven — including the
close reasons on the four items just landed. Everything downstream is graded by a gate we know
can report success without running.

**Invariant.** *A green result is evidence the check ran and could have been red.* Freshness is
part of the verdict; a check with no mutation self-test is not a check; a hook that cannot fire
on this repo's actual workflow is not a hook.

**Enforcement.** Freshness assertion in the runner (each suite's report regenerated in *this*
invocation, or the run is red); a decision on aemue.13's (a)/(b)/(c); prkv.10's
"every guard ships a mutation self-test — a guard without one is not done" applied to the
existing guard corpus, not only to guards this PR touched.

---

## Mechanism D — Guards join on an identifier, not on the artifact

**Members.** prkv.10 (P2), prkv.6 (P2). Adjacent: e0gj.5, 1zq3.11, 1zq3.12, 1zq3.35.

**False assumption.** *A guard can resolve its subject by reconstructing its name.*

prkv.10 already contains this exact diagnosis, written out: *"every blind spot found in this
review is one shape — the guard resolves its subject by DERIVING an identifier instead of
IMPORTING the artifact, so it fails silently in exactly the case it was written for. That is how
`post_capabilities`' dropped `ext` stayed invisible and how this PR's own `_error_details` walked
through the hand-rolled-envelope guard added in the same diff."*

The adjacent tickets are the same shape at matcher granularity: a frozenset-only matcher misses a
bare set literal (1zq3.11); an `ast.Dict` matcher misses the `dict(...)` form (1zq3.12); a guard
scans 18 of 109 files and none of `src/` (1zq3.35).

**Invariant.** *A guard imports the artifact it grades.* Any guard whose subject is resolved by
string reconstruction, hand-maintained name list, or partial glob is defective regardless of
whether it currently passes.

**Enforcement.** prkv.10's own acceptance clause, plus its ~30-line scaffolding meta-guard
(no bare `ast.parse` / `parents[2]` outside `architecture_helpers.py`, seeded empty).

**Dependency.** prkv.10 is gated on lanes A–D settling. A (prkv.2), B (prkv.3), C (prkv.4) are
closed; D is prkv.5 (Mechanism F). So F precedes D.

---

## Mechanism C — The wire is opt-out, not opt-in  ← highest leverage

**Members.** zfm9f (P2), dvx2y (P2, 8 sites), udff5 (P2, 5 sites), j6dp8 (P3), prkv.9's
suggestion-constant half. Adjacent: q9e6.2, q9e6.7, q9e6.11, 1zq3.34, 9phu.

**False assumption.** *A message composed at a raise site is a server-side message.*

It is not. `normalize_to_adcp_error()` returns any `AdCPSalesAgentError` unchanged — in dvx2y's own words,
**"the raise site IS the wire."** Every one of these sites is a developer writing a debug string
that turns out to be a published document governed by AdCP 3.1.1
`transport-errors.mdx` §Security Considerations ("Implementations MUST NOT include: internal
service names, hostnames, or IP addresses").

**Measured size** (AST scan of `src/`, 2026-08-19):

- **13** raise sites construct an `AdCP*Error` interpolating a name bound by `except … as NAME`
  into the buyer-facing message.
- **9** raise sites launder third-party text through `ValueError(f"…{e}")`, which
  `exceptions.py:1327-1328` (`isinstance(exc, ValueError) → AdCPValidationError(str(exc))`)
  forwards verbatim — with a wrong error code on top of the leak.
- **3** further sites interpolate a seller-configured internal hostname (`mcp_url`) rather than an
  exception (zfm9f).
- **25 total.**

**The decisive fact.** The correct primitive **already exists**. `exceptions.py` defines
`internal_detail=` with the docstring *"the single emission point for every raise site that hands
its raw cause to `internal_detail=` instead of interpolating it into the buyer-facing message."*
It is used at **12** sites. Twenty-five sites still do it the old way. **There is no guard.**

That is a 48%-adopted abstraction with no ratchet — the exact half-applied-extension pattern that
makes a ticket-by-ticket approach a treadmill. Four tickets executed separately produce four
bespoke sentences and a twenty-sixth site next month.

**Second half of the same mechanism — code fidelity.** The `ValueError` and `PermissionError`
branches of `normalize_to_adcp_error` are *type-sniffing fallbacks*: the buyer's error code is
decided by whichever Python exception class happened to escape, not by a semantic decision at the
raise site. udff5 site #5 is the proof — a typed `AdCPNotFoundError` is flattened to `ValueError`
and reaches the buyer as `VALIDATION_ERROR`. prkv.8 already removed this trust for untyped
exceptions and left the `ValueError` branch verbatim.

**Invariant.** *No text composed at a raise site reaches the wire, and no error code is inferred
from a Python exception type.* The buyer-facing sentence comes from a registered first-party
catalogue keyed by code; raw cause goes to `internal_detail=` and the server log; an untyped
escape is `INTERNAL_ERROR` with a generic sentence, full stop.

**Enforcement.** A guard — mechanically trivial, it is the 30-line AST scan already written for
this note — asserting that no `except … as NAME` binding appears inside an `AdCP*Error`'s first
positional argument, nor inside a `ValueError`/`PermissionError` raised under a handler. Seed at
25, ratchet to 0. Registered-suggestion conformance (prkv.9's half) folds into the same catalogue.

---

## Mechanism F — Transport edges are hand-assembled

**Members.** prkv.5 (P2). Adjacent: hg1lu (P1), gdsk (P1), 9bt1 (P1), ooke (P1).

**False assumption.** *A transport edge may enumerate its own fields.*

prkv.5's DO-list is a catalogue of this one shape: handlers that do not go through
`build_X_request(**select_request_fields(XRequest, bag))`; `collect_divergences()` pointed at the
transport union instead of `RequestModel.model_fields`; an **18-name hand-list** standing in for
the A2A integer set; a `_body_name()` blind spot.

hg1lu is the purest instance and is *not on the list*: the A2A `get_media_buys` handler declares
its **own local `GetMediaBuysRequest`** instead of using the SDK model, and the hand-rolled copy
diverged — `extra=forbid` now rejects `ext`/other fields. That is the CLAUDE.md Pattern #1
violation in its literal form, filed as an unrelated P1 bug.

**Invariant.** *A transport edge derives its field set from the canonical model's `model_fields`.*
No hand-maintained name list; no locally-declared duplicate of an SDK type; one builder.

**Enforcement.** prkv.5's existing acceptance (red/green on `singular_to_plural_merge`), plus a
guard that no module outside `src/core/schemas/` declares a class whose name matches an SDK
request/response model.

---

## Mechanism A — The unit of work has no composition law

**Members.** db4ci (P1), prkv.13 (P2). Already allowlisted: the two GH #1644 entries in
`tests/unit/test_architecture_nested_unit_of_work.py`.

**False assumption.** *A function may open the unit of work it needs.*

`get_db_session()` yields the thread-scoped Session with **no nesting refcount**
(`database_session.py:225-226`). So opening a unit is safe only if no unit is already open — a
condition expressed nowhere: not in the constructor, not in the type, not (until prkv.16) in a
guard. Every call site that composes two operations is a coin flip.

- **db4ci** — `update_media_buy` calls `_sync_creatives_impl` inside its own `MediaBuyUoW`; the
  inner unit commits the outer's in-flight writes, then closes and de-registers the session out
  from under it. On the preview arm the inner rollback discards the outer's writes.
- **prkv.13** — the same absence seen end-on: `_already_proven_tuples` opens its own `AccountUoW`,
  then network I/O, then the write transaction opens. Three units for one request, with a TOCTOU
  window between them.

**The correct shape already exists**, one function away from the defect:
`_assignments.py:88-91` — `if uow is None: uow = stack.enter_context(CreativeUoW(...))`. Join the
caller's transaction when given one; own one otherwise.

**Invariant.** *One request, one unit. The outermost caller owns; every callee joins.* Ownership
is a caller decision, never a callee decision.

**Enforcement.** `BaseUoW.__enter__` joins-or-refuses when a unit is already active on this
thread, making the defect unconstructible at runtime — plus driving
`test_architecture_nested_unit_of_work.py`'s ALLOWLIST from 2 to 0. The guard already exists
(landed in prkv.16, call-graph-aware, shrink-only, non-vacuous). This cluster is "empty its
allowlist and add the runtime law so it cannot refill."

---

## Mechanism B — A predicate the type owns is recomputed at call sites

**Members.** ka79t (P2). Adjacent: rtapr (P1), ao8dl (its execution epic), u0gy (P1), 9bt1 (P1).

**False assumption.** *A derived fact may be maintained alongside the fact it derives from.*

ka79t is precise: the decision "does this creative need approval" is represented **twice** — as
`needs_approval` / membership in `creatives_needing_approval`, and as `creative.status`. The
UPDATE arm flips `status` to `pending_review` *after* the append decision has been consumed
(`_sync.py:269-274`); the CREATE arm's `needs_approval = True` is a dead store after the append
(`:322-325`) so the warning says "flagged for review" while the row commits `approved`. Both arms
of GH #1987, reached without any assignment failure.

rtapr states the general form in its own title: *"Format identity is one thing the type owns, not
four call-site guesses."* u0gy and 9bt1 are the same disease on the same data.

**Invariant.** *A predicate computable from an object's own data is a property of that object.*
`status` is derived from the approval decision, computed once, never mutated afterwards.

**Enforcement.** Set `status` in exactly one place (or make it a computed property), so status and
list membership cannot disagree; extend the new Then step's grader to drive the provenance route
so its universal name is earned. Guard candidate (shared with rtapr/ao8dl): no comparison of a
model field against a literal set outside the model's own module.

---

## Mechanism G — Genuinely standalone

- **prkv.7** (P2, **should be P1**) — `e381618812f1`'s `downgrade()` silently NULLs spec-valid
  rows. Data loss. The mechanism-flavoured half: this PR set its own migration-test standard and
  applied it to two of its three migrations, because the standard is prose, not a guard. Fix +
  the prescribed shared `abort_downgrade_if_rows` helper + a guard that every non-trivial
  `downgrade()` either aborts-on-rows or has a test.
- **prkv.9(b)** — the header-presence branch at `auth_context.py:126-129` emitting `AUTH_MISSING`
  for a *presented* credential. A genuine one-site logic bug. (prkv.9's other half — unregistered
  suggestion text — belongs to Mechanism C.)

---

## Proposed order

Dependency-correct, mechanism-ordered. The current list's order is shown for contrast.

| New | Mechanism / work | Was |
|-----|------------------|-----|
| 1 | **E — trust the gate.** ssg4e, aemue.13. Nothing downstream is verifiable until this holds. | 5 |
| 2 | **F — edges derive from the model.** prkv.5. Also unblocks prkv.10. | 6 |
| 3 | **D — guards import artifacts; every guard has a mutation self-test.** prkv.10, prkv.6. | 6 |
| 4 | **C — one wire catalogue.** zfm9f, dvx2y, udff5, j6dp8, prkv.9(a). 25 sites, one primitive, one guard. | 4 |
| 5 | **A — one request, one unit.** db4ci, prkv.13, #1644 allowlist → 0. | 1 |
| 6 | **B — the type owns its predicates.** ka79t. | 2 |
| 7 | **G — standalone.** prkv.7 (raise to P1), prkv.9(b). | 3 |
| 8 | **prkv.11 — claims.** Last, because it must describe what actually shipped. | 6 |

**The two inversions worth arguing about.**

1. *E and D move from last to first.* They are not cleanup. E is why "verified green" currently
   means nothing, and D is why C and F stayed invisible long enough to become 25-site and
   18-name-hand-list problems. A defect class that hides other defect classes cannot be sixth.
2. *A moves from first to fifth.* db4ci is the only P1 and a live correctness bug, which is a real
   argument for landing it early. If risk appetite demands it, land it early — but land it as
   **the invariant** (join-or-refuse in `BaseUoW.__enter__`, allowlist to 0), not as the two-line
   `uow` parameter the ticket describes. Fixing the site is what re-opened GH #1987 as ka79t.

## Scope call for the PR

- **In-PR:** C (AdCP 3.1.1 MUST-NOT violation), A, B, prkv.7 (data loss), F, prkv.11.
- **Lands first, separately:** E and D are infrastructure, not #1721's thesis. Small PR, merges to
  main immediately, and every subsequent claim in #1721 becomes provable.
- **Out:** the adjacent members in q9e6 / e0gj / ao8dl stay in their epics — but they inherit the
  cluster's invariant, so when those epics run they are call-site migration behind an existing
  guard rather than fresh design.

## How to execute this

The unit of execution is the **cluster**, not the ticket. Per cluster:

1. Establish the invariant and its enforcement (guard, type, or runtime law) — **first**, before
   any site migration.
2. Migrate the sites behind it, ratcheting the allowlist down to zero.
3. Verify each member ticket is *non-reproducible*, then close the cluster's tickets together.

**The wiring change that makes this stick:** create one beads epic per mechanism, put the
invariant in its `--design` field, re-parent the member tickets under it, and make the execute
formula's research atom read the parent's design before anything else. That parent design is the
artifact that stops each fresh-context agent from re-deriving the cheapest local model — which is
the whole reason a union or an `Any` looks correct at ticket granularity.

Round 1's §6 already reached the operative conclusion and it holds here:
**prose does not bind agents; gates do.** Every invariant above is stated with its gate.

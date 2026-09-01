"""BDD scenarios for UC-018: list_creatives library queries.

Binds the UC-018 feature; several scenarios are wired (the rest xfail at the
conftest harness fixture):

- ``T-UC-018-storyboard-list-all-creatives-after-sync`` (#1405): after the buyer
  syncs creatives across formats, ``list_creatives`` with no filters returns the
  account's library — schema-valid against ``list-creatives-response.json``, each
  entry exposing ``creative_id``, ``name``, ``format_id``, ``status``. Source
  obligation: adcp ``protocols/creative/index.yaml`` · ``list_all``.
- ``T-UC-018-storyboard-filter-by-concept-id`` (#1407): ``filters.concept_ids``
  scopes results to a concept; each returned creative exposes ``concept_id`` and
  ``concept_name``. Source: adcp ``creative/list-creatives-request.json`` +
  ``core/creative-filters.json`` (concept_ids) and ``list-creatives-response.json``
  (concept_id/concept_name).

Schema anchor (one per file): every schema citation across this test module and its
step module resolves against the installed adcp SDK tree — AdCP 3.1.1 (``adcp==6.6.0``,
per docs/adcp-spec-version.md; schemas at ``dist/schemas/3.1.1/``).
``tests.helpers.pinned_schema`` reads that tree directly
(``test_pinned_schema_single_source``), so a frozen spec-commit hash would be a stale
anchor the harness no longer resolves.

- ``T-UC-018-inv-034-1-holds`` / ``T-UC-018-inv-034-1-violated`` (#1503):
  BR-RULE-034 cross-principal isolation — an AdCP normative MUST (v3.1-04f59d2d5:
  accounts-and-security.mdx §Data Isolation; building/by-layer/L1/security.mdx §Agent
  and Account Isolation), ungraded by any conformance storyboard,
  so these two scenarios are its only executable guard. Two principals in one tenant
  each own creatives; a buyer authenticated as one sees exactly its own library (holds)
  and never the other's (counter). Enforced in production by
  ``CreativeRepository.get_by_principal``'s ``principal_id`` filter — dropping it
  leaks the co-tenant principal's rows and fails these scenarios. principal_id is
  ``Field(exclude=True)`` (never on the wire), so ownership is verified by matching
  returned creative_ids to the seeded per-principal id sets. See the section comment
  above those steps for the full spec citation.

- ``T-UC-018-inv-146-2-holds`` / ``-inv-146-2-violated`` / ``-inv-146-3-holds`` (#1502):
  BR-RULE-146 ``filters.statuses`` match-any — an explicit statuses array scopes the
  result set to creatives in any of those statuses via ``Creative.status.in_(...)``.
  ``inv-146-3-holds`` is the one that grades the exact bug #1502 fixes: its "contains 3
  creatives" COUNT assertion reddens under the real regression — a multi-value array
  ``["approved", "rejected"]`` narrowed to its first element ``["approved"]`` drops the
  rejected creative, so 2 come back, not 3 (its "none archived" assertion still holds, so
  it is specifically the count that grades this). The other two pin single-status match-any
  (archived returned when requested; excluded when not) and would pass on the unfixed code,
  where a single-element filter was already applied. Source: adcp
  ``core/creative-filters.json`` (statuses). The @creative-status boundary
  ``["approved", "rejected"] (multi-status array)`` row and the @default-query partition
  ``mixed_statuses`` / ``all_statuses_explicit`` / explicit-status rows are the SAME match-any
  behavior under different phrasings — implementable but not yet wired (they sit in Scenario
  Outlines mixed with rows that genuinely need #1738/#1652; wiring them means splitting those
  generated outlines by tag and adding the multi-status Then bindings, tracked in #2067).
  Genuinely dormant: ``inv-146-1-holds`` and the @default-query ``empty_request`` /
  ``filters_no_status`` rows depend on the no-filter archival-DEFAULT exclusion, a separate
  unimplemented production feature (#1738); the validation-error rows — the @creative-status
  ``["deleted"]`` row and the ``[]`` (empty-array) rows — need dict-passthrough
  validation-error wiring (#1652). The conftest dormancy comment carries the full row-by-row
  account.

Wired to real production across all wire transports (auto-parametrized; UC-018
-> CreativeListEnv via conftest ``_detect_uc`` / ``_harness_env``). The repo
sunsets the IMPL pseudo-transport in BDD, so the scenario runs on a2a/mcp/rest
(plus e2e_rest in-network: this branch's ``RestE2EDispatcher`` stashes the
success-path ``wire_response``, so the isolation Then steps assert real HTTP
bytes there too). Each transport returns the same typed response, and the Then
steps validate the real on-the-wire serialization via the single guarded
``wire_dict``/``wire_field`` accessors (``ctx["result"].require_wire()``); the
parametrization exercises each dispatch path end to end (a broken transport
surfaces as a missing/errored response).

**Where the steps live:** the step definitions are in
``tests/bdd/steps/domain/uc018_list_creatives.py`` (under the directory every BDD
structural guard scans), registered LOCALLY here via ``import *`` rather than globally
via conftest ``pytest_plugins`` — the uc019 pattern. Two of the step texts collide with
globally-registered steps: ``the Buyer is authenticated as principal "…"``
(uc003_ext_error_scenarios) and the parameterized ``the response should be schema-valid
against …`` (uc005_format_id_roundtrip's literal for a different schema file). Global
registration would put both at plugin scope, and pytest-bdd's plugin-order tiebreak could
silently reroute UC-005's roundtrip scenario through UC-018's step body. Module-scoped
(local-import) registration keeps every UC-018 step resolving for UC-018 scenarios only.
The reusable, non-step schema validator lives in ``tests.helpers.pinned_schema``.

The "synced" creatives are seeded via ``CreativeFactory`` rather than a live
``sync_creatives`` call: ``CreativeListEnv`` mocks only the audit logger (it has
none of sync's creative-agent / preview-generation patches), and the obligation
under test is ``list_all`` — the listing contract, not the sync path. The
creatives land in the same DB row shape sync would persist, so the listing query
is exercised faithfully.

**Corrupt-blob coercion reconciliation (#1508):** ``list_creatives`` drops a corrupt
``tags``/``assets`` blob value to absent, and collapses a stored empty ``tags`` list to
omission (both conformant at 3.1.1 — the schema permits ``[]`` and absent for ``tags``,
``{}`` and absent for ``assets``, ``null`` for neither). So whoever wires the dormant
all-13-fields boundary graders (``BR-UC-018-list-creatives.feature:292``, ``:312``, ``:549``,
``:575``) must assert value-when-present, not key-presence-of-13 — a creative with empty or
absent tags legitimately omits the key. (``:403``, the ``BR-RULE-148`` tags-AND-semantics
scenario, is separately dormant but seeds a non-empty ``tags`` value by construction, so this
empty/omission caveat doesn't apply there.) The coercion itself is graded on real wire bytes
across a2a/mcp/rest in ``tests/integration/test_list_creatives_concept_filter.py``.
"""

from __future__ import annotations

from pytest_bdd import scenarios

# Register UC-018 step definitions LOCALLY (module scope) via ``import *`` rather than
# globally via conftest's pytest_plugins — see the module docstring for the collision
# rationale (mirrors the uc019 pattern). The steps live under tests/bdd/steps/ so the BDD
# structural guards scan them; this import keeps them scoped to UC-018 scenarios.
from tests.bdd.steps.domain.uc018_list_creatives import *  # noqa: F401,F403,E402

# Bind the UC-018 feature. Whole-feature binding via scenarios() is the repo convention
# the CI shard-splitter requires (scripts/ci/shard_split.py).
scenarios("features/BR-UC-018-list-creatives.feature")  # noqa: E402

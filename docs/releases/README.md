# Release notes

Human-readable, audience-framed notes for each release. These complement the
auto-generated [`CHANGELOG.md`](../../CHANGELOG.md) (produced by release-please from
conventional commits): the changelog is the complete commit-level record; these notes
explain what actually shipped and why it matters.

## Convention

- One file per release: `docs/releases/<MAJOR.MINOR.PATCH>.md` (e.g. `2.0.0.md`).
- Notes are reconstructed from the **final merged diffs**, not PR titles or descriptions
  (which drift as scope grows during review).
- Dependency and security version bumps are out of scope; a consequential security fix is
  covered under its parent change.

## Entry format

Each PR or notable commit gets:

- A byline: `[#PR] · area · tier · flags`
  - **tier** — `T1` headline (user/integrator-visible) · `T2` notable · `T3` internal
  - **flags** — `breaking` · `schema` · `migration` · `wire` (omit when none)
- An italic 2–3 sentence lead summarizing what shipped.
- Bold-lead bullets for the specifics.
- A callout where a reader needs a stop-and-read flag (most entries have none):
  - `> [!WARNING]` **Breaking change** — existing usage stops working
  - `> [!IMPORTANT]` **Migration / operational** — something to run or set when deploying
  - `> [!NOTE]` **Behavioral change** — runtime behavior shifts without breaking the contract
- A `🛠 Engineering` breakout with the implementation detail.

Group entries by area (AdCP protocol & schema, Delivery & reporting, Creatives, GAM,
Inventory & property discovery, Reliability, Tenant configuration, Test suite, CI & build,
Admin UI & maintenance). Atomic single-commit changes go in an appendix.

## Voice

Factual and neutral. State what changed and its concrete effect. No marketing adjectives,
no self-congratulation, no editorializing. Where significance matters, state it as a fact
(e.g. a spec version), not a claim.

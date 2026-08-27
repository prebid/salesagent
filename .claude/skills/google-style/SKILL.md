---
name: google-style
description: >
  Apply the Google developer documentation style guide to everything this repo
  writes in prose: beads epic/task titles and descriptions, YAML formula step
  names and descriptions, `.claude/notes/*.md` engineering notes, commit
  messages, docstrings, and code comments. Use before writing or reviewing any
  of those artifacts, and when asked to edit writing for style.
args: <file-or-artifact-to-check>
---

# Google Style for Repo Prose

Source: https://developers.google.com/style. Every rule cites the page that
mandates it. Where the guide is silent, this file says so — never invent a rule
and attribute it to Google. Rules are ordered by how often agent-written prose
violates them; rules 1-7 account for most of the damage.

## 1. Active voice — name the actor

https://developers.google.com/style/voice

Passive voice hides who does the thing, which turns a contract into a rumor.

- Wrong: "The identity is resolved and the request is forwarded to `_impl`."
- Right: "The MCP wrapper resolves the identity and forwards the request to `_impl`."

Passive is allowed in exactly three cases: to emphasize the object ("The file
is saved"), to de-emphasize the actor ("Over 50 conflicts were found"), or when
the actor is irrelevant ("The database was purged in January").

## 2. Present tense — drop "will"

https://developers.google.com/style/tense

Use present tense for behavior not tied to a point in time. Reserve "will" for
something that genuinely happens later. Never use "would".

- Wrong: "The repository will raise `NotFoundError` when the tenant is missing."
- Right: "The repository raises `NotFoundError` when the tenant is missing."
- Right (genuinely later): "Add the step to the formula. The atom runs the next time the lane executes."
- Wrong: "You could call the wrapper, and it would then translate the error."

## 3. Second person — "you", not "we"

https://developers.google.com/style/person

Address the reader as "you". "We" is only for the authoring organization, which
has no voice in task descriptions or notes. Imperative is second person with
"you" implied — prefer it in steps.

- Wrong: "We then add the guard to the allowlist, and our test catches it."
- Right: "Add the guard to the allowlist. The test catches the violation on the next `make quality` run."

## 4. No hedging, no filler, no "easy"

https://developers.google.com/style/tone and
https://developers.google.com/style/word-list

Delete: "just", "simply", "easy", "easily", "quickly", "please", "in order to"
(use "to"), "allows you to" (use "lets you"). The guide is silent on
"obviously" and "note that" — cut them under the tone page's ban on placeholder
phrases, but don't cite a word-list entry that doesn't exist.

- Wrong: "Simply just call the repository — it's easy to plug in, and it allows you to skip the session."
- Right: "Call the repository. It manages the session for you."

## 5. Timeless writing — no "currently", "new", "now"

https://developers.google.com/style/timeless-documentation

Avoid: as of this writing, currently, does not yet, eventually, existing,
future, latest, new/newer, now, old/older, presently, soon.

- Wrong: "The new REST transport doesn't currently support idempotency keys."
- Right: "The REST transport doesn't support idempotency keys."

The guide's exception is time-stamped content: release notes, commit messages,
dated entries in a migration note. Not a note's body, not a task description.

## 6. Descriptive link text

https://developers.google.com/style/link-text

Never "here", "this document", "click here", "read more", or a bare URL as the
visible text. Use the target's title, important words first.

- Wrong: "For the boundary rules, see [this doc](../CLAUDE.md) and click [here](https://developers.google.com/style)."
- Right: "See [Transport Boundary: Layer Separation](../CLAUDE.md) and the [Google developer documentation style guide](https://developers.google.com/style)."

## 7. Inclusive and non-ableist terms

https://developers.google.com/style/inclusive-documentation and
https://developers.google.com/style/word-list

The complete list this repo's vocabulary actually touches:

| Avoid | Use instead | Source |
|-------|-------------|--------|
| abort, kill | stop, exit, cancel, end | word-list |
| hang | stop responding, not responding | word-list |
| hit (a button) | click, press, type | word-list |
| sanity check | test, verification, final check | word-list |
| dummy (variable/value) | placeholder | word-list |
| blacklist, graylist | denylist, blocklist | word-list |
| whitelist | allowlist, trustlist, safelist | word-list |
| master / slave | primary, main, controller / replica, secondary | word-list |
| native | of people: avoid entirely; of software: built-in | word-list |
| grandfathered | legacy, exempt, made an exception | word-list |
| crazy, insane, mad, bonkers, lunatic, loony | complicated, complex, baffling, strange, unexpected | word-list |
| cripple | slows down, impairs | inclusive-documentation |
| first-class citizen | higher-order, anonymous, nested | word-list |
| he, him, his, she, her (generic) | they, their | word-list |
| man-hours, manpower | person-hours, staff, workforce | word-list |
| mankind | humanity | inclusive-documentation |
| guru, ninja | expert, teacher | word-list |
| disabled (meaning broken) | inactive, unavailable, turned off | word-list |
| execute (a command) | run | word-list |
| leverage | use, build on | word-list |
| utilize | use | translation |
| e.g. / i.e. | for example / that is | word-list |
| etc., and so on | rewrite the intro so the list reads as non-exhaustive | word-list |
| above / below (in a document) | earlier / preceding, later / following | word-list |

**The guide is silent on these** — no Google rule exists, so do not claim one:
`terminate`, `invalid`, `illegal`, `via`, `segregate`, `tribe`, `chairman`,
`one-click`, `dark pattern`, `obviously`, `note that`. Use repo judgment.

## 8. Headings and titles

https://developers.google.com/style/headings

Sentence case, always. Task headings start with a bare infinitive; conceptual
headings are noun phrases; no `-ing` first word. `Optional:` goes in front, not
in trailing parentheses. Don't skip heading levels, number sections, or put
links in headings.

- Wrong: "Migrating The Creative Approval Workflow (Optional)"
- Right (task): "Migrate the creative approval workflow"
- Right (concept): "Creative approval workflow"
- Right (optional): "Optional: Migrate the creative approval workflow"

## 9. Lists and tables

https://developers.google.com/style/lists

Numbered for sequences, bulleted for non-sequences, description lists for
term/definition pairs. Never a one-item list. Introduce with a complete
sentence, not a fragment the list finishes. Keep items parallel. Capitalize each
item; end-punctuate only items containing a verb.

- Wrong intro: "The guard checks that:"
- Right intro: "The guard checks the following properties:"
- Wrong (non-parallel): "Resolves identity", "the request forwarding", "Error translation"
- Right: "Resolves the identity", "Forwards the request", "Translates the error"

**Tables** (https://developers.google.com/style/tables): use a table only for
three or more related data points per row; two columns of term/definition are a
description list, one column is a list. Sentence-case
headers, no trailing punctuation. Introduce with a complete sentence — screen
readers don't preannounce tables. No merged cells, no table inside a procedure.

## 10. Procedures

https://developers.google.com/style/procedures

One action per step, each opening with an imperative verb. State the location
or condition before the action, and the purpose before the click.

- Wrong: "Click **Approve** to publish the creative, after you open the tenant admin UI."
- Right: "In the tenant admin UI, to publish the creative, click **Approve**."

## 11. Code in text

https://developers.google.com/style/code-in-text

Code font for identifiers, filenames, paths, env vars, flags, HTTP verbs and
status codes, ports, and placeholders — not for product or service names. Never
inflect a code identifier; add a noun and inflect that.

- Wrong: "``MediaBuy``s are created by ``POST``ing to the endpoint, and ``ADDRESS``'s value comes from settings."
- Right: "The endpoint creates `MediaBuy` records from a `POST` request. The `ADDRESS` constant's value comes from the `settings.h` file."

## 12. Global audience, accessibility, and jargon

https://developers.google.com/style/translation and
https://developers.google.com/style/accessibility

Under 26 words per sentence. No idioms, humor, sports or holiday references, or
seasons. Keep "that" after a verb ("assumes that you have"), keep "then" in an
if-clause, repeat the noun rather than sharing it across a conjunction, and
prefer the positive form.

- Wrong: "A missing tenant won't prevent the ball from getting rolling on the request."
- Right: "You can process the request without a tenant."

**Jargon** (https://developers.google.com/style/jargon): write around
figurative jargon or define it once in parentheses — "blast radius" becomes
"affected area", "ingest" becomes "import" or "load", "off-the-shelf" becomes
"pre-built".

## 13. Punctuation and abbreviations

https://developers.google.com/style/commas and
https://developers.google.com/style/abbreviations

Serial comma, always: "zones, regions, and multi-regions". Comma before "which"
in a nonrestrictive clause and before a conjunction joining two independent
clauses. Spell out an abbreviation on first use, expansion first with the
abbreviation in parentheses. No periods in acronyms. Don't verb an abbreviation
("Use SSH to connect", not "SSH into the box").

## Which rules bind which artifact

| Artifact | Binding rules | Relaxed |
|----------|---------------|---------|
| Beads task/epic **title** | 1, 7, 8 (sentence case, bare infinitive, no `-ing`) | 9-11, 13 — a title is a fragment, so no end punctuation and no serial-comma prose |
| Beads task/epic **description** | 1-7, 9, 10, 11, 12 | 8 — descriptions rarely carry headings |
| YAML formula **step/atom name** | 7, 8 (bare infinitive, lowercase kebab matches existing atoms) | Everything prose-shaped |
| YAML formula **description** | 1-7, 9, 12 | 8 |
| `.claude/notes/*.md` | All 13 | None |
| `docs/**` and `CLAUDE.md` | All 13 | None |
| **Commit message** subject | 1, 7, 10 (imperative), 13 | 2 and 5 — a commit is time-stamped content; 8 — Conventional Commits owns the prefix and casing |
| **Code comment / docstring** | 1-5, 7, 11, 12 | 8-10, 13 |

## Reviewer checklist

Run this against any piece of writing before you commit it.

1. Does every sentence name its actor, or is it one of the three allowed passives?
2. Is every "will" describing something that genuinely happens later? Is there any "would"?
3. Any "we", "our", or "us" that is not the authoring organization?
4. Any of: just, simply, easy, easily, quickly, please, in order to, allows you to?
5. Any of: currently, now, new, latest, soon, existing, eventually?
6. Does every link say what it points to without the surrounding sentence?
7. Any term from the table in rule 7?
8. Headings: sentence case, bare infinitive or noun phrase, no `-ing` first word?
9. Lists: complete-sentence intro, parallel items, more than one item?
10. Serial commas present? Abbreviations expanded on first use?
11. Any sentence over 26 words, any idiom, any figurative jargon?

## When repo convention wins

Google's guide loses to this repo in these cases, and only these:

- **Conventional Commits prefixes.** `feat:`, `fix:`, `docs:`, `refactor:`,
  `perf:`, `chore:` are lowercase by mandate of release-please and
  `.github/workflows/pr-title-check.yml`. Sentence case starts after the colon.
- **AdCP spec terms and code identifiers keep their exact casing and spelling.**
  A format named `native`, a method named `execute`, a Flask `abort()` call, a
  branch named `main` — these are names, not prose. Rule 7 applies to the
  sentence around them, never to the identifier.
- **Quoted spec text is never edited for style.** When you cite AdCP prose or a
  storyboard step, quote it verbatim, including its "will" and its passives.
- **Repo abbreviations stay unexpanded in internal artifacts.** AdCP, MCP, A2A,
  BDD, UoW, GAM, DRY, TDD are load-bearing vocabulary in `.claude/notes/` and
  beads tasks. Expand them in `docs/` and anything a contributor reads first.
- **Formula atom names are lowercase kebab-case keys** (`write-test`,
  `verify-prediction`), not headings.
- **A design note may describe future work in future tense.** Rule 2's own
  exception covers it: the migration genuinely hasn't happened yet.

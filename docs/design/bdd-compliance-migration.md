# Retired: the response-compliance sentence, and the rule it left behind

This file was the instruction for adding a schema-compliance check to every BDD
scenario. The migration is applied: 27 of the 34 feature files a
`tests/bdd/test_*.py` module loads carry at least one compliance sentence
(measured over `tests/bdd/features/*.feature` and the feature names those
modules reference). The plan text is gone, and two things survive it — the rule,
and the sentences that express it.

## The rule

Every scenario carries a compliance check that grades the whole document the
buyer received against the pinned AdCP schema. The scenario's own assertions
stay as the specific check. The general one catches a malformed response the
specific assertions never look at; the specific one catches a wrong value the
schema permits.

A refusal is not exempt. It is still a wire contract, and leaving error paths
unchecked is how "the suite is schema-clean" comes to mean "the happy paths
are".

## The sentences

| Situation | Line |
|-----------|------|
| The tool responds in one shape | `Then the response is compliant with the <tool> spec` |
| The scenario pins ONE outcome of a branching tool | `Then the response is compliant with the <tool> <branch> spec` |
| The scenario asserts a refusal of a branching tool | `Then the response is compliant with the <tool> error spec` |
| The scenario asserts a refusal of a single-shape tool | `Then the error is compliant with the AdCP error spec` |
| The scenario grades an outbound delivery webhook | `Then the webhook payload is compliant with the AdCP delivery webhook spec` |

Add one as the first `Then`, with the previous first `Then` becoming an `And`
beneath it. Name the TOOL, never a schema file: a filename in a feature file is
a second spelling of which tool the scenario exercises, and the two drift.

Read the branch words from `branch_names(tool)`
(`tests/helpers/response_schemas.py:80`), which reads the pinned schema's own
`oneOf` titles, rather than from memory.

A `Scenario Outline` whose outcome is an `Examples` column takes the general
form with no branch word. It cannot name a branch, because one outline carries
both valid and invalid rows and runs its single `Then` against both.

## Where the authority is

The authority is `tests/bdd/steps/generic/then_schema.py`. Each step's docstring
states what it grades, why the form exists, and what it measured.
`_assert_compliant` (line 54) is outcome-aware, so it grades a refused call
against the error contract instead of failing it as a missing success body.
`then_response_compliant` (line 256) and `then_response_compliant_branch`
(line 286) carry the general-versus-branch rule. `then_error_compliant`
(line 115) and `then_webhook_payload_compliant` (line 157) carry the two
tool-independent lines.

## What still has no compliance sentence

Seven loaded feature files carry none: `BR-ADMIN-ACCOUNTS`,
`BR-SECURITY-002-tenant-isolation`, `BR-UC-002-manual-overrides`,
`BR-UC-GET-PRODUCTS-pricing-options`, `local-context-echo-every-outcome`,
`local-pre-dispatch-refusals`, and `local-uc006-dry-run-preview-parity`. Some
grade something other than a tool response; verify which before adding a line.

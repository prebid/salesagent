"""DISPOSABLE acceptance criterion for the UC-004 webhook-grading ticket. DELETE ON MERGE.

This is NOT a guard and must not become one. It encodes "is the ticket done yet" as
something runnable, so thirteen parallel agents each have an objective answer instead of
my opinion. A guard states an invariant the codebase must keep forever; this states a
task that stops existing the moment it is finished. Leaving it behind is how a guard
suite grows one dead check per completed ticket.

THE OBLIGATION. Every UC-004 scenario that reaches the webhook-delivery path must say,
as its FIRST Then, what it expects on the wire — either the payload is graded against
the pinned schema, or the scenario asserts that NO POST happened. Those are the only two
honest answers. Today thirteen scenarios say neither: they grade retry counts, breaker
transitions and credential checks while the body being retried is never looked at. A
retry scenario with no payload assertion passes just as happily when the thing being
retried is malformed — which is the exact defect the payload step's own docstring says
shipped to buyers once already.

WHY "first". Ordering is not cosmetics here. A scenario whose compliance check sits
below three content assertions reports the content failure first, and the reader fixes
the symptom. The wire contract is the precondition for every assertion under it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

FEATURE = pathlib.Path(__file__).parent.parent / "bdd" / "features" / "BR-UC-004-deliver-media-buy-metrics.feature"

#: An answer about the wire. Either the payload is graded, or its absence is asserted.
GRADES_THE_WIRE = re.compile(
    r"^(?:Then|And)\s+(?:"
    r"the webhook payload is compliant with the AdCP delivery webhook spec"
    r"|the response is compliant with the .+ spec"
    r"|the error is compliant with the AdCP error spec"
    r"|the webhook delivery should be skipped without an HTTP POST"
    r'|the system should skip "[^"]+" \(no webhook to deliver to\)'
    r")$"
)

#: Scenarios this ticket covers: the ones that REACH the webhook-delivery path, so a
#: statement about the wire is meaningful. Named by TEXT, not line number, so edits
#: elsewhere in the file do not invalidate the list.
IN_SCOPE = [
    "Webhook delivery retries on 5xx response",
    "Webhook delivery retries on network error",
    "Webhook delivery does not retry on 4xx response",
    "Persistent webhook failures open circuit breaker",
    "Circuit breaker half-open probe attempts recovery",
    "Circuit breaker closes after successful recovery probes",
    "Blocked outbound webhook URL skips delivery without POST",
    "Webhook fires for media buy without webhook configuration",
]

#: Deliberately OUT of scope, each for a reason that is not "we did not get to it".
#: Listed so the exclusion is a decision on the record rather than a silent omission.
#:
#:   credentials partition / boundary
#:       Validate webhook CONFIG at the create_media_buy boundary
#:       (_validate_reporting_webhook_credentials). Nothing is delivered, so neither a
#:       payload assertion nor a no-POST assertion applies. Already graded: generic
#:       regex steps (uc004_delivery.py:3540-3543) assert valid/invalid across seven
#:       credential partitions.
#:
#:   forecast-metric vocab / two-layer envelope / delayed-data signalling
#:       DORMANT — their When steps have no definition, so the scenarios execute
#:       nothing. Adding a Then makes them LOOK wired while their subject stays
#:       untested.
#:
#:   delayed adapter data
#:       DORMANT via its Given ("the ad server adapter cannot return data for ... yet").
#:       Its own Thens are unbound too.
#:
#: The dormant four are salesagent-8j5nf / salesagent-r3xvs.5 — step definitions and
#: harness, not assertions.
OUT_OF_SCOPE_WITH_REASON = {
    "Webhook credentials partition - <partition>": "config validation, not a delivery",
    "Webhook credentials boundary - <boundary_point>": "config validation, not a delivery",
    "Webhook entry for media buy with delayed adapter data uses status reporting_delayed": "dormant Given",
    "Forecast metrics map accepts forecastable-metric vocabulary": "dormant When",
    "Two-layer error envelope distinguishes fatal failures from warnings": "dormant When",
    "delayed data signaling boundary - <boundary_point>": "dormant When",
}


def _scenarios() -> dict[str, list[str]]:
    """``{scenario title: [assertion lines]}`` for the whole feature file.

    Assertions start at the first ``Then``. ``And`` is ambiguous in Gherkin — it
    continues whatever block precedes it — so collecting every ``And`` picks up Given
    continuations and reports the wrong line as "the first assertion".
    """
    found: dict[str, list[str]] = {}
    title = None
    in_assertions = False
    for raw in FEATURE.read_text().splitlines():
        line = raw.strip()
        if line.startswith(("Scenario:", "Scenario Outline:")):
            title = line.split(":", 1)[1].strip()
            found[title] = []
            in_assertions = False
        elif title is None:
            continue
        elif line.startswith("Then "):
            in_assertions = True
            found[title].append(line)
        elif line.startswith("And ") and in_assertions:
            found[title].append(line)
        elif line.startswith(("Given ", "When ", "Examples")):
            in_assertions = False
    return found


@pytest.mark.parametrize("title", IN_SCOPE, ids=lambda t: t[:48])
def test_scenario_answers_the_wire_question_first(title: str):
    """The scenario's FIRST assertion says what reached the wire, or that nothing did."""
    scenarios = _scenarios()
    assert title in scenarios, (
        f"scenario {title!r} is not in {FEATURE.name}. If it was renamed, update IN_SCOPE "
        f"in the same change — a scope list that silently stops matching grades nothing."
    )
    assertions = scenarios[title]
    assert assertions, f"{title!r} has no Then at all"

    first = assertions[0]
    assert GRADES_THE_WIRE.match(first), (
        f"{title!r} opens with:\n    {first}\n"
        f"which says nothing about what reached the wire. Open with ONE of:\n"
        f"    Then the webhook payload is compliant with the AdCP delivery webhook spec\n"
        f"    Then the webhook delivery should be skipped without an HTTP POST\n"
        f'    Then the system should skip "<mb_id>" (no webhook to deliver to)\n'
        f"Pick by what the scenario actually does: a delivery that POSTs grades its body; "
        f"one that deliberately does not POST asserts that instead. Do not add a payload "
        f"assertion to a scenario with no payload — that is a different lie."
    )


def test_the_out_of_scope_list_still_names_real_scenarios():
    """An exclusion that stops matching is an exclusion nobody can audit.

    If one of these is renamed or deleted, the reason recorded beside it silently stops
    applying to anything — and the scenario it was protecting rejoins the file ungraded
    with no trace that a decision was ever made about it.
    """
    scenarios = _scenarios()
    missing = [t for t in OUT_OF_SCOPE_WITH_REASON if t not in scenarios]
    assert not missing, (
        "OUT_OF_SCOPE_WITH_REASON names scenarios that no longer exist:\n  "
        + "\n  ".join(missing)
        + "\n\nRe-decide them under their current names, or drop the entries."
    )


def test_a_graded_delivery_also_asserts_what_is_IN_the_payload():
    """Compliance is shape, not subject. A scenario can be schema-clean and still wrong.

    A well-formed envelope reporting the WRONG media buy passes every compliance check
    there is. So a scenario that grades the payload must also say something about its
    content — which media buy, which notification_type, which reporting period.

    Exempt: the absence-assertions, which have no payload to describe.
    """
    content = re.compile(
        r"the payload should include delivery metrics for|"
        r"the payload should include the reporting_period|"
        r"the payload notification_type should be|"
        r"the payload .* include next_expected_at|"
        r'the payload should not include "aggregated_totals" field|'
        r'the entry for "[^"]+" should have status'
    )
    scenarios = _scenarios()
    shapeless = []
    for title in IN_SCOPE:
        assertions = scenarios.get(title) or []
        if not assertions:
            continue
        grades_payload = any("webhook payload is compliant" in a for a in assertions)
        if grades_payload and not any(content.search(a) for a in assertions):
            shapeless.append(title)
    assert not shapeless, (
        "these scenarios grade the payload's SHAPE but never say what is in it, so a "
        "well-formed report about the wrong media buy would pass:\n  " + "\n  ".join(shapeless)
    )

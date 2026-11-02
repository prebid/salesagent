"""Guard: one spelling for "this create request carries N packages".

WHY AN EXACT SET AND NOT A CEILING. A shrink-only ceiling with headroom is permission to
add one more, and it cannot fail when a repair REMOVES an entry either -- so it silently
goes stale in the direction you were hoping for. The set below fails in both directions:
a new spelling fails with the canonical one named, and a retired spelling fails telling you
to delete its row. That is the whole point of pinning a set rather than counting one.

WHAT THIS GUARDS. Establishing packages on a CREATE request had four spellings and four
hand-written copies of the package payload (salesagent-9p7oe). Two are retired:

  "a valid create_media_buy request with 2 packages"   -> the canonical pair below
  a second, shadowed definition of the canonical sentence itself

Both were literal-count spellings, which is the specific failure this guards against: a
sentence that hardcodes "2" cannot express any other number, so the next scenario needing
three packages writes a fifth spelling instead of passing a parameter.

WHAT THIS DOES NOT GUARD, deliberately. UC-003's ``new_packages`` sentences and the
``package update`` family establish packages on an EXISTING media buy -- a different
operation with a different request model (UpdateMediaBuyRequest, not
CreateMediaBuyRequest). They look alike and are not siblings; merging them would be the
silent wrong merge the vertical-slice formula warns about. The response-side sentences
("the response should contain a package with...") are assertions, not setup.

STILL OPEN, not guarded here: 126 dict literals across tests/ carry the package payload
signature {product_id, budget, pricing_option_id} instead of PackageRequestFactory, which
binds the model. That is a tree-wide finding filed separately -- a guard for it needs an
allowlist, and an allowlisted guard is a different instrument from this one.
"""

from __future__ import annotations

import re
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "tests" / "bdd" / "features"

# The ONE spelling. Parameterized on purpose: the count is a value the scenario supplies,
# never part of the sentence, which is what stopped "2 packages" from needing a sibling
# the day a scenario wants three.
CANONICAL = "the request includes {count:d} packages with valid product_ids"
CANONICAL_SINGULAR = "the request includes {count:d} package with a valid product_id"

# Every feature sentence that establishes packages on a CREATE request. Exact set.
EXPECTED: set[str] = {
    "the request includes 2 packages with valid product_ids",
}

# A sentence is in scope when it sets up packages carrying products on the request under
# construction. "valid product_id(s)" is the discriminator: the update-side sentences name
# new_packages or "package update" and never claim product validity this way.
_IN_SCOPE = re.compile(
    r"\brequest\b.*\bpackages?\b.*\bvalid product_ids?\b|\bcreate_media_buy request with \d+ packages?\b"
)


def _feature_sentences() -> set[str]:
    """Every Given/And/But line in the corpus that establishes create-request packages."""
    found: set[str] = set()
    for feature in FEATURES.rglob("*.feature"):
        for raw in feature.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for keyword in ("Given ", "And ", "But "):
                if line.startswith(keyword):
                    sentence = line[len(keyword) :].strip()
                    if _IN_SCOPE.search(sentence):
                        found.add(sentence)
                    break
    return found


def test_one_spelling_establishes_create_request_packages() -> None:
    """No second spelling, and no stale row for one that is gone."""
    found = _feature_sentences()

    new = found - EXPECTED
    assert not new, (
        f"New spelling(s) for 'this create request carries N packages': {sorted(new)}.\n"
        f"Use the canonical sentence instead:\n"
        f"    {CANONICAL!r}\n"
        f"    {CANONICAL_SINGULAR!r}  (the count=1 alias)\n"
        f"Its step builds each package with PackageRequestFactory -- the factory bound to "
        f"src.core.schemas.PackageRequest -- seeds a distinct Product per package, and "
        f"asserts the count. A new spelling means a new hand-written package payload, which "
        f"is how this sentence came to have four of them."
    )

    gone = EXPECTED - found
    assert not gone, (
        f"EXPECTED names spelling(s) no longer in any feature: {sorted(gone)}.\n"
        f"If they were retired, delete their row here -- this is an exact set, so a stale "
        f"row is as much a defect as a missing one."
    )


def test_the_canonical_sentence_has_exactly_one_definition() -> None:
    """One sentence, one step definition.

    The canonical sentence had TWO definitions: the parameterized one in the registered
    uc002 domain module, and a literal-string copy in given_media_buy.py that actually built
    the second package. The literal copy lost on fixture priority and was therefore dead
    code that looked authoritative -- the scenario dispatched ONE package for as long as
    both existed. Fixture priority is not a place to express intent.
    """
    steps = Path(__file__).resolve().parents[2] / "tests" / "bdd" / "steps"
    literal = "the request includes 2 packages with valid product_ids"
    definers = [
        path.relative_to(steps.parent.parent)
        for path in steps.rglob("*.py")
        if re.search(rf'@given\(\s*"{re.escape(literal)}"', path.read_text())
    ]
    assert not definers, (
        f"{literal!r} is defined as a LITERAL string in {definers}. The canonical definition "
        f"is parameterized ({CANONICAL!r}) and lives in "
        f"tests/bdd/steps/domain/uc002_create_media_buy.py. A literal copy cannot win "
        f"reliably -- pytest-bdd resolves by fixture priority, so which one runs depends on "
        f"pytest_plugins ordering rather than on anything a reader can see."
    )

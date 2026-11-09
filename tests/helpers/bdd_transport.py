"""The transport a parametrized BDD nodeid dispatches through — ONE derivation.

Two consumers need it and must not each derive it: ``tests/bdd/conftest.py`` routes and
reports on it, and ``tests/bdd/scenario_liveness.py`` records it per observation. It lived
in the conftest, which the liveness plugin cannot import -- doing so pulls 2098 modules in
2.8s at PLUGIN-LOAD time and reorders plugin against conftest loading -- so the writer grew
its own copy, and the copy was wrong (salesagent-8qela.159): it split on the LAST bracket,
so ``[rest-https://[fc00::1]/hook]`` reported transport ``fc00::1]/hook``. 433 of 6095
observations in one full run, 94 distinct phantom values, failing quietly with a plausible
string rather than an error.

This module exists so there is nowhere for a second copy to be needed. It imports stdlib
only (18 modules via the ``tests.helpers`` package, against the conftest's 2098), which is
what makes it importable from a plugin.

THE DUPLICATE WAS NOT CARELESSNESS, AND THE SAME PRESSURE WILL RECREATE IT. A pytest plugin
cannot import a conftest without dragging the whole collection graph in, so a plugin that
needs a conftest's helper has exactly two options: duplicate it, or have a stdlib-only
shared home. Before this file there was no such home, so the duplicate was the only
available spelling -- and it then drifted, silently, for months. The next plugin needing a
conftest helper faces the identical fork, so THIS FIX IS ONLY DURABLE IF THIS IS WHERE
PEOPLE LOOK: put the next one here rather than copying it, and keep the conftest's call
sites spelled as they were (it aliases these two names) so adopting the shared home never
costs a fifteen-site churn.

THE BRACKET IS THE WHOLE RULE. Anchoring on the OPENING bracket is what makes a parametrize
value containing ``[`` -- an IPv6 URL, a JSON payload, an array -- harmless. The same defect
shape, a nodeid split on a delimiter that also occurs inside a parametrize value, is live in
``suite_delta.nodeid_of`` in a different repository (salesagent-8qela.72/.128); it cannot
share this function because it has only junit text and no item, so it must parse where this
can be anchored. They share the lesson and the missing test case, not the code.
"""

from __future__ import annotations

import re

#: Every transport a BDD nodeid can be parametrized over.
#:
#: DERIVED FROM THE RUN, not chosen. ``admin_integration`` is here because a full-run
#: liveness artifact records 44 observations carrying it; omitting it would have traded
#: 433 silently corrupted transports for 44 silent ``None``s, which is a different defect
#: rather than a fix. ``impl`` is deliberately ABSENT: it was sunsetted from BDD
#: parametrization by #1417 and occurs 0 times in 6095 observations -- a member that cannot
#: fire is the tell that a set was written from imagination rather than from the data it
#: validates (it was in one hand-written copy of this vocabulary, and nowhere in the tree).
NODEID_TRANSPORTS: tuple[str, ...] = ("e2e_rest", "admin_integration", "a2a", "mcp", "rest")

#: ``e2e_rest`` is listed before ``rest`` for readability only -- it is NOT what
#: disambiguates them, and a comment claiming otherwise was wrong. A regex matches at the
#: earliest POSITION before it consults alternation order, and the ``rest`` inside
#: ``e2e_rest`` always sits four characters later, so neither reordering this tuple nor
#: dropping the ``\[`` anchor can make an ``[e2e_rest-row]`` nodeid report as ``rest``
#: (all three mutations verified to leave the guard green). What the bracket discipline
#: DOES buy is refusing a row id that merely CONTAINS a transport name.
_TRANSPORT_IN_NODEID = re.compile(r"\[(" + "|".join(NODEID_TRANSPORTS) + r")[-\]]")


def transport_of(nodeid: str) -> str | None:
    """The transport *nodeid* dispatches through, or ``None`` when it names none.

    ``None`` rather than a guess: a caller that needs a label for an unparametrized item
    supplies its own default, and a function that invented one would make "no transport"
    and "a transport I could not read" the same answer -- which is how the 94 phantom
    values reached a measured artifact.
    """
    match = _TRANSPORT_IN_NODEID.search(nodeid)
    return match.group(1) if match else None

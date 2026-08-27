"""BDD binding for the locally-added request-signature enforcement feature.

Grades the three inbound-signing obligations across every wire transport the suite
parametrizes (a2a / mcp / rest, plus e2e_rest when the live stack is up) —
salesagent-n78j0.1.3. The obligations and their spec citations live in the feature
file; what belongs HERE is why they are bound as a file of their own:

* they are CROSS-transport claims, so grading them inside a per-transport module is
  the exact failure (SF-5) the epic exists to undo;
* they run on ``sync_creatives`` because it is the lightest AdCP operation that
  accepts a ``push_notification_config`` on all three transports (the escalation's
  own trigger, security.mdx :1462-1465) — the operation is scenery, the enforcement
  ladder is the subject;
* one file, one env, one operation, three scenarios differing by exactly one
  variable each, so a difference in outcome is attributable to the variable.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-request-signing-enforcement.feature")

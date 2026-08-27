"""BDD binding for the locally-added UC-011 reserved-TLD normalization feature.

Grades salesagent-og9k.3 where the generated feature leaves it ungraded: the
reserved-TLD refusal on ``sync_accounts`` is exercised upstream with a single
lowercase domain, the one spelling on which the owning predicate
(``is_reserved_tld_host``) and the provisioning path's private
``endswith`` re-implementation happen to agree.

Retire this file together with the local feature once the upstream storyboard
grows a reserved-TLD partition carrying more than one spelling.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc011-reserved-tld-normalization.feature")

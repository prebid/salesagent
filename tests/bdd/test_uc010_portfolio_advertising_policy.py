"""BDD binding for the locally-added UC-010 advertising-policy feature.

Grades what ``media_buy.portfolio.advertising_policies`` carries: the description a
tenant declares in its ``advertising_policy`` column, and nothing when it declares
none or declares a document without one.

No BR-UC-010 storyboard step grades this member, and the obligation previously had
only mocked unit tests behind it — they patched the capabilities unit of work and
asserted against a response built inside the test, so the value never reached a wire
on any transport. Retire this file together with the local feature once the upstream
storyboard grows an equivalent scenario.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc010-portfolio-advertising-policy.feature")

"""BDD binding for the locally-added reporting-cadence refusal feature.

Covers the CADENCE half of the ``reporting_webhook`` registration contract: a
``reporting_frequency`` outside what the media buy's products advertise in
``reporting_capabilities.available_reporting_frequencies`` is refused at
``create_media_buy`` with ``UNSUPPORTED_FEATURE``, naming the cadences on offer.
The correlation-id half of the same document is
``features/local-reporting-stream-correlation.feature`` and
``@T-EGRESS-OPID-create-media-buy``; the two grade different fields and take
different lanes.

WITHOUT THIS MODULE THE FEATURE COLLECTS NOTHING. A ``.feature`` file is inert
until a test module binds it, and the failure is silent in the direction that
matters: ``pytest --collect-only -k cadence`` returns "0 selected" rather than an
error, which reads exactly like a passing narrow selection. That is how this
binding came to be written — the first collection attempt after adding the
feature selected nothing, and a selector that matches nothing cannot tell you
whether the scenarios bind.

The conformance storyboard does not grade this: the runner emits schema-valid
payloads, and `hourly` IS schema-valid — the refusal comes from the product's
declared capability rather than from the schema, so no storyboard step reaches
it. Retire this file together with the local feature if upstream (adcp-req) ever
grows the equivalent scenario.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-reporting-cadence-refusal.feature")

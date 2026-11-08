"""BDD binding for the locally-added reporting-stream correlation feature.

Covers the REPORTING route's half of AdCP 3.2.1 L3 § "AdCP 3.2 interim reporting
correlation" — a ``reporting_webhook`` registered with no correlation id has its
scheduled sends suppressed and the missing value flagged to the buyer on the
``get_media_buy_delivery`` read that :667 points them at. The REGISTRATION route's
half takes a different sanctioned shape and lives in
``features/local-egress-ssrf-refusal.feature`` (@T-EGRESS-OPID-create-media-buy).

The conformance storyboard does not grade either: the runner emits only
schema-valid payloads, and a registration omitting a field its schema does not
declare is schema-valid. Retire this file together with the local feature once
upstream (adcp-req) grows the equivalent scenario — adcp#7885, which AdCP 3.3
settles by standardizing the field.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-reporting-stream-correlation.feature")

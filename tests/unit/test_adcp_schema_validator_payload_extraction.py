"""AdCPSchemaValidator._extract_adcp_payload strips only genuinely non-spec fields.

Regression for PR #1838: the strip-set used to
remove "message", "context_id", and "errors" before validation, with a
comment claiming they're transport-layer additions "not part of the AdCP
spec". All three are genuine spec-defined properties on the pinned schema's
response envelope (message/context_id on the "Protocol Envelope" allOf arm
every bundled *-response.json schema shares; errors as a top-level property
on schemas like get-products-response.json, and required on the "failed"
oneOf arm of schemas like create-media-buy-response.json) — stripping them
meant the validator silently never graded their type/shape, and for
"errors" specifically, silently masked a payload that would otherwise fail
validation outright (a "failed" status response missing its required
"errors" array). "clarification_needed" has no spec basis and stays
stripped.
"""

from __future__ import annotations

from tests.helpers.adcp_schema_validator import AdCPSchemaValidator


class TestExtractAdCPPayload:
    def _extract(self, response_data: dict) -> dict:
        # _extract_adcp_payload is a pure function of response_data — no I/O,
        # no network. __init__ only resolves the local SDK schema directory.
        validator = AdCPSchemaValidator.__new__(AdCPSchemaValidator)
        return validator._extract_adcp_payload(response_data)

    def test_message_survives_extraction(self):
        payload = self._extract({"products": [], "message": "Found 2 products."})
        assert payload.get("message") == "Found 2 products."

    def test_context_id_survives_extraction(self):
        payload = self._extract({"products": [], "context_id": "ctx-123"})
        assert payload.get("context_id") == "ctx-123"

    def test_errors_survives_extraction(self):
        errors = [{"code": "UNSUPPORTED_FEATURE", "message": "property_list_filtering unavailable"}]
        payload = self._extract({"status": "completed", "errors": errors})
        assert payload.get("errors") == errors

    def test_clarification_needed_is_still_stripped(self):
        """The one strip-set entry with no spec basis — must stay stripped."""
        payload = self._extract({"products": [], "clarification_needed": True})
        assert "clarification_needed" not in payload

    def test_unrelated_fields_pass_through_unchanged(self):
        payload = self._extract({"products": [{"id": "p1"}], "cache_scope": "tenant"})
        assert payload == {"products": [{"id": "p1"}], "cache_scope": "tenant"}

"""Truth table for the shared media-buy response-variant classifier.

``classify_media_buy_response_payload`` is the single decision home consumed
by both the A2A reconstructor (live wire artifacts, envelope ``status``
present) and the idempotency replay path (stored response bodies, which never
carry the envelope ``status``). These pins keep the two consumers' contract
from re-forking.
"""

from __future__ import annotations

import pytest

from src.core.schemas import classify_media_buy_response_payload

pytestmark = pytest.mark.unit


class TestClassifyMediaBuyResponsePayload:
    def test_media_buy_id_classifies_success(self):
        assert classify_media_buy_response_payload({"media_buy_id": "mb_1", "packages": []}) == "success"

    def test_stored_submitted_body_without_envelope_status_classifies_submitted(self):
        # Stored idempotency bodies never carry the envelope status key — the
        # deploy-drift case shape discrimination exists for.
        assert classify_media_buy_response_payload({"task_id": "task_1", "message": "pending"}) == "submitted"

    def test_wire_submitted_artifact_with_envelope_status_classifies_submitted(self):
        assert classify_media_buy_response_payload({"status": "submitted", "task_id": "task_1"}) == "submitted"

    def test_media_buy_id_outranks_task_id(self):
        # media_buy_id is the stronger signal: required on the success variants,
        # forbidden on submitted — so it decides before task_id is consulted.
        assert classify_media_buy_response_payload({"media_buy_id": "mb_1", "task_id": "t"}) == "success"

    def test_neither_shape_classifies_error(self):
        assert classify_media_buy_response_payload({"errors": [{"code": "X"}]}) == "error"

    def test_empty_payload_classifies_error(self):
        assert classify_media_buy_response_payload({}) == "error"

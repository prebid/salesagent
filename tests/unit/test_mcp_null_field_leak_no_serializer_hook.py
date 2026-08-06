"""Unit tests for the MCP null-field leak on response classes with NO
@model_serializer hook at all (salesagent-oyiv.16, follow-up to oyiv.7).

FastMCP serializes ToolResult's structured_content via
pydantic_core.to_jsonable_python(), which never calls a class's own
model_dump() Python-method override. oyiv.7 fixed this for classes carrying
NestedModelSerializerMixin (which has a @model_serializer wrap hook that can
apply the exclude_none=True default itself). These three classes have NO
@model_serializer hook anywhere in their MRO — nothing intervenes before
pydantic_core's own default (exclude_none=False) runs, so unset optional
fields leak as null.
"""

import pydantic_core

from src.core.schemas._base import ActivateSignalResponse, UpdateMediaBuySubmitted, UpdatePerformanceIndexResponse
from src.core.schemas.creative import SyncCreativeResult, SyncCreativesResponse


class TestUpdatePerformanceIndexResponseNullLeak:
    def test_pydantic_core_path_omits_unset_top_level_none_fields(self):
        response = UpdatePerformanceIndexResponse(status="success", detail="ok")
        data = pydantic_core.to_jsonable_python(response)

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f"pydantic_core dump leaked null top-level keys: {sorted(null_keys)}"

    def test_bare_model_dump_still_omits_none_fields(self):
        response = UpdatePerformanceIndexResponse(status="success", detail="ok")
        data = response.model_dump()

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f".model_dump() leaked null top-level keys: {sorted(null_keys)}"


class TestActivateSignalResponseNullLeak:
    def test_pydantic_core_path_omits_unset_top_level_none_fields(self):
        response = ActivateSignalResponse(signal_id="sig_1")
        data = pydantic_core.to_jsonable_python(response)

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f"pydantic_core dump leaked null top-level keys: {sorted(null_keys)}"

    def test_bare_model_dump_still_omits_none_fields(self):
        response = ActivateSignalResponse(signal_id="sig_1")
        data = response.model_dump()

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f".model_dump() leaked null top-level keys: {sorted(null_keys)}"


class TestUpdateMediaBuySubmittedNullLeak:
    """Found during salesagent-oyiv.16's sweep-verify atom: media_buy_update.py's
    _update_media_buy_impl returns a Union (UpdateMediaBuyResult |
    UpdateMediaBuySubmitted); oyiv.7's sweep only re-verified UpdateMediaBuyResult
    (correctly TaskResultEnvelope-family), missing that UpdateMediaBuySubmitted —
    a distinct class extending a library type directly, not TaskResultEnvelope —
    has no @model_serializer hook of its own either."""

    def test_pydantic_core_path_omits_unset_top_level_none_fields(self):
        response = UpdateMediaBuySubmitted(task_id="t1")
        data = pydantic_core.to_jsonable_python(response)

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f"pydantic_core dump leaked null top-level keys: {sorted(null_keys)}"

    def test_bare_model_dump_still_omits_none_fields(self):
        response = UpdateMediaBuySubmitted(task_id="t1")
        data = response.model_dump()

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f".model_dump() leaked null top-level keys: {sorted(null_keys)}"


class TestSyncCreativesResponseNullLeak:
    """SyncCreativesResponse.model_dump() is a Python-method override (Pattern #4,
    calls child.model_dump() on nested creatives) — FastMCP's pydantic_core path
    bypasses it entirely, at BOTH the top level and the nested creatives[] level."""

    def _make_response(self):
        creative = SyncCreativeResult(creative_id="cr_1", action="created")
        return SyncCreativesResponse(creatives=[creative])

    def test_pydantic_core_path_omits_unset_top_level_none_fields(self):
        response = self._make_response()
        data = pydantic_core.to_jsonable_python(response)

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f"pydantic_core dump leaked null top-level keys: {sorted(null_keys)}"

    def test_pydantic_core_path_omits_unset_nested_creative_none_fields(self):
        """The nested SyncCreativeResult's own unset fields (status, platform_id,
        assigned_to, assignment_errors, ...) must not leak as null either —
        confirmed live symptom for `status`, documented inline at
        SyncCreativeResult's class docstring (creative.py)."""
        response = self._make_response()
        data = pydantic_core.to_jsonable_python(response)

        creative_data = data["creatives"][0]
        null_keys = {k for k, v in creative_data.items() if v is None}
        assert not null_keys, f"nested creative dump leaked null keys: {sorted(null_keys)}"

    def test_bare_model_dump_still_omits_none_fields(self):
        response = self._make_response()
        data = response.model_dump()

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f".model_dump() leaked null top-level keys: {sorted(null_keys)}"

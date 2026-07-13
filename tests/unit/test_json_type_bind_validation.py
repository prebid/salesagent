"""JSONType(model=) validates raw dicts at the write boundary (salesagent-5mu0).

The typed column validates on every read, so the bind param must reject what
the read path cannot load — otherwise a bad write makes the row unreadable.
These are fast no-DB pins of the TypeDecorator contract; the real-PostgreSQL
leg lives in tests/integration/test_product_format_ids_typed_roundtrip.py
(TestBindTimeValidationRejectsBadAgentUrl).
"""

import pytest
from pydantic import ValidationError

from src.core.database.json_type import JSONType
from src.core.schemas import FormatId


class TestBindValidatesRawDicts:
    def test_invalid_dict_in_typed_list_raises(self):
        col = JSONType(model=FormatId, is_list=True)
        with pytest.raises(ValidationError):
            col.process_bind_param([{"agent_url": "not a url", "id": "display_300x250"}], dialect=None)

    def test_valid_dict_in_typed_list_is_dumped_without_none_keys(self):
        col = JSONType(model=FormatId, is_list=True)
        result = col.process_bind_param(
            [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}], dialect=None
        )
        assert isinstance(result, list) and len(result) == 1
        entry = result[0]
        assert entry["id"] == "display_300x250"
        # AdCP optional fields are ABSENT, not null (plpgsql CHECK rejects nulls).
        assert "width" not in entry and "height" not in entry and "duration_ms" not in entry

    def test_invalid_dict_on_scalar_typed_column_raises(self):
        col = JSONType(model=FormatId)
        with pytest.raises(ValidationError):
            col.process_bind_param({"agent_url": "also not a url", "id": "x"}, dialect=None)

    def test_untyped_column_passes_dicts_through_unchanged(self):
        col = JSONType()
        raw = {"anything": "goes", "agent_url": "not a url"}
        assert col.process_bind_param(raw, dialect=None) == raw

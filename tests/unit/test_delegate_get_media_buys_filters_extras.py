"""Regression test: ``_coerce_to_request_model`` round-trips library-shape
requests into the impl-local schema without raising.

Original context (#273): ``get_media_buys`` returned ``INTERNAL_ERROR`` when the
framework handed the delegate a library ``GetMediaBuysRequest`` carrying
default-populated fields the impl-local schema didn't declare
(``include_snapshot``, ``include_history``, ``adcp_major_version``). The
filter in ``_coerce_to_request_model`` makes the framework→impl hop robust.

Issue #262 collapsed the impl-local schema onto the library type — the local
``GetMediaBuysRequest`` now extends ``LibraryGetMediaBuysRequest`` and honors
``include_snapshot``/``include_history`` per AdCP spec — so the framework
fields are no longer "extras" relative to the local schema. The filter still
exists because other request types may need it, and the framework round-trip
must remain robust to future spec growth on any request model. Dict input
remains strict so internal typos still surface.
"""

from __future__ import annotations

import pytest


def test_coerce_propagates_framework_fields_for_get_media_buys() -> None:
    """Framework-injected library fields round-trip to the impl-local schema.

    With #262, the local schema extends the library, so ``include_snapshot``
    and ``include_history`` now propagate verbatim.
    """
    from adcp.types import GetMediaBuysRequest as LibraryGetMediaBuysRequest

    from core.platforms._delegate import _coerce_to_request_model
    from src.core.schemas import GetMediaBuysRequest as LocalGetMediaBuysRequest

    lib_req = LibraryGetMediaBuysRequest(media_buy_ids=["mb_1"], include_snapshot=True)
    assert lib_req.include_snapshot is True

    local_req = _coerce_to_request_model(lib_req, LocalGetMediaBuysRequest)

    assert isinstance(local_req, LocalGetMediaBuysRequest)
    assert local_req.media_buy_ids == ["mb_1"]
    assert local_req.include_snapshot is True, (
        "include_snapshot is a buyer-facing AdCP spec field — must propagate"
    )


def test_coerce_dict_input_still_strict() -> None:
    """Dict input bypasses the filter — strict dev-mode validation still applies.

    Direct dict input is for callers who control the wire shape (tests, internal
    callers); they shouldn't get a silent drop on a typo.
    """
    from pydantic import ValidationError

    from core.platforms._delegate import _coerce_to_request_model
    from src.core.schemas import GetMediaBuysRequest

    with pytest.raises(ValidationError):
        _coerce_to_request_model({"media_buy_ids": ["x"], "definitely_not_a_field": True}, GetMediaBuysRequest)

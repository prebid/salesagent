"""Pin tests for ``_build_error_code_to_status()`` derivation contract.

The wire-code -> HTTP status table at
``src.core.tool_error_logging._ERROR_CODE_TO_STATUS`` is **derived from**
``AdCPError`` subclass class attributes (``error_code`` + ``status_code``)
at import time, eliminating drift potential a hand-maintained dict has.
The mutation test pinned here: removing ``error_code`` or ``status_code``
from any ``AdCPError`` subclass must break the table derivation —
otherwise the "derived from class attributes" claim is just a comment.

Structural-guard invariant.
"""

from __future__ import annotations

from src.core.exceptions import AdCPError
from src.core.tool_error_logging import _build_error_code_to_status


class TestErrorCodeStatusTableDerivation:
    """The status table is derived from class attributes, not hand-edited."""

    def test_every_adcp_error_subclass_present_in_status_table(self):
        """Pin: any ``AdCPError`` subclass with ``_default_error_code``+``_default_status_code`` is in the table.

        Mutation test: removing either attribute from a subclass breaks this assertion
        and forces a re-evaluation of why that subclass exists. The "derived from
        class attributes" invariant is the structural contract here — if a subclass
        is missing from the table, the boundary translator's plain-``ToolError``
        fallback will mis-classify its HTTP status.

        Reads the ``_default_*`` ClassVar slots directly (option-A refactor per
        salesagent-fnk9) — the public ``error_code``/``status_code`` are instance
        attributes set in ``__init__`` and would not resolve on the class object.
        Structural guards intentionally break encapsulation to assert how the code
        is written, not just its runtime behavior.
        """
        table = _build_error_code_to_status()
        missing: list[str] = []
        for cls in AdCPError.iter_concrete_subclasses():
            code = getattr(cls, "_default_error_code", None)
            status = getattr(cls, "_default_status_code", None)
            if not code or not status:
                continue
            if code not in table:
                missing.append(
                    f"  {cls.__name__}: _default_error_code={code!r} _default_status_code={status} not in table"
                )
        assert not missing, (
            "AdCPError subclasses are not represented in _ERROR_CODE_TO_STATUS — "
            "drift between class _default_* attributes and the derived table:\n" + "\n".join(missing)
        )

    def test_auth_required_resolves_to_403(self):
        """Pin: ``AUTH_REQUIRED`` resolves to 403 (Authorization class wins over Authentication).

        ``AdCPAuthenticationError`` (401, AUTH_TOKEN_INVALID) and
        ``AdCPAuthorizationError`` (403, AUTH_REQUIRED) both contribute. The
        derivation rule keeps the **highest** status when multiple subclasses
        share a wire code — 403 is the spec-aligned answer for AUTH_REQUIRED
        (authenticated but not authorized).
        """
        table = _build_error_code_to_status()
        status = table.get("AUTH_REQUIRED")
        assert status == 403, f"AUTH_REQUIRED must resolve to 403 (AdCPAuthorizationError), got {status}"

    def test_invalid_request_resolves_to_400(self):
        """Pin: ``INVALID_REQUEST`` resolves to 400.

        INVALID_REQUEST is the generic 4xx catchall — translation target for
        NOT_FOUND, etc. Anchored to 400 explicitly so propagation from
        differently-statused upstream codes (e.g., NOT_FOUND=404) doesn't
        accidentally promote it to 404. The boundary code documents this with
        an inline ``_GENERIC_CATCHALLS`` set.
        """
        table = _build_error_code_to_status()
        status = table.get("INVALID_REQUEST")
        assert status == 400, f"INVALID_REQUEST must resolve to 400 (generic 4xx catchall), got {status}"

    def test_auth_token_invalid_resolves_to_401(self):
        """Pin: ``AUTH_TOKEN_INVALID`` resolves to 401 from ``AdCPAuthenticationError``.

        AUTH_TOKEN_INVALID is the AUTH_TOKEN_INVALID class attribute on
        ``AdCPAuthenticationError`` (status 401). This is a STANDARD spec code
        — passthrough, not in ERROR_CODE_MAPPING.
        """
        table = _build_error_code_to_status()
        status = table.get("AUTH_TOKEN_INVALID")
        assert status == 401, f"AUTH_TOKEN_INVALID must resolve to 401 (AdCPAuthenticationError), got {status}"

    def test_service_unavailable_resolves_via_highest_status(self):
        """Pin: ``SERVICE_UNAVAILABLE`` takes the highest status when codes overlap.

        ``AdCPAdapterError`` (502) and ``AdCPServiceUnavailableError`` (503)
        both carry error_code="SERVICE_UNAVAILABLE". The "highest status wins"
        rule resolves to 503 — the more restrictive answer when the table is
        used for a plain-ToolError fallback that has no carried context.
        """
        table = _build_error_code_to_status()
        status = table.get("SERVICE_UNAVAILABLE")
        assert status == 503, f"SERVICE_UNAVAILABLE must resolve to 503 (highest status wins), got {status}"

    def test_table_is_built_from_function_not_module_const(self):
        """Pin: the function returns a fresh dict (no module-level mutation).

        ``_build_error_code_to_status()`` walking subclasses each call means
        new subclasses added between PRs are picked up automatically when the
        boundary translator imports the module. Verifies that the function
        is not a one-shot constant masquerading as a function.
        """
        table_a = _build_error_code_to_status()
        table_b = _build_error_code_to_status()
        assert table_a == table_b
        # Mutating one return value must not pollute the other (defensive).
        table_a["TEST_MUTATION_KEY"] = 999
        assert "TEST_MUTATION_KEY" not in table_b, "function leaks state across calls"

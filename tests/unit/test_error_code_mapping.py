"""Tests for the error code mapping from internal codes to SDK STANDARD_ERROR_CODES.

Verifies that:
1. ERROR_CODE_MAPPING exists and maps all known non-standard codes
2. Every mapped target is in STANDARD_ERROR_CODES
3. INTERNAL_CODES set exists for codes that never reach the wire
4. All AdCPError subclass error_code attributes are either standard or internal
"""

from adcp.server.helpers import STANDARD_ERROR_CODES

from src.core.exceptions import ERROR_CODE_MAPPING, INTERNAL_CODES, AdCPError


def _all_adcp_error_subclasses() -> list[type]:
    """Collect all concrete AdCPError subclasses."""
    result = []
    queue = [AdCPError]
    while queue:
        cls = queue.pop()
        for sub in cls.__subclasses__():
            result.append(sub)
            queue.append(sub)
    return result


class TestErrorCodeMapping:
    """Verify the mapping dict is complete and correct."""

    def test_mapping_exists(self):
        assert isinstance(ERROR_CODE_MAPPING, dict)
        assert len(ERROR_CODE_MAPPING) > 0, "Mapping must not be empty"

    def test_all_targets_are_standard(self):
        """Every mapped-to code must be in SDK STANDARD_ERROR_CODES."""
        std = set(STANDARD_ERROR_CODES)
        bad = {k: v for k, v in ERROR_CODE_MAPPING.items() if v not in std}
        assert not bad, f"Mapping targets not in STANDARD_ERROR_CODES: {bad}"

    def test_internal_codes_exist(self):
        assert isinstance(INTERNAL_CODES, frozenset)
        assert len(INTERNAL_CODES) > 0, "Internal codes set must not be empty"

    def test_internal_codes_overlap_with_mapping_have_wire_safe_targets(self):
        """Internal codes that also appear in the mapping must translate to STANDARD targets.

        INTERNAL_CODES documents codes that should never reach the wire as-is.
        ERROR_CODE_MAPPING is the safety net: if an internal code escapes to a
        boundary (base-class raise instead of a specific subclass), the mapping
        translates it to STANDARD_ERROR_CODES. Overlap is intentional — it
        means "this code is internal AND has a wire-safe fallback if it leaks".
        Both invariants must hold for every overlap entry.
        """
        std = set(STANDARD_ERROR_CODES)
        overlap = set(ERROR_CODE_MAPPING.keys()) & INTERNAL_CODES
        unsafe = {code: ERROR_CODE_MAPPING[code] for code in overlap if ERROR_CODE_MAPPING[code] not in std}
        assert not unsafe, f"Internal codes in mapping must translate to STANDARD targets: {unsafe}"

    def test_class_error_codes_are_standard_or_internal(self):
        """Every AdCPError subclass error_code must be standard, internal, or spec-required."""
        # Spec-required codes not yet in SDK STANDARD_ERROR_CODES
        spec_codes = {"AUTH_TOKEN_INVALID", "BILLING_NOT_SUPPORTED"}
        allowed = set(STANDARD_ERROR_CODES) | INTERNAL_CODES | spec_codes
        violations = []
        for cls in _all_adcp_error_subclasses():
            code = cls.error_code
            if code not in allowed:
                violations.append(f"{cls.__name__}.error_code = {code!r}")
        assert not violations, "AdCPError subclasses with non-standard, non-internal codes:\n" + "\n".join(
            f"  {v}" for v in violations
        )

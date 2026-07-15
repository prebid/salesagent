"""Test schema validation modes (production vs development).

Validation mode is set at class definition time via get_pydantic_extra_mode():
- Dev/test (default): extra='forbid' — rejects unknown fields with ValidationError
- Production (ENVIRONMENT=production): extra='ignore' — silently drops unknown fields

To test production-mode behavior, run:
    ENVIRONMENT=production pytest tests/unit/test_schema_validation_modes.py -v
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    CreateMediaBuyRequest,
    Creative,
    GetMediaBuyDeliveryRequest,
    GetProductsRequest,
    ListCreativeFormatsRequest,
    ListCreativesRequest,
    PackageRequest,
    Targeting,
)

# Minimal valid data for constructing test models
# adcp 3.6.0: brand replaced brand_manifest
_VALID_CMR_DATA = {
    "brand": {"domain": "testproduct.com"},
    "packages": [{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test"}],
    "start_time": "2025-02-15T00:00:00Z",
    "end_time": "2025-02-28T23:59:59Z",
    "idempotency_key": "unit-test-key-cmr-shared-data",
}

_VALID_PACKAGE_DATA = {"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test"}


class TestBuyerModelRejectsExtraInDev:
    """All buyer-facing request models reject unknown fields in dev mode (default)."""

    def test_create_media_buy_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            CreateMediaBuyRequest(**_VALID_CMR_DATA, bogus="injected")

    def test_package_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            PackageRequest(**_VALID_PACKAGE_DATA, bogus="injected")

    def test_targeting_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            Targeting(geo_country_any_of=["US"], bogus="injected")

    def test_creative_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            Creative(
                creative_id="c_1",
                variants=[],
                name="Test",
                format_id={"agent_url": "https://example.com", "id": "display/banner"},
                bogus="injected",
            )

    def test_list_creative_formats_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            ListCreativeFormatsRequest(bogus="injected")

    def test_list_creatives_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            ListCreativesRequest(bogus="injected")

    def test_get_media_buy_delivery_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            GetMediaBuyDeliveryRequest(bogus="injected")


class TestNestedModelRejectsExtraInDev:
    """Extra fields on nested models within CreateMediaBuyRequest are rejected."""

    def test_nested_package_rejects_extra(self):
        """Bogus field on PackageRequest within CMR.packages is rejected."""
        data = {**_VALID_CMR_DATA, "packages": [{**_VALID_PACKAGE_DATA, "bogus_pkg_field": "injected"}]}
        with pytest.raises(ValidationError, match="bogus_pkg_field"):
            CreateMediaBuyRequest(**data)

    def test_nested_targeting_rejects_extra(self):
        """Bogus field on targeting_overlay within a package is rejected."""
        data = {
            **_VALID_CMR_DATA,
            "packages": [
                {
                    **_VALID_PACKAGE_DATA,
                    "targeting_overlay": {"geo_country_any_of": ["US"], "bogus_targeting": "injected"},
                }
            ],
        }
        with pytest.raises(ValidationError, match="bogus_targeting"):
            CreateMediaBuyRequest(**data)


class TestExtFieldAccepted:
    """The AdCP ext field is the sanctioned extension mechanism and must be accepted."""

    def test_ext_field_accepted_on_cmr(self):
        cmr = CreateMediaBuyRequest(
            **_VALID_CMR_DATA,
            ext={"vendor": {"custom": "value"}},
        )
        assert cmr.ext is not None


class TestInternalModelsRejectExtra:
    """Models inheriting from our AdCPBaseModel also reject extra fields in dev."""

    def test_get_products_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="unknown_field"):
            GetProductsRequest(
                brief="test",
                brand={"domain": "test.com"},
                unknown_field="should_fail",
            )


class TestConfigHelperFunctions:
    """Test the config helper functions directly."""

    def test_development_mode(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

    def test_production_mode(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            assert is_production()
            assert get_pydantic_extra_mode() == "ignore"

    def test_staging_defaults_to_strict(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

    def test_case_insensitive(self):
        from src.core.config import is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "PRODUCTION"}):
            assert is_production()
        with patch.dict(os.environ, {"ENVIRONMENT": "Production"}):
            assert is_production()


class TestProductionModeBehavior:
    """Verify production mode end-to-end: env var → config helper → model behavior.

    model_config is evaluated at class definition time, so pre-imported models
    can't change mode at runtime. We create a fresh model class inside the
    patched environment to test the full chain.
    """

    def test_production_model_accepts_extra_fields(self):
        """Model defined under ENVIRONMENT=production silently drops extra fields."""
        from pydantic import BaseModel, ConfigDict

        from src.core.config import get_pydantic_extra_mode

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):

            class ProductionModel(BaseModel):
                model_config = ConfigDict(extra=get_pydantic_extra_mode())
                brief: str

            obj = ProductionModel(brief="test", unknown_field="should_be_ignored")
            assert obj.brief == "test"
            assert not hasattr(obj, "unknown_field")

    def test_dev_model_rejects_extra_fields(self):
        """Model defined under dev mode rejects extra fields."""
        from pydantic import BaseModel, ConfigDict

        from src.core.config import get_pydantic_extra_mode

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)

            class DevModel(BaseModel):
                model_config = ConfigDict(extra=get_pydantic_extra_mode())
                brief: str

            with pytest.raises(ValidationError, match="unknown_field"):
                DevModel(brief="test", unknown_field="should_fail")


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Runs in a SUBPROCESS with ENVIRONMENT=production set BEFORE import, so the
# api_v1 *Body classes are compiled with extra="ignore". Asserts the Pattern #7
# prod contract on the REAL request-body classes: the unknown top-level field
# is silently dropped — validation succeeds, known values survive, and the
# unknown key is absent from the model and its dump.
_PROD_IGNORE_SCRIPT = textwrap.dedent(
    """
    import os

    assert os.environ["ENVIRONMENT"] == "production", "script requires ENVIRONMENT=production"

    from src.routes.api_v1 import CreateMediaBuyBody, GetProductsBody

    cmb = CreateMediaBuyBody.model_validate({"po_number": "po-1", "nonsense_field": "bar"})
    assert cmb.po_number == "po-1", f"known field lost: {cmb!r}"
    assert not hasattr(cmb, "nonsense_field"), "unknown field must be dropped, not stored"
    assert "nonsense_field" not in cmb.model_dump(), "unknown field leaked into model_dump"

    gp = GetProductsBody.model_validate({"brief": "video ads", "nonsense_field": "bar"})
    assert gp.brief == "video ads", f"known field lost: {gp!r}"
    assert not hasattr(gp, "nonsense_field"), "unknown field must be dropped, not stored"
    assert "nonsense_field" not in gp.model_dump(), "unknown field leaked into model_dump"

    print("PROD_IGNORE_OK")
    """
)


class TestProductionModeRestBodyIgnoresExtra:
    """api_v1 REST *Body classes silently DROP unknown top-level fields in production.

    Pattern #7 prod arm (GH #1442, salesagent-cyz0). The extra mode binds at
    CLASS DEFINITION (import) time via ``ConfigDict(extra=get_pydantic_extra_mode())``,
    so a runtime env patch cannot flip already-imported classes — the
    production behavior is pinned in a SUBPROCESS that sets
    ENVIRONMENT=production before importing ``src.routes.api_v1``.

    REST has no runtime unknown-field strip (rest_compat_middleware only
    renames deprecated fields), so this class config IS the entire prod-ignore
    mechanism for REST bodies: pinning it at the class pins the REST behavior.

    The dev-forbid counterpart on the same classes runs in-process below with
    the SAME payload — same input, opposite outcome per environment — proving
    the subprocess assertion bites (it is the mode, not the payload, under test).
    The wire-level dev-forbid contract (INVALID_REQUEST envelope) is pinned in
    tests/integration/test_rest_body_extra_field_policy.py.
    """

    def test_production_rest_body_drops_unknown_top_level_field(self):
        """Subprocess with ENVIRONMENT=production: unknown top-level field ignored."""
        proc = subprocess.run(
            [sys.executable, "-c", _PROD_IGNORE_SCRIPT],
            capture_output=True,
            text=True,
            env={**os.environ, "ENVIRONMENT": "production"},
            cwd=str(_REPO_ROOT),
            timeout=180,
        )
        assert proc.returncode == 0, (
            f"Production-mode Body validation must accept and drop the unknown field.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr[-2000:]}"
        )
        assert "PROD_IGNORE_OK" in proc.stdout, f"Subprocess did not reach final assertion: {proc.stdout}"

    def test_dev_rest_body_rejects_same_payload_in_process(self):
        """Dev mode (this process): the SAME payload is rejected on the same class.

        Establishes that the subprocess test above is non-vacuous: the payload
        only validates under the production extra mode.
        """
        from src.routes.api_v1 import CreateMediaBuyBody

        with pytest.raises(ValidationError, match="nonsense_field"):
            CreateMediaBuyBody.model_validate({"po_number": "po-1", "nonsense_field": "bar"})

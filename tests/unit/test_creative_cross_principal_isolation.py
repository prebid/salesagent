"""Test creative cross-principal isolation (BR-RULE-034 P0).

Bug salesagent-6isd: creative_id is the sole PK in the creatives table,
making cross-principal isolation impossible. Two principals with the same
creative_id hit a UniqueViolation.

The fix: composite PK (creative_id, tenant_id, principal_id).
"""

from sqlalchemy import inspect as sa_inspect

from src.core.database.models import Creative


class TestCreativePrimaryKeyIsolation:
    """Verify the Creative model supports cross-principal isolation via composite PK."""

    def test_creative_pk_includes_tenant_and_principal(self):
        """Creative PK must be (creative_id, tenant_id, principal_id).

        BR-RULE-034 (P0): creative_id is buyer-scoped. Two principals must be
        able to have records with the same creative_id without conflict.
        A sole PK on creative_id makes this impossible.
        """
        mapper = sa_inspect(Creative)
        pk_column_names = [col.name for col in mapper.primary_key]
        assert set(pk_column_names) == {"creative_id", "tenant_id", "principal_id"}, (
            f"Creative PK is {pk_column_names} but must be "
            f"(creative_id, tenant_id, principal_id) for cross-principal isolation"
        )

    def test_creative_pk_is_composite_not_sole(self):
        """Creative PK must not be a single column.

        A sole PK on creative_id is a security boundary violation -- it prevents
        different principals from having creatives with the same external ID.
        """
        mapper = sa_inspect(Creative)
        pk_column_names = [col.name for col in mapper.primary_key]
        assert len(pk_column_names) > 1, (
            f"Creative PK has only {len(pk_column_names)} column(s): {pk_column_names}. "
            f"Sole creative_id PK prevents cross-principal isolation (BR-RULE-034 P0)"
        )

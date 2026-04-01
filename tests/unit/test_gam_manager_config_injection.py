"""Tests that GAM adapter managers receive config at construction time.

Regression tests for PR #1163: eliminate GAM adapter→DB circular dependency.
After the fix, managers receive pre-loaded config instead of querying the DB.
"""

from unittest.mock import patch


class TestGAMTargetingManagerConfigInjection:
    """GAMTargetingManager should accept targeting config as constructor params."""

    def test_accepts_targeting_config_param(self):
        """GAMTargetingManager must accept targeting_config dict at construction."""
        from src.adapters.gam.managers.targeting import GAMTargetingManager

        targeting_config = {
            "axe_include_key": "hb_pb",
            "axe_exclude_key": "hb_exclude",
            "axe_macro_key": None,
            "custom_targeting_keys": {"hb_pb": "123", "hb_source": "456"},
        }

        with patch("src.core.database.database_session.get_db_session") as mock_db:
            manager = GAMTargetingManager(
                tenant_id="test_tenant",
                gam_client=None,
                targeting_config=targeting_config,
            )

        # DB should not have been called — config was injected
        mock_db.assert_not_called()

        # Config should be stored on the manager
        assert manager.axe_include_key == "hb_pb"
        assert manager.axe_exclude_key == "hb_exclude"
        assert manager.custom_targeting_key_ids == {"hb_pb": "123", "hb_source": "456"}

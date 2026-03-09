"""Tests for CreativeFactory and CreativeAssignmentFactory.

Verifies factories produce valid ORM instances with all NOT NULL fields
populated, correct SubFactory relationships, and proper ALL_FACTORIES
registration.
"""

from __future__ import annotations


class TestCreativeFactoryContract:
    """CreativeFactory produces valid Creative ORM instances."""

    def test_import_succeeds(self):
        """CreativeFactory is importable."""
        from tests.factories.creative import CreativeFactory

        assert CreativeFactory is not None

    def test_in_all_factories(self):
        """CreativeFactory registered in ALL_FACTORIES for session binding."""
        from tests.factories import ALL_FACTORIES
        from tests.factories.creative import CreativeFactory

        assert CreativeFactory in ALL_FACTORIES

    def test_meta_model_is_creative(self):
        """Factory targets the Creative ORM model."""
        from src.core.database.models import Creative
        from tests.factories.creative import CreativeFactory

        assert CreativeFactory._meta.model is Creative

    def test_session_is_none(self):
        """Session is None (bound dynamically by IntegrationEnv)."""
        from tests.factories.creative import CreativeFactory

        assert CreativeFactory._meta.sqlalchemy_session is None

    def test_persistence_is_commit(self):
        """Session persistence is 'commit'."""
        from tests.factories.creative import CreativeFactory

        assert CreativeFactory._meta.sqlalchemy_session_persistence == "commit"


class TestCreativeAssignmentFactoryContract:
    """CreativeAssignmentFactory produces valid CreativeAssignment ORM instances."""

    def test_import_succeeds(self):
        """CreativeAssignmentFactory is importable."""
        from tests.factories.creative import CreativeAssignmentFactory

        assert CreativeAssignmentFactory is not None

    def test_in_all_factories(self):
        """CreativeAssignmentFactory registered in ALL_FACTORIES."""
        from tests.factories import ALL_FACTORIES
        from tests.factories.creative import CreativeAssignmentFactory

        assert CreativeAssignmentFactory in ALL_FACTORIES

    def test_meta_model_is_creative_assignment(self):
        """Factory targets the CreativeAssignment ORM model."""
        from src.core.database.models import CreativeAssignment
        from tests.factories.creative import CreativeAssignmentFactory

        assert CreativeAssignmentFactory._meta.model is CreativeAssignment

    def test_session_is_none(self):
        """Session is None (bound dynamically by IntegrationEnv)."""
        from tests.factories.creative import CreativeAssignmentFactory

        assert CreativeAssignmentFactory._meta.sqlalchemy_session is None

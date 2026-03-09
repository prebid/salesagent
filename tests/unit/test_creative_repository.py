"""Unit tests for CreativeRepository and CreativeAssignmentRepository.

These tests verify the repository interface contract: method signatures,
tenant isolation, and return types. For real query execution against Postgres,
see integration tests.

beads: salesagent-o9k4
"""

from unittest.mock import MagicMock, patch

from src.core.database.repositories.creative import (
    CreativeAssignmentRepository,
    CreativeListResult,
    CreativeRepository,
)


class TestCreativeRepositoryInit:
    """Repository construction and tenant isolation."""

    def test_tenant_id_stored(self):
        """Repository stores tenant_id at construction."""
        session = MagicMock()
        repo = CreativeRepository(session, "t1")
        assert repo.tenant_id == "t1"

    def test_tenant_id_property(self):
        """tenant_id is read-only via property."""
        session = MagicMock()
        repo = CreativeRepository(session, "tenant_abc")
        assert repo.tenant_id == "tenant_abc"


class TestCreativeRepositoryGetById:
    """get_by_id scopes by tenant, principal, and creative_id."""

    def test_returns_matching_creative(self):
        """get_by_id returns a creative when found."""
        session = MagicMock()
        fake_creative = MagicMock()
        session.scalars.return_value.first.return_value = fake_creative

        repo = CreativeRepository(session, "t1")
        result = repo.get_by_id("c1", "p1")

        assert result is fake_creative
        session.scalars.assert_called_once()

    def test_returns_none_when_not_found(self):
        """get_by_id returns None when no matching creative."""
        session = MagicMock()
        session.scalars.return_value.first.return_value = None

        repo = CreativeRepository(session, "t1")
        result = repo.get_by_id("nonexistent", "p1")

        assert result is None


class TestCreativeRepositoryGetByPrincipal:
    """get_by_principal returns paginated results with total count."""

    def test_returns_creative_list_result(self):
        """get_by_principal returns CreativeListResult namedtuple."""
        session = MagicMock()
        session.scalar.return_value = 5
        session.scalars.return_value.all.return_value = [MagicMock(), MagicMock()]

        repo = CreativeRepository(session, "t1")
        result = repo.get_by_principal("p1")

        assert isinstance(result, CreativeListResult)
        assert result.total_count == 5
        assert len(result.creatives) == 2

    def test_zero_results(self):
        """get_by_principal returns empty list and zero count when no matches."""
        session = MagicMock()
        session.scalar.return_value = 0
        session.scalars.return_value.all.return_value = []

        repo = CreativeRepository(session, "t1")
        result = repo.get_by_principal("p1")

        assert result.total_count == 0
        assert result.creatives == []

    def test_none_count_treated_as_zero(self):
        """get_by_principal treats None total count as 0."""
        session = MagicMock()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = []

        repo = CreativeRepository(session, "t1")
        result = repo.get_by_principal("p1")

        assert result.total_count == 0


class TestCreativeRepositoryListByPrincipal:
    """list_by_principal returns all creatives for a principal."""

    def test_returns_list(self):
        """list_by_principal returns list of creatives."""
        session = MagicMock()
        fakes = [MagicMock(), MagicMock(), MagicMock()]
        session.scalars.return_value.all.return_value = fakes

        repo = CreativeRepository(session, "t1")
        result = repo.list_by_principal("p1")

        assert len(result) == 3
        session.scalars.assert_called_once()


class TestCreativeRepositoryCreate:
    """create() persists a new creative and returns it."""

    def test_creates_and_flushes(self):
        """create() adds to session and flushes."""
        session = MagicMock()
        repo = CreativeRepository(session, "t1")

        result = repo.create(
            creative_id="c1",
            name="Test Banner",
            agent_url="https://agent.example.com",
            format="display_300x250_image",
            principal_id="p1",
            data={"assets": {"banner": {"url": "https://example.com/banner.png"}}},
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()
        db_obj = session.add.call_args[0][0]
        assert db_obj.creative_id == "c1"
        assert db_obj.tenant_id == "t1"
        assert db_obj.principal_id == "p1"
        assert db_obj.name == "Test Banner"

    def test_generates_id_when_not_provided(self):
        """create() generates a UUID creative_id when not provided."""
        session = MagicMock()
        repo = CreativeRepository(session, "t1")

        result = repo.create(
            name="Auto ID",
            agent_url="https://agent.example.com",
            format="display_300x250_image",
            principal_id="p1",
        )

        db_obj = session.add.call_args[0][0]
        assert db_obj.creative_id is not None
        assert len(db_obj.creative_id) > 0  # UUID string


class TestCreativeRepositoryUpdateData:
    """update_data() sets data and flags modified."""

    def test_sets_data_and_flags(self):
        """update_data sets the data field and calls flag_modified."""
        session = MagicMock()
        repo = CreativeRepository(session, "t1")

        fake_creative = MagicMock()
        new_data = {"assets": {"banner": {"url": "https://new.example.com/banner.png"}}}

        with patch("src.core.database.repositories.creative.attributes") as mock_attrs:
            repo.update_data(fake_creative, new_data)

            assert fake_creative.data == new_data
            mock_attrs.flag_modified.assert_called_once_with(fake_creative, "data")


class TestCreativeRepositoryFlush:
    """flush() delegates to session."""

    def test_flushes_session(self):
        """flush() calls session.flush()."""
        session = MagicMock()
        repo = CreativeRepository(session, "t1")
        repo.flush()
        session.flush.assert_called_once()


# ============================================================================
# CreativeAssignmentRepository
# ============================================================================


class TestCreativeAssignmentRepositoryInit:
    """Repository construction."""

    def test_tenant_id_stored(self):
        """Repository stores tenant_id at construction."""
        session = MagicMock()
        repo = CreativeAssignmentRepository(session, "t1")
        assert repo.tenant_id == "t1"


class TestCreativeAssignmentRepositoryGetByCreative:
    """get_by_creative scopes by tenant and creative_id."""

    def test_returns_matching_assignments(self):
        """get_by_creative returns list of assignments."""
        session = MagicMock()
        fakes = [MagicMock(), MagicMock()]
        session.scalars.return_value.all.return_value = fakes

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.get_by_creative("c1")

        assert len(result) == 2
        session.scalars.assert_called_once()


class TestCreativeAssignmentRepositoryGetByPackage:
    """get_by_package scopes by tenant and package_id."""

    def test_returns_matching_assignments(self):
        """get_by_package returns list of assignments."""
        session = MagicMock()
        fakes = [MagicMock()]
        session.scalars.return_value.all.return_value = fakes

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.get_by_package("pkg_1")

        assert len(result) == 1


class TestCreativeAssignmentRepositoryGetExisting:
    """get_existing looks up by composite key."""

    def test_returns_existing_assignment(self):
        """get_existing returns assignment when found."""
        session = MagicMock()
        fake_assignment = MagicMock()
        session.scalars.return_value.first.return_value = fake_assignment

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.get_existing("mb1", "pkg1", "c1")

        assert result is fake_assignment

    def test_returns_none_when_not_found(self):
        """get_existing returns None when not found."""
        session = MagicMock()
        session.scalars.return_value.first.return_value = None

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.get_existing("mb1", "pkg1", "nonexistent")

        assert result is None


class TestCreativeAssignmentRepositoryCreate:
    """create() persists a new assignment."""

    def test_creates_assignment(self):
        """create() adds assignment to session."""
        session = MagicMock()
        repo = CreativeAssignmentRepository(session, "t1")

        result = repo.create(
            media_buy_id="mb1",
            package_id="pkg1",
            creative_id="c1",
            principal_id="p1",
            weight=75,
        )

        session.add.assert_called_once()
        db_obj = session.add.call_args[0][0]
        assert db_obj.tenant_id == "t1"
        assert db_obj.media_buy_id == "mb1"
        assert db_obj.package_id == "pkg1"
        assert db_obj.creative_id == "c1"
        assert db_obj.weight == 75

    def test_default_weight_100(self):
        """create() uses weight=100 by default."""
        session = MagicMock()
        repo = CreativeAssignmentRepository(session, "t1")

        result = repo.create(
            media_buy_id="mb1",
            package_id="pkg1",
            creative_id="c1",
        )

        db_obj = session.add.call_args[0][0]
        assert db_obj.weight == 100


class TestCreativeAssignmentRepositoryDelete:
    """delete() removes an assignment by ID."""

    def test_deletes_existing(self):
        """delete() returns True when assignment found and deleted."""
        session = MagicMock()
        fake_assignment = MagicMock()
        session.scalars.return_value.first.return_value = fake_assignment

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.delete("assign_1")

        assert result is True
        session.delete.assert_called_once_with(fake_assignment)

    def test_returns_false_when_not_found(self):
        """delete() returns False when assignment not found."""
        session = MagicMock()
        session.scalars.return_value.first.return_value = None

        repo = CreativeAssignmentRepository(session, "t1")
        result = repo.delete("nonexistent")

        assert result is False
        session.delete.assert_not_called()

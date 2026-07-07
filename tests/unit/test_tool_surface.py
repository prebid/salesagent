"""Unit tests for the configurable advertised tool surface (src/core/tool_surface.py)."""

from src.core.tool_surface import advertised_skills, is_advertised, unadvertised_tools

_ENV = "ADCP_UNADVERTISED_TOOLS"


class _Skill:
    """Minimal stand-in for an A2A AgentSkill (only ``.id`` is read)."""

    def __init__(self, skill_id: str):
        self.id = skill_id


class TestUnadvertisedTools:
    def test_empty_by_default(self, monkeypatch):
        monkeypatch.delenv(_ENV, raising=False)
        assert unadvertised_tools() == frozenset()

    def test_parses_comma_separated_and_trims(self, monkeypatch):
        monkeypatch.setenv(_ENV, "update_media_buy, get_media_buys ,sync_creatives")
        assert unadvertised_tools() == frozenset({"update_media_buy", "get_media_buys", "sync_creatives"})

    def test_ignores_blank_entries(self, monkeypatch):
        monkeypatch.setenv(_ENV, " , ,")
        assert unadvertised_tools() == frozenset()


class TestIsAdvertised:
    def test_advertised_when_not_withheld(self):
        assert is_advertised("get_products", frozenset()) is True

    def test_not_advertised_when_withheld(self):
        assert is_advertised("update_media_buy", frozenset({"update_media_buy"})) is False

    def test_reads_env_when_withheld_omitted(self, monkeypatch):
        monkeypatch.setenv(_ENV, "update_media_buy")
        assert is_advertised("update_media_buy") is False
        assert is_advertised("get_products") is True


class TestAdvertisedSkills:
    def test_all_advertised_by_default(self, monkeypatch):
        monkeypatch.delenv(_ENV, raising=False)
        skills = [_Skill("get_products"), _Skill("update_media_buy")]
        assert [s.id for s in advertised_skills(skills)] == ["get_products", "update_media_buy"]

    def test_filters_withheld_skills(self, monkeypatch):
        monkeypatch.setenv(_ENV, "update_media_buy,get_media_buys")
        skills = [_Skill("get_products"), _Skill("update_media_buy"), _Skill("get_media_buys")]
        assert [s.id for s in advertised_skills(skills)] == ["get_products"]

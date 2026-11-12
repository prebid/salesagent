"""``CustomTargeting``: a product's stored custom targeting, and the buyer's, read into one model.

Pure functions over the three stored formats (groups, include/exclude, legacy key-values)
and the AND that combines buyer and product targeting. Rendering the model into the GAM
tree, which looks value names up in GAM, is graded in
``tests/integration/test_gam_product_custom_targeting.py``.
"""

import pytest
from pydantic import ValidationError

from src.adapters.gam_implementation_config_schema import CustomCriterion, CustomTargeting, GAMImplementationConfig
from src.admin.blueprints.products import custom_key_inventory_ids
from src.services.gam_product_config_service import GAMProductConfigService


def _criterion(key_id: int, *values: int | str, exclude: bool = False) -> CustomCriterion:
    return CustomCriterion(key_id=key_id, values=values, exclude=exclude)


def _targeting(*branches: tuple[CustomCriterion, ...]) -> CustomTargeting:
    return CustomTargeting(branches=branches)


class TestLegacyKeyValues:
    """``{key_id: value_name}``: every key ANDed in one branch, every value a name."""

    def test_keys_are_anded(self):
        assert CustomTargeting.from_stored({"11111": "sports", "22222": "premium"}) == _targeting(
            (_criterion(11111, "sports"), _criterion(22222, "premium"))
        )

    def test_not_prefix_excludes(self):
        assert CustomTargeting.from_stored({"11111": "sports", "NOT_22222": "budget"}) == _targeting(
            (_criterion(11111, "sports"), _criterion(22222, "budget", exclude=True))
        )

    def test_a_value_of_digits_is_still_a_name(self):
        """Buyer key-values and AXE segment values are names, whatever they are spelled with."""
        assert CustomTargeting.from_key_values({"11111": "448512"}) == _targeting((_criterion(11111, "448512"),))

    @pytest.mark.parametrize(
        "stored",
        [
            pytest.param({"sport": "football"}, id="key-is-not-a-key-id"),
            pytest.param({"11111": ["sports"]}, id="list-value"),
            pytest.param({"11111": []}, id="empty-list-value"),
            pytest.param({"11111": 448512}, id="number-value"),
            pytest.param({"11111": ""}, id="empty-name"),
        ],
    )
    def test_malformed_is_refused(self, stored):
        with pytest.raises(ValidationError):
            CustomTargeting.from_stored(stored)


class TestIncludeExclude:
    """``{"include": {...}, "exclude": {...}, "operator": ...}``: several values for one key are ORed."""

    def test_include_and_exclude_are_anded_by_default(self):
        stored = {"include": {"11111": ["sports", "news"]}, "exclude": {"33333": ["adult"]}}

        assert CustomTargeting.from_stored(stored) == _targeting(
            (_criterion(11111, "sports", "news"), _criterion(33333, "adult", exclude=True))
        )

    def test_operator_or_gives_each_criterion_its_own_branch(self):
        stored = {"include": {"11111": ["sports"], "22222": ["premium"]}, "operator": "OR"}

        assert CustomTargeting.from_stored(stored) == _targeting(
            (_criterion(11111, "sports"),), (_criterion(22222, "premium"),)
        )

    def test_a_value_of_digits_is_a_value_id(self):
        stored = {"include": {"11111": ["448512", "sports"]}, "exclude": {"33333": ["448601"]}}

        assert CustomTargeting.from_stored(stored) == _targeting(
            (_criterion(11111, 448512, "sports"), _criterion(33333, 448601, exclude=True))
        )

    def test_a_key_with_no_values_is_left_out(self):
        stored = {"include": {"11111": ["sports"], "22222": []}, "exclude": {"33333": []}}

        assert CustomTargeting.from_stored(stored) == _targeting((_criterion(11111, "sports"),))

    def test_nothing_left_is_no_targeting(self):
        assert CustomTargeting.from_stored({"include": {}, "exclude": {}, "operator": "AND"}) == CustomTargeting()

    @pytest.mark.parametrize(
        "stored",
        [
            pytest.param({"include": {"11111": "sports"}}, id="values-not-a-list"),
            pytest.param({"include": {"sport": ["football"]}}, id="key-is-not-a-key-id"),
            pytest.param({"include": {"11111": ["sports"]}, "operator": "XOR"}, id="unknown-operator"),
            pytest.param({"include": {"11111": ["sports"]}, "22222": "premium"}, id="legacy-key-alongside"),
        ],
    )
    def test_malformed_is_refused(self, stored):
        with pytest.raises(ValidationError):
            CustomTargeting.from_stored(stored)


class TestGroups:
    """What the targeting widget saves: groups ORed, criteria in a group ANDed."""

    def test_groups_are_ored_and_criteria_anded(self):
        stored = {
            "groups": [
                {
                    "criteria": [
                        {"keyId": "11001", "values": ["448512", "448513"]},
                        {"keyId": "11002", "values": ["448601"], "exclude": True},
                    ]
                },
                {"criteria": [{"keyId": "11003", "values": ["448701"]}]},
            ]
        }

        assert CustomTargeting.from_stored(stored) == _targeting(
            (_criterion(11001, 448512, 448513), _criterion(11002, 448601, exclude=True)),
            (_criterion(11003, 448701),),
        )

    def test_a_value_of_digits_is_a_value_id_and_anything_else_a_name(self):
        stored = {"groups": [{"criteria": [{"keyId": 11001, "values": ["448512", "sports"]}]}]}

        assert CustomTargeting.from_stored(stored) == _targeting((_criterion(11001, 448512, "sports"),))

    def test_a_name_object_is_a_value_name_even_of_digits(self):
        """How ``to_stored`` writes a name, so a name of digits survives the widget."""
        stored = {"groups": [{"criteria": [{"keyId": "11001", "values": [{"name": "448512"}, "448513"]}]}]}

        assert CustomTargeting.from_stored(stored) == _targeting((_criterion(11001, "448512", 448513),))

    def test_empty_groups_and_criteria_with_no_values_are_left_out(self):
        """The widget saves both while a row is still being filled in."""
        stored = {
            "groups": [
                {"criteria": []},
                {"criteria": [{"keyId": "11111", "values": []}, {"keyId": "22222", "values": ["sports"]}]},
            ]
        }

        assert CustomTargeting.from_stored(stored) == _targeting((_criterion(22222, "sports"),))

    def test_nothing_left_is_no_targeting(self):
        stored = {"groups": [{"criteria": []}, {"criteria": [{"keyId": "11111", "values": []}]}]}

        assert CustomTargeting.from_stored(stored) == CustomTargeting()

    @pytest.mark.parametrize(
        "stored",
        [
            pytest.param({"groups": [{"criteria": [{"values": ["1"]}]}]}, id="no-key-id"),
            pytest.param({"groups": [{"criteria": [{"keyId": "sport", "values": ["1"]}]}]}, id="key-is-not-a-key-id"),
            pytest.param({"groups": [{"criteria": [{"keyId": "11111", "values": "1"}]}]}, id="values-not-a-list"),
            pytest.param({"groups": "11111"}, id="groups-not-a-list"),
            pytest.param(
                {"groups": [{"criteria": [{"keyId": "11111", "values": ["1"]}]}], "22222": "premium"},
                id="legacy-key-alongside",
            ),
            pytest.param({"groups": [{"criteria": [{"keyId": "11111", "value": ["1"]}]}]}, id="value-not-values"),
            pytest.param({"groups": [{"criteria": [], "operator": "AND"}]}, id="unknown-group-key"),
            pytest.param(
                {"groups": [{"criteria": [{"keyId": "11111", "values": [{"name": "a", "id": 1}]}]}]},
                id="unknown-name-key",
            ),
            pytest.param({"groups": [{"criteria": [{"keyId": "11111", "values": [{"name": ""}]}]}]}, id="empty-name"),
        ],
    )
    def test_malformed_is_refused(self, stored):
        with pytest.raises(ValidationError):
            CustomTargeting.from_stored(stored)


class TestToStored:
    """What the edit page hands the widget: the groups format, read back unchanged."""

    def test_ids_are_strings_and_names_are_name_objects(self):
        targeting = CustomTargeting.from_stored({"11001": "448512", "NOT_11002": "sports"})

        assert targeting.to_stored() == {
            "groups": [
                {
                    "criteria": [
                        {"keyId": "11001", "values": [{"name": "448512"}], "exclude": False},
                        {"keyId": "11002", "values": [{"name": "sports"}], "exclude": True},
                    ]
                }
            ]
        }

    def test_reads_back_unchanged(self):
        targeting = _targeting(
            (_criterion(11001, 448512, "448513"), _criterion(11002, "sports", exclude=True)),
            (_criterion(11003, 448701),),
        )

        assert CustomTargeting.from_stored(targeting.to_stored()) == targeting

    def test_no_targeting_is_no_groups(self):
        assert CustomTargeting().to_stored() == {"groups": []}


def test_no_custom_targeting_is_no_branches():
    assert CustomTargeting.from_stored({}) == CustomTargeting()


class TestAnd:
    """Buyer AND product: GAM takes only an OR of ANDs, so the AND is spread over the OR branches."""

    A, B, C, D, E = (_criterion(key_id, key_id * 10) for key_id in (1, 2, 3, 4, 5))

    def test_no_targeting_on_one_side_leaves_the_other(self):
        targeting = _targeting((self.A,))

        assert targeting & CustomTargeting() == targeting
        assert CustomTargeting() & targeting == targeting

    def test_one_branch_each_merges_into_one_branch(self):
        assert _targeting((self.A, self.B)) & _targeting((self.C,)) == _targeting((self.A, self.B, self.C))

    def test_branches_multiply_in_order(self):
        """``(A OR B) AND (CD OR E)``: every pairing, the first side outermost."""
        combined = _targeting((self.A,), (self.B,)) & _targeting((self.C, self.D), (self.E,))

        assert combined == _targeting(
            (self.A, self.C, self.D), (self.A, self.E), (self.B, self.C, self.D), (self.B, self.E)
        )


def test_inventory_mappings_list_each_targeted_key_and_value_once():
    """The product's ``custom_key`` mappings come from the model, whatever format was stored."""
    stored = {
        "groups": [
            {"criteria": [{"keyId": "11001", "values": ["448512", {"name": "sports"}]}]},
            {
                "criteria": [
                    {"keyId": "11001", "values": ["448512"]},
                    {"keyId": "11002", "values": ["9"], "exclude": True},
                ]
            },
        ]
    }

    assert custom_key_inventory_ids(CustomTargeting.from_stored(stored)) == [
        "11001=448512",
        "11001=sports",
        "NOT_11002=9",
    ]


def test_implementation_config_reads_the_stored_format():
    """``GAMImplementationConfig.custom_targeting_keys`` is the model, read from what a row stores."""
    config = GAMImplementationConfig.model_validate(
        {
            "creative_placeholders": [{"width": 300, "height": 250}],
            "custom_targeting_keys": {"groups": [{"criteria": [{"keyId": "11003", "values": ["448701"]}]}]},
        }
    )

    assert config.custom_targeting_keys == _targeting((_criterion(11003, 448701),))


def test_product_config_validation_refuses_custom_targeting_gam_cannot_take():
    """The buy-time check every GAM product goes through refuses it before any GAM call."""
    config = {"priority": 8, "creative_placeholders": [{"width": 300, "height": 250}]}

    assert GAMProductConfigService.validate_config(config | {"custom_targeting_keys": {"11001": "sports"}}) == (
        True,
        None,
    )
    assert GAMProductConfigService.validate_config(config | {"custom_targeting_keys": {"sport": "football"}}) == (
        False,
        "custom_targeting_keys is not custom targeting GAM can take",
    )

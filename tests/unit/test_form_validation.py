"""Admin form sanitization and presence checks.

The dict-of-callables branch of ``validate_form_data`` and the whole
``FormValidator`` class were deleted with ``src/core/validation.py``: no caller
ever reached either, and these tests were the only thing keeping them alive.
Both admin call sites pass a list of required field names.
"""

from decimal import Decimal

import pytest

from src.admin.form_validation import form_number, sanitize_form_data, sanitize_url, validate_form_data
from src.core.operator_errors import FormIssue, InvalidFormInput


class TestValidateFormData:
    def test_all_required_fields_present(self):
        data = {"name": "Test User", "email": "test@example.com"}

        assert validate_form_data(data, ["name", "email"]) == (True, [])

    def test_missing_field_is_named_in_the_error(self):
        is_valid, errors = validate_form_data({"name": "Test User"}, ["name", "email"])

        assert is_valid is False
        assert errors == ["Email is required"]

    def test_whitespace_only_field_counts_as_missing(self):
        is_valid, errors = validate_form_data({"name": "Test User", "email": "   "}, ["name", "email"])

        assert is_valid is False
        assert errors == ["Email is required"]

    def test_no_required_fields_admits_anything(self):
        assert validate_form_data({"any": "data"}, []) == (True, [])


class TestSanitizeFormData:
    def test_string_fields_are_trimmed(self):
        assert sanitize_form_data({"name": "  Test  "})["name"] == "Test"

    def test_a_url_field_gets_the_scheme_the_operator_left_off(self):
        assert sanitize_form_data({"agent_url": "example.com/"})["agent_url"] == "https://example.com"

    def test_a_json_field_is_pretty_printed(self):
        assert sanitize_form_data({"config": '{"a":1}'})["config"] == '{\n  "a": 1\n}'

    def test_unparseable_json_is_handed_back_so_the_form_can_redisplay_it(self):
        assert sanitize_form_data({"config": "{not json"})["config"] == "{not json"

    def test_non_string_values_pass_through_untouched(self):
        assert sanitize_form_data({"count": 3, "on": True}) == {"count": 3, "on": True}


class TestSanitizeUrl:
    def test_an_existing_scheme_is_kept(self):
        assert sanitize_url("http://example.com") == "http://example.com"

    def test_empty_stays_empty(self):
        assert sanitize_url("") == ""


class TestFormNumber:
    @pytest.mark.parametrize(
        ("cast", "raw"),
        [
            (float, "nan"),
            (float, "inf"),
            (float, "-Infinity"),
            (float, "1e999"),
            (Decimal, "NaN"),
            (Decimal, "sNaN"),
            (Decimal, "Infinity"),
            (Decimal, "abc"),
            (int, "1.5"),
            (int, ""),
        ],
    )
    def test_a_value_that_is_not_a_finite_number_is_refused_for_its_field(self, cast, raw):
        with pytest.raises(InvalidFormInput) as refused:
            form_number(cast, "fill_rate", raw)

        assert refused.value.issues == (FormIssue("fill_rate", "fill_rate must be a number"),)

    def test_a_decimal_is_read_exactly(self):
        assert form_number(Decimal, "min_package_budget", "0.10") == Decimal("0.10")

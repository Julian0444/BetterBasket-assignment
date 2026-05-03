"""Tests for betterbasket_matcher.io — Phase 2 regression."""
import pytest
from pathlib import Path

from betterbasket_matcher.io import is_numeric_id, parse_json_dict, parse_tags, read_products

FIXTURES = Path(__file__).parent / "fixtures"


class TestIsNumericId:
    def test_accepts_plain_digits(self):
        assert is_numeric_id("2197626") is True
        assert is_numeric_id("92544") is True
        assert is_numeric_id("0") is True

    def test_rejects_empty_string(self):
        assert is_numeric_id("") is False

    def test_rejects_leading_space(self):
        assert is_numeric_id(" 123") is False

    def test_rejects_pipe_malformed(self):
        assert is_numeric_id(" | Pack of 12") is False

    def test_rejects_alpha(self):
        assert is_numeric_id("abc") is False
        assert is_numeric_id("12a") is False

    def test_rejects_float(self):
        assert is_numeric_id("1.5") is False

    def test_rejects_negative(self):
        assert is_numeric_id("-1") is False

    def test_rejects_pure_whitespace(self):
        assert is_numeric_id("   ") is False


class TestParseJsonDict:
    def test_valid_dict(self):
        result = parse_json_dict('{"category_0": "Food", "category_1": "Dairy"}')
        assert result == {"category_0": "Food", "category_1": "Dairy"}

    def test_nested_null_value(self):
        result = parse_json_dict('{"size_user_friendly": null}')
        assert result == {"size_user_friendly": None}

    def test_dict_with_bool_value(self):
        result = parse_json_dict('{"is_alcohol": false, "storage_type": "frozen"}')
        assert result == {"is_alcohol": False, "storage_type": "frozen"}

    def test_blank_string_returns_empty(self):
        assert parse_json_dict("") == {}
        assert parse_json_dict("   ") == {}

    def test_invalid_json_returns_empty(self):
        assert parse_json_dict("{not json}") == {}
        assert parse_json_dict("{'key': 'val'}") == {}

    def test_json_list_returns_empty(self):
        assert parse_json_dict('["a", "b"]') == {}

    def test_json_scalar_string_returns_empty(self):
        assert parse_json_dict('"hello"') == {}

    def test_json_null_returns_empty(self):
        assert parse_json_dict("null") == {}

    def test_json_number_returns_empty(self):
        assert parse_json_dict("42") == {}

    def test_json_boolean_returns_empty(self):
        assert parse_json_dict("true") == {}
        assert parse_json_dict("false") == {}


class TestParseTags:
    def test_blank_returns_empty(self):
        assert parse_tags("") == []
        assert parse_tags("   ") == []

    def test_empty_braces_returns_empty(self):
        assert parse_tags("{}") == []

    def test_json_list_style(self):
        assert parse_tags('["wegmans brand", "organic"]') == ["wegmans brand", "organic"]

    def test_json_list_single(self):
        assert parse_tags('["kosher"]') == ["kosher"]

    def test_postgres_brace_unquoted(self):
        assert parse_tags("{organic,kosher}") == ["organic", "kosher"]

    def test_postgres_brace_quoted(self):
        assert parse_tags('{"wegmans brand","organic"}') == ["wegmans brand", "organic"]

    def test_postgres_brace_mixed_quoted_unquoted(self):
        assert parse_tags('{"family pack",kosher}') == ["family pack", "kosher"]

    def test_postgres_brace_single_tag(self):
        assert parse_tags("{kosher}") == ["kosher"]

    def test_comma_separated_fallback(self):
        assert parse_tags("organic,gluten free") == ["organic", "gluten free"]

    def test_single_tag_no_comma(self):
        assert parse_tags("kosher") == ["kosher"]

    def test_strips_whitespace_from_each_tag(self):
        assert parse_tags("  organic , kosher  ") == ["organic", "kosher"]

    def test_strips_surrounding_quotes(self):
        # inner quotes stripped by brace parser
        result = parse_tags('{"food you feel good about"}')
        assert result == ["food you feel good about"]


class TestReadProducts:
    def test_mini_a_quarantines_exactly_one_malformed_row(self):
        valid, quarantined = read_products(FIXTURES / "mini_a.csv", "A")
        assert len(quarantined) == 1
        assert quarantined[0]["item_id"] == " | Pack of 12"
        assert quarantined[0]["quarantine_reason"] == "non_numeric_item_id"

    def test_mini_b_has_no_quarantined_rows(self):
        valid, quarantined = read_products(FIXTURES / "mini_b.csv", "B")
        assert quarantined == []

    def test_all_valid_rows_have_numeric_id_and_nonblank_name(self):
        for path, source in [
            (FIXTURES / "mini_a.csv", "A"),
            (FIXTURES / "mini_b.csv", "B"),
        ]:
            valid, _ = read_products(path, source)
            for row in valid:
                assert is_numeric_id(row["item_id"]), (
                    f"{source}: non-numeric item_id {row['item_id']!r}"
                )
                assert row["name"].strip(), (
                    f"{source}: blank name for item_id {row['item_id']!r}"
                )

    def test_pdf_ids_appear_in_valid_rows(self):
        valid_a, _ = read_products(FIXTURES / "mini_a.csv", "A")
        valid_b, _ = read_products(FIXTURES / "mini_b.csv", "B")
        a_ids = {r["item_id"] for r in valid_a}
        b_ids = {r["item_id"] for r in valid_b}
        assert "2197626" in a_ids
        assert "1929544" in a_ids
        assert "92544" in b_ids
        assert "105624" in b_ids

    def test_quarantine_rows_preserve_fields_and_add_reason(self):
        _, quarantined = read_products(FIXTURES / "mini_a.csv", "A")
        for row in quarantined:
            assert "item_id" in row
            assert "name" in row
            assert "quarantine_reason" in row
            assert row["quarantine_reason"]

    def test_invalid_source_raises_value_error(self):
        with pytest.raises(ValueError, match="source"):
            read_products(FIXTURES / "mini_a.csv", "C")

    def test_invalid_source_empty_raises(self):
        with pytest.raises(ValueError):
            read_products(FIXTURES / "mini_a.csv", "")

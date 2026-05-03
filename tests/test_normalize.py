"""Tests for betterbasket_matcher.normalize — Phase 3 regression."""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from betterbasket_matcher.io import read_products
from betterbasket_matcher.normalize import (
    NormalizedProduct,
    SizeInfo,
    _is_private_label_a,
    infer_brand_from_name,
    normalize_product,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def a_by_id():
    valid, _ = read_products(FIXTURES / "mini_a.csv", "A")
    return {r["item_id"]: r for r in valid}


@pytest.fixture(scope="module")
def b_by_id():
    valid, _ = read_products(FIXTURES / "mini_b.csv", "B")
    return {r["item_id"]: r for r in valid}


@pytest.fixture(scope="module")
def match_by_case():
    with open(FIXTURES / "expected_matches.csv", newline="", encoding="utf-8") as fh:
        return {r["case_id"]: r for r in csv.DictReader(fh)}


# ---------------------------------------------------------------------------
# 1. Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_normalize_a_returns_normalized_product(self, a_by_id):
        result = normalize_product(a_by_id["2197626"], "A")
        assert isinstance(result, NormalizedProduct)

    def test_normalize_b_returns_normalized_product(self, b_by_id):
        result = normalize_product(b_by_id["92544"], "B")
        assert isinstance(result, NormalizedProduct)

    def test_size_field_is_size_info(self, a_by_id):
        result = normalize_product(a_by_id["2197626"], "A")
        assert isinstance(result.size, SizeInfo)


# ---------------------------------------------------------------------------
# 2. Brand normalization
# ---------------------------------------------------------------------------

class TestBrandNormalization:
    def test_chobani_a_normalized(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.brand_norm == "chobani"
        assert p.brand_inferred is False

    def test_great_value_is_private_label_a(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.brand_norm == "great value"
        assert p.is_private_label is True
        assert p.brand_inferred is False

    def test_great_value_milk_is_private_label(self, a_by_id):
        # A 9000002: "Great Value Whole Milk, 1 gallon"
        p = normalize_product(a_by_id["9000002"], "A")
        assert p.is_private_label is True

    def test_coca_cola_not_private_label(self, a_by_id):
        p = normalize_product(a_by_id["9000006"], "A")
        assert p.is_private_label is False

    def test_wegmans_b_is_private_label(self, b_by_id):
        # B 105624: brand_raw="Wegmans", tags contain "wegmans brand"
        p = normalize_product(b_by_id["105624"], "B")
        assert p.is_private_label is True

    def test_chobani_b_not_private_label(self, b_by_id):
        p = normalize_product(b_by_id["92544"], "B")
        assert p.is_private_label is False

    def test_brand_norm_is_lowercase(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.brand_norm == p.brand_norm.lower()


# ---------------------------------------------------------------------------
# 3. Size parsing — A side (name-based)
# ---------------------------------------------------------------------------

class TestSizeParsingA:
    def test_chobani_single_5_3_oz(self, a_by_id):
        # A 2197626: "Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.size.unit == "oz"
        assert p.size.unit_size == pytest.approx(5.3)
        assert p.size.pack_count == 1

    def test_tomato_sauce_8_oz(self, a_by_id):
        # A 1929544: "Great Value Organic Tomato Sauce, 8 oz"
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.size.unit == "oz"
        assert p.size.unit_size == pytest.approx(8.0)
        assert p.size.pack_count == 1

    def test_pack_prefix_in_name(self, a_by_id):
        # A 9000004: "(12 Pack) Chobani Non-Fat Plain Greek Yogurt 5.3 oz"
        p = normalize_product(a_by_id["9000004"], "A")
        assert p.size.pack_count == 12
        assert p.size.unit_size == pytest.approx(5.3)
        assert p.size.unit == "oz"

    def test_gallon_unit(self, a_by_id):
        # A 9000002: "Great Value Whole Milk, 1 gallon"
        p = normalize_product(a_by_id["9000002"], "A")
        assert p.size.unit == "gal"
        assert p.size.unit_size == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. Size parsing — B side (sizing_comp-based)
# ---------------------------------------------------------------------------

class TestSizeParsingB:
    def test_chobani_b_5_3_ounce(self, b_by_id):
        # B 92544: sizing_comp size_user_friendly = "5.3 ounce"
        p = normalize_product(b_by_id["92544"], "B")
        assert p.size.unit == "oz"
        assert p.size.unit_size == pytest.approx(5.3)
        assert p.size.pack_count == 1

    def test_tomato_8_ounce(self, b_by_id):
        # B 105624: sizing_comp size_user_friendly = "8 ounce"
        p = normalize_product(b_by_id["105624"], "B")
        assert p.size.unit == "oz"
        assert p.size.unit_size == pytest.approx(8.0)

    def test_pack_x_format_b(self, b_by_id):
        # B 9100003: sizing_comp size_user_friendly = "12 x 5.3 ounce"
        p = normalize_product(b_by_id["9100003"], "B")
        assert p.size.pack_count == 12
        assert p.size.unit_size == pytest.approx(5.3)
        assert p.size.unit == "oz"

    def test_total_size_single(self, b_by_id):
        p = normalize_product(b_by_id["92544"], "B")
        assert p.size.total_size == pytest.approx(5.3)

    def test_total_size_pack(self, b_by_id):
        # 12 x 5.3 = 63.6 oz
        p = normalize_product(b_by_id["9100003"], "B")
        assert p.size.total_size == pytest.approx(12 * 5.3)


# ---------------------------------------------------------------------------
# 5. Organic flag
# ---------------------------------------------------------------------------

class TestOrganicFlag:
    def test_organic_a_from_name(self, a_by_id):
        # A 1929544: "Great Value Organic Tomato Sauce, 8 oz"
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.is_organic is True

    def test_non_organic_a(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.is_organic is False

    def test_organic_b_from_tag(self, b_by_id):
        # B 105624: tags contain "organic"
        p = normalize_product(b_by_id["105624"], "B")
        assert p.is_organic is True

    def test_non_organic_b(self, b_by_id):
        p = normalize_product(b_by_id["92544"], "B")
        assert p.is_organic is False


# ---------------------------------------------------------------------------
# 6. Storage type
# ---------------------------------------------------------------------------

class TestStorageType:
    def test_frozen_a(self, a_by_id):
        # A 9000008: item_info has "storage_type":"frozen"
        p = normalize_product(a_by_id["9000008"], "A")
        assert p.storage_type == "frozen"

    def test_shelf_stable_b(self, b_by_id):
        # B 9100007: item_info has "storage_type":"shelf_stable"
        p = normalize_product(b_by_id["9100007"], "B")
        assert p.storage_type == "shelf_stable"

    def test_frozen_b(self, b_by_id):
        # B 9100012: item_info has "storage_type":"frozen"
        p = normalize_product(b_by_id["9100012"], "B")
        assert p.storage_type == "frozen"

    def test_no_storage_type(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.storage_type is None


# ---------------------------------------------------------------------------
# 7. Form
# ---------------------------------------------------------------------------

class TestForm:
    def test_powder_a(self, a_by_id):
        # A 9000009: item_info has "form":"powder"
        p = normalize_product(a_by_id["9000009"], "A")
        assert p.form == "powder"

    def test_no_form(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.form is None


# ---------------------------------------------------------------------------
# 8. Flavor
# ---------------------------------------------------------------------------

class TestFlavor:
    def test_flavor_a(self, a_by_id):
        # A 9000013: item_info has "flavor":"vanilla"
        p = normalize_product(a_by_id["9000013"], "A")
        assert p.flavor == "vanilla"

    def test_flavor_b(self, b_by_id):
        # B 9100012: item_info has "flavor":"chocolate"
        p = normalize_product(b_by_id["9100012"], "B")
        assert p.flavor == "chocolate"

    def test_no_flavor(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.flavor is None


# ---------------------------------------------------------------------------
# 9. Category
# ---------------------------------------------------------------------------

class TestCategory:
    def test_tomato_sauce_category(self, a_by_id):
        # A 1929544: item_info category_0="Food", category_1="Pantry", category_2="Canned Goods"
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.category_0 == "food"
        assert p.category_1 == "pantry"

    def test_category_lowercase(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.category_0 == p.category_0.lower()

    def test_no_category_is_none(self, a_by_id):
        # Rows without item_info should have None categories
        p = normalize_product(a_by_id["9000006"], "A")
        # category_0 may be None or a string — just confirm it's a str or None
        assert p.category_0 is None or isinstance(p.category_0, str)


# ---------------------------------------------------------------------------
# 10. Retrieval text — national brand included
# ---------------------------------------------------------------------------

class TestRetrievalTextNationalBrand:
    def test_chobani_a_contains_brand(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert "chobani" in p.retrieval_text

    def test_chobani_a_contains_core_name_words(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert "greek" in p.retrieval_text
        assert "yogurt" in p.retrieval_text

    def test_chobani_b_contains_brand(self, b_by_id):
        p = normalize_product(b_by_id["92544"], "B")
        assert "chobani" in p.retrieval_text

    def test_retrieval_text_is_lowercase(self, a_by_id):
        p = normalize_product(a_by_id["2197626"], "A")
        assert p.retrieval_text == p.retrieval_text.lower()


# ---------------------------------------------------------------------------
# 11. Retrieval text — private label brand suppressed
# ---------------------------------------------------------------------------

class TestRetrievalTextPrivateLabel:
    def test_great_value_brand_suppressed(self, a_by_id):
        # A 1929544: "Great Value Organic Tomato Sauce, 8 oz"
        p = normalize_product(a_by_id["1929544"], "A")
        assert "great value" not in p.retrieval_text

    def test_wegmans_brand_suppressed(self, b_by_id):
        # B 105624: brand_raw="Wegmans"
        p = normalize_product(b_by_id["105624"], "B")
        assert "wegmans" not in p.retrieval_text

    def test_private_label_retrieval_text_not_blank(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert p.retrieval_text.strip() != ""

    def test_private_label_retrieval_text_no_leading_space(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert not p.retrieval_text.startswith(" ")

    def test_tomato_retrieval_text_contains_product_words(self, a_by_id):
        p = normalize_product(a_by_id["1929544"], "A")
        assert "tomato" in p.retrieval_text
        assert "sauce" in p.retrieval_text


# ---------------------------------------------------------------------------
# 12. PDF regression — end-to-end normalized attributes
# ---------------------------------------------------------------------------

class TestPdfRegression:
    def test_a_2197626_size_matches_b_92544(self, a_by_id, b_by_id):
        pa = normalize_product(a_by_id["2197626"], "A")
        pb = normalize_product(b_by_id["92544"], "B")
        assert pa.size.unit == pb.size.unit
        assert pa.size.unit_size == pytest.approx(pb.size.unit_size)

    def test_a_1929544_size_matches_b_105624(self, a_by_id, b_by_id):
        pa = normalize_product(a_by_id["1929544"], "A")
        pb = normalize_product(b_by_id["105624"], "B")
        assert pa.size.unit == pb.size.unit
        assert pa.size.unit_size == pytest.approx(pb.size.unit_size)

    def test_a_1929544_size_does_not_match_b_103620(self, a_by_id, b_by_id):
        # B 103620 is the wrong-size 15 oz tomato sauce
        pa = normalize_product(a_by_id["1929544"], "A")
        pb = normalize_product(b_by_id["103620"], "B")
        assert pa.size.unit_size != pytest.approx(pb.size.unit_size)

    def test_a_9000004_pack_matches_b_9100003(self, a_by_id, b_by_id):
        pa = normalize_product(a_by_id["9000004"], "A")
        pb = normalize_product(b_by_id["9100003"], "B")
        assert pa.size.pack_count == pb.size.pack_count
        assert pa.size.unit_size == pytest.approx(pb.size.unit_size)
        assert pa.size.total_size == pytest.approx(pb.size.total_size)


# ---------------------------------------------------------------------------
# Phase 3-fix tests
# ---------------------------------------------------------------------------

def _synthetic_a_row(name, brand_raw="", item_info="", sizing_comp="", tags=""):
    return {
        "item_id": "1",
        "name": name,
        "brand_raw": brand_raw,
        "item_info": item_info,
        "sizing_comp": sizing_comp,
        "tags": tags,
        "is_organic": "",
    }


def _synthetic_b_row(name, brand_raw="", item_info="", sizing_comp="", tags=""):
    return {
        "item_id": "1",
        "name": name,
        "brand_raw": brand_raw,
        "item_info": item_info,
        "sizing_comp": sizing_comp,
        "tags": tags,
        "is_organic": "",
    }


# T1. Brand inference canonical contract
class TestBrandInferenceCanonical:
    def test_great_value(self):
        assert infer_brand_from_name("Great Value Organic Tomato Sauce, 8 oz") == ("great value", True)

    def test_equate_canonical_not_variant(self):
        assert infer_brand_from_name("Equate Extra Strength Headache Relief") == ("equate", True)

    def test_marketside_canonical_not_variant(self):
        assert infer_brand_from_name("Marketside Fresh Caesar Salad Kit") == ("marketside", True)

    def test_bettergoods_canonical_not_variant(self):
        assert infer_brand_from_name("Bettergoods Organic Granola") == ("bettergoods", True)

    def test_apostrophe_brand_normalized(self):
        assert infer_brand_from_name("Ol' Roy Complete Nutrition Dog Food") == ("ol roy", True)

    def test_national_inference_requires_known_brands(self):
        assert infer_brand_from_name("Chobani Whole Milk Greek Yogurt") == (None, False)

    def test_national_with_known_brands_set(self):
        result = infer_brand_from_name("Chobani Whole Milk Greek Yogurt", known_brands={"chobani"})
        assert result == ("chobani", True)

    def test_unknown_brand_returns_none(self):
        assert infer_brand_from_name("Random Unknown Brand Product") == (None, False)

    def test_empty_name_returns_none(self):
        assert infer_brand_from_name("") == (None, False)

    def test_word_boundary_safety_greater_value(self):
        assert infer_brand_from_name("Greater Value Snacks") == (None, False)


# T2. Private-label prefix match with word boundary
class TestPrivateLabelPrefixMatch:
    @pytest.mark.parametrize("brand,expected", [
        ("equate", True),
        ("equate extra", True),
        ("equate beauty", True),
        ("marketside", True),
        ("marketside fresh", True),
        ("bettergoods", True),
        ("bettergoods organic", True),
        ("great value", True),
        ("greater value", False),
        ("equator", False),
        ("ol roy", True),
        ("special kitty", True),
        ("parent s choice", True),
        ("parents choice", True),
        ("clear american", True),
    ])
    def test_membership(self, brand, expected):
        assert _is_private_label_a(brand) is expected


# T3. End-to-end inference for blank brand_raw + Great Value name
class TestBlankBrandInferenceEndToEnd:
    def test_great_value_corn_synthetic(self):
        row = _synthetic_a_row(
            name="Great Value Corn on The Cob",
            brand_raw="",
        )
        p = normalize_product(row, "A")
        assert p.brand_norm == "great value"
        assert p.brand_inferred is True
        assert p.is_private_label is True
        assert "great value" not in p.core_name
        assert "great value" not in p.retrieval_text


# T4. Attribute normalization (item_info path, DEFECT 4)
class TestAttributeItemInfoNormalization:
    def test_uppercase_lowered(self):
        row = _synthetic_a_row(
            name="Whatever Product",
            brand_raw="Brand",
            item_info='{"storage_type":"FROZEN","form":"POWDER","flavor":"VANILLA"}',
        )
        p = normalize_product(row, "A")
        assert p.storage_type == "frozen"
        assert p.form == "powder"
        assert p.flavor == "vanilla"

    def test_non_string_value_returns_none(self):
        row = _synthetic_a_row(
            name="Whatever Product",
            brand_raw="Brand",
            item_info='{"storage_type":["AMBIENT"]}',
        )
        p = normalize_product(row, "A")
        assert p.storage_type is None

    def test_blank_string_returns_none(self):
        row = _synthetic_a_row(
            name="Whatever Product",
            brand_raw="Brand",
            item_info='{"storage_type":"   "}',
        )
        p = normalize_product(row, "A")
        assert p.storage_type is None


# T5. Conservative name-token / tag fallback (DEFECT 4)
class TestAttributeNameFallback:
    def test_storage_frozen_from_name(self):
        row = _synthetic_a_row(name="Birds Eye Frozen Sweet Corn, 16 oz", brand_raw="Birds Eye")
        p = normalize_product(row, "A")
        assert p.storage_type == "frozen"

    def test_form_powder_from_powdered(self):
        row = _synthetic_a_row(name="Quaker Powdered Donut Mix", brand_raw="Quaker")
        p = normalize_product(row, "A")
        assert p.form == "powder"

    def test_flavor_vanilla_from_name(self):
        row = _synthetic_a_row(name="Haagen-Dazs Vanilla Ice Cream", brand_raw="Haagen-Dazs")
        p = normalize_product(row, "A")
        assert p.flavor == "vanilla"

    def test_flavor_chocolate_from_name(self):
        row = _synthetic_a_row(name="Hershey's Chocolate Syrup", brand_raw="Hershey's")
        p = normalize_product(row, "A")
        assert p.flavor == "chocolate"

    def test_no_false_positives(self):
        row = _synthetic_a_row(name="Plain Tomato Sauce", brand_raw="Brand")
        p = normalize_product(row, "A")
        assert p.storage_type is None
        assert p.form is None
        assert p.flavor is None

    def test_b_storage_from_tags(self):
        row = _synthetic_b_row(
            name="Some Cold Item",
            brand_raw="Brand",
            item_info="{}",
            tags='{"frozen"}',
        )
        p = normalize_product(row, "B")
        assert p.storage_type == "frozen"


# T6. Size formatting in retrieval_text (DEFECT 6)
class TestRetrievalTextSizeFormat:
    def test_integral_size_no_dot_zero(self, a_by_id):
        # A 1929544: 8 oz integer-valued
        p = normalize_product(a_by_id["1929544"], "A")
        assert "8oz" in p.retrieval_text
        assert "8.0oz" not in p.retrieval_text

    def test_fractional_size_preserved(self, a_by_id):
        # A 2197626: 5.3 oz fractional
        p = normalize_product(a_by_id["2197626"], "A")
        assert "5.3oz" in p.retrieval_text

    def test_pack_x_integral_size(self):
        row = _synthetic_b_row(
            name="Cola 12 Pack 12 fl oz",
            brand_raw="Coca-Cola",
            sizing_comp='{"size_user_friendly":"12 x 12 fluid ounce"}',
        )
        p = normalize_product(row, "B")
        # Lock format: "{pack}x{size}{unit}" with no .0 noise
        assert "12x12fl oz" in p.retrieval_text
        assert "12.0" not in p.retrieval_text

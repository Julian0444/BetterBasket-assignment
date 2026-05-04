"""Tests for betterbasket_matcher.taxonomy and .scope — Phase 4."""
from __future__ import annotations

from pathlib import Path

import pytest

from betterbasket_matcher.io import read_products
from betterbasket_matcher.normalize import NormalizedProduct, normalize_product
from betterbasket_matcher.taxonomy import (
    assign_matchable_group,
    groups_compatible,
)
from betterbasket_matcher.scope import is_a_in_scope


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def a_by_id():
    valid, _ = read_products(FIXTURES / "mini_a.csv", "A")
    return {r["item_id"]: r for r in valid}


@pytest.fixture(scope="module")
def b_by_id():
    valid, _ = read_products(FIXTURES / "mini_b.csv", "B")
    return {r["item_id"]: r for r in valid}


def _np(source, **kwargs):
    """Build a minimal NormalizedProduct for taxonomy/scope tests."""
    defaults = dict(item_id="x", source=source)
    defaults.update(kwargs)
    return NormalizedProduct(**defaults)


# ---------------------------------------------------------------------------
# 1. Chobani -> dairy
# ---------------------------------------------------------------------------

class TestChobaniDairy:
    def test_a_chobani_dairy(self, a_by_id):
        pa = normalize_product(a_by_id["2197626"], "A")
        # Sanity: fixture must carry plausible dairy/yogurt signal
        ctx = " ".join(filter(None, [pa.category_0, pa.category_1, pa.category_2, pa.core_name]))
        assert "yogurt" in ctx or "dairy" in ctx
        assert assign_matchable_group(pa) == "dairy"

    def test_b_chobani_dairy(self, b_by_id):
        pb = normalize_product(b_by_id["92544"], "B")
        ctx = " ".join(filter(None, [pb.category_0, pb.category_1, pb.category_2, pb.core_name]))
        assert "yogurt" in ctx or "dairy" in ctx
        assert assign_matchable_group(pb) == "dairy"


# ---------------------------------------------------------------------------
# 2. Tomato sauce -> pantry
# ---------------------------------------------------------------------------

class TestTomatoSaucePantry:
    def test_a_tomato_pantry(self, a_by_id):
        pa = normalize_product(a_by_id["1929544"], "A")
        assert assign_matchable_group(pa) == "pantry"

    def test_b_tomato_pantry(self, b_by_id):
        pb = normalize_product(b_by_id["105624"], "B")
        assert assign_matchable_group(pb) == "pantry"


# ---------------------------------------------------------------------------
# 3. Coca-Cola -> beverages
# ---------------------------------------------------------------------------

class TestCocaColaBeverages:
    def test_a_coca_cola(self, a_by_id):
        pa = normalize_product(a_by_id["9000006"], "A")
        assert assign_matchable_group(pa) == "beverages"


# ---------------------------------------------------------------------------
# 4. Cheese maps to cheese or dairy and is dairy-compatible
# ---------------------------------------------------------------------------

class TestCheeseGroup:
    def test_a_kraft_singles_is_cheese_or_dairy(self, a_by_id):
        # 9000010: Kraft Singles, c0=Food, c1=Dairy & Eggs, c2=Cheese
        pa = normalize_product(a_by_id["9000010"], "A")
        assert assign_matchable_group(pa) in {"cheese", "dairy"}

    def test_b_kraft_shredded_is_cheese_or_dairy(self, b_by_id):
        # 9100009: c0=Dairy, c1=Cheese, c2=Shredded Cheese
        pb = normalize_product(b_by_id["9100009"], "B")
        assert assign_matchable_group(pb) in {"cheese", "dairy"}

    def test_compatible_dairy_cheese(self):
        assert groups_compatible("dairy", "cheese") is True
        assert groups_compatible("cheese", "dairy") is True


# ---------------------------------------------------------------------------
# 5. Baby/pets/household/personal_care/health/beauty mapping
# ---------------------------------------------------------------------------

class TestStableTopLevelGroups:
    def test_pets_from_fixture(self, a_by_id):
        # 9000011: c0=Pets
        pa = normalize_product(a_by_id["9000011"], "A")
        assert assign_matchable_group(pa) == "pets"

    def test_baby_synthetic_a(self):
        p = _np("A", category_0="baby", category_1="diapers")
        assert assign_matchable_group(p) == "baby"

    def test_household_synthetic_a(self):
        p = _np("A", category_0="household essentials", category_1="paper goods")
        assert assign_matchable_group(p) == "household"

    def test_personal_care_synthetic_a(self):
        p = _np("A", category_0="personal care", category_1="oral care")
        assert assign_matchable_group(p) == "personal_care"

    def test_health_synthetic_a(self):
        p = _np("A", category_0="health and medicine", category_1="pain relief")
        assert assign_matchable_group(p) == "health"

    def test_beauty_synthetic_a(self):
        p = _np("A", category_0="beauty", category_1="makeup")
        assert assign_matchable_group(p) == "beauty"


# ---------------------------------------------------------------------------
# 6. Out-of-scope A categories
# ---------------------------------------------------------------------------

class TestOutOfScopeCategories:
    def test_toys_fixture(self, a_by_id):
        pa = normalize_product(a_by_id["9000014"], "A")
        ok, reason = is_a_in_scope(pa)
        assert ok is False
        assert reason and isinstance(reason, str)
        assert reason == "excluded_category"

    @pytest.mark.parametrize("cat0", [
        "Toys",
        "Clothing",
        "Electronics",
        "Home Improvement",
        "Books",
        "Cell Phones",
        "Sports & Outdoors",
    ])
    def test_synthetic_out_of_scope(self, cat0):
        p = _np("A", category_0=cat0)
        ok, reason = is_a_in_scope(p)
        assert ok is False
        assert reason == "excluded_category"


# ---------------------------------------------------------------------------
# 7. Home > Kitchen & Dining stays in scope and maps to kitchen_home
# ---------------------------------------------------------------------------

class TestHomeKitchenInScope:
    def test_kitchenaid_in_scope(self, a_by_id):
        pa = normalize_product(a_by_id["9000005"], "A")
        ok, reason = is_a_in_scope(pa)
        assert ok is True
        assert reason and isinstance(reason, str)
        assert reason == "in_scope"
        assert assign_matchable_group(pa) == "kitchen_home"


# ---------------------------------------------------------------------------
# 8. Home > Picture Frames is out of scope
# ---------------------------------------------------------------------------

class TestHomeDecorOutOfScope:
    def test_picture_frame_out_of_scope(self, a_by_id):
        pa = normalize_product(a_by_id["9000015"], "A")
        ok, reason = is_a_in_scope(pa)
        assert ok is False
        assert reason == "excluded_home_decor"


# ---------------------------------------------------------------------------
# 9-12. groups_compatible behavior
# ---------------------------------------------------------------------------

class TestGroupsCompatible:
    def test_dairy_cheese_symmetric(self):
        assert groups_compatible("dairy", "cheese") is True
        assert groups_compatible("cheese", "dairy") is True

    def test_pantry_personal_care_rejected(self):
        assert groups_compatible("pantry", "personal_care") is False
        assert groups_compatible("personal_care", "pantry") is False

    def test_none_or_empty(self):
        assert groups_compatible(None, "dairy") is False
        assert groups_compatible("dairy", None) is False
        assert groups_compatible("", "dairy") is False
        assert groups_compatible("dairy", "") is False
        assert groups_compatible(None, None) is False

    @pytest.mark.parametrize("a,b", [
        ("pantry", "snacks"),
        ("beverages", "wine_beer_spirits"),
        ("prepared_foods", "frozen"),
        ("snacks", "candy"),
        ("dairy", "frozen"),
        ("kitchen_home", "household"),
    ])
    def test_non_exact_non_dairy_cheese_rejected(self, a, b):
        assert groups_compatible(a, b) is False
        assert groups_compatible(b, a) is False

    def test_exact_match(self):
        for g in ("pantry", "snacks", "dairy", "frozen", "kitchen_home", "wine_beer_spirits"):
            assert groups_compatible(g, g) is True


# ---------------------------------------------------------------------------
# 13. is_a_in_scope on a B product returns (True, "not_store_a")
# ---------------------------------------------------------------------------

class TestScopeOnBProduct:
    def test_b_returns_not_store_a(self, b_by_id):
        pb = normalize_product(b_by_id["92544"], "B")
        ok, reason = is_a_in_scope(pb)
        assert ok is True
        assert reason == "not_store_a"


# ---------------------------------------------------------------------------
# 14. Category normalization handles ampersand vs "and"
# ---------------------------------------------------------------------------

class TestCategoryNormalization:
    def test_ampersand_form_out_of_scope(self):
        p = _np("A", category_0="Sports & Outdoors")
        ok, reason = is_a_in_scope(p)
        assert ok is False
        assert reason == "excluded_category"

    def test_and_form_out_of_scope(self):
        p = _np("A", category_0="Sports and Outdoors")
        ok, reason = is_a_in_scope(p)
        assert ok is False
        assert reason == "excluded_category"


# ---------------------------------------------------------------------------
# 15. B-specific taxonomy mappings
# ---------------------------------------------------------------------------

class TestBTaxonomy:
    def test_grocery_pantry(self):
        p = _np("B", category_0="Grocery", category_1="Pantry")
        assert assign_matchable_group(p) == "pantry"

    def test_wine_beer_spirits(self):
        p = _np("B", category_0="Wine, Beer & Spirits")
        assert assign_matchable_group(p) == "wine_beer_spirits"

    def test_more_departments_kitchen_and_home(self):
        p = _np("B", category_0="More Departments", category_1="Kitchen and Home")
        assert assign_matchable_group(p) == "kitchen_home"

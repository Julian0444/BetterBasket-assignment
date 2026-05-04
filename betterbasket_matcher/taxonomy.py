"""Matchable-group taxonomy for the BetterBasket matching pipeline.

Maps a NormalizedProduct's category fields to a stable shared group key so
Phase 6 hard rules can require group equality (with a narrow dairy<->cheese
adjacency). Groups_compatible stays conservative; widen later if Phase 9
calibration shows recall loss, not in Phase 4.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

def _norm_cat(value: Optional[str]) -> str:
    """Lowercase, replace '&' with 'and', collapse punctuation/whitespace."""
    if not value:
        return ""
    s = value.lower().replace("&", " and ")
    s = re.sub(r"[,;./]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Stable group names
# ---------------------------------------------------------------------------

GROUPS: tuple = (
    "pantry",
    "snacks",
    "candy",
    "beverages",
    "dairy",
    "cheese",
    "frozen",
    "produce",
    "meat",
    "seafood",
    "bakery",
    "prepared_foods",
    "baby",
    "pets",
    "household",
    "personal_care",
    "health",
    "beauty",
    "kitchen_home",
    "wine_beer_spirits",
)


# ---------------------------------------------------------------------------
# Store A (Walmart) dispatch
# ---------------------------------------------------------------------------

_A_FOOD_C1: dict = {
    "dairy and eggs": "dairy",
    "dairy": "dairy",
    "pantry": "pantry",
    "canned goods": "pantry",
    "condiments": "pantry",
    "beverages": "beverages",
    "snacks": "snacks",
    "snacks cookies and chips": "snacks",
    "candy": "candy",
    "shop all candy": "candy",
    "frozen": "frozen",
    "frozen foods": "frozen",
    "baking": "pantry",
    "international food": "pantry",
    "breakfast and cereal": "pantry",
    "organic shop": "pantry",
    "coffee": "beverages",
    "meat": "meat",
    "meat and seafood": "meat",
    "seafood": "seafood",
    "bakery": "bakery",
    "bread and bakery": "bakery",
    "bakery and bread": "bakery",
    "shop all bread and bakery": "bakery",
    "holiday baked goods": "bakery",
    "deli": "prepared_foods",
    "produce": "produce",
    "fresh produce": "produce",
    "alcohol": "wine_beer_spirits",
}

_A_TOP_LEVEL: dict = {
    "baby": "baby",
    "pets": "pets",
    "household essentials": "household",
    "household": "household",
    "personal care": "personal_care",
    "health and medicine": "health",
    "health": "health",
    "beauty": "beauty",
    "alcohol": "wine_beer_spirits",
    "wine and spirits": "wine_beer_spirits",
    "wine beer and spirits": "wine_beer_spirits",
}

_A_HOME_KITCHEN_C1 = frozenset({"kitchen and dining"})

_ALCOHOL_TOKENS = ("wine", "beer", "spirits", "liquor")


def _assign_a(product) -> Optional[str]:
    c0 = _norm_cat(product.category_0)
    c1 = _norm_cat(product.category_1)
    c2 = _norm_cat(product.category_2)

    if c0 == "food":
        # Beverages > Wine/Beer/Spirits override
        if c1 == "beverages" and any(t in c2 for t in _ALCOHOL_TOKENS):
            return "wine_beer_spirits"
        # Dairy > Cheese override
        if c1 in ("dairy and eggs", "dairy") and "cheese" in c2:
            return "cheese"
        if c1 == "meat and seafood" and "seafood" in c2:
            return "seafood"
        return _A_FOOD_C1.get(c1)

    if c0 == "home":
        if c1 in _A_HOME_KITCHEN_C1:
            return "kitchen_home"
        return None

    return _A_TOP_LEVEL.get(c0)


# ---------------------------------------------------------------------------
# Store B (Wegmans) dispatch
# ---------------------------------------------------------------------------

_B_GROCERY_C1: dict = {
    "canned tomato products": "pantry",
    "canned tomatoes and italian pantry": "pantry",
    "condiments": "pantry",
    "pantry": "pantry",
    "canned goods": "pantry",
    "pasta and sauce": "pantry",
    "pasta and pasta sauce": "pantry",
    "salad dressing and condiments": "pantry",
    "soups and broths": "pantry",
    "baking and baking ingredients": "pantry",
    "breakfast": "pantry",
    "international foods": "pantry",
    "kosher grocery": "pantry",
    "nut butters jelly and honey": "pantry",
    "oils and vinegars": "pantry",
    "snacks": "snacks",
    "chips and snack foods": "snacks",
    "protein and snack bars": "snacks",
    "candy": "candy",
    "beverages": "beverages",
    "soda": "beverages",
    "baby": "baby",
    "pets": "pets",
    "pet": "pets",
    "household": "household",
    "household essentials": "household",
    "personal care": "personal_care",
    "health": "health",
    "health and medicine": "health",
    "beauty": "beauty",
}

_B_TOP_LEVEL: dict = {
    "frozen": "frozen",
    "produce and floral": "produce",
    "produce": "produce",
    "meat": "meat",
    "seafood": "seafood",
    "cheese": "cheese",
    "bakery": "bakery",
    "prepared foods": "prepared_foods",
    "wine beer and spirits": "wine_beer_spirits",
    "wine and spirits": "wine_beer_spirits",
    "alcohol": "wine_beer_spirits",
    "baby": "baby",
    "pets": "pets",
    "household": "household",
    "personal care": "personal_care",
    "health": "health",
    "beauty": "beauty",
}

_B_MORE_DEPTS_C1: dict = {
    "health and wellness": "health",
    "household essentials": "household",
    "baby and toddler": "baby",
}

_B_PERSONAL_CARE_BEAUTY_C2 = frozenset({
    "makeup and nail care",
    "hair care",
    "facial skin care",
    "lip care",
})

_B_PERSONAL_CARE_C2 = frozenset({
    "bath and body",
    "oral care",
    "deodorant and antiperspirant",
    "hand and body lotion",
    "feminine products",
    "shaving and grooming",
    "travel",
    "hand soap",
    "sun care",
    "essential oils",
    "cotton swabs rounds and balls",
    "hand sanitizer",
})


def _assign_b(product) -> Optional[str]:
    c0 = _norm_cat(product.category_0)
    c1 = _norm_cat(product.category_1)
    c2 = _norm_cat(product.category_2)

    if c0 == "dairy":
        if c1 == "cheese" or "cheese" in c2:
            return "cheese"
        return "dairy"

    if c0 == "more departments":
        if c1 == "kitchen and home":
            return "kitchen_home"
        if c1 == "personal care and makeup":
            if c2 in _B_PERSONAL_CARE_BEAUTY_C2:
                return "beauty"
            if c2 in _B_PERSONAL_CARE_C2:
                return "personal_care"
            return "personal_care"
        if c1 == "bulk foods":
            if "candy" in c2 or "gum" in c2:
                return "candy"
            if any(t in c2 for t in ("nuts", "dried fruit", "snacks", "cookies")):
                return "snacks"
            if "baking" in c2:
                return "pantry"
            return None
        if c1 == "deli":
            if "cheese" in c2:
                return "cheese"
            if any(t in c2 for t in ("ham", "turkey", "chicken", "beef", "charcuterie", "salami")):
                return "meat"
            return "prepared_foods"
        return _B_MORE_DEPTS_C1.get(c1)

    if c0 == "grocery":
        return _B_GROCERY_C1.get(c1)

    return _B_TOP_LEVEL.get(c0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_matchable_group(product) -> Optional[str]:
    """Return the stable shared group key for a NormalizedProduct, or None."""
    if product is None:
        return None
    if product.source == "A":
        return _assign_a(product)
    if product.source == "B":
        return _assign_b(product)
    return None


_DAIRY_CHEESE = frozenset({"dairy", "cheese"})


def groups_compatible(a_group: Optional[str], b_group: Optional[str]) -> bool:
    """Conservative compatibility: exact match plus dairy<->cheese only."""
    if not a_group or not b_group:
        return False
    if a_group == b_group:
        return True
    if a_group in _DAIRY_CHEESE and b_group in _DAIRY_CHEESE:
        return True
    return False

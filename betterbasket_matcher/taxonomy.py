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
    "candy": "candy",
    "frozen": "frozen",
    "frozen foods": "frozen",
    "meat": "meat",
    "meat and seafood": "meat",
    "seafood": "seafood",
    "bakery": "bakery",
    "bread and bakery": "bakery",
    "produce": "produce",
    "fresh produce": "produce",
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
    "condiments": "pantry",
    "pantry": "pantry",
    "canned goods": "pantry",
    "pasta and sauce": "pantry",
    "snacks": "snacks",
    "candy": "candy",
    "beverages": "beverages",
    "soda": "beverages",
    "baby": "baby",
    "pets": "pets",
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


def _assign_b(product) -> Optional[str]:
    c0 = _norm_cat(product.category_0)
    c1 = _norm_cat(product.category_1)
    c2 = _norm_cat(product.category_2)

    if c0 == "dairy":
        if c1 == "cheese" or "cheese" in c2:
            return "cheese"
        return "dairy"

    if c0 == "more departments" and c1 == "kitchen and home":
        return "kitchen_home"

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

"""A-side scope filter for the BetterBasket matching pipeline.

Drops Walmart products from categories Wegmans cannot plausibly carry
(toys, electronics, home decor, etc.) before retrieval. Reasons are stable
strings so audit output can group rejections.
"""
from __future__ import annotations

from typing import Tuple

from betterbasket_matcher.taxonomy import _norm_cat


_OUT_OF_SCOPE_CAT0 = frozenset({
    "toys",
    "clothing",
    "electronics",
    "home improvement",
    "books",
    "cell phones",
    "sports and outdoors",
    "party and occasions",
    "office supplies",
    "auto and tires",
    "arts crafts and sewing",
    "jewelry",
})

_HOME_DECOR_C1 = frozenset({
    "home decor",
    "decor",
    "picture frames",
    "bedding",
    "furniture",
    "rugs",
    "wall art",
})

_HOME_KITCHEN_C1 = frozenset({"kitchen and dining"})


def is_a_in_scope(product) -> Tuple[bool, str]:
    """Return (in_scope, reason). Reason is always a non-empty stable string."""
    if product is None or product.source != "A":
        return (True, "not_store_a")

    c0 = _norm_cat(product.category_0)
    c1 = _norm_cat(product.category_1)

    if c0 in _OUT_OF_SCOPE_CAT0:
        return (False, "excluded_category")

    if c0 == "home":
        if c1 in _HOME_DECOR_C1:
            return (False, "excluded_home_decor")
        if c1 in _HOME_KITCHEN_C1:
            return (True, "in_scope")
        # Other Home subcategories default to in scope; Phase 9 may tighten.
        return (True, "in_scope")

    return (True, "in_scope")

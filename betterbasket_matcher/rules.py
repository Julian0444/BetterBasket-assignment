"""Hard compatibility rules for the BetterBasket matching pipeline.

Phase 6 deterministic rules that run after retrieval and before scoring.
Every rule that requires both sides to have a value short-circuits to
"no rule fires" when either side is unknown (None). Organic mismatch is
intentionally NOT a hard rule (it is a scoring component only).

Rule ordering (first failing rule wins; alcohol runs before the generic
group check so the reason is the more specific one):

    1. alcohol_mismatch
    2. group_mismatch
    3. national_vs_private_label
    4. brand_mismatch
    5. storage_mismatch
    6. form_mismatch
    7. pet_type_mismatch
    8. flavor_mismatch
    9. size_mismatch
    10. pack_mismatch

storage_type/form/pet/flavor are checked before size because differing
forms (powder vs liquid, sliced vs shredded) make total-size comparison
meaningless across the two products.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from betterbasket_matcher.normalize import NormalizedProduct, SizeInfo
from betterbasket_matcher.taxonomy import (
    assign_matchable_group,
    groups_compatible,
)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleResult:
    passed: bool
    reason: str  # stable string; "ok" when passed=True


# ---------------------------------------------------------------------------
# Local unit conversion (Phase 6 only; does not modify normalize.py)
# ---------------------------------------------------------------------------

# (canonical_unit) -> (family, factor_to_base)
#   weight base = oz; volume base = fl oz; count base = ct
_UNIT_FAMILY: dict = {
    "oz":    ("weight", 1.0),
    "lb":    ("weight", 16.0),
    "g":     ("weight", 0.035274),
    "kg":    ("weight", 35.274),
    "fl oz": ("volume", 1.0),
    "ml":    ("volume", 0.033814),
    "l":     ("volume", 33.814),
    "gal":   ("volume", 128.0),
    "ct":    ("count",  1.0),
}


def _to_base_size(size: SizeInfo) -> Optional[Tuple[str, float]]:
    """Return (family, base_value) for a SizeInfo, or None.

    Uses total_size when available; otherwise falls back to unit_size when
    pack_count == 1. Returns None if the unit is unrecognized or no usable
    quantity is present.
    """
    if size is None or not size.unit:
        return None
    fam_factor = _UNIT_FAMILY.get(size.unit)
    if fam_factor is None:
        return None
    family, factor = fam_factor

    value: Optional[float] = None
    if size.total_size is not None:
        value = float(size.total_size)
    elif size.unit_size is not None and size.pack_count == 1:
        value = float(size.unit_size)

    if value is None:
        return None
    return family, value * factor


def _relative_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b))
    if denom == 0.0:
        return 0.0
    return abs(a - b) / denom


# ---------------------------------------------------------------------------
# Brand / private-label helpers
# ---------------------------------------------------------------------------

def _has_known_national_brand(p: NormalizedProduct) -> bool:
    """A confidently known national brand: non-empty, not inferred, not PL."""
    return bool(p.brand_norm) and not p.brand_inferred and not p.is_private_label


# ---------------------------------------------------------------------------
# Alcohol detection
# ---------------------------------------------------------------------------

_ALCOHOL_KEYWORDS_RE = re.compile(
    r"\b(wine|beer|ale|lager|spirits|vodka|whiskey|whisky|rum|gin|tequila|"
    r"liqueur|champagne|sake)\b",
    re.IGNORECASE,
)
_HARD_CIDER_RE = re.compile(r"\bhard\s+cider\b", re.IGNORECASE)

# Phrases that contain an alcohol keyword but describe a non-alcoholic product.
# These act as a negative override on the keyword fallback so e.g. "ginger
# beer", "wine vinegar", "rum cake", "beer cheese" don't get flagged as alcohol.
_NON_ALCOHOL_PHRASE_RE = re.compile(
    r"\b("
    r"ginger\s+ale|"
    r"(?:ginger|root|birch)\s+beer|"
    r"wine\s+vinegar|"
    r"cooking\s+(?:wine|sherry)|"
    r"rum\s+(?:cake|extract|raisin)|"
    r"beer\s+(?:cheese|bread|batter(?:ed)?)|"
    r"non[-\s]?alcoholic|"
    r"alcohol[-\s]?free"
    r")\b",
    re.IGNORECASE,
)


def _is_alcoholic(p: NormalizedProduct, group: Optional[str]) -> bool:
    """True if p is an alcoholic product per group OR core_name keywords.

    A non-alcoholic phrase in core_name (ginger beer, wine vinegar, rum cake,
    beer cheese, etc.) suppresses the keyword fallback. The wine_beer_spirits
    group remains a strong positive signal — department classification trusts
    the source data for that case.
    """
    if group == "wine_beer_spirits":
        return True
    text = p.core_name or ""
    if _NON_ALCOHOL_PHRASE_RE.search(text):
        return False
    if _ALCOHOL_KEYWORDS_RE.search(text) or _HARD_CIDER_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Pet type detection
# ---------------------------------------------------------------------------

_CAT_RE = re.compile(r"\b(cat|kitten)\b", re.IGNORECASE)
_DOG_RE = re.compile(r"\b(dog|puppy)\b", re.IGNORECASE)


def _pet_species(p: NormalizedProduct) -> Optional[str]:
    text = " ".join(filter(None, (p.core_name, p.category_2 or "")))
    has_cat = bool(_CAT_RE.search(text))
    has_dog = bool(_DOG_RE.search(text))
    if has_cat and not has_dog:
        return "cat"
    if has_dog and not has_cat:
        return "dog"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PASS = RuleResult(passed=True, reason="ok")


def evaluate_hard_rules(
    a: NormalizedProduct,
    b: NormalizedProduct,
) -> RuleResult:
    """Run the hard-rule chain; return the first failing reason or 'ok'."""
    group_a = assign_matchable_group(a)
    group_b = assign_matchable_group(b)

    # 1. alcohol_mismatch — runs before group so the reason is specific.
    a_alc = _is_alcoholic(a, group_a)
    b_alc = _is_alcoholic(b, group_b)
    if a_alc != b_alc:
        return RuleResult(passed=False, reason="alcohol_mismatch")

    # 2. group_mismatch
    if not groups_compatible(group_a, group_b):
        return RuleResult(passed=False, reason="group_mismatch")

    # 3. national_vs_private_label
    if a.is_private_label != b.is_private_label:
        # exactly one side is PL; reject if the other has a confident national.
        national_side = b if a.is_private_label else a
        if _has_known_national_brand(national_side):
            return RuleResult(passed=False, reason="national_vs_private_label")

    # 4. brand_mismatch — both confidently known nationals, brands differ.
    if (
        _has_known_national_brand(a)
        and _has_known_national_brand(b)
        and a.brand_norm != b.brand_norm
    ):
        return RuleResult(passed=False, reason="brand_mismatch")

    # 5. storage_mismatch
    if a.storage_type and b.storage_type and a.storage_type != b.storage_type:
        return RuleResult(passed=False, reason="storage_mismatch")

    # 6. form_mismatch
    if a.form and b.form and a.form != b.form:
        return RuleResult(passed=False, reason="form_mismatch")

    # 7. pet_type_mismatch (only meaningful inside the pets group on both
    # sides; when one side's group is unclassified we already rejected above)
    if group_a == "pets" and group_b == "pets":
        sp_a = _pet_species(a)
        sp_b = _pet_species(b)
        if sp_a is not None and sp_b is not None and sp_a != sp_b:
            return RuleResult(passed=False, reason="pet_type_mismatch")

    # 8. flavor_mismatch
    if a.flavor and b.flavor and a.flavor != b.flavor:
        return RuleResult(passed=False, reason="flavor_mismatch")

    # 9. size_mismatch — converted base values, same family only.
    base_a = _to_base_size(a.size)
    base_b = _to_base_size(b.size)
    if base_a is not None and base_b is not None and base_a[0] == base_b[0]:
        if _relative_diff(base_a[1], base_b[1]) > 0.08:
            return RuleResult(passed=False, reason="size_mismatch")

    # 10. pack_mismatch — only when both sides are multipack with differing
    # pack counts AND the converted total/base size also drifts materially.
    # The single-vs-multipack case is handled by size_mismatch (rule 9):
    # any drift large enough to matter (>8%) already triggers there, so a
    # second branch here would be unreachable.
    pa, pb = a.size.pack_count, b.size.pack_count
    if base_a is not None and base_b is not None and base_a[0] == base_b[0]:
        if pa > 1 and pb > 1 and pa != pb:
            diff = _relative_diff(base_a[1], base_b[1])
            if diff > 0.01:
                return RuleResult(passed=False, reason="pack_mismatch")

    return _PASS

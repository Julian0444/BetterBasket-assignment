"""Product normalization for the BetterBasket matching pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from betterbasket_matcher.io import parse_json_dict, parse_tags


# ---------------------------------------------------------------------------
# Private-label brand sets
# ---------------------------------------------------------------------------

# Canonical PL brands only. Variants like "equate extra" are matched via the
# word-boundary prefix rule in _is_private_label_a, not added here.
_PRIVATE_LABEL_A: frozenset = frozenset({
    "great value",
    "marketside",
    "freshness guaranteed",
    "equate",
    "mainstays",
    "bettergoods",
    "sam s choice",
    "sams choice",
    "parent s choice",
    "parents choice",
    "ol roy",
    "special kitty",
    "clear american",
})

# B is private-label when brand_norm == "wegmans" OR a tag signals it
_WEGMANS_TAG_SIGNALS: frozenset[str] = frozenset({"wegmans brand", "wegmans_brand"})


# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------

# Maps raw unit token → (canonical_unit, dimension)
_UNIT_MAP: dict[str, tuple[str, str]] = {
    "ounce": ("oz", "weight"),
    "ounces": ("oz", "weight"),
    "oz": ("oz", "weight"),
    "fluid ounce": ("fl oz", "volume"),
    "fluid ounces": ("fl oz", "volume"),
    "fl oz": ("fl oz", "volume"),
    "fl. oz": ("fl oz", "volume"),
    "fl. oz.": ("fl oz", "volume"),
    "gallon": ("gal", "volume"),
    "gallons": ("gal", "volume"),
    "gal": ("gal", "volume"),
    "pound": ("lb", "weight"),
    "pounds": ("lb", "weight"),
    "lb": ("lb", "weight"),
    "lbs": ("lb", "weight"),
    "gram": ("g", "weight"),
    "grams": ("g", "weight"),
    "g": ("g", "weight"),
    "kilogram": ("kg", "weight"),
    "kilograms": ("kg", "weight"),
    "kg": ("kg", "weight"),
    "ml": ("ml", "volume"),
    "milliliter": ("ml", "volume"),
    "milliliters": ("ml", "volume"),
    "liter": ("l", "volume"),
    "liters": ("l", "volume"),
    "litre": ("l", "volume"),
    "litres": ("l", "volume"),
    "l": ("l", "volume"),
    "count": ("ct", "count"),
    "ct": ("ct", "count"),
    "pk": ("ct", "count"),
    "pack": ("ct", "count"),
    "packs": ("ct", "count"),
}

# Tokens that should be stripped from core_name but are not units
_STRIP_TOKENS: frozenset[str] = frozenset({"cup", "cups", "bottle", "bottles", "can", "cans", "container"})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SizeInfo:
    unit: Optional[str] = None
    unit_size: Optional[float] = None
    pack_count: int = 1
    total_size: Optional[float] = None


@dataclass
class NormalizedProduct:
    item_id: str
    source: str  # "A" or "B"

    brand_norm: str = ""
    is_private_label: bool = False
    brand_inferred: bool = False

    size: SizeInfo = field(default_factory=SizeInfo)
    is_organic: bool = False
    storage_type: Optional[str] = None
    form: Optional[str] = None
    flavor: Optional[str] = None

    category_0: Optional[str] = None
    category_1: Optional[str] = None
    category_2: Optional[str] = None

    core_name: str = ""
    retrieval_text: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_text(s: str) -> str:
    return s.strip().lower()


def _normalize_brand(brand_raw: str) -> str:
    return re.sub(r"\s+", " ", brand_raw.strip().lower())


def _is_private_label_a(brand_norm: str) -> bool:
    """True if brand_norm is a PL or starts with '{pl} ' (word-boundary prefix)."""
    if not brand_norm:
        return False
    return any(
        brand_norm == pl or brand_norm.startswith(pl + " ")
        for pl in _PRIVATE_LABEL_A
    )


def _normalize_name_for_inference(name: str) -> str:
    """Lowercase, hyphens/apostrophes -> space, collapse whitespace."""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_canonical_prefix(text: str, candidates: Iterable[str]) -> Optional[str]:
    """Return the longest candidate that equals text or is a word-boundary prefix."""
    matches = [c for c in candidates if c and (text == c or text.startswith(c + " "))]
    if not matches:
        return None
    return max(matches, key=len)


def infer_brand_from_name(
    name: str,
    known_brands: Optional[Iterable[str]] = None,
) -> tuple:
    """Return (canonical_brand, was_inferred).

    Tries the built-in private-label set first (longest word-boundary prefix
    match), then known_brands if provided. National-brand inference never
    fires when known_brands is None.
    """
    norm = _normalize_name_for_inference(name)
    if not norm:
        return (None, False)

    pl_match = _match_canonical_prefix(norm, _PRIVATE_LABEL_A)
    if pl_match:
        return (pl_match, True)

    if known_brands:
        kb_norm = {b.strip().lower() for b in known_brands if b}
        kb_match = _match_canonical_prefix(norm, kb_norm)
        if kb_match:
            return (kb_match, True)

    return (None, False)


def _is_private_label_b(brand_norm: str, tags: list[str]) -> bool:
    if brand_norm == "wegmans":
        return True
    tag_set = {t.lower().strip() for t in tags}
    return bool(tag_set & _WEGMANS_TAG_SIGNALS)


# ---------------------------------------------------------------------------
# Size parsing
# ---------------------------------------------------------------------------

# Matches: "(12 Pack)" or "(12-Pack)" at the start of a name
_PACK_PREFIX_RE = re.compile(r"^\((\d+)\s*-?\s*pack\)", re.IGNORECASE)

# Matches: "12 x 5.3 ounce" (B sizing_comp format)
_PACK_X_RE = re.compile(
    r"(\d+)\s*[xX×]\s*(\d+(?:\.\d+)?)\s+([a-zA-Z .]+?)(?:\s*$|\s+\()",
    re.IGNORECASE,
)

# Matches a plain size token like "5.3 oz", "8 ounce", "1 gallon"
_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz\.?|ounces?|fluid\s+ounces?|gallons?|pounds?|grams?|kilograms?|"
    r"milliliters?|liters?|litres?|oz|lbs?|kg|ml|l\b|g\b|counts?|pk|ct)",
    re.IGNORECASE,
)


def _lookup_unit(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Return (canonical_unit, dimension) for a raw unit string, or (None, None)."""
    key = raw.strip().lower().rstrip(".")
    # normalize "fl. oz" variants
    key = re.sub(r"\s+", " ", key)
    return _UNIT_MAP.get(key, (None, None))


def _parse_size_from_text(text: str) -> tuple[int, Optional[float], Optional[str]]:
    """Return (pack_count, unit_size, canonical_unit) from a text string.

    Tries pack_x format first, then plain size, then falls back to (1, None, None).
    """
    # Try "12 x 5.3 ounce" format
    m = _PACK_X_RE.search(text)
    if m:
        pack_count = int(m.group(1))
        unit_size = float(m.group(2))
        canonical, _ = _lookup_unit(m.group(3).strip())
        if canonical:
            return pack_count, unit_size, canonical

    # Try plain size "5.3 oz"
    m = _SIZE_RE.search(text)
    if m:
        unit_size = float(m.group(1))
        canonical, _ = _lookup_unit(m.group(2))
        if canonical:
            return 1, unit_size, canonical

    return 1, None, None


def _parse_size_a(row: dict) -> SizeInfo:
    """Parse size for a Store-A row (name-first, then sizing_comp fallback)."""
    name = row.get("name", "")
    pack_count = 1

    # Strip pack prefix from name and extract pack_count
    m_pack = _PACK_PREFIX_RE.match(name.strip())
    if m_pack:
        pack_count = int(m_pack.group(1))
        name = name[m_pack.end():].strip()

    # Try plain size from (stripped) name
    m = _SIZE_RE.search(name)
    if m:
        unit_size = float(m.group(1))
        canonical, _ = _lookup_unit(m.group(2))
        if canonical:
            total = pack_count * unit_size
            return SizeInfo(unit=canonical, unit_size=unit_size, pack_count=pack_count, total_size=total)

    # Fallback: sizing_comp.size_user_friendly
    sc = parse_json_dict(row.get("sizing_comp", ""))
    suf = sc.get("size_user_friendly", "") or ""
    if suf:
        pc, us, cu = _parse_size_from_text(suf)
        if cu:
            actual_pack = pack_count if pc == 1 else pc
            total = actual_pack * us if us is not None else None
            return SizeInfo(unit=cu, unit_size=us, pack_count=actual_pack, total_size=total)

    return SizeInfo(pack_count=pack_count)


def _parse_size_b(row: dict) -> SizeInfo:
    """Parse size for a Store-B row (sizing_comp.size_user_friendly)."""
    sc = parse_json_dict(row.get("sizing_comp", ""))
    suf = sc.get("size_user_friendly", "") or ""
    if suf:
        pc, us, cu = _parse_size_from_text(suf)
        if cu and us is not None:
            total = pc * us
            return SizeInfo(unit=cu, unit_size=us, pack_count=pc, total_size=total)
    return SizeInfo()


# ---------------------------------------------------------------------------
# Attribute extraction
# ---------------------------------------------------------------------------

def _extract_categories(item_info: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    c0 = item_info.get("category_0")
    c1 = item_info.get("category_1")
    c2 = item_info.get("category_2")
    return (
        c0.strip().lower() if isinstance(c0, str) and c0.strip() else None,
        c1.strip().lower() if isinstance(c1, str) and c1.strip() else None,
        c2.strip().lower() if isinstance(c2, str) and c2.strip() else None,
    )


def _is_organic_a(row: dict, item_info: dict) -> bool:
    name_lower = row.get("name", "").lower()
    if "organic" in name_lower:
        return True
    if item_info.get("is_organic") is True:
        return True
    if str(item_info.get("is_organic", "")).lower() == "true":
        return True
    return False


# Attribute normalization. None means "unknown / not detected" — Phase 6 hard
# rules must treat None as no-mismatch rather than rejecting.

def _normalize_attribute(value) -> Optional[str]:
    if isinstance(value, str):
        s = value.strip().lower()
        return s if s else None
    return None


_FROZEN_RE = re.compile(r"\bfrozen\b", re.IGNORECASE)
_REFRIG_RE = re.compile(r"\brefrigerate(?:d)?\b", re.IGNORECASE)

_FORM_PATTERNS = (
    (re.compile(r"\bwhole\s+bean\b", re.IGNORECASE), "whole_bean"),
    (re.compile(r"\bpowder(?:ed)?\b", re.IGNORECASE), "powder"),
    (re.compile(r"\bliquid\b", re.IGNORECASE), "liquid"),
    (re.compile(r"\bsliced\b", re.IGNORECASE), "sliced"),
    (re.compile(r"\bshredded\b", re.IGNORECASE), "shredded"),
    (re.compile(r"\bground\b", re.IGNORECASE), "ground"),
)

_FLAVOR_PATTERNS = (
    (re.compile(r"\bvanilla\b", re.IGNORECASE), "vanilla"),
    (re.compile(r"\bchocolate\b", re.IGNORECASE), "chocolate"),
)


def _storage_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    if _FROZEN_RE.search(name):
        return "frozen"
    if _REFRIG_RE.search(name):
        return "refrigerated"
    return None


def _form_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    for pat, label in _FORM_PATTERNS:
        if pat.search(name):
            return label
    return None


def _flavor_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    for pat, label in _FLAVOR_PATTERNS:
        if pat.search(name):
            return label
    return None


def _storage_from_tags(tags) -> Optional[str]:
    tag_set = {t.strip().lower() for t in tags or []}
    if "frozen" in tag_set:
        return "frozen"
    if "refrigerated" in tag_set:
        return "refrigerated"
    return None


def _is_organic_b(row: dict, item_info: dict, tags: list[str]) -> bool:
    is_org_raw = row.get("is_organic", "")
    if str(is_org_raw).strip().lower() in ("true", "1", "yes"):
        return True
    if item_info.get("is_organic") is True:
        return True
    tag_set = {t.lower().strip() for t in tags}
    if "organic" in tag_set:
        return True
    if "organic" in row.get("name", "").lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Core name and retrieval text
# ---------------------------------------------------------------------------

def _build_core_name(name: str, brand_norm: str) -> str:
    """Strip brand prefix, pack prefix, size tokens, and collapse whitespace."""
    text = name.strip().lower()

    # Remove pack prefix like "(12 pack)"
    text = re.sub(r"^\(\d+\s*-?\s*pack\)\s*", "", text, flags=re.IGNORECASE)

    # Remove brand prefix if it appears at the start
    if brand_norm and text.startswith(brand_norm):
        text = text[len(brand_norm):].strip()

    # Remove size tokens (e.g. "5.3 oz", "1 gallon")
    text = _SIZE_RE.sub("", text)

    # Remove trailing punctuation artifacts and container words
    text = re.sub(r"\b(?:cup|cups|bottle|bottles|can|cans|container)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[,;.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _fmt_num(x) -> str:
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _build_retrieval_text(
    brand_norm: str,
    is_private_label: bool,
    core_name: str,
    size: SizeInfo,
    category_0: Optional[str],
    category_1: Optional[str],
    is_organic: bool,
    storage_type: Optional[str],
    form: Optional[str],
    flavor: Optional[str],
) -> str:
    parts: list[str] = []

    # Brand token: suppressed for private labels
    if not is_private_label and brand_norm:
        parts.append(brand_norm)

    if core_name:
        parts.append(core_name)

    # Size string. Drop trailing ".0" on integer-valued floats so we emit
    # "8oz" rather than "8.0oz".
    if size.unit and size.unit_size is not None:
        us = _fmt_num(size.unit_size)
        if size.pack_count > 1:
            parts.append(f"{size.pack_count}x{us}{size.unit}")
        else:
            parts.append(f"{us}{size.unit}")

    # Category tokens
    for cat in (category_0, category_1):
        if cat:
            parts.append(cat)

    # Attribute tokens
    if is_organic:
        parts.append("organic")
    if storage_type:
        parts.append(storage_type)
    if form:
        parts.append(form)
    if flavor:
        parts.append(flavor)

    return " ".join(filter(None, parts)).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_product(row: dict, source: str) -> NormalizedProduct:
    """Normalize a raw CSV row from store A or B into a NormalizedProduct."""
    item_id = row.get("item_id", "")
    item_info = parse_json_dict(row.get("item_info", ""))
    tags = parse_tags(row.get("tags", "")) if source == "B" else []

    # Brand. For A rows with blank brand_raw, fall back to canonical
    # name-based inference against the private-label set so that, e.g.,
    # "Great Value Corn on The Cob" with brand_raw="" is detected as PL
    # and its brand token is suppressed in retrieval_text.
    brand_raw = row.get("brand_raw", "") or ""
    brand_norm = _normalize_brand(brand_raw)
    brand_inferred = False

    if source == "A" and not brand_norm:
        inferred, was_inferred = infer_brand_from_name(row.get("name", ""))
        if inferred:
            brand_norm = inferred
            brand_inferred = was_inferred

    if source == "A":
        is_pl = _is_private_label_a(brand_norm)
    else:
        is_pl = _is_private_label_b(brand_norm, tags)

    # Size
    if source == "A":
        size = _parse_size_a(row)
    else:
        size = _parse_size_b(row)

    # Organic
    if source == "A":
        is_organic = _is_organic_a(row, item_info)
    else:
        is_organic = _is_organic_b(row, item_info, tags)

    # Other attributes. Normalize to lowercase strings; cascade to a
    # conservative name-token fallback (and B tag fallback for storage)
    # when item_info is missing or malformed. None means "unknown".
    name_for_fallback = row.get("name", "") or ""

    storage_type = _normalize_attribute(item_info.get("storage_type"))
    if storage_type is None:
        storage_type = _storage_from_name(name_for_fallback)
    if storage_type is None and source == "B":
        storage_type = _storage_from_tags(tags)

    form = _normalize_attribute(item_info.get("form"))
    if form is None:
        form = _form_from_name(name_for_fallback)

    flavor = _normalize_attribute(item_info.get("flavor"))
    if flavor is None:
        flavor = _flavor_from_name(name_for_fallback)

    # Categories
    category_0, category_1, category_2 = _extract_categories(item_info)

    # Core name and retrieval text
    core_name = _build_core_name(row.get("name", ""), brand_norm)

    retrieval_text = _build_retrieval_text(
        brand_norm=brand_norm,
        is_private_label=is_pl,
        core_name=core_name,
        size=size,
        category_0=category_0,
        category_1=category_1,
        is_organic=is_organic,
        storage_type=storage_type,
        form=form,
        flavor=flavor,
    )

    return NormalizedProduct(
        item_id=item_id,
        source=source,
        brand_norm=brand_norm,
        is_private_label=is_pl,
        brand_inferred=brand_inferred,
        size=size,
        is_organic=is_organic,
        storage_type=storage_type,
        form=form,
        flavor=flavor,
        category_0=category_0,
        category_1=category_1,
        category_2=category_2,
        core_name=core_name,
        retrieval_text=retrieval_text,
    )

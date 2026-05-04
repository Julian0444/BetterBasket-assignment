#!/usr/bin/env python3
"""Streaming audit for the BetterBasket grocery matching datasets.

The script intentionally avoids pandas so the main audit can run in a small
memory footprint. It emits a JSON stats file and, optionally, a compact
Markdown summary that can be used as evidence for algorithm design.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_A = Path("/Users/jirustaroure/Downloads/grocery_store_a_items_final.csv")
DEFAULT_B = Path("/Users/jirustaroure/Downloads/grocery_store_b_items_final.csv")
DEFAULT_JSON_OUT = Path("docs/audit_stats.json")

JSON_FIELDS = ("item_info", "sizing_comp")
RAW_CATEGORY_FIELDS = ("category", "department", "subcategory")
EMPTY_DECODING = {"", "null", "none", "nan", "[]", "{}"}

PRIVATE_LABEL_A = {
    "bettergoods",
    "clear american",
    "equate",
    "freshness guaranteed",
    "great value",
    "hyper tough",
    "mainstays",
    "marketside",
    "members mark",
    "no boundaries",
    "ol roy",
    "onn",
    "ozark trail",
    "parent s choice",
    "pen gear",
    "sam s choice",
    "special kitty",
    "time and tru",
    "wonder nation",
}

PRIVATE_LABEL_B = {
    "wegmans",
    "wegmans organic",
    "wegmans food you feel good about",
}

LIKELY_NO_B_COUNTERPART_CAT0 = {
    "arts crafts sewing",
    "arts crafts and sewing",
    "auto tires",
    "auto and tires",
    "books",
    "cell phones",
    "clothing",
    "electronics",
    "home improvement",
    "jewelry",
    "office supplies",
    "party occasions",
    "party and occasions",
    "sports outdoors",
    "sports and outdoors",
    "toys",
}

SIZE_UNIT_PATTERN = (
    r"fl\.?\s*oz\.?|fluid\s+ounces?|floz|ounces?|ounce|oz\.?|"
    r"pounds?|lbs?\.?|grams?|g\b|kilograms?|kgs?\.?|"
    r"milliliters?|ml\b|liters?|litres?|l\b|"
    r"gallons?|gal\b|quarts?|qt\b|pints?|pt\b|cups?|"
    r"count|ct\b|each|ea\b|pack|pk\b|sq\.?\s*ft\.?"
)

NUMBER_PATTERN = r"\d+\s+\d+\s*/\s*\d+|\d+(?:\.\d+)?|\d+\s*/\s*\d+"

SIZE_RE = re.compile(
    rf"(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{SIZE_UNIT_PATTERN})",
    re.IGNORECASE,
)

MULTIPACK_RE = re.compile(
    rf"(?P<pack>\d+)\s*(?:x|×)\s*(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{SIZE_UNIT_PATTERN})",
    re.IGNORECASE,
)

PACK_PREFIX_RE = re.compile(
    r"^\s*(?:\(?\d+\s*(?:pack|pk|ct|count)\)?|pack\s+of\s+\d+)\b",
    re.IGNORECASE,
)

DIMENSION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:x|×)\s*\d+(?:\.\d+)?(?:\s*(?:x|×)\s*\d+(?:\.\d+)?)?\b"
)

HTML_RE = re.compile(r"<[^>]+>")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
UPC_KEY_RE = re.compile(r"(?:upc|gtin|ean|barcode|ic_item_id)", re.IGNORECASE)


@dataclass
class ParsedSize:
    raw: str
    value: float
    unit: str
    comparable_value: float
    comparable_unit: str
    pack_count: int | None = None


def pct(part: int | float, whole: int | float) -> float:
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 2)


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("&", " and ")
    text = text.replace("'", " ")
    text = re.sub(r"\b(?:tm|r|reg|registered|trademark)\b", " ", text)
    text = NON_ALNUM_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in EMPTY_DECODING


def parse_json_dict(value: str) -> tuple[dict[str, Any], str]:
    if is_blank(value):
        return {}, "blank"
    try:
        parsed = json.loads(value)
    except Exception:
        return {}, "invalid"
    if isinstance(parsed, dict):
        return parsed, "valid_dict"
    return {}, "valid_other"


def parse_tags(value: str) -> list[str]:
    if is_blank(value):
        return []
    text = value.strip()
    if text == "{}":
        return []
    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            return [text]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return [text]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip('"') for part in inner.split(",") if part.strip()]
    return [text]


def parse_number(value: str) -> float | None:
    value = value.strip()
    mixed_fraction = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", value)
    if mixed_fraction:
        whole, numerator, denominator = mixed_fraction.groups()
        try:
            denom = float(denominator)
            if denom == 0:
                return None
            return float(whole) + float(numerator) / denom
        except Exception:
            return None
    if "/" in value:
        left, right = re.split(r"\s*/\s*", value, maxsplit=1)
        try:
            denom = float(right)
            if denom == 0:
                return None
            return float(left) / denom
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def normalize_unit(unit: str) -> str:
    unit_norm = normalize_text(unit)
    if unit_norm in {"fl oz", "floz", "fluid ounce", "fluid ounces"}: 
        return "fl_oz"
    if unit_norm in {"oz", "ounce", "ounces"}:
        return "oz"
    if unit_norm in {"lb", "lbs", "pound", "pounds"}:
        return "lb"
    if unit_norm in {"g", "gram", "grams"}:
        return "g"
    if unit_norm in {"kg", "kgs", "kilogram", "kilograms"}:
        return "kg"
    if unit_norm in {"ml", "milliliter", "milliliters"}:
        return "ml"
    if unit_norm in {"l", "liter", "liters", "litre", "litres"}:
        return "l"
    if unit_norm in {"gallon", "gallons", "gal"}:
        return "gal"
    if unit_norm in {"quart", "quarts", "qt"}:
        return "qt"
    if unit_norm in {"pint", "pints", "pt"}:
        return "pt"
    if unit_norm in {"cup", "cups"}:
        return "cup"
    if unit_norm in {"count", "ct", "each", "ea", "pack", "pk"}:
        return "count"
    if unit_norm in {"sq ft"}:
        return "sq_ft"
    return unit_norm


def comparable_size(value: float, unit: str) -> tuple[float, str]:
    if unit == "oz":
        return value, "oz_weight"
    if unit == "lb":
        return value * 16.0, "oz_weight"
    if unit == "g":
        return value / 28.349523125, "oz_weight"
    if unit == "kg":
        return value * 35.27396195, "oz_weight"
    if unit == "fl_oz":
        return value, "fl_oz"
    if unit == "ml":
        return value / 29.5735295625, "fl_oz"
    if unit == "l":
        return value * 33.8140227018, "fl_oz"
    if unit == "gal":
        return value * 128.0, "fl_oz"
    if unit == "qt":
        return value * 32.0, "fl_oz"
    if unit == "pt":
        return value * 16.0, "fl_oz"
    if unit == "cup":
        return value * 8.0, "fl_oz"
    if unit == "count":
        return value, "count"
    if unit == "sq_ft":
        return value, "sq_ft"
    return value, unit


def extract_sizes(text: str | None) -> list[ParsedSize]:
    if not text:
        return []
    found: list[ParsedSize] = []

    for match in MULTIPACK_RE.finditer(text):
        value = parse_number(match.group("value"))
        pack = parse_number(match.group("pack"))
        if value is None:
            continue
        unit = normalize_unit(match.group("unit"))
        comparable_value, comparable_unit = comparable_size(value, unit)
        found.append(
            ParsedSize(
                raw=match.group(0),
                value=value,
                unit=unit,
                comparable_value=comparable_value,
                comparable_unit=comparable_unit,
                pack_count=int(pack) if pack is not None else None,
            )
        )

    covered_spans = [m.span() for m in MULTIPACK_RE.finditer(text)]
    for match in SIZE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in covered_spans):
            continue
        value = parse_number(match.group("value"))
        if value is None:
            continue
        unit = normalize_unit(match.group("unit"))
        comparable_value, comparable_unit = comparable_size(value, unit)
        pack_count = int(value) if unit == "count" and value.is_integer() else None
        found.append(
            ParsedSize(
                raw=match.group(0),
                value=value,
                unit=unit,
                comparable_value=comparable_value,
                comparable_unit=comparable_unit,
                pack_count=pack_count,
            )
        )

    return found


def best_size(text: str | None) -> ParsedSize | None:
    sizes = extract_sizes(text)
    if not sizes:
        return None
    non_count = [s for s in sizes if s.comparable_unit not in {"count", "sq_ft"}]
    if non_count:
        return non_count[-1]
    return sizes[-1]


def sizes_conflict(left: ParsedSize, right: ParsedSize) -> bool:
    if left.comparable_unit != right.comparable_unit:
        return True
    if left.comparable_value == 0 or right.comparable_value == 0:
        return False
    diff = abs(left.comparable_value - right.comparable_value)
    return diff / max(left.comparable_value, right.comparable_value) > 0.08


def remove_size_tokens(text: str) -> str:
    text = MULTIPACK_RE.sub(" ", text)
    text = SIZE_RE.sub(" ", text)
    text = DIMENSION_RE.sub(" ", text)
    text = re.sub(r"\b(?:pack of|multi pack|multipack|pack|pk|ct|count|each|ea)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalized_name_group(name: str, brand: str) -> str:
    name_norm = normalize_text(name)
    brand_norm = normalize_text(brand)
    if brand_norm:
        name_norm = re.sub(rf"^\b{re.escape(brand_norm)}\b\s*", "", name_norm)
    name_norm = remove_size_tokens(name_norm)
    name_norm = re.sub(r"\b(?:fresh|new|shop all|online|grocery|brand)\b", " ", name_norm)
    return re.sub(r"\s+", " ", name_norm).strip()


def private_label_a(row: dict[str, str]) -> bool:
    brand = normalize_text(row.get("brand_raw"))
    name = normalize_text(row.get("name"))
    return any(brand == label or name.startswith(label + " ") for label in PRIVATE_LABEL_A)


def private_label_b(row: dict[str, str], tags: Iterable[str]) -> bool:
    brand = normalize_text(row.get("brand_raw"))
    tag_text = " ".join(normalize_text(tag) for tag in tags)
    return (
        brand in PRIVATE_LABEL_B
        or "wegmans brand" in tag_text
        or "wegmans_brand" in str(tags).lower()
    )


def flatten_json_keys(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_json_keys(child, child_key)
    else:
        yield prefix, value


def serialize_counter(counter: Counter, limit: int = 20) -> list[dict[str, Any]]:
    return [{"value": key, "count": count} for key, count in counter.most_common(limit)]


def size_to_dict(size: ParsedSize | None) -> dict[str, Any] | None:
    if size is None:
        return None
    data = asdict(size)
    data["value"] = round(data["value"], 4)
    data["comparable_value"] = round(data["comparable_value"], 4)
    return data


def sample_row(row: dict[str, str], fields: Iterable[str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in fields}


def audit_store(path: Path, label: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "file_size_mb": round(path.stat().st_size / 1024 / 1024, 2),
        "rows": 0,
        "schema": [],
        "blank_counts": {},
        "blank_rates": {},
        "id_quality": {
            "non_numeric_count": 0,
            "non_numeric_examples": [],
            "duplicate_item_id_count": 0,
            "duplicate_item_id_examples": [],
        },
        "json_health": {},
        "upc_like": {
            "field_hits": {},
            "candidate_value_count": 0,
            "plausible_digit_value_count": 0,
            "examples": [],
        },
        "brand": {
            "blank_count": 0,
            "blank_rate": 0.0,
            "unique_nonblank_normalized": 0,
            "top_nonblank": [],
            "food_blank_brand_count": 0,
            "food_row_count": 0,
        },
        "private_label": {
            "estimated_count": 0,
            "top_signals": [],
            "examples": [],
        },
        "categories": {
            "raw_field_blank_counts": {},
            "category_0_top": [],
            "category_1_top": [],
            "category_2_top": [],
            "a_likely_no_b_counterpart_count": 0,
            "a_likely_no_b_counterpart_top": [],
        },
        "size": {
            "name_parse_count": 0,
            "sizing_parse_count": 0,
            "sizing_size_user_friendly_nonblank": 0,
            "both_parse_count": 0,
            "same_comparable_size_count": 0,
            "conflict_count": 0,
            "pack_prefix_count": 0,
            "multipack_count": 0,
            "dimension_like_count": 0,
            "html_description_count": 0,
            "conflict_examples": [],
            "multipack_examples": [],
            "pack_prefix_examples": [],
            "unparsed_sizing_examples": [],
        },
        "tags": {
            "nonempty_count": 0,
            "postgres_style_count": 0,
            "python_list_style_count": 0,
            "top_tags": [],
        },
        "ingredients": {
            "item_info_ingredients_count": 0,
            "description_nonblank_count": 0,
        },
        "known_examples": {
            "chobani_a": [],
            "chobani_b": [],
            "tomato_a": [],
            "tomato_b": [],
        },
        "duplicate_groups": {},
    }

    brand_counter: Counter[str] = Counter()
    brand_norms: Counter[str] = Counter()
    private_label_signals: Counter[str] = Counter()
    cat0_counter: Counter[str] = Counter()
    cat1_counter: Counter[str] = Counter()
    cat2_counter: Counter[str] = Counter()
    no_counterpart_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    field_hit_counter: Counter[str] = Counter()
    json_key_counters: dict[str, Counter[str]] = {field: Counter() for field in JSON_FIELDS}
    json_health: dict[str, Counter[str]] = {field: Counter() for field in JSON_FIELDS}
    ids_seen: set[str] = set()
    duplicate_ids: Counter[str] = Counter()
    blank_counts: Counter[str] = Counter()
    raw_category_blanks: Counter[str] = Counter()
    b_duplicate_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        stats["schema"] = reader.fieldnames or []
        for row in reader:
            stats["rows"] += 1
            rows = stats["rows"]
            item_id = row.get("item_id", "")
            name = row.get("name", "")
            brand_raw = row.get("brand_raw", "")
            brand_norm = normalize_text(brand_raw)

            for column in stats["schema"]:
                if is_blank(row.get(column)):
                    blank_counts[column] += 1

            if not re.fullmatch(r"\d+", item_id or ""):
                stats["id_quality"]["non_numeric_count"] += 1
                if len(stats["id_quality"]["non_numeric_examples"]) < 8:
                    stats["id_quality"]["non_numeric_examples"].append(
                        sample_row(row, ("item_id", "name", "brand_raw", "item_info", "sizing_comp"))
                    )
            if item_id in ids_seen:
                duplicate_ids[item_id] += 1
            ids_seen.add(item_id)

            parsed_json: dict[str, dict[str, Any]] = {}
            for json_field in JSON_FIELDS:
                data, status = parse_json_dict(row.get(json_field, ""))
                parsed_json[json_field] = data
                json_health[json_field][status] += 1
                if status == "valid_dict":
                    for key, value in flatten_json_keys(data):
                        json_key_counters[json_field][key] += 1
                        if UPC_KEY_RE.search(key):
                            field_hit_counter[f"{json_field}.{key}"] += 1
                            if not is_blank(value):
                                value_text = str(value).strip()
                                stats["upc_like"]["candidate_value_count"] += 1
                                if re.fullmatch(r"\d{8,14}", value_text):
                                    stats["upc_like"]["plausible_digit_value_count"] += 1
                                if len(stats["upc_like"]["examples"]) < 8:
                                    stats["upc_like"]["examples"].append(
                                        {
                                            "item_id": item_id,
                                            "name": name,
                                            "key": f"{json_field}.{key}",
                                            "value": value_text,
                                        }
                                    )

            for field in RAW_CATEGORY_FIELDS:
                if is_blank(row.get(field)):
                    raw_category_blanks[field] += 1

            item_info = parsed_json["item_info"]
            sizing_info = parsed_json["sizing_comp"]
            cat0 = item_info.get("category_0")
            cat1 = item_info.get("category_1")
            cat2 = item_info.get("category_2")
            cat0_norm = normalize_text(cat0)
            if cat0:
                cat0_counter[str(cat0)] += 1
            if cat1:
                cat1_counter[str(cat1)] += 1
            if cat2:
                cat2_counter[str(cat2)] += 1
            if label == "A" and cat0_norm in LIKELY_NO_B_COUNTERPART_CAT0:
                stats["categories"]["a_likely_no_b_counterpart_count"] += 1
                no_counterpart_counter[str(cat0)] += 1

            if cat0 == "Food":
                stats["brand"]["food_row_count"] += 1
                if is_blank(brand_raw):
                    stats["brand"]["food_blank_brand_count"] += 1

            if is_blank(brand_raw):
                stats["brand"]["blank_count"] += 1
            else:
                brand_counter[brand_raw] += 1
                brand_norms[brand_norm] += 1

            tags = parse_tags(row.get("tags", ""))
            if tags:
                stats["tags"]["nonempty_count"] += 1
                if row.get("tags", "").strip().startswith("{"):
                    stats["tags"]["postgres_style_count"] += 1
                if row.get("tags", "").strip().startswith("["):
                    stats["tags"]["python_list_style_count"] += 1
                for tag in tags:
                    tag_counter[normalize_text(tag)] += 1

            is_pl = private_label_a(row) if label == "A" else private_label_b(row, tags)
            if is_pl:
                stats["private_label"]["estimated_count"] += 1
                signal = brand_norm or normalize_text(name).split(" ", 2)[:2]
                if isinstance(signal, list):
                    signal = " ".join(signal)
                private_label_signals[str(signal)] += 1
                if len(stats["private_label"]["examples"]) < 12:
                    stats["private_label"]["examples"].append(
                        sample_row(row, ("item_id", "name", "brand_raw", "tags"))
                    )

            name_size = best_size(name)
            sizing_text = str(sizing_info.get("size_user_friendly") or "")
            sizing_size = best_size(sizing_text)
            if name_size:
                stats["size"]["name_parse_count"] += 1
            if not is_blank(sizing_text):
                stats["size"]["sizing_size_user_friendly_nonblank"] += 1
            if sizing_size:
                stats["size"]["sizing_parse_count"] += 1
            elif not is_blank(sizing_text) and len(stats["size"]["unparsed_sizing_examples"]) < 15:
                stats["size"]["unparsed_sizing_examples"].append(
                    {"item_id": item_id, "name": name, "size_user_friendly": sizing_text}
                )
            if name_size and sizing_size:
                stats["size"]["both_parse_count"] += 1
                if sizes_conflict(name_size, sizing_size):
                    stats["size"]["conflict_count"] += 1
                    if len(stats["size"]["conflict_examples"]) < 15:
                        stats["size"]["conflict_examples"].append(
                            {
                                "item_id": item_id,
                                "name": name,
                                "name_size": size_to_dict(name_size),
                                "sizing_size": size_to_dict(sizing_size),
                                "size_user_friendly": sizing_text,
                            }
                        )
                else:
                    stats["size"]["same_comparable_size_count"] += 1

            if PACK_PREFIX_RE.search(name or ""):
                stats["size"]["pack_prefix_count"] += 1
                if len(stats["size"]["pack_prefix_examples"]) < 12:
                    stats["size"]["pack_prefix_examples"].append(
                        sample_row(row, ("item_id", "name", "brand_raw"))
                    )
            if MULTIPACK_RE.search((name or "") + " " + sizing_text):
                stats["size"]["multipack_count"] += 1
                if len(stats["size"]["multipack_examples"]) < 12:
                    stats["size"]["multipack_examples"].append(
                        {
                            "item_id": item_id,
                            "name": name,
                            "brand_raw": brand_raw,
                            "size_user_friendly": sizing_text,
                        }
                    )
            if DIMENSION_RE.search((name or "") + " " + sizing_text):
                stats["size"]["dimension_like_count"] += 1
            if HTML_RE.search(row.get("description", "") or ""):
                stats["size"]["html_description_count"] += 1

            if item_info.get("ingredients"):
                stats["ingredients"]["item_info_ingredients_count"] += 1
            if not is_blank(row.get("description")):
                stats["ingredients"]["description_nonblank_count"] += 1

            name_norm = normalize_text(name)
            example_payload = {
                "item_id": item_id,
                "name": name,
                "brand_raw": brand_raw,
                "category_0": cat0,
                "category_1": cat1,
                "category_2": cat2,
                "size_user_friendly": sizing_text,
                "name_size": size_to_dict(name_size),
                "sizing_size": size_to_dict(sizing_size),
            }
            if (
                "chobani" in name_norm
                and "honey" in name_norm
                and "blended" in name_norm
                and len(stats["known_examples"]["chobani_a" if label == "A" else "chobani_b"]) < 12
            ):
                stats["known_examples"]["chobani_a" if label == "A" else "chobani_b"].append(
                    example_payload
                )
            if (
                "organic tomato sauce" in name_norm
                and ("great value" in name_norm or "wegmans" in name_norm)
                and len(stats["known_examples"]["tomato_a" if label == "A" else "tomato_b"]) < 20
            ):
                stats["known_examples"]["tomato_a" if label == "A" else "tomato_b"].append(
                    example_payload
                )

            if label == "B":
                key = (brand_norm, normalized_name_group(name, brand_raw))
                if key[1]:
                    b_duplicate_groups[key].append(
                        {
                            "item_id": item_id,
                            "name": name,
                            "brand_raw": brand_raw,
                            "size_user_friendly": sizing_text,
                        }
                    )

            if rows % 100000 == 0:
                pass

    rows = stats["rows"]
    stats["blank_counts"] = dict(blank_counts)
    stats["blank_rates"] = {column: pct(count, rows) for column, count in blank_counts.items()}
    stats["id_quality"]["duplicate_item_id_count"] = sum(duplicate_ids.values())
    stats["id_quality"]["duplicate_item_id_examples"] = [
        {"item_id": key, "repeat_count": count + 1} for key, count in duplicate_ids.most_common(10)
    ]
    stats["json_health"] = {
        field: {
            **dict(json_health[field]),
            "top_keys": serialize_counter(json_key_counters[field], 25),
        }
        for field in JSON_FIELDS
    }
    stats["upc_like"]["field_hits"] = dict(field_hit_counter)
    stats["brand"]["blank_rate"] = pct(stats["brand"]["blank_count"], rows)
    stats["brand"]["unique_nonblank_normalized"] = len(brand_norms)
    stats["brand"]["top_nonblank"] = serialize_counter(brand_counter, 25)
    stats["brand"]["normalized_counts_top"] = serialize_counter(brand_norms, 25)
    stats["brand"]["normalized_counts_all"] = dict(brand_norms)
    stats["private_label"]["top_signals"] = serialize_counter(private_label_signals, 25)
    stats["categories"]["raw_field_blank_counts"] = dict(raw_category_blanks)
    stats["categories"]["raw_field_blank_rates"] = {
        field: pct(count, rows) for field, count in raw_category_blanks.items()
    }
    stats["categories"]["category_0_top"] = serialize_counter(cat0_counter, 25)
    stats["categories"]["category_1_top"] = serialize_counter(cat1_counter, 25)
    stats["categories"]["category_2_top"] = serialize_counter(cat2_counter, 25)
    stats["categories"]["a_likely_no_b_counterpart_top"] = serialize_counter(no_counterpart_counter, 25)
    stats["tags"]["top_tags"] = serialize_counter(tag_counter, 25)

    for key in (
        "name_parse_count",
        "sizing_parse_count",
        "sizing_size_user_friendly_nonblank",
        "both_parse_count",
        "same_comparable_size_count",
        "conflict_count",
        "pack_prefix_count",
        "multipack_count",
        "dimension_like_count",
        "html_description_count",
    ):
        stats["size"][f"{key}_rate"] = pct(int(stats["size"][key]), rows)

    if label == "B":
        duplicate_groups = {key: rows_ for key, rows_ in b_duplicate_groups.items() if len(rows_) > 1}
        stats["duplicate_groups"] = {
            "group_count": len(duplicate_groups),
            "row_count": sum(len(rows_) for rows_ in duplicate_groups.values()),
            "largest_groups": [
                {
                    "brand": key[0],
                    "normalized_name": key[1],
                    "count": len(rows_),
                    "items": rows_[:20],
                }
                for key, rows_ in sorted(
                    duplicate_groups.items(), key=lambda item: len(item[1]), reverse=True
                )[:20]
            ],
            "keyword_examples": duplicate_keyword_examples(duplicate_groups),
        }

    return stats


def duplicate_keyword_examples(
    duplicate_groups: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    examples: dict[str, list[dict[str, Any]]] = {}
    keywords = ("water", "candy", "tomato sauce", "soda", "cola", "yogurt", "dressing")
    for keyword in keywords:
        rows_for_keyword: list[dict[str, Any]] = []
        for (brand, name), rows in duplicate_groups.items():
            haystack = f"{brand} {name}"
            if keyword in haystack:
                rows_for_keyword.append(
                    {
                        "brand": brand,
                        "normalized_name": name,
                        "count": len(rows),
                        "items": rows[:15],
                    }
                )
        rows_for_keyword.sort(key=lambda group: group["count"], reverse=True)
        examples[keyword] = rows_for_keyword[:5]
    return examples


def compute_cross_store_stats(a_stats: dict[str, Any], b_stats: dict[str, Any]) -> dict[str, Any]:
    a_brands = a_stats["brand"].get("normalized_counts_all", {})
    b_brands = b_stats["brand"].get("normalized_counts_all", {})
    shared = sorted(set(a_brands) & set(b_brands))
    a_shared_rows = sum(a_brands[brand] for brand in shared)
    b_shared_rows = sum(b_brands[brand] for brand in shared)
    a_nonblank = a_stats["rows"] - a_stats["brand"]["blank_count"]
    b_nonblank = b_stats["rows"] - b_stats["brand"]["blank_count"]
    return {
        "shared_normalized_brand_count": len(shared),
        "shared_normalized_brand_examples": shared[:100],
        "a_branded_rows_with_brand_in_b": a_shared_rows,
        "a_branded_rows_with_brand_in_b_rate": pct(a_shared_rows, a_nonblank),
        "b_branded_rows_with_brand_in_a": b_shared_rows,
        "b_branded_rows_with_brand_in_a_rate": pct(b_shared_rows, b_nonblank),
    }


def write_markdown(stats: dict[str, Any], path: Path) -> None:
    a = stats["stores"]["A"]
    b = stats["stores"]["B"]
    cross = stats["cross_store"]

    def row(label: str, key: str) -> str:
        return f"| {label} | {a[key]:,} | {b[key]:,} |"

    lines = [
        "# BetterBasket Dataset Audit Stats",
        "",
        "Generated by `scripts/audit_data.py`.",
        "",
        "## Core Counts",
        "",
        "| Metric | Store A | Store B |",
        "|---|---:|---:|",
        row("Rows", "rows"),
        f"| File size MB | {a['file_size_mb']} | {b['file_size_mb']} |",
        "",
        "## Brand Coverage",
        "",
        "| Metric | Store A | Store B |",
        "|---|---:|---:|",
        f"| Blank brand rows | {a['brand']['blank_count']:,} ({a['brand']['blank_rate']}%) | {b['brand']['blank_count']:,} ({b['brand']['blank_rate']}%) |",
        f"| Unique normalized nonblank brands | {a['brand']['unique_nonblank_normalized']:,} | {b['brand']['unique_nonblank_normalized']:,} |",
        f"| Private-label estimate | {a['private_label']['estimated_count']:,} | {b['private_label']['estimated_count']:,} |",
        "",
        f"Shared normalized brands: {cross['shared_normalized_brand_count']:,}.",
        "",
        "## Size Coverage",
        "",
        "| Metric | Store A | Store B |",
        "|---|---:|---:|",
        f"| Size parsed from name | {a['size']['name_parse_count']:,} ({a['size']['name_parse_count_rate']}%) | {b['size']['name_parse_count']:,} ({b['size']['name_parse_count_rate']}%) |",
        f"| `sizing_comp.size_user_friendly` nonblank | {a['size']['sizing_size_user_friendly_nonblank']:,} ({a['size']['sizing_size_user_friendly_nonblank_rate']}%) | {b['size']['sizing_size_user_friendly_nonblank']:,} ({b['size']['sizing_size_user_friendly_nonblank_rate']}%) |",
        f"| Size parsed from sizing | {a['size']['sizing_parse_count']:,} ({a['size']['sizing_parse_count_rate']}%) | {b['size']['sizing_parse_count']:,} ({b['size']['sizing_parse_count_rate']}%) |",
        f"| Conflicts when both parse | {a['size']['conflict_count']:,} | {b['size']['conflict_count']:,} |",
        "",
        "## UPC-Like Fields",
        "",
        f"- Store A candidate values: {a['upc_like']['candidate_value_count']:,}; plausible digit values: {a['upc_like']['plausible_digit_value_count']:,}.",
        f"- Store B candidate values: {b['upc_like']['candidate_value_count']:,}; plausible digit values: {b['upc_like']['plausible_digit_value_count']:,}.",
        "",
        "## B Duplicate Groups",
        "",
        f"- Groups: {b['duplicate_groups'].get('group_count', 0):,}.",
        f"- Rows in duplicate groups: {b['duplicate_groups'].get('row_count', 0):,}.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-csv", type=Path, default=DEFAULT_A)
    parser.add_argument("--b-csv", type=Path, default=DEFAULT_B)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--markdown-out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    a_stats = audit_store(args.a_csv, "A")
    b_stats = audit_store(args.b_csv, "B")
    stats = {
        "stores": {"A": a_stats, "B": b_stats},
        "cross_store": compute_cross_store_stats(a_stats, b_stats),
    }

    # The all-brand maps are useful internally for cross-store stats but noisy in
    # human-facing JSON. Keep the top counters and remove the full maps.
    for store_stats in stats["stores"].values():
        store_stats["brand"].pop("normalized_counts_all", None)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
    if args.markdown_out:
        write_markdown(stats, args.markdown_out)

    print(f"Wrote {args.json_out}")
    if args.markdown_out:
        print(f"Wrote {args.markdown_out}")
    print(
        "Rows: "
        f"A={a_stats['rows']:,}, B={b_stats['rows']:,}; "
        f"shared brands={stats['cross_store']['shared_normalized_brand_count']:,}; "
        f"B duplicate groups={b_stats['duplicate_groups'].get('group_count', 0):,}"
    )


if __name__ == "__main__":
    main()

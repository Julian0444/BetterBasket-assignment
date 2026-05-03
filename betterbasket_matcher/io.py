"""IO and tolerant parsing for the BetterBasket matching pipeline."""
import ast
import csv
import json
import re
from pathlib import Path
from typing import Union


def is_numeric_id(value: str) -> bool:
    """Return True only when value matches ^\\d+$ (no spaces, letters, or symbols)."""
    return bool(re.fullmatch(r"\d+", value))


def parse_json_dict(value: str) -> dict:
    """Parse a JSON object string into a dict.

    Returns {} for blank input, invalid JSON, or any JSON type that is not a dict
    (lists, scalars, null, booleans, numbers).
    """
    if not value or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def parse_tags(value: str) -> list:
    """Parse a B-side tags field into a list of lowercase-preserving tag strings.

    Handles:
    - blank / whitespace-only  -> []
    - {}                       -> []
    - ["tag1", "tag2"]         -> JSON list parse via ast.literal_eval
    - {tag1,tag2}              -> Postgres brace-style, unquoted
    - {"tag1","tag2"}          -> Postgres brace-style, quoted
    - tag1,tag2                -> comma-separated fallback
    - single value             -> [value]

    Surrounding whitespace and quotes are stripped from each tag.
    """
    if not value or not value.strip():
        return []
    text = value.strip()
    if text == "{}":
        return []

    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            pass
        else:
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        # fallback: treat the raw bracketed text as comma-separated after stripping brackets
        inner = text[1:-1] if text.endswith("]") else text
        return [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]

    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip('"') for part in inner.split(",") if part.strip()]

    # comma-separated fallback (includes plain single-tag strings)
    return [t.strip() for t in text.split(",") if t.strip()]


def read_products(
    path: Union[str, Path],
    source: str,
) -> tuple:
    """Read a store CSV and split rows into valid and quarantined lists.

    Args:
        path:   Path to the CSV file.
        source: "A" for Store A (Walmart) or "B" for Store B (Wegmans).

    Returns:
        (valid_rows, quarantined_rows) where every valid row has a numeric
        item_id and a nonblank name. Quarantined rows include all original
        fields plus a "quarantine_reason" key.

    Raises:
        ValueError: if source is not "A" or "B".
    """
    if source not in ("A", "B"):
        raise ValueError(f"source must be 'A' or 'B', got {source!r}")

    valid: list = []
    quarantined: list = []

    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            item_id = row.get("item_id", "")
            name = row.get("name", "")
            if not is_numeric_id(item_id):
                quarantined.append({**row, "quarantine_reason": "non_numeric_item_id"})
            elif not name.strip():
                quarantined.append({**row, "quarantine_reason": "blank_name"})
            else:
                valid.append(row)

    return valid, quarantined

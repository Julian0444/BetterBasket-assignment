"""Sanity tests for Phase 1 regression fixtures."""
import csv
import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

A_HEADER = [
    "item_id", "name", "brand_raw", "name_clean", "description", "category",
    "department", "url", "item_type", "item_info", "tags", "subcategory",
    "is_private_label", "sizing_comp", "size_raw", "datapoint_id",
    "raw_data_id", "created_at_utc", "updated_at_utc",
]
B_HEADER = [
    "item_id", "name", "brand_raw", "name_clean", "description", "category",
    "department", "url", "is_organic", "item_info", "tags", "subcategory",
    "sizing_comp", "size_raw", "datapoint_id", "raw_data_id",
    "created_at_utc", "updated_at_utc",
]
MATCHES_HEADER = ["item_id_A", "item_id_B", "case_id", "reason"]

REQUIRED_CASE_IDS = {
    "pdf_chobani",
    "pdf_tomato_8oz",
    "tomato_wrong_size_variants_present",
    "private_label_cross_store",
    "national_brand_exact",
    "national_vs_private_label_reject",
    "pack_prefix",
    "size_mismatch",
    "frozen_storage_mismatch",
    "powder_liquid_mismatch",
    "sliced_shredded_mismatch",
    "cat_dog_mismatch",
    "alcohol_mismatch",
    "flavor_mismatch",
    "out_of_scope_category",
    "home_kitchen_in_scope",
    "home_decor_out_of_scope",
}

MALFORMED_ID = " | Pack of 12"
PDF_A_IDS = {"2197626", "1929544"}
PDF_B_IDS = {"92544", "105624"}


def _load(filename):
    path = FIXTURES / filename
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def test_a_header_exact():
    fieldnames, _ = _load("mini_a.csv")
    assert fieldnames == A_HEADER, f"A header mismatch:\n  got:      {fieldnames}\n  expected: {A_HEADER}"


def test_b_header_exact():
    fieldnames, _ = _load("mini_b.csv")
    assert fieldnames == B_HEADER, f"B header mismatch:\n  got:      {fieldnames}\n  expected: {B_HEADER}"


def test_matches_header_exact():
    fieldnames, _ = _load("expected_matches.csv")
    assert fieldnames == MATCHES_HEADER, (
        f"Matches header mismatch:\n  got: {fieldnames}\n  expected: {MATCHES_HEADER}"
    )


def test_pdf_ids_present():
    _, a_rows = _load("mini_a.csv")
    _, b_rows = _load("mini_b.csv")
    a_ids = {r["item_id"] for r in a_rows}
    b_ids = {r["item_id"] for r in b_rows}
    assert PDF_A_IDS <= a_ids, f"Missing A PDF IDs: {PDF_A_IDS - a_ids}"
    assert PDF_B_IDS <= b_ids, f"Missing B PDF IDs: {PDF_B_IDS - b_ids}"


def test_exactly_one_malformed_a_row():
    _, rows = _load("mini_a.csv")
    malformed = [r for r in rows if r["item_id"] == MALFORMED_ID]
    assert len(malformed) == 1, f"Expected 1 malformed row, found {len(malformed)}"


def test_malformed_not_in_expected_matches():
    _, rows = _load("expected_matches.csv")
    for row in rows:
        assert row["item_id_A"] != MALFORMED_ID, "Malformed ID found in item_id_A"
        assert row["item_id_B"] != MALFORMED_ID, "Malformed ID found in item_id_B"


def test_expected_matches_a_ids_numeric_and_in_mini_a():
    _, a_rows = _load("mini_a.csv")
    _, m_rows = _load("expected_matches.csv")
    a_ids = {r["item_id"] for r in a_rows}
    for row in m_rows:
        aid = row["item_id_A"]
        assert re.fullmatch(r"\d+", aid), f"Non-numeric item_id_A: {aid!r}"
        assert aid in a_ids, f"item_id_A {aid!r} missing from mini_a.csv"


def test_expected_matches_b_ids_in_mini_b():
    _, b_rows = _load("mini_b.csv")
    _, m_rows = _load("expected_matches.csv")
    b_ids = {r["item_id"] for r in b_rows}
    for row in m_rows:
        bid = row["item_id_B"]
        if bid != "NO_MATCH":
            assert bid in b_ids, f"item_id_B {bid!r} missing from mini_b.csv"


def test_at_least_one_match_and_one_no_match():
    _, rows = _load("expected_matches.csv")
    matches = [r for r in rows if r["item_id_B"] != "NO_MATCH"]
    no_matches = [r for r in rows if r["item_id_B"] == "NO_MATCH"]
    assert len(matches) >= 1, "No accepted matches in expected_matches.csv"
    assert len(no_matches) >= 1, "No NO_MATCH rows in expected_matches.csv"


def test_unique_item_id_a_in_expected_matches():
    _, rows = _load("expected_matches.csv")
    ids = [r["item_id_A"] for r in rows]
    dupes = [x for x in ids if ids.count(x) > 1]
    assert len(ids) == len(set(ids)), f"Duplicate item_id_A values: {sorted(set(dupes))}"


def test_all_required_case_ids_present():
    _, rows = _load("expected_matches.csv")
    found = {r["case_id"] for r in rows}
    missing = REQUIRED_CASE_IDS - found
    assert not missing, f"Missing case_ids: {sorted(missing)}"


def test_json_fields_are_valid_dicts():
    for filename in ("mini_a.csv", "mini_b.csv"):
        _, rows = _load(filename)
        for row in rows:
            for col in ("item_info", "sizing_comp"):
                val = row.get(col, "").strip()
                if not val:
                    continue
                try:
                    parsed = json.loads(val)
                except json.JSONDecodeError as exc:
                    raise AssertionError(
                        f"{filename} item_id={row.get('item_id')!r} col={col!r} "
                        f"value={val!r} is not valid JSON: {exc}"
                    ) from exc
                assert isinstance(parsed, dict), (
                    f"{filename} item_id={row.get('item_id')!r} col={col!r} "
                    f"parsed to {type(parsed).__name__}, expected dict"
                )
